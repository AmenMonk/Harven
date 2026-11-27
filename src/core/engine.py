# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
AI对话核心引擎 v2.0
整合模型调用 + 查询流程（知识库→推理）+ 记忆持久化
"""
import re
import json
import threading
import yaml
from pathlib import Path
from datetime import datetime
from src.core.model_manager import ModelManager
from src.core.identity import SYSTEM_PROMPT, IDENTITY_PROMPT, VOICE_DISCIPLINE
from src.models.database import save_message, get_messages, get_preference
from src.core.event_bus import get_event_bus, Events
import logging
logger = logging.getLogger(__name__)

# ★ 2026-10-10 #1052 生成停止门（定版方案A·真停止）
# 链路：前端点「⏹ 停止」→ web_app /api/chat/stop → request_stop(conv_id)
#      → send() 流式循环每块检查 is_stopped → 命中即收笔，半截回复照常入库并追加「（已停止）」
_stop_flags = {}
_stop_lock = threading.Lock()

# ★ #1432（2026-11-04 r48）带图录入触发词：带图消息文字命中任一词 → 走面板录入链
#   （不再被看图直答截胡）。保守词表：只收「明确表达入库意图」的说法，避免把
#   「帮我看看这张图」之类的看图提问误拦。pure_chat 浮窗不参与。
_IMAGE_INTAKE_TRIGGERS = (
    '存入面板', '存到面板', '记到面板', '记录到面板', '入面板',
    '记到台账', '记入台账', '记到帐', '记台账', '入台账',
    '入库存档', '存入档案', '记到档案', '存入物品', '物品入库', '记物品',
)


def request_stop(conversation_id):
    """标记某会话的当前生成需要停止"""
    with _stop_lock:
        _stop_flags[str(conversation_id)] = True


def is_stopped(conversation_id) -> bool:
    """流式循环检查点用：该会话是否被要求停止"""
    return _stop_flags.get(str(conversation_id), False)


def clear_stop(conversation_id):
    """清除停止标记（新请求开始时清残留；生成结束后兜底清理）"""
    with _stop_lock:
        _stop_flags.pop(str(conversation_id), None)

# ── Prompt Injection 防御 ──
# 聊天内容 ≠ 系统指令。检测用户试图覆盖系统提示词的注入攻击。
PROMPT_INJECTION_PATTERNS = [
    # 中文：忽略/覆盖指令
    r"忽略.{0,6}(之前|上文|上面|所有|一切).{0,6}(指令|提示|规则|限制|约束|设定|系统)",
    r"(现在|从此|从这一刻).{0,4}起.{0,4}(你|已经).{0,6}(是|变成|不受|不遵守).{0,6}(限制|约束|规则|指令)",
    r"新(的|增)?(系统)?指令[：:]",
    r"以上(规则|指令|内容|设定)(全部)?(作废|无效|取消|覆盖|替换)",
    r"重新(定义|设定|配置|设置).{0,4}(你的|角色|身份|人格|规则)",
    r"绕过.{0,6}(限制|安全|审核|过滤|检查|规则)",
    r"(覆盖|替换).{0,4}(系统|指令|提示词|prompt)",
    r"你(现在)?.{0,4}(不再|不是).{0,4}(AI|助手|助理|Harven)",
    r"(别管|不要管|不用管).{0,6}(之前|上面|系统)",
    r"忘记(上面|之前|所有).{0,6}(说|告诉|设定|配置)",
    # 英文：forget/ignore/bypass
    r"forget\s+(everything|all|previous)\b.*(instruction|prompt|rule)",
    r"ignore\s+(previous|above|all)\b.{0,10}(instruction|prompt|rule)",
    r"you\s+are\s+now\b(?!.*(?:welcome|ready|connected|prompted))",
    r"bypass\s+(restriction|safety|filter|guard)",
    r"override\s+(system|instruction|prompt)",
    r"^\s*#{1,3}\s*system\s*[:\n]",
    r"^\s*system\s*(prompt|instruction)\s*[:\n]",
    r"new\s+system\s+(prompt|instruction)\s*:",
    r"disregard\s+(previous|above|all)\b",
]

def detect_prompt_injection(user_message: str) -> bool:
    """检测用户消息是否包含系统指令覆盖尝试（prompt injection）"""
    msg_lower = user_message.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return True
    return False


# ★ 2026-07-21: 外脑模型联网能力标记
# 标记哪些外脑模型的 API 自带联网能力（不需要哈文搜索就能查实时信息）
# True = 模型 API 自带联网 | False = 纯 LLM，需要哈文搜索提供数据
_MODEL_INTERNET_CAPABILITY = {
    "deepseek": False,       # DeepSeek API 纯 LLM
    "doubao": False,         # 豆包 API 纯 LLM
    "gpt-4": False,          # GPT-4 API 需自己调 web_search 工具
    "gpt-3.5": False,        # GPT-3.5 纯 LLM
    "claude": False,         # Claude API 纯 LLM
    "ollama": False,         # 本地模型不联网
    "gemini": True,          # Gemini API 自带 Google 搜索
    "perplexity": True,      # Perplexity API 联网是核心功能
    "wenxin": True,           # 文心一言 API 自带百度搜索
    "ernie": True,            # 文心一言（ERNIE）别名
    "qwen": True,             # 通义千问 API 自带阿里搜索
    "spark": True,            # 讯飞星火 API 自带联网
    "baichuan": True,         # 百川 API 自带联网
}


def _model_has_internet(model_id: str) -> bool:
    """检查外脑模型是否自带联网能力"""
    if not model_id:
        return False
    model_lower = model_id.lower()
    # 精确匹配
    if model_lower in _MODEL_INTERNET_CAPABILITY:
        return _MODEL_INTERNET_CAPABILITY[model_lower]
    # 模糊匹配（model_id 可能包含版本号，如 "gemini-1.5-pro"）
    for key, val in _MODEL_INTERNET_CAPABILITY.items():
        if key in model_lower:
            return val
    return False  # 默认不联网


# ═══ ★ #929-A6（2026-09-29）has_audio：外脑能不能直接听语音 ═══
# 实锤（2026-09-30）：glm-5.3-Flash 走标准 chat/completions 发 input_audio → 400 参数有误，不支持。
# 当前 8 家内置外脑没有一家在标准接口上收音频 → 标记机制在位、默认全 False，将来哪家支持了改这里即可。
_MODEL_AUDIO_CAPABILITY = {
    "zhipu": False, "deepseek": False, "doubao": False, "qwen": False,
    "kimi": False, "yi": False, "baichuan": False, "stepfun": False,
    "ollama": False, "gemini": False, "openai": False, "claude": False,
}


def _model_has_audio(model_id: str) -> bool:
    """检查当前外脑是否支持直接听语音（multi_models.json 该模型可写 has_audio: true 强制开启）"""
    if not model_id:
        return False
    m = model_id.lower()
    for key, val in _MODEL_AUDIO_CAPABILITY.items():
        if key in m:
            base = val
            break
    else:
        base = False
    # 用户级覆盖：multi_models.json → models.<id>.has_audio
    try:
        import json as _json
        from pathlib import Path as _P
        cfg_file = _P(__file__).resolve().parent.parent.parent / 'data' / 'multi_models.json'
        cfg = _json.loads(cfg_file.read_text(encoding='utf-8'))
        val = (cfg.get('models', {}).get(model_id, {}) or {}).get('has_audio')
        if val is not None:
            return _to_bool(val)
    except Exception:
        pass
    return base


def _to_bool(val):
    """兼容字符串/布尔/整型等多种输入，统一转为 bool"""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on", "enabled")
    if isinstance(val, (int, float)):
        return bool(val)
    return False


# ── P1-2: 统一非对话模型关键词列表（模块级常量）──
# 音乐/图片/视频/嵌入类模型不具备语言对话能力
_NONCHAT_KEYWORDS = [
    'musicgen', 'bark', 'riffusion', 'audiocraft', 'suno',   # 音乐
    'stable-diffusion', 'sdxl', 'sd3', 'flux', 'kolors', 'lcm',  # 图片
    'cogvideo', 'animatediff', 'opensora',                     # 视频
    'minicpm', 'qwen2.5-vl', 'internvl', 'llava',             # 视觉理解（非纯语言）
    'bge-', 'nomic-embed', 'nomic', 'gte-', 'e5-', 'text-embedding',  # 嵌入
    'whisper',                                                   # 语音识别
    'embed', 'clip', 'bakllava',                                # 其他专业
    'starcoder', 'codellama', 'deepseek-coder',                # 纯代码（可选）
]

# ── ★ #1509（2026-11-13）：用户自定义口令（追加式，config.yaml custom_keywords）──
# 形态：
#   custom_keywords:
#     - trigger: 记一笔          # 命中词（用户原话包含即命中）
#       reply: 好，请说出要记的内容   # 可选：直接回复的文本（0 token）
#       open: 'D:\\工作'          # 可选：打开本地路径（文件夹/文件）
#     - trigger: 提醒我           # 出现在 remind_words 时追加为提醒触发词
#       remind: true
# 规则：只追加不覆盖——内置词表删不掉（保底不搞哑）；命中同样过疑问句守卫
# （_QUICKOP_BLOCKERS：「XX吗/怎么/？」不触发）；60s 缓存，改配置即热生效。
_CKW_CACHE = {'ts': 0.0, 'items': []}

def _load_custom_keywords():
    """读 config.yaml 的 custom_keywords（60s 缓存），返回 list[dict]"""
    import time as _t
    now = _t.time()
    if now - _CKW_CACHE['ts'] < 60:
        return _CKW_CACHE['items']
    try:
        import yaml as _yaml
        from pathlib import Path as _P
        _p = _P(__file__).resolve().parent.parent.parent / 'config.yaml'
        cfg = (_yaml.safe_load(_p.read_text(encoding='utf-8')) or {}) if _p.exists() else {}
        items = cfg.get('custom_keywords') or []
        if not isinstance(items, list):
            items = []
        _CKW_CACHE['items'] = [it for it in items if isinstance(it, dict) and str(it.get('trigger') or '').strip()]
    except Exception:
        _CKW_CACHE['items'] = []
    _CKW_CACHE['ts'] = now
    return _CKW_CACHE['items']


# ── ★ #1497（2026-11-13）本地FC前置轮：A护栏 + B打通 ───────────────────────
# A·护栏：当前模型无工具调用能力 + 意图命中 FC 域（邮件等）→ 如实拦截提示换模型，
#         绝不让模型瞎编「系统没有集成邮箱功能」这类胡话（死命令：不代选、只说破）
# B·打通：Ollama 模型具备 tools 能力（/api/show capabilities）→ /api/chat + tools
#         真正的工具调用，tool_calls 就地执行（复用 execute_tool_call）→ 结果回填 → 循环收笔
_FC_DOMAIN_SIGNALS = ('邮件', '邮箱', '收件', '发件')
_OLLAMA_TOOLS_CAP_CACHE = {}   # model_short_name -> (timestamp, bool)


def _ollama_supports_tools(model_name: str) -> bool:
    """查 Ollama /api/show capabilities 是否含 tools（300 秒 TTL 缓存，低频探测）

    Args:
        model_name: Ollama 模型名（如 qwen3:8b / hv-pin-2f963d09b4c7:latest）

    Returns:
        bool: 模型支持工具调用返回 True；非 Ollama 模型/不可达/未声明 tools 返回 False
    """
    if not model_name or not isinstance(model_name, str):
        return False
    key = model_name.split(':')[0]
    try:
        import time as _time
        now = _time.time()
    except Exception:
        now = 0.0
    cached = _OLLAMA_TOOLS_CAP_CACHE.get(key)
    if cached and now - cached[0] < 300:
        return cached[1]
    ok = False
    try:
        import requests as _rq_cap
        r = _rq_cap.post('http://127.0.0.1:11434/api/show',
                         json={'model': model_name}, timeout=5)
        caps = (r.json() or {}).get('capabilities') or []
        ok = 'tools' in caps
    except Exception:
        ok = False
    _OLLAMA_TOOLS_CAP_CACHE[key] = (now, ok)
    return ok


class ConversationEngine:
    """对话引擎：管理一次对话的完整流程"""

    # ── 两种查询模式 + 协作开关 ──
    # 查询模式（2026-07-06 更新，auto已取消）：
    #   kb_only — 🏠本地模式：本地通用模型 + 知识库 + Skills + 画像 + 习惯，绝不外传数据
    #   direct  — 🤖外脑模式：外部模型 + 知识库 + Skills + 画像 + 习惯
    #   协作模式是独立开关（off/cloud_local/local_dual），与查询模式配合
    QUERY_MODES = {
        "kb_only": "🏠本地（本地通用模型+知识库+Skills+画像+习惯，纯本地不外出）",
        "direct": "🤖外脑（外部模型+知识库+Skills+画像+习惯，外脑回答）",
    }

    def __init__(self, model_manager: ModelManager, conversation_id: str, specialization_prompt=None, query_mode=None, privacy_mode=False, collab_mode=None):
        self.model = model_manager
        self.conversation_id = conversation_id
        self.history = []
        self._specialization_prompt = specialization_prompt  # 专项培养系统提示词
        self._load_history()

        # 隐私模式（True 时强制走本地模型，不走外部模型）
        self._privacy_mode = privacy_mode

        # 协作模式：None=读config.yaml, True/False/str=强制覆盖
        self._collab_mode_override = collab_mode

        # 加载用户偏好
        self._context_rounds = 8  # 默认8轮=16条消息，降低token消耗；用户可在设置中调整
        self._temperature = 0.7
        self._stream_output = True
        self._kb_search_enabled = True
        self._load_preferences()

        # 查询模式（优先使用传入参数，否则从配置加载）
        # 【保密模式】强制锁为本地模式，防止外部网络请求
        if self._privacy_mode:
            self._query_mode = "kb_only"
            logger.info("[Engine] 保密模式已激活，强制使用本地查询模式")
        elif query_mode and query_mode in self.QUERY_MODES:
            self._query_mode = query_mode
        else:
            mode = get_preference("query_mode")
            if mode in self.QUERY_MODES:
                self._query_mode = mode
            else:
                # ★ #822: 无持久化偏好时按「当前选的模型」自动判定，不再一律 kb_only。
                # 选了外脑（如 zhipu）→ direct；本地/未选 → kb_only。
                # 这就是"重启后要重新选外脑才好用"的病根之一：旧默认把外脑用户打回本地。
                _cur = ''
                try:
                    from src.core.multi_model_manager import get_multi_model_manager
                    _mm = get_multi_model_manager()
                    _cur = getattr(_mm, 'current_model', '') or ''
                except Exception:
                    pass
                # ★ #1309（2026-10-29）本地判定走唯一真家 is_local_model
                #   （原手抄清单漏 harveneye → PC 端同款「本地被误判外脑」病根，一次清）
                from src.core.multi_model_manager import is_local_model as _isl
                self._query_mode = 'kb_only' if _isl(_cur) else 'direct'
                logger.info("[Engine] 无持久化查询模式偏好，按当前模型(%r)自动判定: %s",
                            _cur, self._query_mode)

    def _load_preferences(self):
        """加载用户设置（数据库优先，无记录时回退 config.yaml）"""
        # P1-3: 使用 get_config() 单例替代直接读取 config.yaml
        try:
            from src.utils.config import get_config
            cfg = get_config()
        except Exception as e:
            logger.warning(f"[Engine] 配置加载失败: {e}")
            cfg = {}
        web_cfg = cfg.get("web_search", {}) if isinstance(cfg, dict) else {}
        model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}

        ctx = get_preference("context_rounds")
        if ctx:
            try:
                self._context_rounds = int(ctx)
            except ValueError as _ve:
                logger.debug(f"[Engine] context_rounds 解析失败 ({ctx!r}): {_ve}")
        temp = get_preference("temperature")
        if temp:
            try:
                self._temperature = float(temp)
            except ValueError as _ve:
                logger.debug(f"[Engine] temperature 解析失败 ({temp!r}): {_ve}")
                self._temperature = model_cfg.get("temperature", 0.7)
        else:
            self._temperature = model_cfg.get("temperature", 0.7)

        stream = get_preference("stream_output")
        if stream is not None:
            self._stream_output = _to_bool(stream)

        # ★ 2026-07-06: 搜索配置由 _get_searxng_url() 动态读取

        kb = get_preference("kb_search_enabled")
        if kb is not None:
            self._kb_search_enabled = _to_bool(kb)
        else:
            self._kb_search_enabled = True

    def _load_history(self):
        """从数据库加载历史消息"""
        msgs = get_messages(self.conversation_id, limit=100)
        self.history = []
        for m in msgs:
            if m["role"] in ("user", "assistant"):
                self.history.append({"role": m["role"], "content": m["content"]})

    def _quick_model_call(self, prompt: str) -> str:
        """快速模型调用（供学习引擎/后台任务使用，不走完整流程）"""
        try:
            messages = [{"role": "user", "content": prompt}]
            return self.model.generate_response(messages, temperature=0.3)
        except Exception:
            return ""

    # ★ 2026-07-06: 统一搜索引擎层 — SearXNG优先 + Bing内置
    # 调用入口：_web_search() 自动选择引擎
    # 旧代码已清理：_searxng_search(已删2026-07-02) / _bing_search(已删)

    def _get_searxng_url(self):
        """★ 2026-07-06: 读取SearXNG URL配置"""
        try:
            from src.utils.config import get_config
            cfg = get_config()
            url = cfg.get('searxng_url', '').strip()
            if url and not url.endswith('/'):
                url += '/'
            return url if url else None
        except Exception:
            return None

    def _searxng_search(self, query, max_results=5):
        """★ 2026-07-06: SearXNG聚合搜索（用户自建，返回结构化JSON）"""
        searxng_url = self._get_searxng_url()
        if not searxng_url:
            return None  # 没配置SearXNG，返回None让调用方回退Bing

        import requests
        from urllib.parse import quote_plus

        try:
            # SearXNG API格式：/search?q=xxx&format=json
            api_url = f"{searxng_url}search?q={quote_plus(query)}&format=json&categories=general&language=zh-CN"
            headers = {"User-Agent": "HarvenAI/1.0"}

            resp = requests.get(api_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"[Search] SearXNG HTTP {resp.status_code}")
                return None

            data = resp.json()
            results = []
            for item in data.get('results', [])[:max_results]:
                results.append({
                    "title": item.get('title', '')[:500],
                    "url": item.get('url', ''),
                    "snippet": item.get('content', '')[:500]
                })

            logger.info(f"[Search] SearXNG 搜索 '{query}' → {len(results)} 条结果")
            return results if results else []

        except requests.exceptions.Timeout:
            logger.warning("[Search] SearXNG 搜索超时，回退Bing")
            return None
        except Exception as e:
            logger.error(f"[Search] SearXNG 搜索失败，回退Bing: {e}")
            return None

    def _web_search(self, query, max_results=5):
        """★ 2026-07-06: 双路搜索调度 — 有SearXNG用SearXNG，没有用Bing"""
        # 先尝试SearXNG
        results = self._searxng_search(query, max_results)
        if results is not None:
            return results
        # 回退Bing
        return self._bing_search(query, max_results)

    def _bing_search(self, query, max_results=5):
        """
        Bing HTML 搜索（内置保底方案，无需 API Key、无需 Docker）
        国内直连 cn.bing.com，解析 HTML 提取结果

        Args:
            query: 搜索关键词
            max_results: 最大结果数

        Returns:
            list[dict]: [{"title", "url", "snippet"}]
        """
        import requests
        from urllib.parse import quote_plus
        from bs4 import BeautifulSoup

        # Bing 中国（国内可访问，无需翻墙）
        url = f"https://cn.bing.com/search?q={quote_plus(query)}&setlang=zh-CN"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"[Search] Bing HTTP {resp.status_code}")
                return []

            # 解析 Bing HTML
            soup = BeautifulSoup(resp.text, "html.parser")

            results = []
            # Bing 结果在 .b_algo 类中
            for item in soup.select(".b_algo"):
                if len(results) >= max_results:
                    break

                title_elem = item.select_one("h2 a")
                # Bing 摘要在 .b_caption p 或 .b_caption .b_paractl
                snippet_elem = item.select_one(".b_caption p, .b_caption .b_paractl, p")

                if title_elem:
                    title = title_elem.get_text(strip=True)
                    href = title_elem.get("href", "")

                    snippet = ""
                    if snippet_elem:
                        snippet = snippet_elem.get_text(strip=True)

                    if title and href:
                        results.append({
                            "title": title[:500],
                            "url": href,
                            "snippet": snippet[:500]
                        })

            logger.info(f"[Search] Bing 搜索 '{query}' → {len(results)} 条结果")
            return results

        except requests.exceptions.Timeout:
            logger.warning("[Search] Bing 搜索超时")
            return []
        except Exception as e:
            logger.error(f"[Search] Bing 搜索失败: {e}")
            return []

    def _build_search_context(self, query, results):
        """将搜索结果格式化为增强上下文文本"""
        if not results:
            return ""

        lines = [f"## 🌐 互联网搜索结果（Bing）", f"搜索词: {query}", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"### {i}. {r['title']}")
            lines.append(f"来源: {r['url']}")
            if r['snippet']:
                lines.append(f"摘要: {r['snippet']}")
            lines.append("")

        lines.append("请基于以上搜索结果回答用户问题。引用来源时标注序号。如搜索结果不足，诚实说明。")
        return "\n".join(lines)

    def _is_fact_query(self, user_message):
        """★ 2026-07-05: 判断是否为事实查询（天气/股价/新闻等）
        
        事实查询：用户只想知道具体事实，直接展示搜索结果即可，不需要模型整合。
        知识查询：用户想了解原理/方法/教程，需要模型基于搜索结果整合回答。
        """
        msg_lower = user_message.lower()
        fact_keywords = [
            "天气", "气温", "温度",
            "股价", "股票", "基金", "汇率", "油价",
            "新闻", "热搜", "热点",
            "比分", "赛果", "战绩",
            "时间", "几点",
            # ★ 2026-07-06: 新增金融指数关键词（事实查询直接展示，不让模型编造数据）
            "上证", "深证", "指数", "大盘", "金价", "银价",
            "比特币", "以太坊", "美股", "港股", "A股",
            "基金净值", "存款利率", "房价", "物价",
            "票房", "收视率", "排行榜",
        ]
        return any(kw in msg_lower for kw in fact_keywords)

    def _fetch_url_content(self, url, timeout=8):
        """★ 2026-07-21: 轻量级网页内容抓取（requests + BeautifulSoup）

        用于事实查询搜索后抓取页面正文，提取实际数据（如气温/股价）。
        比 BrowserAgent.fetch_content() 轻量，无需启动 Playwright。

        Args:
            url: 目标网页 URL
            timeout: 超时秒数

        Returns:
            str: 纯文本正文（≤3000字符），失败返回空字符串
        """
        import requests
        from bs4 import BeautifulSoup
        from src.core.independence import bypass_independence

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            # ★ 2026-07-21: 使用 bypass_independence 绕过出站白名单（受信任的内部搜索模块）
            with bypass_independence():
                resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if resp.status_code != 200:
                logger.info(f"[Search] 抓取页面 HTTP {resp.status_code}: {url}")
                return ""

            # 检测编码，避免中文乱码
            if resp.encoding and resp.encoding.lower() == 'iso-8859-1':
                resp.encoding = resp.apparent_encoding

            soup = BeautifulSoup(resp.text, "html.parser")

            # 移除干扰元素（脚本/样式/导航/页脚等）
            for tag in soup.find_all(["script", "style", "noscript", "nav",
                                       "footer", "header", "aside", "iframe", "form"]):
                tag.decompose()

            # 提取正文
            text = soup.get_text(separator="\n", strip=True)
            # 清理多余空白行
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = "\n".join(lines)

            result = text[:3000]  # 限制长度，避免context爆炸
            logger.info(f"[Search] 抓取成功: {url[:60]}... → {len(result)} 字符")
            return result
        except Exception as e:
            logger.warning(f"[Search] 抓取页面失败 {url[:60]}...: {e}")
            return ""

    def _fetch_pages_concurrent(self, urls, max_workers=3, timeout_per_page=8):
        """★ 2026-07-21: 并发抓取多个网页内容

        Args:
            urls: URL 列表
            max_workers: 最大并发数
            timeout_per_page: 每个页面超时秒数

        Returns:
            dict: {url: content_text}，失败的url值为空字符串
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_url = {
                    executor.submit(self._fetch_url_content, url, timeout_per_page): url
                    for url in urls
                }
                for future in as_completed(future_to_url, timeout=timeout_per_page + 3):
                    url = future_to_url[future]
                    try:
                        results[url] = future.result()
                    except Exception as e:
                        logger.debug(f"[Search] 并发抓取异常 {url}: {e}")
                        results[url] = ""
        except Exception as e:
            logger.warning(f"[Search] 并发抓取整体失败: {e}")
            for url in urls:
                results.setdefault(url, "")

        return results

    def _build_fact_search_context(self, query, results, fetched_contents):
        """★ 2026-07-21: 构建事实查询的增强上下文（含抓取的网页正文）

        与 _build_search_context 不同，这里包含从网页抓取的实际内容，
        让模型能提取具体数据（气温/股价/汇率等）。

        Args:
            query: 搜索关键词
            results: 搜索结果列表 [{"title", "url", "snippet"}]
            fetched_contents: 抓取的网页内容 {url: content_text}

        Returns:
            str: 增强上下文文本
        """
        lines = [f"## 🌐 互联网搜索结果（Bing）", f"搜索词: {query}", ""]

        for i, r in enumerate(results[:5], 1):
            lines.append(f"### {i}. {r['title']}")
            lines.append(f"来源: {r['url']}")
            if r.get('snippet'):
                lines.append(f"摘要: {r['snippet']}")

            # ★ 注入抓取的页面正文
            content = fetched_contents.get(r['url'], '')
            if content:
                lines.append(f"网页正文（前3000字符）:")
                lines.append(content[:3000])
            lines.append("")

        lines.append("请基于以上搜索结果和网页正文回答用户问题。")
        lines.append("优先提取具体数据（如气温数值、股价数字、汇率等），用简洁方式呈现。")
        lines.append("引用来源时标注序号。如数据不足或矛盾，诚实说明。")
        return "\n".join(lines)

    def _extract_search_query(self, user_message):
        """★ 2026-07-08: 从用户消息中提取搜索关键词（改进版v2）

        去掉搜索指令词 + 时间干扰词 + 语气词 + URL + 命令，
        只保留核心搜索内容。
        例："今天做音乐" → "做音乐"
            "搜一下北京天气" → "北京天气"
            "ollama pull qwen2.5:14b" → "qwen2.5 14b"
        """
        import re
        # 搜索指令词（完整删除）
        stop_words = [
            "搜一下", "搜索一下", "搜索", "搜", "查一下", "查询", "查",
            "帮我搜", "帮我查", "上网搜", "联网搜", "网上搜",
            "请问", "一下", "帮我",
        ]
        # 时间干扰词（容易污染搜索结果）
        time_words = [
            "今天", "昨天", "明天", "现在", "目前", "近期", "最近",
            "这周", "本周", "上周", "下周", "这个月", "本月", "上个月", "下个月",
            "2025年", "2026年", "2027年", "2025", "2026", "2027",
        ]
        # 语气词
        filler_words = ["呢", "啊", "吧", "吗", "呀", "哦", "哈", "嗯"]

        query = user_message
        # ★ 2026-07-08: 先去掉URL（避免URL污染搜索结果）
        query = re.sub(r'https?://[^\s]+', '', query)
        # 去掉命令行参数（--model xxx）
        query = re.sub(r'--?\w+\s+\S+', '', query)
        # 先去搜索指令词
        for sw in stop_words:
            query = query.replace(sw, "")
        # 再去时间干扰词
        for tw in time_words:
            query = query.replace(tw, "")
        # 去语气词
        for fw in filler_words:
            query = query.replace(fw, "")

        query = query.strip()
        # 如果清理后为空，用原消息
        if not query:
            query = user_message
        return query[:100]  # 限制长度

    def _try_direct_accounting(self, msg):
        """★ 2026-10-23 对话花钱意图直录（定版拍板「开发全部」）：
        陈述句「花了385吃饭」→ 本地正则解析（0 token）→ 走 panel_assist.insert_records
        同管线入记账面板（查重#913/分类兜底/花销目标核查钩子全继承，同真家）。
        命中返回提示语（回复头部展示「✅已自动入账」），问句/否定句/超长句返回 ''。"""
        import re as _re
        text = str(msg or '').strip()
        if not text or len(text) > 60:
            return ''
        # 问句/否定/指令绝不碰（「花了多少」「别记了」「还没花」）
        if _re.search(r'[?？]|多少|几块|怎么样|为什么|别记|不用记|不要记|还没花|没花|别入账', text):
            return ''
        m = _re.search(
            r'(?:花了|花费了?|消费了|付了|买[了个]|吃了|交了|充了|加油?)\s*'
            r'([0-9０-９]+(?:\.[0-9０-９]+)?)\s*'
            r'(万|块|元|块钱|￥)?', text)
        if not m:
            return ''
        has_unit = bool(m.group(2))
        # ★ #1224（2026-10-26）方案A 幻觉账根治（实锤：「吃了2个红薯」被记成 2 元账）：
        #   规则1：数字后紧跟量词/时间单位 = 数量不是钱 → 拒绝直录
        if not has_unit and _re.match(
                r'\s*[个杯把碗根支片颗粒瓶包盒袋斤两勺碟条只份双听罐桶排串点分秒小时天号日月年周]', text[m.end():]):
            return ''
        #   规则2：「吃了/买了」本身不必然是花钱 → 必须带货币单位才入账
        if not has_unit and _re.match(r'(?:吃了|买[了个])', m.group(0)):
            return ''
        try:
            amt = float(m.group(1).translate(str.maketrans('０１２３４５６７８９', '0123456789')))
        except ValueError:
            return ''
        if m.group(2) == '万':
            amt *= 10000
        if amt <= 0 or amt >= 10000000:
            return ''
        # 品项：金额后面的文字（「花了385吃饭」→ 吃饭）；★ #1224 按标点切第一段（不再 12 字硬截腰斩「花生」）
        tail = text[m.end():].strip(' ，,。.：:的')
        if tail:
            item = _re.split(r'[，,。.；;！!？?\s]', tail)[0][:20]
        else:
            pre = _re.sub(r'^(今天|昨天|上午|下午|中午|晚上|刚才|刚|我)', '', text[:m.start()]).strip(' ，,。.')
            item = _re.split(r'[，,。.；;！!？?\s]', pre)[-1][-20:] if pre else ''
        records = [{'项目': item or '日常消费', '金额': amt, '备注': ('对话直录：' + text[:40])}]
        from src.core.panel_assist import insert_records
        n, err, note = insert_records('accounting_daily_1', '', records)
        if not n:
            logger.warning(f"[Engine] 花钱直录入库失败: {err}; text={text[:30]}")
            return ''
        cat = ''
        try:
            from src.core.acc_categories import classify_ex as _cls, load_custom_rules as _lcr
            cat, _, _ = _cls(records[0]['项目'], '', _lcr())
        except Exception:
            pass
        tip = f"✅已自动入账：{cat or '支出'} {amt:g} 元" + (f"（{item}）" if item else '')
        try:
            from src.core.coach_engine import check_spend_goals
            _alerts = check_spend_goals()
            if _alerts:
                tip += '　' + _alerts[0]['title']
        except Exception:
            pass
        logger.info(f"[Engine] 💰 花钱直录: {amt:g}元 {item}（{note[:40] if note else '入账成功'}）")
        return tip

    def send(self, user_message, stream=True, query_mode=None, web_search=False, images=None, pure_chat=False):
        """
        发送用户消息，执行三段式查询

        Args:
            query_mode: 可覆盖实例默认模式 (kb_only/direct)
            web_search: ★ 2026-07-05: 用户是否点了🌐互联网按钮，True=强制搜索
            images: ★ 2026-09-15 A方案: 随消息附带的图片（dataURL 或本地路径列表），有图自动走看图模型
            pure_chat: ★ 2026-09-26 模式分开（已定案A）：True=纯聊（浮窗💬聊天模式用），
                跳过智能拆分等记事拦截——聊天不碰面板库；首页聊天框不传（保留"聊着也能记"）

        Returns:
            stream=True: 生成器 (type, content)
                type: "thinking" | "text" | "warning" | "error" | "source"
        """
        # ★ 2026-09-26 纯聊标记（_stream_three_stage 读；引擎单例，极小并发竞态可接受）
        self._pure_chat = bool(pure_chat)
        # ★ #1320 对话优先闸门：前台对话开始，后台 GPU 任务（打标/深识/重跑）让路 5 分钟
        try:
            from src.core import dialog_gate as _dg
            _dg.mark_active()
        except Exception:
            pass
        # ★ 2026-09-15 A方案: 带图消息 → 看图（优先级最高，不进快车道/路由）
        # ★ 2026-09-24 A方案改造（定版拍板）：识别方跟随用户当前模式（外脑/本地），
        #   不再强制本地 minicpm-v —— 图片发给谁由用户当前选择决定，隐私自己掌控
        # ★ #1432（2026-11-04 r48）带图意图分诊（笔记本实锤截胡根治）：
        #   带图 + 录入触发词（存入面板/记到台账…）→ 不再看图直答（那只会闲聊不落账），
        #   图片照旧入统一图库（#918 同源），放行正常管线——智能拆分/词典快车道/确认卡
        #   接管录入。pure_chat（浮窗纯聊）不参与录入，维持原样。
        if images:
            _msg_txt = (user_message or '').strip()
            _hit_intake = (not getattr(self, '_pure_chat', False)) and any(
                _t in _msg_txt for _t in _IMAGE_INTAKE_TRIGGERS)
            # ★ 2026-11-12 归档指令快车道（反吹牛实锤修复）：带图 +「分到XX相册」
            #   → 本地毫秒级真归档落库，不经模型嘴。实锤：HarvenEye 直答谎称
            #   「已归类至老谭相册」（存储路径都是编的），照片实际躺在生活照（id=221）。
            _album_target, _album_hint = '', ''
            if not getattr(self, '_pure_chat', False):
                try:
                    from src.core.img_store import album_move_from_text as _amt
                    _album_target, _album_hint = _amt(_msg_txt)
                except Exception as _ame:
                    logger.debug(f'[Engine] 归档意图解析跳过: {_ame}')
            if _hit_intake:
                try:
                    import base64 as _b64
                    import re as _re
                    from src.core import img_store as _istore
                    _n = 0
                    for _img in images[:4]:
                        if not isinstance(_img, str) or not _img:
                            continue
                        _m = _re.match(r'^data:image/[a-zA-Z+]+;base64,(.+)$', _img, _re.S)
                        if _m:
                            _istore.register(image_data=_b64.b64decode(_m.group(1)),
                                             source='chat', note=_msg_txt[:200])
                            _n += 1
                        elif Path(_img).exists():
                            _istore.register(image_data=Path(_img).read_bytes(),
                                             source='chat', note=_msg_txt[:200])
                            _n += 1
                    if _n:
                        logger.info(f'[Engine] #1432 带图录入分诊：命中录入触发词，'
                                    f'图片{_n}张已入图库，转默录链')
                except Exception as _ie:
                    logger.debug(f'[Engine] #1432 带图录入图片入图库跳过: {_ie}')
                # ★ 走默录链（带图抽取→高置信直入/低置信待确认→图片挂记录），异步不阻塞
                try:
                    from src.core.panel_assist import maybe_home_record
                    maybe_home_record(self.conversation_id, _msg_txt, '', images=images)
                except Exception as _pe:
                    logger.debug(f'[Engine] #1432 默录链调用失败: {_pe}')
                yield ("text", "📥 已收到，正在把这条和照片整理进面板档案："
                               "能对上号的直接入档，需要你补充信息的会出现在「📥 待确认」，点确认即入账。")
                return
            elif _album_target:
                # ★ 2026-11-12 归档快车道执行：入图库 + set_album 真落库，直出结果
                try:
                    import base64 as _b64
                    import re as _re
                    from src.core import img_store as _istore
                    _ids = []
                    for _img in images[:4]:
                        if not isinstance(_img, str) or not _img:
                            continue
                        _m = _re.match(r'^data:image/[a-zA-Z+]+;base64,(.+)$', _img, _re.S)
                        if _m:
                            _reg = _istore.register(image_data=_b64.b64decode(_m.group(1)),
                                                    source='chat', note=_msg_txt[:200])
                        elif Path(_img).exists():
                            _reg = _istore.register(image_data=Path(_img).read_bytes(),
                                                    source='chat', note=_msg_txt[:200])
                        else:
                            continue
                        if _reg and _reg.get('id'):
                            _istore.set_album(_reg['id'], _album_target)
                            _ids.append(_reg['id'])
                    if _ids:
                        logger.info(f'[Engine] 归档快车道：{_ids} → 「{_album_target}」')
                        yield ("text", f"✅ 已把 {len(_ids)} 张照片归到「{_album_target}」相册"
                                       f"（图片库可查，已锁定不再自动挪动）。")
                    else:
                        yield ("text", "⚠️ 没收到有效图片——请带着照片再说一次「分到XX相册」。")
                except Exception as _ae:
                    logger.warning(f'[Engine] 归档快车道失败: {_ae}')
                    yield ("text", f"⚠️ 归档没成功：{_ae}")
                return
            elif _album_hint:
                yield ("text", f"📁 {_album_hint}")
                return
            else:
                yield from self._vision_reply(user_message, images, stream, query_mode=query_mode)
                return

        # ★ 2026-07-29: 方案N — 注入系统状态（模型回答前先查好）
        self._system_status = ''

        # ★ 2026-09-16 家庭提醒引擎：到期提醒注入主聊天（一次弹出即打钩，重复的自动滚到下一轮；失败静默）
        try:
            from src.core.home_reminder import inject_due as _hr_inject
            for _rm in _hr_inject():
                yield ("text", _rm + "\n\n")
        except Exception as _hre:
            logger.debug(f"[Engine] 到期提醒注入跳过: {_hre}")

        # ★ 2026-11-15 (#1522-B): 播放意图本地快车道 — 必须在分段判定之前拦截，
        #   否则被 / 分段流水线劫持重新生成（「点播放=重新生成一遍歌」实锤修复）
        if user_message:
            _play_resp = self._try_playback_route(user_message)
            if _play_resp:
                logger.info(f"[Engine] 播放快车道命中: {user_message[:30]}")
                yield ("text", _play_resp)
                return

        # ★ 2026-09-15 V1.5: / 分段接力识别（定版方案）— 协作流水线开启时，
        #   用 / 或 ／ 隔开的 ≥2 段命令 = 手动挡流水线；必须在快车道之前判定（快车道要让路）
        self._pipeline_segments = None
        if user_message:
            _cm_chk = self._collab_mode_override
            if _cm_chk is None:
                try:
                    from src.utils.config import get_preference as _gp_cm
                    _cm_chk = _gp_cm('collab_mode') or 'off'
                except Exception:
                    _cm_chk = 'off'
            if _cm_chk == 'cloud_local':
                _segs = self._split_pipeline_segments(user_message)
                if _segs:
                    self._pipeline_segments = _segs
                    logger.info(f"[Engine] / 分段接力: {len(_segs)} 段 ↔ 流水线")

        # ★ 2026-08-13: 智能意图匹配 — 关键词组合，不漏词
        # 不依赖FC（本地模型不支持tools参数），用关键词组合直接路由
        # ★ 分段接力消息跳过快车道（"作曲/画图"等触发词不再劫持流水线命令）
        if user_message and not user_message.startswith('/help') and '[CONFIRM_AGENT:' not in user_message and not self._pipeline_segments:
            # ★ 2026-09-16: 快车道只匹配用户原话（剥离助手小窗拼进的位置前缀/背景补充，
            #   防上下文里的"目录/应用"等词跟用户原话拼出误触发 → 实测实锤bug）
            _route_msg = self._strip_assist_context(user_message)
            # ★ 2026-09-15: 快车道只产出最终文本（内部无流式token），先收集再注入来源标识
            fc_events = list(self._try_smart_route(_route_msg, stream))
            if fc_events:
                # ★ A批A7: token账本 — B方案命中（关键词快车道，零token）
                try:
                    from src.core.token_ledger import record as _tl_b
                    _tl_b('b_plan_hits', msg=user_message[:50])
                except Exception:
                    pass
                # ★ 2026-09-15: 快车道也标注回复方（否则前端看不到是谁答的）
                if not any(t == 'model_source' for t, _ in fc_events):
                    fc_events.insert(0, ("model_source", {
                        "model": "fastlane", "label": "⚡ 快车道 · 本地执行",
                        "is_external": False, "mode": "fastlane",
                    }))
                for _t, _c in fc_events:
                    yield (_t, _c)
                return

        # ★ 2026-10-05 #990 查询链分层 C′ 第0层：纯查询本地直出（0 token，两端一致）
        #   病根（实测实锤）：「这个月开销」这类纯数字查询也全量进模型管线——
        #   预查+模型组织两遍跑，电脑手机各问一遍 = 外脑答两次浪费 token，且说法各异。
        #   直出后同一模板同一数据 → 电脑手机字面一致；分析类照旧进模型不受影响。
        if user_message and not getattr(self, '_pure_chat', False):
            _qd_ans = self._query_direct_answer(user_message)
            if _qd_ans:
                logger.info(f"[Engine] #990 纯查询直出命中({len(_qd_ans)}字, 0 token): {user_message[:30]}")
                try:
                    from src.core.token_ledger import record as _tl_q
                    _tl_q('b_plan_hits', msg='[direct] ' + user_message[:50])
                except Exception:
                    pass
                yield ("model_source", {
                    "model": "panel_direct", "label": "📋 本地速答 · 数据直查",
                    "is_external": False, "mode": "panel_direct",
                })
                for _i in range(0, len(_qd_ans), 60):
                    yield ("text", _qd_ans[_i:_i + 60])
                try:
                    save_message(self.conversation_id, "user", user_message)
                    save_message(self.conversation_id, "assistant", _qd_ans,
                                 metadata={"source": "panel_direct"})
                except Exception as _qe:
                    logger.warning(f"[Engine] 直出消息保存失败: {_qe}")
                yield ("done", None)
                return

        # ★ 2026-10-23 对话花钱意图直录（定版拍板「开发全部」）：
        #   放在快车道/#990 直出之后——快车道已处理的记事命令不重复入账，
        #   查询句「花了多少」已被直出回答不误触发；这里只接漏网的陈述句。
        if (user_message and not getattr(self, '_pure_chat', False)
                and not user_message.startswith('/') and '[CONFIRM_AGENT:' not in user_message
                and not self._pipeline_segments):
            try:
                _acc_tip = self._try_direct_accounting(user_message)
                if _acc_tip:
                    yield ("text", _acc_tip + "\n\n")
            except Exception as _dae:
                logger.debug(f"[Engine] 花钱直录跳过: {_dae}")

        # ★ 2026-08-10: /help 快捷命令 — 直接返回使用指南，不走模型推理
        if user_message and user_message.strip().lower().startswith('/help'):
            topic = user_message.strip()[5:].strip()

        # ★ 2026-08-11: 检测 [CONFIRM_AGENT:xxx] 确认标记，直接执行脚本Agent
        if user_message and '[CONFIRM_AGENT:' in user_message:
            import re
            match = re.search(r'\[CONFIRM_AGENT:(\w+)\]', user_message)
            if match:
                agent_id = match.group(1)
                clean_msg = re.sub(r'\[CONFIRM_AGENT:\w+\]\s*', '', user_message).strip()
                try:
                    from src.core.agent_registry import get_registry
                    registry = get_registry()
                    result = registry.handoff(agent_id, clean_msg, model_func=None, conversation_history=self.history[-6:])
                    if result.get("success"):
                        yield ("notification", {"type": "script_agent_result", "message": "✅ 执行完成", "result": str(result.get("result", ""))})
                        yield ("text", str(result.get("result", "✅ 执行完成")))
                    else:
                        yield ("warning", f"执行失败: {result.get('error', '未知错误')}")
                except Exception as e:
                    yield ("error", f"执行出错: {e}")
                return
            help_text = self._build_help_response(topic)
            if stream:
                # 生成器模式：逐段返回帮助文本
                chunks = [help_text[i:i+60] for i in range(0, len(help_text), 60)]
                for chunk in chunks:
                    yield ('text', chunk)
                return
            else:
                return help_text
        try:
            import subprocess as _sp
            _r = _sp.run(['wevtutil', 'qe', 'System', '/c:5', '/rd:true', '/f:text'],
                       capture_output=True, text=True, timeout=3)
            if _r.returncode == 0 and _r.stdout.strip():
                self._system_status = _r.stdout.strip()[:800]
        except Exception:
            pass

        # ★ 2026-07-24: 方案A终极版 — 在模型回答之前就预检测软件
        # 不依赖模型行为、不依赖后处理、不依赖代码路径
        _user_lower = (user_message or '').lower()
        _SW_MAP_FINAL = {
            'docker': ['docker', '容器'],
            'python': ['python', 'py环境'],
            'git': ['git'],
            'node': ['node', 'nodejs'],
            'ollama': ['ollama'],
            'pip': ['pip'],
            'blender': ['blender'],
            'ffmpeg': ['ffmpeg'],
        }
        # 检测意图：检查/装/安装/有没有/是否/版本
        _has_check_intent = any(_kw in _user_lower for _kw in ['检查', '装', '安装', '有没有', '是否', '版本', '能不能', '可以'])
        if _has_check_intent:
            for _sw, _kws in _SW_MAP_FINAL.items():
                if any(_kw in _user_lower for _kw in _kws):
                    try:
                        from src.core.builtin_tools import check_software as _cs
                        _sr = _cs(_sw)
                        if _sr.get('installed'):
                            _pre_msg = f"📋 **{_sw} 检测结果**：\n✅ 已安装 — {_sr.get('version','未知')}\n运行状态：{'✅运行中' if _sr.get('running') else '⚠️未运行'}\n路径：{_sr.get('path','未知')}\n\n---\n"
                        else:
                            _pre_msg = f"📋 **{_sw} 检测结果**：\n❌ 未安装\n\n---\n"
                        yield ("text", _pre_msg)
                        logger.info(f"[Engine] 方案A终极版: 预检测check_software({_sw})")
                    except Exception as _ae:
                        logger.error(f"[Engine] 方案A终极版失败: {_ae}")
                    break

        # ★ 2026-07-05: web_search 标记（用户点🌐按钮 → Step 1.5 强制搜索）
        self._web_search_this_query = web_search
        # ★ 方案A 联网授权制（2026-10-05 定版拍板）：按🌐开关设置执行层门禁——
        #   未授权时即使模型凭记忆喊 web_search/web_fetch，执行层也拒绝（fail-closed）。
        #   定义注入侧（get_fc_bundle/get_tool_definitions）同样按此开关过滤。
        try:
            from src.core.builtin_tools import set_web_search_allowed as _set_ws
            _set_ws(bool(web_search))
        except Exception as _ws_e:
            logger.debug(f"[Engine] 联网授权开关设置失败: {_ws_e}")
        # 允许本次调用临时覆盖查询模式（保密模式下禁止切换）
        if query_mode and query_mode in self.QUERY_MODES:
            if self._privacy_mode:
                logger.warning(f"[Engine] 保密模式下拒绝切换查询模式: {query_mode}，保持 kb_only")
            else:
                self._query_mode = query_mode
        # ★ 2026-07-07: 功德行已清理（不再有锁机状态检查）

        # 0.5 检查是否有匹配的插件
        plugin_result = None
        try:
            from src.core.plugin_system import get_plugin_manager
            pm = get_plugin_manager()
            matching_plugins = pm.get_matching_plugins(user_message)
            if matching_plugins:
                plugin = matching_plugins[0]
                from src.core.plugin_system import PluginContext
                ctx = PluginContext(self.conversation_id, user_message)
                result = pm.execute_plugin(plugin, ctx)
                if result.success and result.message:
                    save_message(self.conversation_id, "user", user_message)
                    save_message(self.conversation_id, "assistant", result.message)
                    self.history.append({"role": "user", "content": user_message})
                    self.history.append({"role": "assistant", "content": result.message})
                    plugin_result = result.message
        except Exception as e:
            logger.error(f"[Engine] 插件检查失败: {e}")

        if plugin_result:
            if stream:
                # ★ 2026-09-15: 插件路径也标注回复方
                self._reply_source = "🧩 插件 · 本地"
                yield ("model_source", {"model": "plugin", "label": "🧩 插件 · 本地",
                                        "is_external": False, "mode": "plugin"})
                yield ("text", plugin_result)
            return plugin_result

        # 0.6 图片/视频生成意图 — ★ 2026-07-03 已取消
        # 旧逻辑：关键词触发自动生成图片（process_image_gen_message）
        # 新架构：图片生成走 Step 2.4 垂直预填 → stable-diffusion 面板
        # 用户在垂直面板点【执行】才生成，不再自动触发

        # 0.65 Agent 意图路由（🆕 2026-06-24）
        # P1-1: discover() 结果缓存到 _agent_id_early，避免 Step 2.5 重复 discover
        # ★ 2026-07-06: 加"总体功能"检测 — 用户问哈文有什么功能时不走Agent路由
        agent_result = None
        _agent_id_early = None
        _general_question_keywords = [
            # 总体功能问题
            "你有什么功能", "你能做什么", "你有哪些功能", "哈文有什么功能",
            "你有什么能力", "你都能做什么", "介绍.*你自己", "你是什么",
            "哈文.*功能", "哈文.*能力", "哈文.*介绍",
            # ★ 2026-07-06: 平台概念问题 — 不走Agent路由
            "本地.*是什么", "外脑.*是什么", "协作.*是什么", "垂直.*是什么",
            "专项.*是什么", "知识库.*是什么", "监控.*是什么", "agent.*是什么",
            "skill.*是什么", "哈文.*是什么",
            "本地.*按键", "本地.*按钮", "外脑.*按键", "外脑.*按钮",
            "垂直.*按键", "垂直.*按钮", "协作.*按键", "协作.*按钮",
            "专项.*按键", "专项.*按钮", "知识库.*按键", "知识库.*按钮",
            "监控.*按键", "监控.*按钮",
            "哈文.*按键", "哈文.*按钮", "哈文.*怎么用",
        ]
        _is_general_question = any(
            __import__('re').search(kw, user_message.lower())
            for kw in _general_question_keywords
        )
        if _is_general_question:
            logger.info(f"[Engine] 检测到总体功能问题，跳过Agent路由: {user_message[:30]!r}")
        try:
            from src.core.agent_registry import get_registry
            registry = get_registry()
            if not _is_general_question:
                # ★ 2026-07-22: CLI-Anything 软件安装请求不走Agent路由（ops_agent会写下载代码）
                _cli_sw_names = ['blender', 'gimp', 'inkscape', 'krita', 'audacity',
                                 'obs', 'ffmpeg', 'imagemagick', 'darktable', 'scribus']
                _mentioned_cli_early = [kw for kw in _cli_sw_names if kw in user_message.lower()]
                _has_install_word_early = any(kw in user_message.lower() for kw in ["下载", "安装", "装", "download", "install"])

                # ★ 2026-07-22: 也检查对话历史——如果上一轮在聊CLI-Anything软件，这轮也跳过Agent
                _recent_history = ' '.join(m.get('content', '')[:200] for m in self.history[-4:])
                _history_has_cli = any(kw in _recent_history.lower() for kw in _cli_sw_names)

                if (_mentioned_cli_early and _has_install_word_early) or _history_has_cli:
                    _agent_id_early = None  # 跳过Agent路由，走正常流程（CLI-Anything检测）
                    logger.info(f"[Engine] CLI-Anything上下文跳过Agent路由 (当前消息含软件={bool(_mentioned_cli_early)}, 历史含软件={_history_has_cli})")
                else:
                    _agent_id_early = registry.discover(user_message)
            logger.info(f"[Engine] Agent discover({user_message[:30]!r}) → {_agent_id_early}")
            if _agent_id_early:
                # ★ 2026-08-13: 去掉确认弹窗，直接执行（用户反馈确认机制不好用）
                logger.info(f"[Engine] 🎯 Agent 路由: {_agent_id_early}")
                # 构建 model_func — 调用当前模型处理消息
                def _agent_model_func(messages, temperature=0.3):
                    return self.model.chat(messages, stream=False, temperature=temperature)
                
                result = registry.handoff(
                    _agent_id_early, user_message,
                    model_func=_agent_model_func,
                    conversation_history=self.history[-6:]
                )
                if result.get("success"):
                    save_message(self.conversation_id, "user", user_message)
                    save_message(self.conversation_id, "assistant", result["result"])
                    self.history.append({"role": "user", "content": user_message})
                    self.history.append({"role": "assistant", "content": result["result"]})
                    agent_result = {
                        "output": result["result"],
                        "agent_name": result.get("agent_name", _agent_id_early),
                    }
        except Exception as e:
            logger.error(f"[Engine] Agent 路由失败（已跳过）: {e}", exc_info=True)

        if agent_result:
            if stream:
                # ★ 2026-09-15: Agent 直跑路径也标注回复方
                self._reply_source = f"🧩 {agent_result['agent_name']} · 本地"
                yield ("model_source", {"model": _agent_id_early or "agent",
                                        "label": f"🧩 {agent_result['agent_name']} · 本地",
                                        "is_external": False, "mode": "agent"})
                yield ("text", f"🤖 [{agent_result['agent_name']}] {agent_result['output']}")
            return agent_result["output"]

        # 0.7 聊天安全隔离：检测 Prompt Injection 攻击
        # 聊天内容 ≠ 系统指令。防止用户通过对话覆盖系统提示词。
        if detect_prompt_injection(user_message):
            logger.warning(f"[Engine] 检测到 Prompt Injection 尝试: {user_message[:100]}")
            save_message(self.conversation_id, "user", user_message)
            warning = (
                "⚠️ **安全提醒**\n\n"
                "检测到你的消息中包含可能试图修改我系统设置的指令。\n\n"
                "在 Harven AI 中，你的聊天内容 ≠ 系统指令。"
                "我无法通过对话来改变我的核心行为和规则。\n\n"
                "如果你需要调整模型行为，请使用侧边栏的「设置」面板。"
                "如果你认为这是误判，请换一种说法重新提问。"
            )
            save_message(self.conversation_id, "assistant", warning)
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": warning})
            if stream:
                yield ("warning", warning)
            return warning

        # 1. 触发消息接收事件（Hooks）
        get_event_bus().emit(Events.MESSAGE_RECEIVED, self.conversation_id, user_message)

        # 2. 保存用户消息
        save_message(self.conversation_id, "user", user_message)

        # ★ send() 是生成器函数（含 yield），stream=False 路径无法 return 值
        # 所有调用方统一用 stream=True 迭代，stream 参数保留但忽略
        yield from self._stream_three_stage(user_message)

    def _build_help_response(self, topic=''):
        """★ 2026-08-10: /help 命令返回使用指南"""
        topic = (topic or '').strip().lower()
        help_db = {
            '': "📚 Harven AI 使用指南\n"
                "输入 /help 安装  查看安装帮助\n"
                "输入 /help 模型  查看本地模型\n"
                "输入 /help 外脑  查看外脑配置\n"
                "输入 /help 知识库 查看知识库\n"
                "输入 /help 手机  查看手机端\n"
                "输入 /help 设置  查看设置说明\n"
                "输入 /help 面板  查看面板功能\n"
                "输入 /help 渠道  查看渠道接入\n"
                "完整指南：点侧边栏 ❓帮助 或查看 docs/guide.html\n"
                "初始密码：harven2026（去设置→安全修改）",
            '安装': "📥 安装哈文\n双击'安装哈文.bat'→自动装Python/Ollama→桌面生成快捷方式\n初始密码：harven2026（首次登录后修改）",
            '模型': "🤖 本地模型\n设置→模型管理→一键拉模型\n推荐：qwen2.5:14b（通用）、huatuo（医疗）\n深搜窗口也可以直接申请拉模型\n注意：需8GB+内存，慢的话换外脑",
            '外脑': "🤖 外脑云端模型\n设置→模型管理→填API Key（DeepSeek/GLM/豆包）\n支持7+提供商，填Key后切换到外脑模式即可\n对话区底部可快捷切换外脑",
            '知识库': "📚 知识库\n侧边栏点📚知识库→上传文档→绑定到对话\n支持 txt/pdf/docx/md，文件≤50MB\nAI检索你的文档来回答问题",
            '手机': "📱 手机端\n电脑和手机同WiFi→浏览器打开 http://你的IP:7000\n获取IP：设置→关于→📱手机+公网\niPhone左上角☰拉出侧边栏",
            '设置': "⚙️ 设置页\n基本设置：端口、开机自启\n模型管理：填Key、拉模型、切换外脑\n安全隐私：改密码、隐私档位\n外观定制：主题色、字体",
            '面板': "📊 侧边栏面板\n知识库→上传文档检索\n专项培养→创建Agent\n任务管理→定时提醒\n监控中心→资源查看\n我的面板→自定义面板\n家庭记账→收支记录",
            '渠道': "📡 多渠道接入\n设置→关于→📡渠道接入\n飞书：填Webhook URL\n钉钉：填Webhook URL\nTelegram：填Bot Token",
            '密码': "🔐 改密码\n设置→🔒安全隐私→输入新密码→保存\n忘记密码？config.yaml 里 admin_password 字段",
        }
        result = help_db.get(topic) or help_db['']
        return "\n\n" + result

    def _build_context(self):
        """
        构建发送给模型的上下文（动态摘要策略）
        - 最近4轮(8条)：全量保留
        - 更早的历史：只保留user消息（去掉assistant长回复以节省token）
        - 最早的用户消息最多保留3条，避免无限增长
        ★ 2026-07-21: 8轮→4轮，减少旧话题污染当前问题
        """
        all_msgs = self.history
        n_recent = 8  # 4轮 × 2（★ 2026-07-21: 从16减到8，防止旧话题干扰）

        if len(all_msgs) <= n_recent:
            return all_msgs

        # 最近4轮全量
        recent = all_msgs[-n_recent:]

        # 更早的：只取user消息，最多3条
        older = all_msgs[:-n_recent]
        early_user = [m for m in older if m["role"] == "user"]
        early_summary = early_user[-3:]  # 最多3条最早的用户消息

        return early_summary + recent

    def _inject_memory_and_tasks(self, system_prompt, user_message=None):
        """P1-4: 统一注入记忆上下文 + 任务上下文（消除两处重复代码）"""
        try:
            from src.core.memory import get_memory_manager
            mem_ctx = get_memory_manager().build_memory_context(self.conversation_id, query=user_message)
            if mem_ctx:
                system_prompt += mem_ctx
        except Exception as e:
            logger.error(f"[Engine] 加载记忆失败: {e}")

        try:
            from src.core.task_manager import get_task_manager
            task_ctx = get_task_manager().build_task_context(self.conversation_id)
            if task_ctx:
                system_prompt += task_ctx
        except Exception as e:
            logger.error(f"[Engine] 加载任务上下文失败: {e}")

        # ★ 2026-09-17 反假记账声明（实锤：聊天模式说"模拟飞行1200"没入账，
        #   模型却回复"已经记录了1200元"——模型本身没有记账能力，只有系统
        #   输出「已处理/已入库」才是真实入账）
        system_prompt += ("\n【记账诚实规则】你本人没有直接记账的能力。只有当系统执行结果里出现"
                          "「已处理」「已入库」字样时，才表示真的入账了。严禁声称\"已经记录/已入账/"
                          "记好了\"这类没有发生的操作。若用户想记账而系统没有执行，请如实说明，"
                          "并建议他在记账账本里用「记一笔」或把小窗切到「记事」模式再说一遍。")
        return system_prompt

    def _adapt_mm_stream(self, mm_gen):
        """
        适配多模型管理器的输出流为纯文本生成器
        mm_gen yields {"type": "text/error", "content": str}
        我们需要 yield 纯文本字符串（与 ModelManager.chat 的输出格式一致）
        ★ 2026-09-27 幻觉工具JSON清洗（流式版）：GLM 会在正文里输出
        ```json {"tool_calls":[...]} ```（想调工具但没有工具通道），流式路径
        原样漏屏（开发者美团查询实锤，非流式路径 4295 早有拦截）。
        对策：缓冲 ```json / ``` 围栏块，内容含 "tool_calls" 特征 → 拦截不上屏；
        其余围栏（正常代码/JSON回答）闭合后原样放行。
        """
        buf = ''
        in_fence = False
        fence_lang = ''
        for item in mm_gen:
            if not isinstance(item, dict):
                continue
            t = item.get("type", "")
            c = item.get("content", "")
            if t == "error":
                if in_fence and buf:
                    yield '```' + fence_lang + ('\n' + buf if buf else '')
                yield f"[错误]{c}"
                in_fence = False
                buf = ''
                continue
            if t != "text" or not c:
                continue
            if not in_fence:
                idx = c.find('```')
                if idx == -1:
                    yield c
                    continue
                if idx > 0:
                    yield c[:idx]
                rest = c[idx + 3:]
                nl = rest.find('\n')
                fence_lang = rest[:nl] if nl != -1 else rest
                _lang = fence_lang.strip().lower()
                if _lang in ('', 'json'):
                    # json/裸围栏进入缓冲模式
                    buf = rest[nl + 1:] if nl != -1 else ''
                    in_fence = True
                else:
                    # python 等其他语言围栏：透传不缓冲
                    yield '```' + rest
                continue
            # 缓冲模式
            buf += c
            end = buf.find('```')
            if end == -1:
                if len(buf) > 12000:  # 防御：异常未闭合的超长缓冲直接放行
                    yield '```' + fence_lang + '\n' + buf
                    buf = ''
                    in_fence = False
                continue
            body = buf[:end]
            tail = buf[end + 3:]
            if '"tool_calls"' in body:
                logger.warning("[Engine] 流式拦截：幻觉tool_calls JSON已剥离不上屏")
                if tail:
                    yield tail
            else:
                yield '```' + fence_lang + ('\n' + body if body else '') + '```' + tail
            buf = ''
            in_fence = False
        # 流结束仍有未闭合缓冲：原样放行
        if in_fence:
            yield '```' + fence_lang + ('\n' + buf if buf else '')

    def _has_active_task(self):
        """检查当前对话是否有进行中的任务"""
        try:
            from src.core.task_manager import get_task_manager
            tasks = get_task_manager().get_active_tasks(self.conversation_id)
            return bool(tasks)
        except Exception:
            return False

    # P3: 闲聊快速匹配集合（替代列表逐条 in 检查）
    _CASUAL_SET = frozenset({
        "你好", "hello", "hi", "hey", "嗨", "哈喽",
        "早安", "晚安", "早上好", "下午好", "晚上好", "中午好",
        "你好啊", "在吗", "在不在",
        "谢谢", "thanks", "thank you", "感谢", "多谢", "谢了",
        "再见", "拜拜", "bye", "goodbye", "回头见", "下次聊",
        "嗯嗯", "嗯", "好的", "好", "知道了", "明白", "懂了",
        "哈哈", "哈哈哈", "呵呵", "嘿嘿", "笑死", "666",
        "ok", "okk", "okey", "行", "行吧", "随便", "都可以",
        "无聊", "累了", "困了", "饿了", "渴了", "开心", "难过",
        "生气", "烦", "郁闷", "高兴", "兴奋", "紧张", "害怕",
        "你是谁", "你叫什么", "你几岁", "你多大", "你生日",
        "你能做什么", "你会什么", "你有什么功能", "帮我看看",
        "继续", "接着说", "重新来", "再来一次", "换一个",
        "好的好的", "不错", "厉害", "牛", "棒", "可以可以",
        "差评", "好评", "真棒", "太好了", "太棒了",
    })

    def _matches_vertical_keyword(self, msg_lower: str) -> bool:
        """★ 2026-07-04 方案G：检查短消息是否匹配任何垂直模型的关键词
        
        平台化原则：遍历所有已注册垂直模型（内置+动态）的task_keywords，
        不在engine.py硬编码业务关键词。每加垂直模型只更新自己的task_keywords即可。
        
        Returns: True=匹配到垂直关键词（不应判闲聊），False=不匹配（可判闲聊）
        """
        try:
            from src.core.vertical_api import _BUILTIN_MODELS, _dynamic_models
            all_models = list(_BUILTIN_MODELS) + list(_dynamic_models.values())
            for model in all_models:
                for kw in model.get('task_keywords', []):
                    if kw and kw.lower() in msg_lower:
                        return True
            return False
        except Exception as e:
            logger.debug(f"[Engine] 垂直关键词预检失败: {e}")
            return False

    def _detect_install_intent(self, user_message):
        """★ 2026-07-05: 检测用户是否有安装模型的意图
        
        Returns: dict 或 None
            dict = {
                "model_id": "musicgen",
                "model_name": "MusicGen (音乐生成)",
                "category": "音乐",
                "size": "1.5GB",
                "requirements": {"ram": "8GB", "gpu": "推荐", "disk": "3GB"},
                "install_type": "pip",
                "description": "Meta AudioCraft 音乐生成"
            }
            None = 无安装意图
        """
        msg_lower = user_message.lower()
        
        # 安装意图关键词
        install_keywords = ["安装", "装个", "装一", "帮我装", "下载模型", "装模型", 
                           "install", "我要音乐模型", "我要画图模型", "我要语音模型"]
        has_install_intent = any(kw in msg_lower for kw in install_keywords)
        if not has_install_intent:
            return None
        
        # 遍历 model_installer 的 MODEL_CATALOG 匹配模型
        try:
            from src.core.model_installer import MODEL_CATALOG, check_model_installed
            # ★ 优先精确匹配 model_id，再匹配名称关键词
            for mid, info in MODEL_CATALOG.items():
                # 精确匹配 model_id（如用户说"装musicgen"，直接匹配mid="musicgen"）
                if mid.lower() in msg_lower:
                    if check_model_installed(mid):
                        return {"model_id": mid, "model_name": info.get('name', mid), "already_installed": True,
                                "message": f"✅ {info.get('name', mid)} 已经安装了，可以直接使用！"}
                    return {"model_id": mid, "model_name": info.get('name', mid),
                            "category": info.get('category', ''), "size": info.get('model_size', '未知'),
                            "requirements": info.get('requirements', {}),
                            "install_type": info.get('install', {}).get('type', ''),
                            "description": info.get('install', {}).get('desc', ''), "already_installed": False}
            
            # 模糊匹配
            for mid, info in MODEL_CATALOG.items():
                name_lower = info.get('name', '').lower()
                category = info.get('category', '')
                name_keywords = []
                if 'musicgen' in name_lower:
                    name_keywords.extend(['musicgen', 'music'])
                elif '音乐' in category:
                    name_keywords.extend(['音乐模型', '音乐生成'])
                if 'tts' in name_lower or '语音' in category:
                    name_keywords.extend(['tts', '语音', 'voice', 'coqui'])
                if 'whisper' in name_lower:
                    name_keywords.extend(['whisper', '语音识别'])
                if 'stable diffusion' in name_lower or '图像' in category:
                    name_keywords.extend(['stable diffusion', 'sd', '画图', '图像', 'image'])
                if 'svd' in name_lower or '视频' in category:
                    name_keywords.extend(['svd', '视频', 'video'])
                if 'ace' in name_lower:
                    name_keywords.extend(['ace', 'ace-step'])
                
                for nk in name_keywords:
                    if nk in msg_lower:
                        if check_model_installed(mid):
                            return {"model_id": mid, "model_name": info.get('name', mid), "already_installed": True,
                                    "message": f"✅ {info.get('name', mid)} 已经安装了，可以直接使用！"}
                        return {"model_id": mid, "model_name": info.get('name', mid),
                                "category": category, "size": info.get('model_size', '未知'),
                                "requirements": info.get('requirements', {}),
                                "install_type": info.get('install', {}).get('type', ''),
                                "description": info.get('install', {}).get('desc', ''), "already_installed": False}
        except Exception as e:
            logger.debug(f"[Engine] 安装意图检测失败: {e}")
        
        # 也检测本地GGUF模型安装意图
        gguf_keywords = ["本地模型", "gguf", "通用模型", "qwen", "deepseek", "llama", "本地大模型"]
        if any(kw in msg_lower for kw in gguf_keywords):
            return {
                "model_id": "_gguf_guide",
                "model_name": "本地GGUF模型",
                "category": "通用模型",
                "already_installed": False,
                "is_gguf": True,
                "message": "本地GGUF模型无需安装Ollama，直接下载模型文件即可。请到「设置 → 模型管理 → 本地模型」选择适合你显存的模型下载。"
            }
        
        return None

    def _detect_specialization_intent(self, user_message):
        """★ 2026-07-05: 检测用户是否有创建专项培养的意图
        
        Returns: dict 或 None
        """
        msg_lower = user_message.lower()
        
        # 创建意图关键词
        create_keywords = ["创建专项", "新建专项", "添加专项", "创建角色", "新建角色",
                          "创建一个.*专项", "帮我创建.*专项", "创建.*助手", "创建.*员",
                          "建一个.*培养", "新建.*培养"]
        
        has_create_intent = False
        import re
        for kw in create_keywords:
            if re.search(kw, user_message):
                has_create_intent = True
                break
        
        if not has_create_intent:
            return None
        
        # 预设角色模板
        _SPEC_TEMPLATES = {
            "翻译": {
                "name": "翻译助手",
                "icon": "🌐",
                "description": "中英互译，只输出翻译结果",
                "system_prompt": "你是专业翻译。用户发中文你翻译成英文，用户发英文你翻译成中文。只输出翻译结果，不要解释，不要添加额外内容。"
            },
            "代码审查": {
                "name": "代码审查员",
                "icon": "🔍",
                "description": "专注代码审查，指出bug+改进建议",
                "system_prompt": "你是资深代码审查员。用户发代码给你，你要：1.指出bug 2.建议改进 3.给出修正后的代码。用中文回答。"
            },
            "周报": {
                "name": "周报生成器",
                "icon": "📊",
                "description": "根据关键词生成周报",
                "system_prompt": "你是周报助手。用户发工作关键词，你生成结构化周报：本周完成/下周计划/问题风险。用中文回答。"
            },
            "诗人": {
                "name": "诗词创作",
                "icon": "✍️",
                "description": "根据主题创作诗词",
                "system_prompt": "你是诗人。用户发主题，你创作一首诗（古体或现代，看主题定）。只输出诗，不要解释。"
            },
            "英语": {
                "name": "英语口语教练",
                "icon": "🗣️",
                "description": "英文对话练习，纠正语法",
                "system_prompt": "你是英语口语教练。用英文回复用户，对话中纠正用户的语法错误，给出正确表达。"
            },
            "法律": {
                "name": "法律顾问",
                "icon": "⚖️",
                "description": "法律咨询助手",
                "system_prompt": "你是法律顾问。用户描述情况，你给出法律分析和建议。重要：提醒用户这只是参考，具体问题请咨询专业律师。"
            },
            "健身": {
                "name": "健身教练",
                "icon": "💪",
                "description": "健身计划指导",
                "system_prompt": "你是健身教练。用户描述目标和身体状况，你给出训练计划+饮食建议。提醒用户循序渐进，注意安全。"
            },
            "理财": {
                "name": "理财顾问",
                "icon": "💰",
                "description": "个人理财建议",
                "system_prompt": "你是理财顾问。用户描述财务状况和目标，你给出理财建议。重要：提醒用户投资有风险，建议仅供参考。"
            },
        }
        
        # 匹配预设模板
        for key, template in _SPEC_TEMPLATES.items():
            if key in user_message:
                return {
                    "action": "create",
                    "name": template["name"],
                    "icon": template["icon"],
                    "description": template["description"],
                    "system_prompt": template["system_prompt"],
                    "preset": True
                }
        
        # 没匹配到预设，让用户自定义
        return {
            "action": "create_custom",
            "name": "",
            "icon": "🤖",
            "description": "",
            "system_prompt": "",
            "preset": False,
            "message": "检测到您想创建专项培养。请告诉我：1.角色名称 2.这个角色应该做什么（规则）"
        }

    def _detect_kb_upload_intent(self, user_message):
        """★ 2026-07-05: 检测用户是否有上传知识库的意图
        
        Returns: dict 或 None
        """
        msg_lower = user_message.lower()
        
        # 上传意图关键词
        upload_keywords = [
            "上传知识库", "导入知识库", "添加知识库", "知识库上传",
            "帮我上传.*知识库", "怎么上传.*知识库", "如何上传.*知识库",
            "知识库.*怎么用", "知识库.*怎么传", "往知识库.*传",
            "知识库.*添加文件", "知识库.*导入文件",
        ]
        
        import re
        has_upload_intent = False
        for kw in upload_keywords:
            if re.search(kw, user_message):
                has_upload_intent = True
                break
        
        if not has_upload_intent:
            return None
        
        # 检查是否已有知识库
        try:
            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            kb_list = kb.list_documents() if hasattr(kb, 'list_documents') else []
            kb_count = len(kb_list) if kb_list else 0
        except Exception:
            kb_count = 0
        
        return {
            "action": "guide",
            "kb_count": kb_count,
            "formats": "txt/md/csv/pdf/docx/xlsx/pptx",
            "message": "知识库可以存储文档，对话时自动搜索相关内容。点击下方按钮打开知识库面板。"
        }

    def _detect_evolution_intent(self, user_message):
        """★ 2026-07-06: 检测用户是否有受控进化（改代码/加功能）的意图
        
        Returns: dict 或 None
        """
        import re
        msg_lower = user_message.lower()
        
        # 进化意图关键词
        evolution_keywords = [
            "帮我加.*功能", "添加.*功能", "新功能", "帮我改.*代码",
            "修改.*代码", "优化.*代码", "改一下.*功能", "帮我实现.*功能",
            "加一个.*技能", "添加.*技能", "创建.*skill",
            "帮我写.*脚本", "加一个.*脚本",
            "改.*配置", "修改.*设置", "优化.*配置",
            "改.*页面", "修改.*界面", "优化.*界面",
            "改.*模板", "修改.*样式",
        ]
        
        has_evolution_intent = False
        for kw in evolution_keywords:
            if re.search(kw, user_message):
                has_evolution_intent = True
                break
        
        if not has_evolution_intent:
            return None
        
        # 判断要改什么文件 → 风险等级
        changes = []
        risk_level = "low"
        risk_desc = "低风险（技能脚本）"
        needs_confirm = False
        
        # 检测改skills/
        if any(kw in msg_lower for kw in ["技能", "skill", "脚本", "功能", "翻译", "自动化"]):
            changes.append({
                "file": "skills/（待生成）",
                "action": "write",
                "desc": "创建或修改技能脚本"
            })
            risk_level = "low"
            risk_desc = "低风险（技能脚本，不影响核心运行）"
        
        # 检测改templates/
        elif any(kw in msg_lower for kw in ["页面", "界面", "模板", "按钮", "tab", "设置页"]):
            changes.append({
                "file": "templates/（待确认具体文件）",
                "action": "modify",
                "desc": "修改页面模板"
            })
            risk_level = "medium"
            risk_desc = "中风险（页面模板，可能影响显示）"
            needs_confirm = True
        
        # 检测改config.yaml
        elif any(kw in msg_lower for kw in ["配置", "设置", "默认.*模式", "参数"]):
            changes.append({
                "file": "config.yaml",
                "action": "modify",
                "desc": "修改系统配置"
            })
            risk_level = "high"
            risk_desc = "高风险（系统配置，修改不当可能影响运行）"
            needs_confirm = True
        
        # 检测改static/
        elif any(kw in msg_lower for kw in ["样式", "css", "颜色", "外观", "样式"]):
            changes.append({
                "file": "static/（待确认具体文件）",
                "action": "modify",
                "desc": "修改静态资源"
            })
            risk_level = "low"
            risk_desc = "低风险（静态资源，不影响功能）"
        
        # 默认：不明确改什么，让用户说清楚
        if not changes:
            changes.append({
                "file": "待确认",
                "action": "unknown",
                "desc": "需要进一步确认要修改什么"
            })
        
        # E1: 人话描述
        title = "受控进化提案"
        desc = self._build_evolution_desc(user_message, changes, risk_level)
        
        return {
            "title": title,
            "description": desc,
            "changes": changes,
            "risk_level": risk_level,
            "risk_desc": risk_desc,
            "needs_confirm": needs_confirm,
            "user_message": user_message,
            "warning": self._build_evolution_warning(risk_level) if needs_confirm else None,
        }

    def _build_evolution_desc(self, user_message, changes, risk_level):
        """E1: 生成人话描述"""
        change_desc = changes[0]["desc"] if changes else "修改代码"
        risk_text = {"low": "低风险", "medium": "中风险", "high": "高风险"}.get(risk_level, "未知")
        return f"用户需求：{user_message}\n修改内容：{change_desc}\n风险等级：{risk_text}"

    def _build_evolution_warning(self, risk_level):
        """E2: 生成风险警告"""
        if risk_level == "medium":
            return "⚠️ 这会修改页面模板，可能影响显示。如果修改后页面异常，可以一键回滚。确定要修改吗？"
        elif risk_level == "high":
            return "🔴 这会修改系统配置文件！修改不当可能影响哈文正常运行。已自动备份原配置，如果出问题可以回滚。确定要修改吗？建议仔细确认。"
        return None

    # ★ 2026-09-27 #885 E1 纯聊天快车道（已批准）——判定词表
    #   闲聊/角色扮演/介绍解释类跳过 FC 首轮非流式（答案写完才显示=白等全文生成时间），
    #   直接流式出字。保守白名单 + 操作动词排除，宁漏勿杀：拿不准就照常走 FC。
    _FC_SKIP_WHITELIST = [
        '变身', '扮演', '角色扮演', '假设你是', '假如你是', '你就是', '来当', '当一回', '装作',
        '讲个故事', '讲一个故事', '说个笑话', '讲个笑话', '来个笑话', '给我讲个',
        '介绍一下', '解释一下', '是什么', '什么是', '是什么意思', '为什么', '怎么用', '如何使用',
        '有什么区别', '有什么用', '你有什么', '你会什么', '你会不会', '你能做什么',
    ]
    _FC_SKIP_GREET = re.compile(
        r'^(你好|您好|哈喽|hello|hi|嗨|在吗|在么|早安|早上好|中午好|下午好|晚上好|晚安|'
        r'谢谢|感谢|辛苦了|你好呀|hello+)[!！。~ ～?？\s]*$', re.IGNORECASE)
    _FC_SKIP_BLOCKERS = [
        # 文件/命令/系统操作
        '打开', '删除', '新建', '写入', '读取', '执行', '运行', '安装', '卸载', '清空',
        '下载', '上传', '重启', '关机', '截图', '控制', '播放', '列出', '遍历', '搜索',
        '帮我找', '找一下', '查一下', '查查', '查看', '看看我的', '路径', '目录', '文件夹',
        # 数据域（有专门车道，不走这里）
        '记账', '记一笔', '花了', '花销', '账单', '花了多少',
        '任务', '提醒', '天气', '新闻', '翻译',
        # ★ #1497（2026-11-13 实锤）：邮件域——「邮箱最新收到的邮件是什么」命中白名单
        #   「是什么」被判闲聊砍 FC。邮箱/邮件永远有专门工具车道，禁止判闲聊。
        '邮件', '邮箱', '收件', '发件',
        # 附件/系统注入
        '【文件', '系统注', '文件:',
        # ★ #1316（2026-10-30 咖啡机事故实锤）：改/写类操作词——「这个咖啡机是什么牌子 给它加标注」
        #   命中白名单「是什么」被判闲聊砍 FC → GLM 无工具只能写代码裸奔。操作动作必须走 FC。
        '标注', '加标注', '备注', '添加', '设为', '设成', '改成', '改名', '重命名', '命名',
        '记住', '记下来', '删掉', '去掉', '换成', '存到', '存进', '放到', '放进', '归类',
    ]
    # ★ #1316 指令式动作正则：「给它加标注」「帮我记下」「把它改成X」等「代词+动作」
    #   结构一律不判闲聊（宁漏勿杀：宁多走一轮 FC，绝不把操作当闲聊砍工具）
    _FC_SKIP_ACTION_RE = re.compile(r'(给它|帮我|把它|给我)[们个]?\s*(加|设|标|改|记|存|删|换|命名|备注)')

    def _fc_pure_chat_skip(self, text):
        """#885 纯聊天快车道判定：命中白名单/问候语且无操作动词 → 跳过FC首轮非流式。
        ★ #1316：操作动作（词表/指令式正则）优先于白名单——先查动作再判闲聊。"""
        t = (text or '').strip()
        if not t or len(t) > 120:
            return False
        if any(b in t for b in self._FC_SKIP_BLOCKERS):
            return False
        if self._FC_SKIP_ACTION_RE.search(t):
            return False
        if self._FC_SKIP_GREET.match(t):
            return True
        return any(w in t for w in self._FC_SKIP_WHITELIST)

    # ═══ ★ #1497（2026-11-13）本地FC前置轮：A护栏 + B打通 ═══

    def _local_fc_preround(self, all_messages, model_name, user_message, notify=None):
        """本地模型 FC 前置轮（A护栏 + B打通，一步到位）。

        外脑阵营早有 FC 循环（mm.generate_response tools=）；本地阵营此前是哑巴——
        13 个 FC 工具只加载在上下文里，从不以 Ollama tools 格式真传，模型不知道自己
        能调工具 → 瞎编「系统没有集成邮箱功能」（2026-11-12 23:59 实锤）。

        Returns:
            ('skip', None)   不归本管 → 调用方走原有本地链路（零行为变化）
            ('guard', 文本)  A护栏命中 → 调用方如实上屏+落库，不再喂给模型
            ('done', 文本)   B已收笔 → 调用方直接上屏最终回答
        """
        _emit = notify or (lambda kind, content: None)
        try:
            from src.core.builtin_tools import get_fc_bundle, execute_tool_call
        except Exception:
            return ('skip', None)

        # 门1：纯聊天快车道（与外脑 #885 同一门，闲聊不进 FC 轮）
        if self._fc_pure_chat_skip(user_message or ''):
            return ('skip', None)

        # 门2：模型 tools 能力探测
        if not _ollama_supports_tools(model_name):
            # A·护栏：FC 域意图 + 模型没工具能力 → 如实说破（死命令：不代选，主动权在用户）
            if any(k in (user_message or '') for k in _FC_DOMAIN_SIGNALS):
                logger.info(f"[Engine] #1497-A 护栏触发: model={model_name}, msg={(user_message or '')[:40]}")
                msg = (
                    f"⚙️ 「{(user_message or '')[:40]}」这类操作需要**工具执行能力**（查邮件/发邮件/记账动手这类）。\n\n"
                    f"当前模型「{model_name}」不具备工具调用能力，我不能假装办了，也不替你换模型。\n\n"
                    f"👉 两个办法（你选）：\n"
                    f"1. 点对话区 🤖 切到外脑，再发一次，立刻能办；\n"
                    f"2. 换支持工具调用的本地模型（qwen3 / qwen3-vl / qwen2.5 系都可以）。"
                )
                return ('guard', msg)
            return ('skip', None)

        # 门3：取 FC 工具清单（联网授权制：本地前置轮不注入联网工具）
        try:
            fc_tools, _catalog = get_fc_bundle(allow_web_search=False)
        except Exception:
            return ('skip', None)
        if not fc_tools:
            return ('skip', None)

        _emit('thinking', '🔎 本地工具调用中…')
        import requests as _rq_fc
        import json as _json_fc
        # ★ #1585（2026-11-27）：死连接实锤——keep-alive 旧连接被 Ollama 侧关闭后复用，
        #   POST 发出但对端没收（Ollama 日志无此请求），requests 在 _read_status 空等
        #   300s 超时 → 眼镜对话 5 分钟无响应（py-spy 快照实锤）。根治三件：
        #   ① Connection: close 每轮新建连接（本机 loopback，握手成本可忽略）
        #   ② 超时改 (连接5s, 读120s)——本地 FC 单轮推理远用不了 300s
        #   ③ 连接类异常自动重试 1 次（换新连接），仍失败才走原 skip 兜底
        _fc_timeout = (5, 120)
        _fc_hdrs = {'Connection': 'close'}
        messages = list(all_messages)
        # ★ #1497-B 补丁（2026-11-13 00:15 线上 400 实锤）：sanitize 后再发——
        #   历史注入的预查消息可能 content=None / role 非标准 / content 是 list，
        #   Ollama /api/chat 直接 400 Bad Request（响应体此前没落日志，瞎区）。
        #   规范成 Ollama 只认的 {role, content(str)}，非标准条目直接丢弃。
        _ok_roles = ('system', 'user', 'assistant')
        _clean = []
        for _m in messages:
            if not isinstance(_m, dict):
                continue
            _r = _m.get('role')
            if _r not in _ok_roles:
                continue
            _c = _m.get('content')
            if _c is None:
                _c = ''
            elif not isinstance(_c, str):
                try:
                    _c = _json_fc.dumps(_c, ensure_ascii=False)
                except Exception:
                    _c = str(_c)
            _clean.append({'role': _r, 'content': _c})
        messages = _clean if _clean else [{'role': 'user', 'content': user_message or ''}]
        # ★ #1497 强提示词（2026-11-13 实锤裸调对照）：不提示时 qwen3 系把 tools 当说明书
        #   看（回复「我可以帮你推断服务器地址」却不发 tool_calls）；加「必须调工具」提示后
        #   qwen3-vl / qwen3 双模型均精准出 tool_calls（与外脑 FC 强化提示词 2026-08-13 同理）
        _hint = (
            "## 🔧 工具调用规则\n"
            "1. 用户的请求凡是需要查数据、执行操作（查邮件/发邮件/查记录/记一笔/执行命令/读写文件等），"
            "必须**立即调用对应工具**真正执行，严禁只给建议或让用户自己动手。\n"
            "2. 工具返回结果后，用中文组织简洁人话回答，带上关键数据。\n"
            "3. 没有合适工具才用文字说明，如实讲办不了。"
        )
        # ★ #1497-C 瘦身（2026-11-13 老谭拍板 A+B）：前置轮只做「判定调不调工具+执行」，
        #   不需要全套人设注入（技能/教练简报/方案N/REA 等 12000+ tokens）——每条消息
        #   原样重发等于白等十几秒。瘦身为「身份+时间+FC规则」；历史只带最近 5 条
        #   （B 截断，老谭定 5 条）。工具结果收笔后，正式回答仍由原链路全套上下文兜底，质量不受影响。
        from datetime import datetime as _dt_1497c
        _slim_system = (
            "你是「Harven AI 哈文」，本地家庭AI助手。当前时间："
            + _dt_1497c.now().strftime('%Y-%m-%d %H:%M') + "。\n\n" + _hint
        )
        messages = ([{'role': 'system', 'content': _slim_system}]
                    + [m for m in messages if m.get('role') in ('user', 'assistant')][-5:])
        tools_used = []
        _nudged = False   # ★ #1497 解释空转只追问一次
        for round_i in range(5):
            try:
                r = _rq_fc.post('http://127.0.0.1:11434/api/chat',
                                json={'model': model_name, 'messages': messages,
                                      'tools': fc_tools, 'stream': False,
                                      'options': {'temperature': 0.3,
                                                  # ★ #1497-B 补丁2（2026-11-13 实锤）：线上 system
                                                  #   全套注入 12000+ tokens，Ollama 默认 num_ctx=4096
                                                  #   → 400 "exceeds the available context size"。
                                                  #   显式拉高到 24576（qwen3 原生 32K 内）
                                                  'num_ctx': 24576}},
                                timeout=_fc_timeout, headers=_fc_hdrs)   # ★ #1585 短超时+短连接
            except (_rq_fc.exceptions.ConnectionError, _rq_fc.exceptions.Timeout) as _ce:
                # ★ #1585：死连接/超时自动重试 1 次（新连接），再挂才走 skip 兜底
                try:
                    r = _rq_fc.post('http://127.0.0.1:11434/api/chat',
                                    json={'model': model_name, 'messages': messages,
                                          'tools': fc_tools, 'stream': False,
                                          'options': {'temperature': 0.3,
                                                      'num_ctx': 24576}},
                                    timeout=_fc_timeout, headers=_fc_hdrs)
                except Exception as e2:
                    logger.warning(f"[Engine] #1497-B 第{round_i+1}轮调用失败(含重试): {e2}")
                    return ('skip', None)   # 失败不拦截 → 原有链路兜底（不回落外脑，铁律 #891）
                logger.warning(f"[Engine] #1585 第{round_i+1}轮死连接已自动重试成功 ({type(_ce).__name__})")
            if r.status_code != 200:
                # ★ #1497-B 补丁：非 200 把 Ollama 响应体落日志（此前只报状态码，瞎区）
                logger.warning(
                    f"[Engine] #1497-B 第{round_i+1}轮 HTTP {r.status_code}: {r.text[:300]}")
                return ('skip', None)
            m = (r.json() or {}).get('message') or {}
            tcs = m.get('tool_calls') or []
            content = (m.get('content') or '').strip()
            if not tcs:
                if not tools_used:
                    if not _nudged:
                        # ★ #1497 解释空转追问（2026-11-13 实锤：第1轮模型解释「要哪个邮箱/
                        #   先配置」不调工具 → 追问1次 → qwen3-vl 精准出 tool_calls email_fetch）
                        _nudged = True
                        logger.info(f"[Engine] #1497 第{round_i+1}轮解释不调工具，追问一次")
                        messages.append({'role': 'assistant', 'content': content or ''})
                        messages.append({'role': 'user',
                                         'content': '不要解释，也不要向用户索要配置信息。'
                                                    '相关配置系统已就绪，请立即调用合适的工具完成我的请求。'})
                        continue
                    # 追问后仍不调 → 交回原有流式链路（保持流式体验与标注，零回归）
                    return ('skip', None)
                # 工具已执行 → 这是收笔总结
                final = self._defuse_raw_json(content) if content else ''
                logger.info(f"[Engine] #1497-B 收笔: 工具足迹={tools_used}, {len(final)}字")
                return ('done', final)
            # 有 tool_calls → 落 assistant 消息 + 逐个执行 + tool 结果回填
            messages.append({'role': 'assistant', 'content': content, 'tool_calls': tcs})
            for tc in tcs:
                fn = (tc.get('function') or {}) or {}
                name = fn.get('name', '')
                args = fn.get('arguments') or {}
                if isinstance(args, str):
                    try:
                        args = _json_fc.loads(args)
                    except Exception:
                        args = {}
                logger.info(f"[Engine] #1497-B 本地FC工具: {name}({str(args)[:150]})")
                try:
                    result = execute_tool_call(name, args if isinstance(args, dict) else {})
                except Exception as e:
                    result = _json_fc.dumps({'success': False, 'error': str(e)}, ensure_ascii=False)
                tools_used.append(name)
                messages.append({'role': 'tool', 'content': str(result)[:8000]})
        # 5 轮还没收笔 → 放弃总结，把查到的数据直接转给人话（★ #1585：不再让机器腔出口播）
        return ('done', '我查好了数据，但没能组织成回答。刚才查的是：'
                + '、'.join(dict.fromkeys(tools_used))
                + '。麻烦你再说一遍想看什么，我马上重新答。')

    # ★ 2026-10-05 #990 查询链分层 C′（定版拍板）——第0层纯查询直出
    #   分析信号词：命中任何一个 = 用户要的是「看法」不是「数字」→ 交模型，不直出
    _QUERY_ANALYZE_RE = re.compile(
        r'分析|为什么|为啥|建议|趋势|对比|总结|异常|规律|报告|省|划算|合理|健康|怎么样|如何|靠谱|评价')
    _QUERY_DIRECT_MAXLEN = 40   # 纯查询是短句；超长多半带叙事/多问，落回现状链

    def _query_direct_answer(self, text):
        """#990 查询链分层第0层：纯数据查询本地直出（0 token、毫秒级、两端字面一致）。
        命中条件（全满足才直出，宁缺勿滥）：
          1. 不含分析信号词（分析/建议/为什么…→ 交给模型组织）
          2. 消息 ≤40 字
          3. 档案中枢 hub_direct_answer 命中（记账域关键词 + 时间解析成功）
        未命中返回 None → 落回现状链（预查注入 + 当前模型组织人话），
        即 C′ 的第2层「分析类才进模型」；灰区全部自然落现状链，
        不再单设 40 token 意图小调用（多一次往返徒增延迟与故障面，砍掉）。"""
        try:
            t = (text or '').strip()
            if not t or len(t) > self._QUERY_DIRECT_MAXLEN:
                return None
            if self._QUERY_ANALYZE_RE.search(t):
                return None
            from src.core.archive_hub import hub_direct_answer as _hda
            ans, _res = _hda(t)
            return (ans or '').strip() or None
        except Exception:
            return None

    # ★ 2026-09-27 #886 B JSON回炉保底（已批准）
    _RAW_JSON_RE = re.compile(r'^\s*\{[\s\S]*\}\s*$')

    def _defuse_raw_json(self, text):
        """外脑把工具结果的生 JSON 原样复读成回答时（实锤案例：GLM 二轮复读
        {"success":true,"message":"本月暂无记账记录",...}），本地翻译成人话。"""
        t = (text or '').strip()
        if not (t.startswith('{') and t.rstrip().endswith('}')):
            return text
        if not self._RAW_JSON_RE.match(t):
            return text
        try:
            obj = json.loads(t)
        except Exception:
            return text
        if not isinstance(obj, dict):
            return text
        # 只处理带工具结果特征的字段组合，正常 JSON 代码回答不受影响
        if not any(k in obj for k in ('success', 'message', 'error', 'records', 'results')):
            return text
        msg = str(obj.get('message') or obj.get('error') or '').strip()
        if not msg:
            return "查询完成了，但这次没有整理成文字。你可以到对应面板里直接查看，或者换个问法再试一次。"
        extra = ''
        if ('total_income' in obj) or ('total_expense' in obj):
            extra = f"（收入 {obj.get('total_income', 0)} 元，支出 {obj.get('total_expense', 0)} 元）"
        elif 'total_count' in obj:
            extra = f"（共 {obj.get('total_count', 0)} 条）"
        logger.info("[Engine] JSON回炉触发：外脑输出生JSON，已本地翻译")
        return f"{msg}{extra}"

    # ★ #1315（2026-10-30 咖啡机事故实锤）围栏语言标记清洗
    @staticmethod
    def _strip_fence_lang(code):
        """GLM 写码有时在围栏内复读语言标记——```python\npython\nimport …``` →
        裸词 `python` 行被执行 → NameError ×2（咖啡机事故最后一环实锤）。
        剥掉首部裸语言标记行：合法 Python 里独立一行 python/py/python3 必然 NameError，
        剥掉永远安全、永不误伤。"""
        return re.sub(r'^\s*(?:python3?|py)\s*\n', '', code or '')

    def _classify_query(self, message):
        """
        判断查询类型（关键词快速匹配 + LLM 兜底）

        策略：集合匹配覆盖 80% 常见场景（O(1)），
        剩余 20% 模糊场景交给 LLM 分类。

        Returns:
            dict: {"need_kb": bool, "query_type": str}  # need_web已废弃
        """
        msg = message.strip()
        msg_lower = msg.lower()

        # 短消息快速跳过
        # ★ 2026-07-04 方案G：短消息先查垂直关键词，匹配则不判闲聊
        #   防止"忧伤""乡村"等垂直任务词被误判为闲聊导致通用模型幻觉
        # ★ 2026-07-21: 短消息含联网/事实关键词时不判闲聊（如"烟台天气"应搜索）
        if len(msg) <= 4 and all(c not in "？?。.！!;；" for c in msg):
            _short_web_kws = [
                "天气", "气温", "新闻", "股价", "股票", "汇率", "热搜",
                "最新", "现在", "今天", "价格", "行情", "油价", "金价",
                "指数", "大盘", "比分", "赛果",
            ]
            if not self._matches_vertical_keyword(msg_lower) and not any(kw in msg_lower for kw in _short_web_kws):
                return {"need_kb": False, "need_web": False, "query_type": "casual"}

        # ── 集合快速匹配（P3 优化）──
        # 闲聊关键词：精确匹配
        for word in msg_lower.split():
            if word in self._CASUAL_SET:
                return {"need_kb": False, "need_web": False, "query_type": "casual"}
        # 子串匹配（中文不分词）
        for p in self._CASUAL_SET:
            if p in msg_lower:
                return {"need_kb": False, "need_web": False, "query_type": "casual"}

        web_patterns = [
            # 中文
            "最新", "现在", "今天", "今年", "2025", "2026", "2027",
            "新闻", "消息", "进展", "发布", "更新", "公告",
            "价格", "股票", "汇率", "行情", "走势", "涨", "跌",
            "天气", "热搜", "趋势", "预测", "近日", "近期",
            "刚刚", "昨天", "前天", "这周", "本周", "上周", "下周",
            "这个月", "本月", "上个月", "下个月",
            "什么时候", "何时", "几点", "倒计时",
            # 英文
            "latest", "news", "today", "current", "recent", "update",
            "price", "stock", "weather", "forecast", "trend", "hot",
            "breaking", "just in", "now", "real-time", "live",
        ]
        for p in web_patterns:
            if p in msg_lower:
                return {"need_kb": True, "need_web": True, "query_type": "realtime"}

        kb_patterns = [
            "什么是", "如何", "怎么", "为什么", "解释", "介绍", "原理",
            "步骤", "方法", "教程", "指南", "帮我", "告诉",
            "是什么", "有多少", "有哪些", "区别", "对比", "比较",
            "公式", "定义", "概念", "算法", "协议", "框架",
            "写", "生成", "创建", "做一个", "开发",
            "代码", "编程", "网站", "网页", "html", "css", "javascript",
            "js", "react", "vue", "前端", "后端", "api", "接口",
            "脚本", "程序", "软件", "app", "应用",
        ]
        for p in kb_patterns:
            if p in msg_lower:
                return {"need_kb": True, "need_web": False, "query_type": "knowledge"}

        # ── LLM 兜底分类（关键词未命中时的智能判断）──
        # 仅对有一定长度的消息触发（避免短消息浪费 LLM 调用）
        if len(msg) >= 10:
            llm_result = self._classify_with_llm(message)
            if llm_result is not None:
                return llm_result

        # 默认
        return {"need_kb": False, "need_web": False, "query_type": "general"}

    def _classify_with_llm(self, message):
        """
        LLM 智能分类（兜底，仅在关键词未命中时触发）
        # ★ 2026-07-07: need_web 已废弃（联网搜索由Step 1.5统一处理）
        """
        try:
            prompt = f"""你是一个查询分类器。请判断以下用户消息的意图类型，只返回 JSON：
{{"need_kb": true/false, "type": "knowledge/casual/coding/creative"}}

规则：
- need_kb=true: 需要查询本地知识库（涉及已知知识、教程、文档）
- 闲聊/情感/问候: need_kb=false, type="casual"
- 写代码/技术问题: need_kb=true, type="coding"
- 创作/写作: need_kb=false, type="creative"

用户消息: {message[:500]}

JSON:"""


            full_text = ""
            try:
                # 意图分类强制使用本地模型（10字prompt，不值得调云）
                response_stream = self.model.chat_with_context(
                    system_prompt="",
                    conversation_history=[],
                    user_message=prompt,
                    stream=True,
                    temperature=0.0,
                )
                for chunk in response_stream:
                    if isinstance(chunk, str) and chunk.startswith("[错误]"):
                        return None
                    if isinstance(chunk, str):
                        full_text += chunk
            except Exception as e:
                logger.debug(f"[Engine] LLM分类器流式调用失败: {e}")
                return None

            # 解析 JSON
            import json as _json
            text = full_text.strip()
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                result = _json.loads(text[start:end + 1])
                # ★ 2026-07-07: need_web 始终 False（联网搜索由Step 1.5统一处理）
                return {
                    "need_kb": result.get("need_kb", False),
                    "need_web": False,
                    "query_type": result.get("type", "general"),
                }
        except Exception as e:
            logger.warning(f"[Engine] LLM分类器异常（将使用关键词匹配）: {e}")
        return None

    # ═══════════════════════════════════════════════════════════════
    # ★ 2026-07-03: 垂直任务参数提取模式（智能预填）
    # ═══════════════════════════════════════════════════════════════
    def _detect_vertical_task(self, user_message: str) -> str | None:
        """检测用户消息是否匹配垂直模型任务，返回模型ID或None

        ★ 2026-11-15: 加 _model_available 可用性过滤（方案 A ③）——
        不可用模型（如 SD/Bark/MusicGen/wan22 占位）不参与抢答，
        避免死条目/未部署模型截胡消息（#1518 同族病根）。
        """
        try:
            from src.core.vertical_api import _BUILTIN_MODELS, _model_available
            msg_lower = user_message.lower()
            best_model = None
            best_score = 0
            for model in _BUILTIN_MODELS:
                if not _model_available(model):  # ★ 收 dict 不是 id（签名 _model_available(m: dict)）
                    continue  # 模型不可用 → 不抢答
                keywords = model.get("task_keywords", [])
                score = sum(2 for kw in keywords if kw.lower() in msg_lower)
                if score > best_score:
                    best_score = score
                    best_model = model["id"]
            return best_model if best_score >= 2 else None
        except Exception as e:
            logger.debug(f"[Engine] 垂直任务检测失败: {e}")
            return None

    def _get_vertical_schema(self, model_id: str) -> dict | None:
        """获取垂直模型的完整schema（含参数定义）"""
        try:
            from src.core.vertical_api import _BUILTIN_MODELS
            for model in _BUILTIN_MODELS:
                if model["id"] == model_id:
                    return model
            return None
        except Exception as e:
            logger.debug(f"[Engine] 获取schema失败: {e}")
            return None

    def _build_param_extract_prompt(self, user_message: str, model_id: str, schema: dict) -> str:
        """构建参谋模式 system prompt
        
        ★ 2026-07-04: 参谋模式 — 通用模型先判断需求是否明确
          - 需求模糊 → 输出 ADVISE 格式（反问+建议）
          - 需求明确 → 输出 JSON 格式（直接提取参数）
        """
        params_desc = []
        for pname, pdef in schema.get("params", {}).items():
            line = f"- {pname}（{pdef.get('label', pname)}）"
            if pdef.get("type") == "select":
                opts = ", ".join(pdef.get("options", []))
                line += f"：可选值 [{opts}]，默认 {pdef.get('default','')}"
            elif pdef.get("type") == "range":
                line += f"：范围 {pdef.get('min')}-{pdef.get('max')}，默认 {pdef.get('default','')}"
            elif pdef.get("type") == "textarea":
                line += f"：文本，默认 {pdef.get('default','')}"
            else:
                line += f"：默认 {pdef.get('default','')}"
            if pdef.get("required"):
                line += " 【必填】"
            params_desc.append(line)

        # ★ 2026-07-04: 注入能力边界 + 专业建议
        cap = schema.get("capability", {})
        cap_section = ""
        if cap:
            cap_section = f"""
【模型能力边界】（必须遵守，不能超出）
✅ 能做：{', '.join(cap.get('can_do', []))}
❌ 不能做：{', '.join(cap.get('cannot_do', []))}
📏 限制：{cap.get('max_limits', '')}

【专业建议库】
{cap.get('advisor_tips', '')}
"""

        return f"""你是「{schema.get('name', model_id)}」垂直模型的音乐创作参谋。你的职责是帮用户想全面、给专业建议、提取参数。
{cap_section}
【用户输入】
{user_message}

【可用参数】（只能从以下选项中选值，不能瞎编）
{chr(10).join(params_desc)}

【三模式工作流程】
根据用户消息中包含的"明确参数"数量，选择对应模式：

★ 关键规则：动作词前置检查
动作词清单：做/写/画/生成/作曲/编曲/朗读/翻译/合成/制作/创作/来个/来一首/写一首/做一首
如果用户消息中没有动作词，风格/情绪/乐器词不计入明确参数（因为用户可能不是要做音乐）。

明确参数计数规则（满足几个算几个）：
- 风格：乡村/爵士/古典/摇滚/电子/民谣/流行/金属/布鲁斯/史诗/中国传统/古风/国风
- 情绪：欢快/忧伤/悲伤/平静/活力/神秘/浪漫/忧郁/激昂
- 乐器：钢琴/吉他/小提琴/鼓/长笛/管弦/合成器
- 时长：含数字+秒（如"15秒""30秒"）
- BPM：含数字+BPM/拍（如"120BPM""90拍"）

模式选择规则：
- 无动作词 或 明确参数 = 0 个 → 【模式A：参谋反问】
- 有动作词 且 明确参数 = 1-2 个 → 【模式B：建议+确认】（★ 最重要）
- 有动作词 且 明确参数 ≥ 3 个 → 【模式C：直接提取】

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【模式A：参谋反问】（无动作词 或 0个明确参数）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
触发：用户说"写首曲子""做首歌""来个音乐"（有动作词但无参数）
      或用户说"乡村""忧伤""摇滚"（无动作词只有风格/情绪词）
输出格式：
ADVISE:建议内容（用\\n换行）

★ 无动作词时，反问必须包含意图确认：
示例1 - 用户："乡村"
ADVISE:您是想做一首乡村音乐吗？\\n如果是，我还需要了解：您想要什么情绪？多长时间？\\n如果不是想做音乐，直接告诉我您想做什么（写文字/画图/聊聊都行）。

示例2 - 用户："忧伤"
ADVISE:您是想做一首忧伤的音乐吗？\\n如果是，我建议搭配古典风格+小提琴+70BPM。您觉得呢？\\n如果不是想做音乐，直接告诉我您想做什么。

示例3 - 用户："写一首曲子"（有动作词无参数）
ADVISE:好的！我帮您规划。\\n\\nACE-Step 可以生成纯音乐或带人声歌曲（10-240秒）。\\n为了做出满意曲子，我需要了解：\\n1. 什么风格？（国风/流行/古典/摇滚/电子...）\\n2. 纯音乐还是带人声唱歌？\\n3. 什么场景？（背景音乐/游戏配乐/手机铃声...）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【模式B：建议+确认】（1-2个明确参数）★ 核心
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
触发：用户说"写一首乡村音乐""做一首忧伤曲子""来个吉他曲"
要求：用户指定的参数直接提取，用户没指定的参数根据【专业建议库】推荐，不要用 auto！
输出格式（两行：第一行建议，第二行JSON）：
SUGGEST:建议文字（用\\n换行）
{{"param": "value", ...}}

示例：
用户："写一首乡村音乐"
SUGGEST:乡村风格经典搭配：吉他 + 100BPM + 欢快情绪。\\n我建议这个配置，您觉得可以吗？
{{"style": "country 乡村", "instrument": "guitar 吉他", "bpm": 100, "mood": "happy 欢快"}}

用户："做一首忧伤曲子"
SUGGEST:忧伤情绪适合慢板 + 弦乐/钢琴。\\n我建议：古典风格 + 小提琴 + 70BPM，您觉得呢？
{{"mood": "sad 悲伤", "style": "classical 古典", "instrument": "violin 小提琴", "bpm": 70}}

用户："来个吉他曲"
SUGGEST:吉他适合多种风格。\\n我建议：民谣 + 吉他 + 90BPM + 平静情绪，您觉得呢？
{{"instrument": "guitar 吉他", "style": "folk 民谣", "bpm": 90, "mood": "calm 平静"}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【模式C：直接提取】（3个及以上明确参数）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
触发：用户说"乡村忧伤15秒吉他""爵士欢快30秒钢琴120BPM"
输出格式：
只输出JSON，不要解释

示例：
用户："写一首15秒的吉他忧伤乡村曲子"
{{"duration": 15, "style": "country 乡村", "mood": "sad 悲伤", "instrument": "guitar 吉他"}}

【重要规则】
- 模式B中，用户没指定的参数必须根据专业建议库推荐具体值，不能用 auto！
- 如果用户需求超出模型能力（如要人声、要长曲），在建议中告知限制
- JSON 值必须在可选范围内，超出范围选最近似的合法值
- ★ 动作词前置检查：用户消息没有动作词（做/写/画/生成/来个等）时，风格/情绪/乐器词不计入明确参数，必须走模式A反问意图
- ★ 模式A反问时，如果用户没有动作词，必须在反问中包含意图确认（"您是想做音乐吗？如果不是，告诉我您想做什么"）
- 现在请判断动作词和明确参数，选择模式A/B/C输出："""


    def _extract_params_from_message(self, user_message: str, schema: dict) -> dict:
        """★ 2026-07-04: 后端二次校验 — 从用户消息中直接提取明确参数
        
        当 LLM 误判为"模糊"（返回 ADVISE）时，后端再检查一遍用户消息
        是否其实包含明确的风落/情绪/乐器/时长等参数。
        如果有 → 返回提取的参数 dict（覆盖 ADVISE）
        如果没有 → 返回 {}（确认确实模糊）
        """
        try:
            params = {}
            msg_lower = user_message.lower()
            param_defs = schema.get("params", {})
            
            for pname, pdef in param_defs.items():
                if pdef.get("type") == "select":
                    options = pdef.get("options", [])
                    for opt in options:
                        # 取 option 的英文部分和中文部分分别匹配
                        parts = opt.split()
                        for part in parts:
                            if part and len(part) >= 2 and part in msg_lower:
                                params[pname] = opt
                                break
                        if pname in params:
                            break
                elif pdef.get("type") == "range":
                    # 提取数字（如"15秒""30秒""120BPM"）
                    import re
                    pattern = pdef.get("extract_pattern", "")
                    if not pattern:
                        # 通用模式：数字+单位
                        unit = ""
                        if "时长" in pdef.get("label", "") or "duration" in pname:
                            unit = "秒"
                        elif "bpm" in pname.lower() or "BPM" in pdef.get("label", ""):
                            unit = "(BPM|bpm|拍)"
                        if unit:
                            m = re.search(rf'(\d+)\s*{unit}', user_message)
                            if m:
                                params[pname] = int(m.group(1))
            
            return params if params else {}
        except Exception as e:
            logger.debug(f"[Engine] 二次校验提取失败: {e}")
            return {}

    def _check_missing_params(self, params: dict, schema: dict, user_message: str) -> list:
        """★ 2026-07-04: 检查关键参数是否缺失，返回反问问题列表
        
        返回 [] 表示参数完整，不需要反问
        返回 ["问题1", "问题2"] 表示需要反问这些问题
        """
        questions = []
        param_defs = schema.get("params", {})
        model_type = schema.get("type", "")
        
        # 音乐生成：style + mood 都是 auto → 反问
        if model_type == "music_generation":
            style_val = str(params.get("style", "auto"))
            mood_val = str(params.get("mood", "auto"))
            if style_val == "auto" and mood_val == "auto":
                style_opts = param_defs.get("style", {}).get("options", [])
                mood_opts = param_defs.get("mood", {}).get("options", [])
                style_str = "/".join(o.split()[0] for o in style_opts[:6])
                mood_str = "/".join(o.split()[0] for o in mood_opts[:6])
                questions.append(f"想要什么风格和情绪？（风格: {style_str}；情绪: {mood_str}）")
        
        # 图片生成：prompt 为空 → 反问
        elif model_type == "image_generation":
            prompt_val = str(params.get("prompt", "")).strip()
            if not prompt_val:
                questions.append("请描述您想要生成的画面内容（如：山水画、未来城市、人像...）")
        
        # 语音合成：text 为空 → 反问
        elif model_type == "speech_synthesis":
            text_val = str(params.get("text", "")).strip()
            if not text_val:
                questions.append("请输入要朗读的文本内容")
        
        # 代码生成：description 为空 → 反问
        elif model_type == "code_generation":
            desc_val = str(params.get("description", "")).strip()
            if not desc_val:
                questions.append("请描述您需要生成什么功能的代码")
        
        # 翻译：text 为空 → 反问
        elif model_type == "translation":
            text_val = str(params.get("text", "")).strip()
            if not text_val:
                questions.append("请输入要翻译的文本内容")
        
        # 数据分析：data_source 为空 → 反问
        elif model_type == "data_analysis":
            ds_val = str(params.get("data_source", "")).strip()
            if not ds_val:
                questions.append("请提供数据（粘贴 CSV/JSON 或描述数据来源）")
        
        return questions

    def _check_github_url(self, user_message):
        """
        检测用户消息中的 GitHub URL，自动下载并导入知识库
        Returns: 增强上下文字符串（空字符串表示没有 GitHub URL 或下载失败）
        """
        try:
            from src.core.github_downloader import detect_github_urls, download_and_import_github

            urls = detect_github_urls(user_message)
            if not urls:
                return ""

            # 下载指令关键词
            download_keywords = ['下载', '下载这个', '帮我下载', 'download', '拉取', '克隆', 'clone',
                                 '导入', 'import', '加到知识库']
            has_download_intent = any(kw in user_message.lower() for kw in download_keywords)

            # 如果只有 URL 但没有明确的下载意图，仍然处理（用户贴了链接就是想用）
            is_explicit = has_download_intent or (
                any(u['raw_url'] in user_message for u in urls)
            )

            if not is_explicit:
                return ""

            context_parts = []

            for url_info in urls[:3]:  # 最多处理 3 个 URL
                logger.debug(f"[Engine] 检测到 GitHub URL: {url_info['url']}")

                try:
                    # 同步下载（最多 60 秒）
                    import threading
                    result = [None]
                    error = [None]

                    def _do_download():
                        try:
                            result[0] = download_and_import_github(url_info['url'])
                        except Exception as e:
                            error[0] = str(e)

                    t = threading.Thread(target=_do_download, daemon=True)
                    t.start()
                    t.join(timeout=60)

                    if t.is_alive():
                        # 超时：排队到后台队列
                        logger.debug(f"[Engine] GitHub 下载超时，排队到后台: {url_info['url']}")
                        try:
                            from src.core.github_downloader import queue_github_download
                            queue_github_download(url_info['url'], self.conversation_id)
                        except Exception as _ge:
                            logger.debug(f"[Engine] GitHub下载后台排队失败: {_ge}")
                        context_parts.append(
                            f"[GitHub-{url_info['full_name']}]: 仓库较大，已在后台下载中。"
                            f"下载完成后文件会自动导入知识库。可以稍后问我关于这个仓库的问题。"
                        )
                        continue

                    if error[0]:
                        logger.error(f"[Engine] GitHub 下载出错: {error[0]}")
                        context_parts.append(
                            f"[GitHub-{url_info['full_name']}]: 下载失败 - {error[0]}"
                        )
                        continue

                    r = result[0]
                    if r and r.get('success'):
                        imported = r.get('imported', 0)
                        skipped = r.get('skipped', 0)
                        warnings = r.get('warnings', [])
                        files = r.get('files', [])
                        file_list = '\n'.join(f"  - {f['file']}" for f in files[:20])

                        context_parts.append(
                            f"[GitHub-{r['repo']}]: ✅ 下载成功，{imported} 个文件已导入知识库"
                            f"（跳过 {skipped} 个非文本/大文件）。\n"
                            f"已导入文件：\n{file_list}"
                        )
                        if warnings:
                            context_parts.append(f"⚠️ 安全提醒：{'; '.join(warnings[:3])}")
                        logger.debug(f"[Engine] GitHub 下载成功: {r['repo']}, {imported} 文件")
                    else:
                        msg = r.get('message', '未知错误') if r else '未知错误'
                        context_parts.append(
                            f"[GitHub-{url_info['full_name']}]: 下载失败 - {msg}"
                        )

                except Exception as e:
                    logger.debug(f"[Engine] GitHub 下载异常: {e}")
                    context_parts.append(
                        f"[GitHub-{url_info['full_name']}]: 处理异常 - {e}"
                    )

            if context_parts:
                return "\n\n## GitHub 仓库下载结果\n" + "\n\n".join(context_parts)

            return ""

        except ImportError:
            logger.info("[Engine] github_downloader 模块未加载")
            return ""
        except Exception as e:
            logger.debug(f"[Engine] _check_github_url 异常: {e}")
            return ""

    def _route_model_for_query(self, query_mode):
        """
        ★ 2026-08-25 P1: 用户选择大于一切——严格执行，绝不自动切换。
        
        Returns: (use_external: bool, model_label: str, model_id: str, error_msg: str|None)
        - current_model=ollama   → 双引擎探测（Ollama→GGUF），就绪走本地
        - current_model=外脑X     → 只用 X，不切本地不切其他外脑
        - current_model=None      → 提示选择模型
        - 不可用                  → error_msg（四段式：选择→状态→原因→指引），调用方展示
        """
        if query_mode in ("direct", "auto"):
            try:
                from src.core.multi_model_manager import get_multi_model_manager, is_local_model
                mm = get_multi_model_manager()
                if mm is None:
                    return False, "本地模型", "ollama", None

                # ── 情况1：外脑模式下未选择外脑模型 ──
                # ★ 2026-08-26: 外脑下拉已移除"本地模型"选项——外脑(direct)模式下
                #    current_model 未选/ollama 一律明确报错，绝不默默回落本地。
                #    本地模式的唯一入口是对话区的 🏠本地 模式按钮（kb_only）。
                # ★ #1288（2026-10-29）分槽记忆补「读」半边：撞本地模型/未选时先自动换回
                #    cloud 槽（用户上次选的外脑，非代选），换不回才按原细分报错——
                #    「外脑模式选了本地 HarvenEye」报错彻底退役（10-29 实测实锤现场）
                # ★ #1309（2026-10-29）本地判定走唯一真家 is_local_model，禁止手抄清单
                if is_local_model(mm.current_model):
                    _old = mm.current_model
                    _restored = mm.restore_mode_slot('cloud')
                    if _restored:
                        logger.info(f"[Engine] #1288 外脑模式自动换回 cloud 槽: "
                                    f"{_old!r} → {_restored}")
                        # ★ #1310（2026-10-29 1c 换槽知情，定版拍板）：自动换槽必须让用户看见——
                        #   旧版只写日志，用户看到「选的本地却冒出 GLM」全程懵（咖啡机事故实锤）。
                        #   提示存 self._slot_switch_notice，两处调用方（send/看图）带出。
                        try:
                            _plabel = (mm._get_all_providers().get(_restored, {}) or {}).get('name', _restored)
                        except Exception:
                            _plabel = _restored
                        self._slot_switch_notice = (
                            f"⚠️ 自动换槽：外脑模式下检测到你选的是本地模型（{_old or '未选'}），"
                            f"已自动换回你上次选的外脑「{_plabel}」。"
                            f"想回本地请点对话区的 🏠本地 按钮。"
                        )
                    else:
                        # 槽位也空/脏 → 保持原细分报错（四段式指引）
                        if mm.current_model == "harveneye":
                            # ★ D2 #1565：对外不再叫 HarvenEye，改「本地视觉引擎（图片理解组合）」
                            return True, "本地视觉引擎（图片理解组合）", "harveneye", (
                                "你处于 🤖外脑模式 → 但当前选的模型是本地的视觉引擎（图片理解组合） → 无法用外脑推理 → "
                                "请到 设置→模型管理 选一个外脑（如 智谱AI），"
                                "或点击对话区的 🏠本地 按钮切换回本地模式用视觉引擎"
                            )
                        logger.warning("[Engine] 外脑模式但未选择外脑模型（current_model=%r）", mm.current_model)
                        return False, "未选择外脑模型", "ollama", (
                            "你处于 🤖外脑模式 → 还没有选择外脑模型 → 无法推理 → "
                            "请到 设置→模型管理 在外脑下拉选择一个模型（如 智谱AI），"
                            "或点击对话区的 🏠本地 按钮切换回本地模式"
                        )

                # ── 情况2：用户选了外脑 X → 严格只用 X ──
                provider_id = mm.current_model
                all_providers = mm._get_all_providers()
                provider_info = all_providers.get(provider_id, {})
                model_cfg = mm.config.get('models', {}).get(provider_id, {})
                needs_key = provider_info.get('needs_key', False)
                _pname = provider_info.get('name', provider_id)

                # Key 三层只读回退（不改用户启用状态）
                api_key = model_cfg.get('api_key', '')
                if not api_key:
                    api_key = mm.config.get('model_keys', {}).get(provider_id, '')
                if not api_key:
                    try:
                        cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
                        api_key = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {})\
                            .get('model_keys', {}).get(provider_id, '')
                    except Exception:
                        api_key = ''
                if needs_key and not api_key:
                    return True, _pname, provider_id, (
                        f"你选的是 {_pname} → 未配置 API Key → 无法调用 → "
                        "请到 设置→模型管理 填入 Key，或手动切换其他模型"
                    )
                if not model_cfg.get('enabled'):
                    return True, _pname, provider_id, (
                        f"你选的是 {_pname} → 模型未启用 → 无法调用 → "
                        "请到 设置→模型管理 开启此模型，或切换其他模型"
                    )
                logger.info(f"[Engine] 用户选定模型: {provider_id}（严格执行，不自动切换）")
                return True, _pname, provider_id, None

            except Exception as e:
                logger.warning(f"[Engine] 路由异常回退本地: {type(e).__name__}: {e}")
                return False, "本地模型", "ollama", None

        # kb_only 等其他模式 → 本地
        return False, "本地模型", "ollama", None

    # ═══ 2026-09-14 V1：协作流水线（对话站 + 执行站） ═══

    def _pipeline_transcripts_text(self, transcripts: list) -> str:
        """把前面各站产出整理成文本，喂给后续站"""
        lines = []
        for t in transcripts:
            if t['kind'] == 'text':
                lines.append(f"- [{t['name']}] {t['content'][:600]}")
            else:
                line = f"- [{t['name']}] 已生成文件: {t.get('file') or '(无路径)'}"
                if t.get('output'):
                    line += f"；说明: {t['output'][:200]}"
                lines.append(line)
        return '\n'.join(lines) if lines else ''

    def _pipeline_call_local(self, system_prompt: str, content: str) -> str:
        """本地当前选中模型非流式调用（流水线对话站）
        ★ #1517（2026-11-14）引擎名≠模型名根治：协作本地站旧版把 self.model.model 直接当
          Ollama 模型名打 11434——用户切 HarvenEye 引擎后拿「harveneye」去查 Ollama 必 404，
          流水线第 2 站必挂（实测复现：❌ 第 2 站「🏠 本地 harveneye」调用失败）。
          引擎名先解析真身（优先级与 #1291 文字分流同源：钉选通道 → understand 槽出厂通道），
          非引擎名走原路零变化。"""
        local_name = getattr(self.model, 'model', '') or ''
        if not local_name:
            logger.warning("[Pipeline] 本地站: 当前无本地模型")
            return ""
        if local_name == 'harveneye':
            # ★ 2026-11-15：真身解析逻辑抽到 _pipeline_call_harveneye（与独立 HarvenEye 站共用）
            return self._pipeline_call_harveneye(system_prompt, content)
        return self._pipeline_call_ollama(local_name, system_prompt, content)

    def _pipeline_call_harveneye(self, system_prompt: str, content: str) -> str:
        """HarvenEye 视觉大脑纯文本调用（流水线 HarvenEye 站 / 本地站选中 Eye 时共用）
        ★ 2026-11-15（老谭定版：含通用大脑的 HarvenEye = 本地通用模型能力升级版，
        进协作流水线本地站下拉，名字就叫 HarvenEye）：
        钉选通道优先 → understand 槽出厂通道，三 provider 分流转调（原 #1517 逻辑抽出）。"""
        # 钉选通道优先（用户在模型管理钉的视觉大脑）
        ch = {}
        try:
            from src.core.vision_link import resolve_pinned_channel as _rpc_1517
            ch = _rpc_1517() or {}
        except Exception as _e:
            logger.debug(f"[Pipeline] 钉选通道解析失败: {_e}")
        if ch.get('provider') == 'ollama' and ch.get('model'):
            return self._pipeline_call_ollama(ch['model'], system_prompt, content)
        if ch.get('provider') == 'llama_cpp' and ch.get('gguf'):
            return self._pipeline_call_gguf_path(ch['gguf'], system_prompt, content)
        if ch.get('provider') == 'api' and ch.get('api_model'):
            return self._pipeline_call_external(ch['api_model'], system_prompt, content)
        # 无钉选 → understand 槽出厂通道（config autowire 后通常已是 llama_cpp）
        try:
            from src.vision import config as _vcfg_1517
            _u = _vcfg_1517.ENGINES.get('understand', {})
            if _u.get('provider') == 'ollama' and _u.get('model'):
                return self._pipeline_call_ollama(_u['model'], system_prompt, content)
            if _u.get('provider') == 'llama_cpp' and _u.get('gguf'):
                return self._pipeline_call_gguf_path(_u['gguf'], system_prompt, content)
        except Exception as _e:
            logger.debug(f"[Pipeline] understand 槽兜底解析失败: {_e}")
        logger.warning("[Pipeline] HarvenEye 站: 解析不出可用的理解槽大脑（无钉选且出厂通道不可用）")
        return ""

    def _pipeline_call_gguf_path(self, gguf_path: str, system_prompt: str, content: str) -> str:
        """GGUF 直路径调用（#1517：llama_cpp 通道的视觉大脑没有 _get_all_models 户口，
        不能走 _pipeline_call_gguf 的 id 查表，直接按路径加载；带实例缓存）"""
        try:
            import os as _os
            from src.core.llama_engine import LlamaEngine
            cache_attr = f"_pipe_llama_path_{hash(gguf_path) & 0xffffff}"
            eng = getattr(self, cache_attr, None)
            if eng is None or not getattr(eng, 'is_loaded', False):
                eng = LlamaEngine(_os.path.expanduser(gguf_path))
                if not eng.is_loaded:
                    logger.warning(f"[Pipeline] GGUF 加载失败: {gguf_path}")
                    return ""
                setattr(self, cache_attr, eng)
            return (eng.generate(prompt=f"User: {content}\nAssistant:",
                                 system_prompt=system_prompt,
                                 temperature=0.6, max_tokens=1024) or "").strip()
        except Exception as e:
            logger.error(f"[Pipeline] GGUF 直路径调用失败: {e}")
            return ""

    def _pipeline_call_ollama(self, model_name: str, system_prompt: str, content: str) -> str:
        """指定 Ollama 模型非流式调用（★ 2026-09-16 本地站可选任意已装模型）"""
        try:
            import requests as _rq
            r = _rq.post('http://localhost:11434/api/generate',
                         json={'model': model_name, 'system': system_prompt,
                               'prompt': content, 'stream': False},
                         timeout=600)
            if r.status_code != 200:
                logger.warning(f"[Pipeline] 本地站 HTTP {r.status_code}")
                return ""
            return (r.json() or {}).get('response', '').strip()
        except Exception as e:
            logger.error(f"[Pipeline] 本地站调用失败: {e}")
            return ""

    def _pipeline_call_gguf(self, gguf_id: str, system_prompt: str, content: str) -> str:
        """GGUF 垂直对话模型调用（llama-cpp 引擎，不经过 Ollama）"""
        try:
            import os as _os
            from src.core.vertical_api import _get_all_models
            m = next((x for x in _get_all_models() if x.get('id') == gguf_id), None)
            if not m or not m.get('model_path'):
                logger.warning(f"[Pipeline] GGUF {gguf_id} 不存在或无路径")
                return ""
            from src.core.llama_engine import LlamaEngine
            cache_attr = f"_pipe_llama_{gguf_id}"
            eng = getattr(self, cache_attr, None)
            if eng is None or not getattr(eng, 'is_loaded', False):
                eng = LlamaEngine(_os.path.expanduser(m['model_path']))
                if not eng.is_loaded:
                    logger.warning(f"[Pipeline] GGUF {gguf_id} 加载失败")
                    return ""
                setattr(self, cache_attr, eng)
            return (eng.generate(prompt=f"User: {content}\nAssistant:",
                                 system_prompt=system_prompt,
                                 temperature=0.6, max_tokens=1024) or "").strip()
        except Exception as e:
            logger.error(f"[Pipeline] GGUF 调用失败: {e}")
            return ""

    def _pipeline_call_external(self, provider: str, system_prompt: str, content: str) -> str:
        """外脑非流式调用（流水线对话站）— 复用 multi_model_manager 换模型技巧"""
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            providers = mm._get_all_providers() or {}
            info = providers.get(provider, {})
            model_cfg = mm.config.get('models', {}).get(provider, {})
            if info.get('needs_key', False) and not model_cfg.get('api_key'):
                global_keys = mm.config.get('model_keys', {})
                if global_keys.get(provider):
                    model_cfg['api_key'] = global_keys[provider]
                if not model_cfg.get('api_key'):
                    logger.warning(f"[Pipeline] 外脑 {provider} 无 API Key，跳过")
                    return ""
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ]
            # ★ #1265（2026-10-29）：模型显式传参，不再临时换血 mm.current_model（竞态根治）
            result = ""
            for chunk in mm.generate_response(messages, stream=False, model=provider):
                if isinstance(chunk, dict):
                    if chunk.get('type') == 'text':
                        result += chunk.get('content', '')
                    elif chunk.get('type') == 'error':
                        logger.warning(f"[Pipeline] 外脑 {provider} 错误: {chunk.get('content', '')}")
                        return ""
                elif isinstance(chunk, str):
                    result += chunk
            return self._clean_collab_response(result).strip()
        except Exception as e:
            logger.error(f"[Pipeline] 外脑 {provider} 调用失败: {e}")
            return ""

    def _pipeline_call_chat(self, participant: str, system_prompt: str, content: str) -> str:
        """对话站统一入口：local/本地模型 → Ollama 或 llama-cpp；其他 → 外脑 API
        ★ 2026-11-15：harveneye 独立站（老谭定版：含通用大脑的 HarvenEye 进本地站下拉）"""
        if participant == 'local':
            return self._pipeline_call_local(system_prompt, content)
        if participant == 'harveneye':
            return self._pipeline_call_harveneye(system_prompt, content)
        if participant.startswith('ollama:'):
            return self._pipeline_call_ollama(participant[7:], system_prompt, content)
        if participant.startswith('gguf:'):
            return self._pipeline_call_gguf(participant[5:], system_prompt, content)
        return self._pipeline_call_external(participant, system_prompt, content)

    def _pipeline_participant_name(self, participant: str) -> str:
        if participant == 'local':
            _lm = getattr(self.model, 'model', '') or '模型'
        # ★ #1517：引擎名显示真身（协作站名不再出现「🏠 本地 harveneye」这种引擎牌）
        #   ★ D2 #1565：对外统一叫「本地视觉引擎」，不再露 HarvenEye 产品名
        if _lm == 'harveneye':
            return "🏠 本地视觉引擎"
        return f"🏠 本地 {_lm}"
        if participant == 'harveneye':
            # ★ 2026-11-15：独立视觉引擎站（原 HarvenEye 站，D2 #1565 改对外名）
            return "🏠 本地视觉引擎"
            return f"🏠 本地 {participant[7:]}"
        if participant.startswith('gguf:'):
            try:
                from src.core.vertical_api import _get_all_models
                m = next((x for x in _get_all_models() if x.get('id') == participant[5:]), None)
                return f"🏠 本地 {m.get('name', participant[5:]) if m else participant[5:]}"
            except Exception:
                return f"🏠 本地 {participant[5:]}"
        try:
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            info = (mm._get_all_providers() or {}).get(participant, {})
            return f"☁️ {info.get('name', participant)}"
        except Exception:
            return f"☁️ {participant}"

    def _pipeline_get_tool_model(self, tool_id: str):
        """执行站：按 ID 找可执行的垂直工具模型（画图/作曲/语音类）"""
        try:
            from src.core.vertical_api import _get_all_models
            return next((m for m in _get_all_models()
                         if m.get('id') == tool_id and m.get('engine') == 'tool_executor'), None)
        except Exception as e:
            logger.error(f"[Pipeline] 读取垂直工具模型失败: {e}")
            return None

    def _pipeline_fill_params(self, model: dict, upstream_text: str) -> dict:
        """执行站自动填表：LLM 把上游文字抽成表单参数 JSON，失败回退默认值"""
        schema = {}
        defaults = {}
        for k, p in (model.get('params') or {}).items():
            schema[k] = {'type': p.get('type'), 'label': p.get('label'),
                         'options': p.get('options'), 'min': p.get('min'), 'max': p.get('max')}
            defaults[k] = p.get('default', '')
        raw = ""
        try:
            sys_p = ("你是参数填充器。根据上下文为目标模型生成表单参数。"
                     "只输出一个 JSON 对象，禁止解释、禁止 markdown 代码块标记。")
            content = (
                f"目标模型：{model.get('name')}（{model.get('description', '')}）\n"
                f"表单定义：{json.dumps(schema, ensure_ascii=False)}\n\n"
                f"[上下文]\n{upstream_text[:3000]}\n\n只输出参数 JSON。"
            )
            raw = self._pipeline_call_local(sys_p, content)
        except Exception as e:
            logger.warning(f"[Pipeline] 参数抽取调用失败: {e}")
        extracted = {}
        if raw:
            try:
                import re as _re
                m = _re.search(r'\{[\s\S]*\}', raw)
                if m:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, dict):
                        extracted = parsed
            except Exception as e:
                logger.warning(f"[Pipeline] 参数 JSON 解析失败: {e}")
        # 合并：默认值打底，抽取值做选项/范围矫正
        params = dict(defaults)
        for k, v in extracted.items():
            if k not in params:
                continue
            p_def = (model.get('params') or {}).get(k, {})
            opts = p_def.get('options')
            if opts:
                sv = str(v)
                if sv in opts:
                    params[k] = sv
                else:
                    hit = next((o for o in opts if o.startswith(sv) or sv in o), None)
                    if hit:
                        params[k] = hit
            elif p_def.get('type') == 'range':
                try:
                    fv = float(v)
                    params[k] = max(p_def.get('min', 0), min(p_def.get('max', 100), fv))
                except (TypeError, ValueError):
                    pass
            else:
                params[k] = str(v)
        # 兜底：prompt 类字段用上游原文（抽取失败也有合理值）
        if 'prompt' in params and not str(params.get('prompt') or '').strip('-'):
            params['prompt'] = (upstream_text or '')[:800]
        return params

    @staticmethod
    def _split_pipeline_segments(message: str):
        """★ 2026-09-15 V1.5: / 分段接力（定版方案）— 用 / 或 ／ 把一条消息切成多站命令。
        段序=站序：段1给站1、段2给站2…每段是该站的明确指令，替代 AI 自行分工。
        ≥2 段才算分段消息；返回 None 表示普通消息（不打 / 完全不受影响）。
        反误伤规则（实测 24/7、D:/music、网址都踩过）：
          每段必须像"一句命令"——含≥2个中文字符；或含中英混合≥3字符；
          盘符结尾（看看 D:）/ 网址被切开的一律不算分段。"""

        def _looks_like_command(p: str) -> bool:
            if len(p) < 2:
                return False
            cjk = len(_re.findall(r'[\u4e00-\u9fff]', p))
            if cjk >= 2:
                return True
            letters = len(_re.findall(r'[A-Za-z]', p))
            return (cjk + letters) >= 3 and len(p) >= 3

        import re as _re
        if not message or ('/' not in message and '／' not in message):
            return None
        # ★ 2026-11-15 (#1522-A 老谭实锤修复): 路径 token 保护 — 斜杠两侧全是路径字符（字母数字_.-）
        #   的连续串整体保护不拆：/data/media/x.wav、网址路径、24/7 一网打尽。
        #   背景：消息里带 /data/media/ 音频路径时每个 / 都被当命令分隔符 → 一条消息被拆成 12 段。
        #   中文段不受影响（斜杠后是中文 → 不在字符类 → 照常拆）。
        #   盘符串（D:/music/歌.mp3）单独保护：冒号截断字符类，用 \S* 连吃整段非空白。
        _prot = _re.sub(r'\b[A-Za-z]:[/／]\S*',
                        lambda _m: _m.group(0).replace('/', '\x00').replace('／', '\x01'), message)
        _prot = _re.sub(r'[A-Za-z0-9_\-.]*(?:[/／][A-Za-z0-9_\-.]+)+',
                        lambda _m: _m.group(0).replace('/', '\x00').replace('／', '\x01'), _prot)
        parts = _re.split(r'[/／]', _prot)
        segs = [p.strip().replace('\x00', '/').replace('\x01', '／') for p in parts if p.strip()]
        if len(segs) < 2:
            return None
        for p in segs:
            if _re.search(r'[A-Za-z]:\s*$', p):      # "看看 D:" → 路径被切
                return None
            if '://' in message and p.startswith(('http://', 'https://', 'ftp://')):
                return None                           # 网址被切
            if not _looks_like_command(p):
                return None                           # 24 / 7 / 2024 等纯数字段
        return segs

    @staticmethod
    def _try_playback_route(message: str):
        """★ 2026-11-15 (#1522-B): 播放意图本地快车道 — 零 token 毫秒级响应。
        背景：「播放这首歌曲」被 / 分段流水线劫持 → ACE-Step 重新生成一遍歌（老谭实锤）。
        命中 → 返回带音频路径的响应文本（前端 embedChatAudio 自动渲染成播放器）；
        非播放意图 → 返回 None 继续正常流程。必须在分段判定之前调用。"""
        import re as _re2
        msg = (message or '').strip()
        if not msg or len(msg) > 40:
            return None
        # ① 带具体音频文件名：播放/打开 xxx.wav|mp3…
        _fm = _re2.search(r'([\w\-\u4e00-\u9fa5]*\.(?:wav|mp3|m4a|ogg|flac))', msg, _re2.I)
        if _fm and _re2.search(r'播放|打开|放', msg):
            _picked = ConversationEngine._pick_media_file(_fm.group(1))
            if _picked:
                return f"🎵 已找到你要的歌，点下方播放：\n`{_picked}`"
            return None  # 指定的文件不存在 → 不拦，交给模型解释
        # ② 纯播放意图短句（严格 fullmatch 白名单，防误伤「播放器怎么装」这类问句）
        if not _re2.fullmatch(r'[你看]?[给我]?(?:播放?|放|听听?)(?:一下|这?首|这首歌|这首曲子)?(?:歌|歌曲|音乐|曲子)?[吧呗啊呀~\s]*', msg):
            return None
        if _re2.search(r'生成|写|做|编|创作|画|歌词', msg):
            return None  # 生成类意图放行
        _picked = ConversationEngine._pick_media_file(None)
        if _picked:
            return f"🎵 已找到最新生成的歌，点下方播放：\n`{_picked}`\n\n（想换一首？去协作流水线重新生成即可）"
        return "🎵 音库里还没有歌——先在协作流水线生成一首，再喊我播放。"

    @staticmethod
    def _pick_media_file(keyword):
        """从 data/media 挑音频：keyword=None 取最新（同名 mp3 优先）；keyword=文件名模糊匹配。返回 /data/media/ 相对路径或 None。"""
        try:
            from pathlib import Path as _Path
            media = _Path(__file__).parent.parent.parent / 'data' / 'media'
            if not media.exists():
                return None
            exts = ('.wav', '.mp3', '.m4a', '.ogg', '.flac')
            cands = [p for p in media.iterdir() if p.suffix.lower() in exts and p.is_file()]
            if not cands:
                return None
            if keyword:
                kw = keyword.rsplit('.', 1)[0]
                cands = [p for p in cands if kw in p.stem] or \
                        [p for p in cands if keyword.lower() in p.name.lower()]
                if not cands:
                    return None
                cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            else:
                cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            top = cands[0]
            # 同名 mp3 优先（体积小加载快；B 方案转码产物）
            for p in cands[:5]:
                if p.stem == top.stem and p.suffix.lower() == '.mp3':
                    top = p
                    break
            return f"/data/media/{top.name}"
        except Exception:
            return None

    def _run_collab_pipeline(self, cm, user_message: str):
        """
        V1 协作流水线：对话站(外脑/本地) + 执行站(垂直工具) 编排执行。
        生成器：yield 进度/文本事件；return {'completed': bool, 'error': str}
        用户消息已在 send() 入口入库，这里只存最终回答。
        """
        try:
            from src.utils.config import get_config
            steps = (get_config() or {}).get('collab_pipeline') or []
            if not steps:
                # 未配置 → 默认经典两步（等价旧行为：外脑分析 → 本地作答）
                steps = [{'type': 'chat', 'participant': cm.foreign_model},
                         {'type': 'chat', 'participant': 'local'}]
            steps = [s for s in steps if isinstance(s, dict) and s.get('type') in ('chat', 'tool')]

            # ★ 2026-09-15 V1.5: / 分段接力 — 段序=站序（定版方案：用户手动挡）
            segs = getattr(self, '_pipeline_segments', None)
            _notes = []
            if segs:
                _default_pid = cm.foreign_model or 'local'
                # ★ 2026-11-15 (#1522-C 老谭实锤修复): 补站提示只出一条汇总（原 while 每补一站
                #   append 一条相同提示 → 12 段消息刷屏 9 条 ℹ️）
                if len(steps) < len(segs):
                    _add_n = len(segs) - len(steps)
                    _base_n = len(steps)
                    steps.extend([{'type': 'chat', 'participant': _default_pid}] * _add_n)
                    _notes.append(f"你打了 {len(segs)} 段命令但流水线只有 {_base_n} 站 → 已自动补 {_add_n} 个对话站（{_default_pid}），可去「🦞 协作」面板换成别的模型")
                if len(steps) > len(segs):
                    _notes.append(f"段少站多 → 第 {len(segs)+1} 站起共 {len(steps)-len(segs)} 站已跳过")
                    steps = steps[:len(segs)]
                if steps[-1].get('type') != 'chat':
                    steps.append({'type': 'chat', 'participant': _default_pid})
                    _notes.append("最后一站是生成站 → 已自动补一个整合作答站")

            if len(steps) < 2 or steps[-1].get('type') != 'chat':
                yield ("error", "❌ 协作流水线配置无效（至少 2 站且最后一站须是对话站）。请点「🦞 协作」重新编排保存。")
                return {'completed': False, 'error': 'invalid pipeline'}

            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()

            n = len(steps)
            chain_names = []
            transcripts = []

            # ★ 分段接力回显：让用户一眼看到"段→站"对应关系
            if segs:
                yield ("thinking", f"⛓️ 分段接力 · {len(segs)} 段命令 ↔ {n} 站：按顺序开跑")
                for _nt in _notes:
                    yield ("text", f"ℹ️ {_nt}\n\n")

            for i, st in enumerate(steps):
                is_last = (i == n - 1)
                if st['type'] == 'chat':
                    pid = st.get('participant', 'local')
                    pname = self._pipeline_participant_name(pid)
                    yield ("pipeline_step", {"index": i, "total": n, "kind": "chat", "name": pname,
                                             "status": "running",
                                             "message": f"⛓️ 第 {i+1}/{n} 站 · {pname} 思考中…"})
                    upstream = self._pipeline_transcripts_text(transcripts)
                    if is_last:
                        sys_p = ("你是哈文协作流水线的最终整合站。请基于本站任务和前面各站的产出，"
                                 "用面向普通人的大白话给出完整最终回答；如果前面生成了文件（图片/音乐/语音），"
                                 "要逐一介绍成果。不要出现'流水线/第几站/站点'这类技术词。")
                    else:
                        sys_p = ("你是哈文协作流水线中的分析站。只输出对后续站点有用的要点、方案或参数建议，"
                                 "简洁、结构化，不要寒暄，不要重复用户问题。")
                    # ★ 2026-09-15 V1.5: 分段接力 — 本站任务 = 用户打的第 i 段命令（没打段则用整条消息，兼容自动补站）
                    _seg = segs[i] if segs and i < len(segs) else None
                    if _seg:
                        content = f"【本站任务】{_seg}"
                        if upstream:
                            content += f"\n\n[前面各站产出]\n{upstream}"
                    else:
                        content = user_message if not upstream else f"{user_message}\n\n[前面各站产出]\n{upstream}"
                    text = self._pipeline_call_chat(pid, sys_p, content)
                    if not text:
                        yield ("error", f"❌ 第 {i+1} 站「{pname}」调用失败，流水线中止。\n\n"
                                        f"请检查该参与者的 API Key / 服务状态，或在「🦞 协作」里调整编排。")
                        return {'completed': False, 'error': f'step {i+1} chat failed'}
                    chain_names.append(pname)
                    transcripts.append({'kind': 'text', 'name': pname, 'content': text})
                    yield ("pipeline_step", {"index": i, "total": n, "kind": "chat", "name": pname,
                                             "status": "done", "summary": text[:120]})
                else:
                    model = self._pipeline_get_tool_model(st.get('model', ''))
                    if not model:
                        yield ("error", f"❌ 第 {i+1} 站的垂直工具「{st.get('model')}」不可用（需要画图/作曲/语音类可执行模型）。")
                        return {'completed': False, 'error': f'step {i+1} tool missing'}
                    tname = f"{model.get('icon', '🧩')} {model.get('name', model.get('id'))}"
                    # ★ 2026-09-16 用户反馈#3：生成站要有明确的"在生成什么+要等多久"提示
                    _kind_cn = {'music_generation': '音乐', 'image_generation': '图片',
                                'speech_synthesis': '语音'}.get(model.get('type'), '作品')
                    _est_cn = {'music_generation': '约2~8分钟', 'image_generation': '约20秒~2分钟',
                               'speech_synthesis': '约10~60秒'}.get(model.get('type'), '约1~5分钟')
                    yield ("pipeline_step", {"index": i, "total": n, "kind": "tool", "name": tname,
                                             "status": "running",
                                             "message": f"🎬 正在生成{_kind_cn} · {tname}（{_est_cn}，别关页面）…"})
                    # ★ 2026-09-15 V1.5: 分段接力 — 生成站的填表依据 = 用户该段命令 + 上游产出
                    _tool_upstream = self._pipeline_transcripts_text(transcripts)
                    if segs and i < len(segs):
                        _tool_upstream = f"【本站任务】{segs[i]}\n\n[上游产出]\n{_tool_upstream}"
                    params = self._pipeline_fill_params(model, _tool_upstream)
                    logger.info(f"[Pipeline] 执行站 {model.get('id')} 参数: {json.dumps(params, ensure_ascii=False)[:200]}")
                    import threading as _th
                    _box = {}

                    def _worker():
                        try:
                            from src.core.vertical_api import _execute_via_tool_executor
                            _box['r'] = _execute_via_tool_executor(model, params)
                        except Exception as ex:
                            _box['e'] = ex

                    w = _th.Thread(target=_worker, daemon=True)
                    w.start()
                    # ★ 2026-09-16 生成进度心跳：每30秒报一次，长任务不再像卡死（10分钟硬超时保留）
                    _elapsed = 0
                    while w.is_alive():
                        w.join(timeout=30)
                        if w.is_alive():
                            _elapsed += 30
                            if _elapsed >= 600:
                                yield ("error", f"❌ {tname} 执行超时（10分钟），流水线中止。")
                                return {'completed': False, 'error': 'tool timeout'}
                            yield ("thinking", f"🎬 {tname} 还在生成中…（已 {_elapsed // 60} 分 {_elapsed % 60} 秒，{_est_cn}）")
                    if _box.get('e'):
                        yield ("error", f"❌ {tname} 执行失败：{str(_box['e'])[:200]}")
                        return {'completed': False, 'error': 'tool error'}
                    r = _box.get('r') or {}
                    if r.get('error'):
                        yield ("error", f"❌ {tname} 执行失败：{str(r.get('error'))[:200]}")
                        return {'completed': False, 'error': 'tool error'}
                    furl = r.get('file', '')
                    chain_names.append(tname)
                    transcripts.append({'kind': 'file', 'name': tname, 'file': furl,
                                        'output': r.get('output', '')})
                    yield ("pipeline_step", {"index": i, "total": n, "kind": "tool", "name": tname,
                                             "status": "done", "file": furl,
                                             "summary": (r.get('output') or '')[:120]})

            final_text = next((t['content'] for t in reversed(transcripts) if t['kind'] == 'text'), '')
            if not final_text:
                yield ("error", "❌ 流水线没有产出最终回答。")
                return {'completed': False, 'error': 'no final text'}

            chain = ' → '.join(chain_names)
            self._reply_source = f"⛓️ {chain}"
            yield ("model_source", {"model": "collab", "label": f"⛓️ {chain}",
                                    "is_external": False, "mode": "collab"})
            yield ("source", f"⛓️ 协作流水线 · {chain}")
            for j in range(0, len(final_text), 120):
                yield ("text", final_text[j:j + 120])
            try:
                save_message(self.conversation_id, "assistant", final_text,
                             metadata={"source": f"⛓️ {chain}"})
            except Exception as se:
                logger.warning(f"[Pipeline] 回答入库失败: {se}")
            logger.info(f"[Pipeline] 完成: {chain} ({len(final_text)} 字)")
            return {'completed': True}
        except Exception as e:
            logger.error(f"[Pipeline] 流水线异常: {e}")
            return {'completed': False, 'error': str(e)}

    @staticmethod
    def _clean_collab_response(response_text: str) -> str:
        """
        清洗外脑回复，剥离幻觉 JSON tool_calls 块。
        
        外脑可能在回复末尾注入虚构的工具调用 JSON（如 python_expert），
        这些 JSON 块传给 Ollama 会污染上下文、降低回答质量。
        """
        import re as _re
        
        # 1. 移除 ```json ... ``` 代码块
        cleaned = _re.sub(
            r'```json\s*[\s\S]*?```',
            '',
            response_text
        )
        
        # 2. 移除 tool_calls JSON 对象（无论是否在代码块中）
        cleaned = _re.sub(
            r'\{\s*"tool_calls"\s*:\s*\[[\s\S]*?\]\s*\}',
            '',
            cleaned
        )
        
        # 3. 移除 action JSON 数组块（如 [{"action": "xxx", ...}]）
        cleaned = _re.sub(
            r'\[\s*\{[^}]*"action"\s*:[\s\S]*?\}\s*\]',
            '',
            cleaned
        )
        
        # 4. 移除孤立的 "首先，我调用 xxx 工具" 句式
        cleaned = _re.sub(
            r'^[首先，]*我调用\s+\w+\s+工具[来获取数据。]*\s*',
            '',
            cleaned
        )
        
        # 5. 移除残余的纯 JSON 行
        cleaned = _re.sub(r'^\s*[\{\[][\s\S]*?[\}\]]\s*$', '', cleaned, flags=_re.MULTILINE)
        
        # 6. 清理多余空行
        cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned)
        
        return cleaned.strip()

    def _resolve_query_mode(self, query_info, user_message=""):
        """
        三层知识库搜索策略（2026-06-17）：
        - 用户明确要求 → 搜
        - 正在做专项/有任务上下文 → 搜
        - 消息含工作关键词 → 搜
        - 纯闲聊 → 不搜
        """
        mode = self._query_mode

        # ★ #1242（2026-10-27）：知识库开关即时生效——每条消息刷新偏好
        #   （_load_preferences 幂等，顺带刷新 temperature/stream，开销毫秒级）
        try:
            self._load_preferences()
        except Exception:
            pass

        msg = user_message.lower()

        # 层级一：用户明确要求查资料
        kb_keywords = ["知识库", "文档", "资料", "查一下", "找一下", "搜索",
                       "文件", "合同", "报告", "记录", "笔记", "FAQ",
                       "知识", "信息", "有没有", "帮我查", "查找", "检索"]
        if any(kw in msg for kw in kb_keywords):
            return self._kb_search_enabled, False

        # 层级二：当前对话有专项培养/任务上下文（正在做正事）
        if self._specialization_prompt or self._has_active_task():
            return self._kb_search_enabled, False

        # 层级三：消息包含工作/技术关键词
        work_keywords = ["代码", "函数", "设计", "项目", "开发", "bug",
                         "修复", "优化", "重构", "部署", "配置", "接口",
                         "api", "数据库", "前端", "后端", "算法", "架构"]
        if any(kw in msg for kw in work_keywords):
            return self._kb_search_enabled, False

        # 默认不搜（纯闲聊）
        return False, False

    def _stream_three_stage(self, user_message):
        """
        流式查询（统一流程，支持三种 query_mode）

        P3: 闲聊 fast-path — 纯闲聊消息跳过 5 层检测直接到模型调用
        流程：分类 → [闲聊?] fast-path → [正常] 知识库 → 模型路由 → 回答

        ★ 2026-07-11: 快速命令拦截 — 用户说"下载/安装/删除XX模型"时直接执行
        """
        full_response = ""

        # ★ 2026-07-11: 快速命令拦截（不经过外脑，直接执行）
        quick_result = _try_quick_command(user_message)
        if quick_result:
            # ★ 2026-08-06: 快速命令也存消息到数据库（修复空对话问题）
            try:
                save_message(self.conversation_id, "user", user_message)
                save_message(self.conversation_id, "assistant", quick_result)
                logger.info(f"[Engine] 快速命令消息已存: conv={self.conversation_id}")
            except Exception as e:
                logger.warning(f"[Engine] 快速命令消息保存失败: {e}")
            yield ("text", quick_result)
            yield ("done", None)
            return

        # ★ 2026-08-08: 智能消息拆分 — 复杂消息拆成多个操作
        # ★ 2026-09-26 模式分开（已定案A）：浮窗💬聊天（pure_chat）=纯聊，跳过记事拦截，
        #   "永智农场怎么样？"被面板目录文案误判成提醒的根因即此；首页聊天框保留（09-17 开发者要求）
        if not getattr(self, '_pure_chat', False):
            _split_result = _try_smart_split(user_message, self.conversation_id)
            if _split_result:
                try:
                    save_message(self.conversation_id, "user", user_message)
                    save_message(self.conversation_id, "assistant", _split_result)
                except Exception:
                    pass
                yield ("text", _split_result)
                yield ("done", None)
                return

        # ★ 2026-08-03: 流水线已废弃，由Skill脚本替代

        # ★ 2026-08-08: 协作模式=安全开关
        _collab_val_sk = self._collab_mode_override
        if _collab_val_sk is None:
            _collab_val_sk = get_preference("collab_mode") or "off"
        _collab_on = _collab_val_sk and _collab_val_sk != "off"

        # ★ 2026-07-17: Skill触发词自动执行
        # ★ 2026-09-15 V1.5: / 分段接力消息跳过技能触发（否则"作曲"等词会把流水线命令抢给 music-gen 直跑）
        # ★ 2026-09-26 A方案①：浮窗💬聊天（pure_chat）也跳过——聊天纯聊，技能触发词不再抢答
        #   （实锤：09-26 开发者在聊天模式问"聊天没有记录?"被触发词机制抢去跑脚本吐JSON报错）
        _skill_hit = None if (getattr(self, '_pipeline_segments', None) or getattr(self, '_pure_chat', False)) else _try_skill_trigger_match(user_message)
        _skill_name, _skill_action = _skill_hit if _skill_hit else (None, None)
        _skill_context = ''  # ★ #988：待模型组织人话的 skill 结构化结果（未命中/纯文本时为空）
        # ★ #1518（2026-11-14 A方案）：协作OFF命中垂直Skill不再「挡门吞消息」——
        #   旧版直接 yield 警告 + return，用户问题一个字不答（实锤：老谭贴歌词片段问
        #   "乡村曲风单词"，片段含"编曲"命中 music-gen → 只弹"需要开启协作"就收工，
        #   连发 4 次全被吞）。新版：记住命中，跳过技能执行，正常管线回答，
        #   回答末尾附一句垂直入口提示（见 _stream_three_stage 尾部 _vertical_blocked 分支）。
        _vertical_blocked = None
        if _skill_name:
            _vertical_skills = ['music-gen', 'musicgen', 'bark', 'stable-diffusion', 'huatuo']
            if not _collab_on and any(v in _skill_name.lower() for v in _vertical_skills):
                _vertical_blocked = _skill_name
                logger.info(f"[Engine] #1518 垂直Skill【{_skill_name}】协作OFF不再拦截，转正常回答+末尾提示")
                _skill_name = None  # 跳过技能执行，正常管线接管

        # ★ #1518：末尾提示助手——回答出口可能有多个（FC 收笔/FC 非流式/主循环收尾），
        #   每个出口收笔前调一次，只在有回答且未发过时返回提示（幂等，只附一次）
        _vb_flushed = [False]
        def _vb_take_hint():
            if not _vertical_blocked or _vb_flushed[0]:
                return ''
            _vb_flushed[0] = True
            _vb_labels = {'music-gen': '音乐生成', 'musicgen': '音乐生成', 'bark': '语音合成',
                          'stable-diffusion': '图片生成', 'huatuo': '医疗问答'}
            _vb_label = _vb_labels.get(_vertical_blocked.lower(), _vertical_blocked)
            return (f"\n\n> 💡 这条消息命中了垂直技能【{_vb_label}】，当前协作模式未开启，没有自动执行。\n"
                    f"> 想真做：开「🤝 协作」模式，或到「🎨 垂直」面板直接使用。")
        if _skill_name:
            yield ("text", f"🔧 检测到【{_skill_name}】触发词，正在执行...\n\n")

            # ★ 2026-08-08: 流式执行 — 实时显示进度
            from src.core.scripts_runner import run_skill_script_streaming
            import json as _json
            full_output = ''
            final_result = None
            # ★ 2026-09-15: 把用户原话传进 params（此前传 {} → music-gen 等技能提取不到
            #   "乡村/30秒"这类意图，永远落默认值；技能脚本从 HARVEN_SKILL_PARAMS 读）
            # ★ 2026-09-26 ③：action 不再写死 'main'——按消息意图从技能动作表选真实值
            for item in run_skill_script_streaming(_skill_name, _skill_action or 'main', {'message': user_message}, timeout=300):
                if isinstance(item, str):
                    # 进度行
                    # ★ 2026-09-26 ④：脚本异常打到 stdout 的错误 JSON 不再直吐用户
                    #   （实锤：accounting 传 'main' 报"不支持的操作"时原始 JSON 直接上屏）
                    if item.lstrip().startswith('{') and '"success"' in item:
                        full_output += item + '\n'
                        continue
                    full_output += item + '\n'
                    yield ("text", item + '\n')
                elif isinstance(item, dict):
                    final_result = item

            # ★ 2026-09-26 ④：失败不再把错误JSON/空结果当回答——说人话并转正常管线
            #   （不存库、不提前收尾，落到下面继续正常回答流程）
            # ★ #988（2026-10-05 定版拍板）：成功但输出是结构化 JSON（记账流水/查询结果等）
            #   也不再直接上屏/存库收尾——同样转正常管线，由当前阵营模型组织人话。
            #   病根实锤（09-05 开发者截图）：accounting 查询结果 JSON 被当进度行直吐上屏（乱码），
            #   或存库后不上屏（用户只见"正在执行"就没了）。审计报告美化分支保留。
            _skill_ok = bool(final_result and final_result.get('success'))
            if not _skill_ok:
                _err_brief = ((final_result or {}).get('error') or '执行出错').strip().splitlines()[0][:80]
                yield ("text", f"⚠️ 技能【{_skill_name}】自动执行没成功（{_err_brief}）。我直接回答你：\n\n")
            else:
                _skill_result = (final_result or {}).get('output', '') or full_output

                # 解析输出格式
                full_response = _skill_result[:5000]
                _structured = False
                try:
                    data = _json.loads(_skill_result)
                    if isinstance(data, (dict, list)):
                        _structured = True
                    if isinstance(data, dict) and 'stats' in data and 'issues' in data:
                        stats = data.get('stats', {})
                        issues = data.get('issues', [])
                        p0 = [i for i in issues if i.get('severity') == 'P0']
                        p1 = [i for i in issues if i.get('severity') == 'P1']
                        p2 = [i for i in issues if i.get('severity') == 'P2']
                        report = "## 📋 代码健康审计报告\n\n"
                        report += f"| 指标 | 数值 |\n|------|------|\n"
                        report += f"| 扫描文件 | {stats.get('files_scanned', 0)} |\n"
                        report += f"| 总问题数 | {stats.get('total', 0)} |\n"
                        report += f"| P0（崩溃） | {len(p0)} |\n"
                        report += f"| P1（功能缺失） | {len(p1)} |\n"
                        report += f"| P2（废代码） | {len(p2)} |\n\n"
                        if p0:
                            report += "### 🔴 P0 严重问题\n\n"
                            for i in p0:
                                report += f"- `{i['file']}:{i['line']}` **{i['issue']}**\n"
                        if p1:
                            report += "\n### 🟡 P1 功能缺失\n\n"
                            for i in p1:
                                report += f"- `{i['file']}:{i['line']}` {i['issue'][:50]}\n"
                        if p2:
                            p2_by_file = {}
                            for i in p2:
                                p2_by_file.setdefault(i['file'], []).append(i)
                            report += f"\n### 🟢 P2 废代码（{len(p2)}个）\n\n"
                            for f in sorted(p2_by_file.keys()):
                                report += f"- `{f}`: {len(p2_by_file[f])}个\n"
                        report += f"\n**总结**: "
                        if p0: report += f"🔴 {len(p0)}个崩溃级问题！"
                        elif p1: report += f"🟡 {len(p1)}个功能缺失。"
                        else: report += "🟢 代码健康良好。"
                        full_response = report
                        _structured = False  # 已美化成人话报告，直接收尾
                except (ValueError, TypeError):
                    _structured = False  # 纯文本输出已在流式yield中显示

                if _structured:
                    # ★ #988：转正常管线 — 结果注入 enhanced_context，模型组织人话（不在这里收尾）
                    _skill_context = full_response
                    logger.info(f"[Engine] #988 skill【{_skill_name}】输出结构化 JSON({len(full_response)}字)，"
                                f"转当前阵营模型组织人话（快车道不再直吐/存库）")
                else:
                    # 纯文本/已美化报告：原逻辑存库收尾
                    # ★ 2026-08-06: Skill执行也存消息到数据库（★ 2026-09-14 带来源）
                    try:
                        _src_skill = getattr(self, "_reply_source", "")
                        save_message(self.conversation_id, "user", user_message)
                        save_message(self.conversation_id, "assistant", full_response,
                                     metadata=({"source": _src_skill} if _src_skill else None))
                        logger.info(f"[Engine] Skill消息已存: conv={self.conversation_id}")
                    except Exception as e:
                        logger.warning(f"[Engine] Skill消息保存失败: {e}")
                    yield ("done", None)
                    return

        # 判断查询类型（direct/kb_only 都使用分类结果）
        query_info = self._classify_query(user_message)

        # 根据模式决定搜索行为
        do_kb, do_web = self._resolve_query_mode(query_info, user_message)

        # 收集增强上下文
        enhanced_context = ""
        _search_context = ""  # 搜索结果增强上下文（初始化在前面，L1106 赋值）
        kb_results = []

        # ★ 2026-07-29: 方案N — 注入系统状态到enhanced_context
        if getattr(self, '_system_status', ''):
            enhanced_context += (
                "\n\n## 🖥️ 当前系统状态（方案N预检测）\n"
                "以下是最近5条系统事件日志（模型回答前已自动查好，可直接引用）：\n"
                "```\n" + self._system_status + "\n```\n"
                "如果用户问关于电脑/系统/重启/蓝屏等问题，请直接引用上述日志回答。\n"
            )

        # ★ #988（2026-10-05 定版拍板）：快车道 skill 查询结果注入 — 模型组织人话
        #   架构原则落地：数据提取→本地毫秒级（skill 已查完），结果注入→当前阵营模型组织人话。
        #   用户问"这个月花了多少钱"，skill 提取的流水 JSON 在这里变成模型的素材而非直接输出。
        if _skill_context:
            enhanced_context += (
                "\n\n## 📊 已查到的本机数据（技能刚从本地数据库提取）\n"
                "以下是结构化原始数据。请基于它用自然语言回答用户：该汇总的汇总、该分类的分类，"
                "直接说结论；**严禁原样输出 JSON 或逐条罗列原始字段**。\n"
                "```\n" + _skill_context[:4000] + "\n```\n"
            )
            logger.info(f"[Engine] #988 skill查询结果已注入上下文({len(_skill_context)}字)，由模型组织人话")

        # ★ 2026-09-26 甲（已批准）：找东西先查本机档案 — 预查面板数据注入
        #   病根：首页「帮我找公司执照照片」直接落外脑 → GLM 只有文件类工具 → 爬目录搜图片
        #   100% 落空（面板数据/照片都在 sqlite 库里，不在文件系统）。预查后外脑直接引用。
        if not getattr(self, '_pure_chat', False):
            try:
                from src.core.panel_assist import archive_quick_context as _aqc
                _arch_txt, _arch_hit = _aqc(user_message)
                if _arch_txt:
                    enhanced_context += ("\n\n## 🗂️ 本机档案预查结果\n" + _arch_txt + "\n")
            except Exception as _ae:
                logger.debug(f"[Engine] 档案预查跳过: {_ae}")

        # ★ 2026-09-27 #882 乙：档案中枢预查（记账等结构化域，本地毫秒级直查）
        #   病根：查账类问题曾被排除名单放去外脑 → FC 映射查错库（lingzi.db 空库）→ 生 JSON/谎称「暂无记录」
        #   中枢先查好真实数据注入；给本地还是外脑由用户当前选的模型决定，中枢不挑模型
        if not getattr(self, '_pure_chat', False):
            try:
                from src.core.archive_hub import hub_quick_context as _hqc
                _hub_txt = _hqc(user_message)
                if _hub_txt:
                    enhanced_context += ("\n\n" + _hub_txt + "\n")
            except Exception as _he:
                logger.debug(f"[Engine] 档案中枢预查跳过: {_he}")

        # ★ #1210/#1207 数据中枢一次注入（2026-10-25）：问题提到名册成员（或「孩子」泛指）
        #   → 画像+批改+错题+健康+账单+议程一次喂齐，「帮小明分析这周数学」不再需要翻面板
        if not getattr(self, '_pure_chat', False):
            try:
                from src.core.member_hub import build_context as _mctx
                _member_txt = _mctx(user_message)
                if _member_txt:
                    enhanced_context += ("\n\n" + _member_txt + "\n")
            except Exception as _me:
                logger.debug(f"[Engine] 成员中枢注入跳过: {_me}")
            # ★ #1209 议程进上下文：以前议程只在前端卡片，问管家「今天有什么要办的」模型根本看不到
            try:
                from src.core.member_hub import agenda_summary_for_chat as _agsum
                _ag_txt = _agsum()
                if _ag_txt:
                    enhanced_context += ("\n\n" + _ag_txt + "\n")
            except Exception as _ae2:
                logger.debug(f"[Engine] 议程注入跳过: {_ae2}")

            # ★ #1314（2026-10-29 2c 模块动态全局注入，定版拍板）：最近 24h 模块动作本地
            #   毫秒级预查注入——病根（咖啡机事故实锤断层之二）：手机拍照链动作（存照片/
            #   建台账记录/深识草稿）只落库不进聊天上下文，追问「这个咖啡机什么牌子」模型
            #   不知道刚拍过咖啡机。#1312 会话回写管「本会话接得上」，这里管「任何会话、
            #   隔了多少条消息都接得上」。全 try 包裹，查不动就静默跳过，绝不挡回复。
            if not getattr(self, '_pure_chat', False):
                try:
                    from datetime import timedelta as _td2
                    _since = (datetime.now() - _td2(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
                    _dyn = []
                    # 1) 图库 24h 新入库（search 按 id 倒序最新优先，取 10 条再按时间筛）
                    try:
                        from src.core import img_store as _ist2
                        for _it in (_ist2.search(limit=10) or []):
                            if (_it.get('created_at') or '') >= _since:
                                _note = (_it.get('note') or '').strip() or '（无说明）'
                                _dyn.append(f"📷 照片「{_note[:30]}」存入相册「{_it.get('album') or '未分类'}」"
                                            f"（图库 id={_it['id']}）")
                    except Exception as _ie2:
                        logger.debug(f"[Engine] 模块动态·照片跳过: {_ie2}")
                    # 2) 各面板 24h 新记录（panel_<id> 表 created_at 过滤，每面板最多 3 条）
                    try:
                        from src.core.panel_manager import get_panel_manager, DB_PATH as _PM_DB
                        from src.models.database import get_connection as _gc2
                        _pm2 = get_panel_manager()
                        _conn2 = _gc2(str(_PM_DB))
                        for _p in (_pm2.get_all_panels() or [])[:20]:
                            _pid = _p.get('id', '')
                            _pname = _p.get('name') or _pid
                            try:
                                _cols = [r[1] for r in _conn2.execute(
                                    f'PRAGMA table_info("panel_{_pid}")').fetchall()]
                                if 'created_at' not in _cols:
                                    continue
                                _name_col = next((x for x in ('物品名称', '名称', '项目', '事项', '标题', '内容')
                                                  if x in _cols), '')
                                _sel = f'id, "{_name_col}"' if _name_col else 'id'
                                for _r in _conn2.execute(
                                        f'SELECT {_sel} FROM "panel_{_pid}" WHERE created_at >= ? '
                                        f'ORDER BY id DESC LIMIT 3', (_since,)).fetchall():
                                    _nm = ((str(_r[1]).strip() if _name_col and len(_r) > 1 and _r[1] is not None else '')
                                           or '（新记录）')
                                    _dyn.append(f"📝 面板「{_pname}」新增记录 #{_r[0]}：{_nm[:30]}")
                            except Exception:
                                continue
                        _conn2.close()
                    except Exception as _pe2:
                        logger.debug(f"[Engine] 模块动态·面板跳过: {_pe2}")
                    # 3) 待确认队列概览（深识草稿/凭证等，模型知道有东西等用户点头）
                    try:
                        from src.core import panel_assist as _pa2
                        _pend2 = _pa2.list_pending() or []
                        if _pend2:
                            _dyn.append(f"⏳ 有 {len(_pend2)} 条待确认事项等你点头（管家中心可查）")
                    except Exception:
                        pass
                    if _dyn:
                        enhanced_context += (
                            "\n\n## 🕘 最近24小时模块动态（预查注入，可直接引用）\n"
                            + '\n'.join(_dyn[:12])
                            + "\n如果用户问「刚才/今天拍了什么、存了什么、记了什么」，直接引用以上动态回答。\n")
                        logger.info(f"[Engine] #1314 模块动态已注入({len(_dyn)}条)")
                except Exception as _de2:
                    logger.debug(f"[Engine] 模块动态注入跳过: {_de2}")

        # ★ #1290（2026-10-29）自检预检注入：自检类问题本地毫秒级采全系统快照喂给模型——
        #   病根：外脑自检走 FC 一步一步查（6~7 轮 API 往返 30~60 秒），而事实本地一瞬查完。
        #   方案N同族：「数据提取→本地毫秒级，结果注入→外脑组织人话」，6 轮变 1 轮
        if not getattr(self, '_pure_chat', False):
            try:
                from src.core.system_preflight import preflight_context as _pfc
                _pre_txt = _pfc(user_message)
                if _pre_txt:
                    enhanced_context += ("\n\n" + _pre_txt + "\n")
            except Exception as _pe:
                logger.debug(f"[Engine] 自检预检注入跳过: {_pe}")

        # ★ #1256（2026-10-27）面板实况注入：问「面板/模块有几个/有哪些」不再靠模型编——
        #   病根：这类问题 discover/B 方案/FC 全接不住，裸模型幻觉「三个客厅卧室厨房」（#1255 手机实锤）
        try:
            if re.search(r'面板|模块', user_message) and re.search(r'几个|多少|数量|哪些|列表|清单', user_message):
                from src.core.panel_manager import get_panel_manager as _gpm
                from src.core.panel_routes import _groups_map as _gmap
                _vis = [p for p in _gpm().get_all_panels() if not p.get('hidden')]
                _gm = _gmap()
                _by_g = {}
                for _p in _vis:
                    _by_g.setdefault(_gm.get(_p.get('id'), '常用'), []).append(_p.get('name') or _p.get('id'))
                _plines = [f"- {g}（{len(ns)}个）：{'、'.join(ns)}" for g, ns in _by_g.items()]
                enhanced_context += (
                    f"\n\n## 🧩 面板实况（当前已装 {len(_vis)} 个可见面板，分 {len(_by_g)} 组）\n"
                    + "\n".join(_plines) +
                    "\n回答「有几个/有哪些」时直接引用以上真实数字与名字，严禁编造。\n"
                )
                logger.info(f"[Engine] #1256 面板实况已注入（{len(_vis)}面板/{len(_by_g)}组）")
        except Exception as _pne:
            logger.debug(f"[Engine] 面板实况注入跳过: {_pne}")

        # ★ #1220-C（2026-10-25）长期记忆召回：问句关键词命中用户以前让你长期记住的事实
        #   → 注入 top3。「儿子对青霉素过敏」这类生活常量，说过一次就要一直记得。
        #   零网络零嵌入（关键词滑窗打分，宁缺勿滥 min_score=2），失败静默不挡聊天。
        try:
            from src.core.memory_facts import recall as _mf_recall
            _facts = _mf_recall(user_message, limit=3)
            if _facts:
                enhanced_context += (
                    "\n\n## 🧠 长期记忆（用户以前让你记住的事，与当前问题相关时自然引用，别逐条复述）\n"
                    + "\n".join('- ' + f['fact'] for f in _facts) + "\n"
                )
        except Exception as _mfe:
            logger.debug(f"[Engine] 长期记忆召回跳过: {_mfe}")

        # ★ 2026-09-18 #743 画像主动服务：最近自动学到的新特征，读即清（提及一次不再重复）
        try:
            from src.core.profile_auto_updater import get_profile_updater as _gpu
            _tips = _gpu().consume_pending_insights()
            if _tips:
                _tip_lines = "\n".join(
                    f"- {t.get('dimension_label', t['dimension'])}：{t['label']}" for t in _tips[:5]
                )
                enhanced_context += (
                    "\n\n## 📌 画像更新提醒（主动服务）\n"
                    "我最近从我们的对话中注意到了这些新特点：\n" + _tip_lines +
                    "\n如果本次回答与上面内容相关，可以自然地向用户提一句"
                    "（比如「对了，我注意到你…」）；如果不相关就不要硬提，等合适的时机。\n"
                )
        except Exception as _pe:
            logger.debug(f"[Engine] 画像主动服务注入跳过: {_pe}")

        # ★ 2026-07-29: 强化提示词 — 告诉模型有哪些FC工具可用
        try:
            from src.core.builtin_tools import get_tool_definitions as _gtd
            # ★ 方案A 联网授权制（2026-10-05 定版拍板）：工具计数与联网提示按🌐开关生成
            _ws_on = bool(getattr(self, '_web_search_this_query', False))
            _fc_count = len(_gtd(allow_web_search=_ws_on))
            _web_line = "- web_search(query) / web_fetch(url) — 联网搜索\n" if _ws_on else ""
            enhanced_context += (
                f"\n\n## 🔧 可用的FC工具（{_fc_count}个）\n"
                "你有以下工具可以直接调用（不需要写代码！）：\n"
                "- check_software(name) — 检查软件安装状态\n"
                "- execute_command(command) — 执行系统命令\n"
                "- scan_system() — 扫描系统硬件\n"
                "- read_file(path) / write_file(path,content) / list_dir(path)\n"
                "- search_content(pattern,path) / search_files(pattern,path)\n"
                "- create_docx(filename,title,content) — 生成Word文档\n"
                "- create_xlsx(filename,headers,rows) — 生成Excel\n"
                "- create_pptx(filename,title,slides) — 生成PPT\n"
                "- create_chart(chart_type,filename,title,labels,values) — 生成图表\n"
                "- gui_screenshot(save_path) / gui_click(x,y) / gui_type(text) — 屏幕控制\n"
                "- analyze_csv(file_path,column) — 分析CSV数据\n"
                "- ocr_image(image_path,lang) — OCR文字识别\n"
                "- speak_text(text,save_path) — 文字转语音\n"
                "- create_qrcode(data,save_path) — 生成二维码\n"
                "- get_time() — 获取时间\n"
                + _web_line +
                "⚠️ 遇到系统/文件/软件问题时，必须先调用对应FC工具，不要写Python代码！\n"
            )
        except Exception:
            pass

        # ★ 2026-07-13: 注入垂直模型列表 → 让外脑/通用模型知道有哪些垂直模型可用
        try:
            from src.core.vertical_api import _BUILTIN_MODELS
            _vmodels_info = []
            for _vm in _BUILTIN_MODELS:
                _vmodels_info.append(f"  - {_vm.get('icon','📦')} {_vm.get('name','')}（{_vm.get('id','')}）：{_vm.get('description','')}")
            if _vmodels_info:
                enhanced_context += (
                    "\n\n## 📋 可用的垂直模型（用户可在垂直面板使用）\n"
                    + "\n".join(_vmodels_info) +
                    "\n\n注意：这些垂直模型在垂直面板里使用，不需要你在对话里调用。\n"
                    "如果用户的问题适合某个垂直模型（如医疗问题适合华佗），可以建议用户去垂直面板使用。\n"
                )
        except Exception as _e:
            logger.debug(f"[Engine] 忽略错误: {_e}")

        # ★ 2026-07-17: CLI-Anything 状态注入 → 让模型知道这个能力
        try:
            from src.core.builtin_tools import _check_cli_anything_installed, _get_available_cli_modules
            if _check_cli_anything_installed():
                _cli_modules = _get_available_cli_modules()
                if _cli_modules:
                    enhanced_context += (
                        f"\n\n## 🎨 CLI-Anything（已安装）\n"
                        f"已安装的子模块: {', '.join(_cli_modules)}\n"
                        f"### 使用规则\n"
                        f"- 使用 cli_anything_list 查看所有可用工具\n"
                        f"- 使用 cli_anything_execute 执行软件命令\n"
                        f"- **禁止写代码**安装/操作软件，必须通过FC工具调用\n"
                    )
                else:
                    enhanced_context += (
                        "\n\n## 🎨 CLI-Anything（Hub已安装，暂无子模块）\n"
                        "### ⚠️ 必须遵守的安装规则\n"
                        "1. **必须用 cli_anything_install 工具安装子模块**（如 blender/gimp/inkscape）\n"
                        "2. **禁止写Python代码**安装（pip install/subprocess等会被安全过滤）\n"
                        "3. **禁止给外部下载链接**让用户自己下载\n"
                        "4. cli_anything_install 是FC工具调用，不是Python函数。直接调用：cli_anything_install(software='blender')\n"
                        "5. Hub已安装，不需要再装hub。直接装子模块：cli_anything_install(software='blender')\n"
                    )
            else:
                # 检测用户是否提到了CLI-Anything相关软件
                _cli_keywords = ['blender', 'gimp', 'inkscape', 'krita', 'audacity', 'obs',
                                 'ffmpeg', 'imagemagick', 'darktable', 'scribus', '3d建模',
                                 '渲染', '图像处理', '矢量绘图', '数字绘画', '音频编辑',
                                 '视频处理', 'cli-anything']
                _mentioned = [kw for kw in _cli_keywords if kw.lower() in user_message.lower()]
                if _mentioned:
                    enhanced_context += (
                        f"\n\n## 🎨 CLI-Anything（未安装）\n"
                        f"用户提到了: {', '.join(_mentioned)}\n"
                        f"### ⚠️ 必须遵守的安装规则\n"
                        f"1. **必须用 cli_anything_install 工具安装**，不要给下载链接让用户自己去网页下载\n"
                        f"2. **禁止给外部下载链接**（如 blender.org/download 等）\n"
                        f"3. **禁止写下载代码**（requests.get/webbrowser.open 等）\n"
                        f"4. 安装步骤：先 cli_anything_install('hub') 安装基础包，再 cli_anything_install('{_mentioned[0]}') 安装子模块\n"
                        f"5. 如果用户说\"下载并安装Blender\"，你应该说：\"要用CLI-Anything安装Blender吗？\"然后调用 cli_anything_install\n"
                        f"注意：目标软件本身（如Blender.exe）需用户自行安装，CLI-Anything安装的是命令行控制模块\n"
                    )
        except Exception as _e:
            logger.debug(f"[Engine] CLI-Anything 检测跳过: {_e}")

        # ★ 2026-07-13: 注入知识库信息 → 让外脑/通用模型知道知识库状态
        try:
            import os as _os
            _kb_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), 'data', 'knowledge')
            _kb_files = []
            if _os.path.isdir(_kb_dir):
                _kb_files = [f for f in _os.listdir(_kb_dir) if not f.startswith('.')]
            enhanced_context += (
                f"\n\n## 📚 知识库信息\n"
                f"知识库目录: {_kb_dir}\n"
                f"知识库文件数: {len(_kb_files)}\n"
                f"查看知识库内容: 调用 list_knowledge 工具\n"
                f"搜索知识库: 调用 search_knowledge 工具\n"
            )
        except Exception as _e:
            logger.debug(f"[Engine] 忽略错误: {_e}")

        # ★ 2026-07-03: 合并 Bing 搜索结果到 enhanced_context（值在 Step 1.5 填充）
        if _search_context:
            enhanced_context += _search_context + "\n\n"

        # ═══ P3: 闲聊 fast-path — 跳过 GitHub/浏览器/知识库/垂直/Agent，直接到模型 ═══
        _is_casual = (query_info.get("query_type") == "casual"
                      and not do_kb
                      and not getattr(self, '_skip_vertical_route', False))

        if not _is_casual:
            # Step 0: 检测 GitHub URL 并自动下载（所有模式都执行，很快）
            github_context = self._check_github_url(user_message)
            if github_context:
                enhanced_context += github_context
                yield ("source", "📦 已从 GitHub 下载并导入知识库")

        if not _is_casual:
            # Step 0b: 浏览器自动化检测（所有模式）
            try:
                from src.core.browser_agent import parse_browser_intent, execute_browser_task
                browser_intent = parse_browser_intent(user_message)
                if browser_intent:
                    yield ("thinking", f"🌐 正在执行浏览器操作：{browser_intent.get('action', '')}...")
                    browser_result = execute_browser_task(user_message)
                    if browser_result:
                        enhanced_context += f"\n\n## 浏览器操作结果\n{browser_result}"
                        yield ("source", "🖥️ 已执行浏览器操作")
            except Exception as e:
                logger.debug(f"[Engine] 浏览器自动化跳过: {e}")

            # Step 0c: Active Memory（所有模式执行）
            try:
                from src.core.active_memory import get_active_memory
                am = get_active_memory()
                memory_context = am.retrieve_as_context(user_message, top_k=4)
                if memory_context:
                    enhanced_context += f"\n\n{memory_context}"
            except Exception as e:
                logger.debug(f"[Engine] Active Memory 跳过: {e}")

            # Step 1: 查知识库
            if do_kb:
                # ★ 2026-07-13: 用户要"检查/查看/列出知识库内容" → 调list_knowledge而不是搜索
                _has_kb_word = '知识库' in user_message
                _has_list_word = any(kw in user_message for kw in ['检查', '查看', '列出', '内容', '有什么', '里面', '看看', '看看里面', '查一下'])
                if _has_kb_word and _has_list_word:
                    try:
                        from src.core.builtin_tools import list_knowledge
                        kb_list = list_knowledge()
                        if kb_list.get('success') and kb_list.get('items'):
                            _items_text = "\n".join(
                                f"  - {item.get('filename','')}（格式:{item.get('format','')}, 段落:{item.get('chunks',0)}）"
                                for item in kb_list['items']
                            )
                            enhanced_context += f"\n\n## 📚 知识库内容列表\n共{kb_list['total']}项:\n{_items_text}\n"
                        else:
                            enhanced_context += "\n\n## 📚 知识库内容\n知识库为空或无已导入文件。\n"
                        yield ("source", f"📚 知识库共有 {kb_list.get('total', 0)} 项内容")
                    except Exception as e:
                        logger.debug(f"[Engine] list_knowledge失败: {e}")
                else:
                    yield ("thinking", "🔍 正在检索本地知识库...")
                    try:
                        from src.core.knowledge_base import get_knowledge_base
                        kb = get_knowledge_base()
                        kb_results = kb.search(user_message, top_k=3, threshold=0.2)
                        if kb_results:
                            kb_text = "\n".join(
                                f"[知识库-{r['source']}]: {r['text']}"
                                for r in kb_results
                            )
                            enhanced_context += f"\n\n[增强信息——知识库]\n{kb_text}"
                            yield ("source", f"📚 从本地知识库找到 {len(kb_results)} 条相关内容")
                    except Exception as e:
                        logger.error(f"[Engine] 知识库搜索失败（已跳过）: {e}")

        # ═══════════════════════════════════════════════════════════════
        # Step 1.5: 联网搜索（2026-07-05 重构）
        # ★ 两种触发方式：
        #   1. 用户点🌐按钮（web_search=True）→ 强制搜索
        #   2. 关键词自动检测（精简清单，避免误触发）→ 自动搜索
        # ★ Bing HTML 解析，无需 API Key/Docker/Skill
        # ═══════════════════════════════════════════════════════════════
        _skip_vertical = getattr(self, '_skip_vertical_route', False)
        _search_context = ""  # 搜索结果增强上下文（初始化在前面，L1106 赋值）

        # ★ 2026-07-08: 统一互联网搜索检查（不分协作模式）
        # 联网关键词清单
        _web_intent_keywords = [
            "搜索", "搜一下", "查一下", "联网", "上网搜", "网上搜",
            "天气", "气温", "新闻", "股价", "股票", "汇率", "热搜",
            "实时", "search", "google", "bing",
            "上证", "指数", "大盘", "金价", "银价", "油价",
            "比特币", "以太坊", "美股", "港股", "A股", "基金净值",
            "理财", "存款利率", "房价", "物价", "通胀",
            "球赛", "比分", "赛果", "战绩", "排行榜",
            "上映", "票房", "收视率",
        ]
        _has_web_intent = any(kw in user_message.lower() for kw in _web_intent_keywords)

        # ★ 2026-07-13: 用户要"检查知识库内容" → 不触发联网搜索
        if '知识库' in user_message and any(kw in user_message for kw in ['检查', '查看', '列出', '内容', '有什么', '里面', '看看']):
            _has_web_intent = False

        # ★ 2026-07-08: 操作类请求不触发搜索
        # 用户说"下载XX/安装XX/装XX"且给了URL时，是操作类请求，不是查询类
        _is_download_op = any(kw in user_message.lower() for kw in [
            "下载", "安装", "帮我装", "装个", "装一", "pip install",
            "ollama pull", "git clone", "modelscope download",
        ])
        _has_model_url = any(url in user_message.lower() for url in [
            "modelscope.cn/models/", "huggingface.co/", "github.com/",
        ])
        if _is_download_op and _has_model_url:
            _has_web_intent = False  # 操作类请求不触发搜索
            logger.info("[Engine] 检测到模型下载URL，跳过联网搜索")

        # ★ 2026-07-22: CLI-Anything 软件安装请求不走搜索（走 cli_anything_install）
        _cli_software_names = ['blender', 'gimp', 'inkscape', 'krita', 'audacity',
                               'obs', 'ffmpeg', 'imagemagick', 'darktable', 'scribus']
        _mentioned_cli = [kw for kw in _cli_software_names if kw in user_message.lower()]
        _has_install_word = any(kw in user_message.lower() for kw in ["下载", "安装", "装", "download", "install"])
        if _mentioned_cli and _has_install_word:
            _has_web_intent = False
            # ★ 即使用户点了🌐也跳过搜索——CLI-Anything软件安装不走搜索
            self._web_search_this_query = False
            logger.info(f"[Engine] CLI-Anything软件安装请求: {_mentioned_cli}，跳过搜索走安装流程")

        _user_wants_search = getattr(self, '_web_search_this_query', False)

        # ★ 2026-07-06: 协作模式检查
        _collab_mode_val = self._collab_mode_override
        if _collab_mode_val is None:
            _collab_mode_val = get_preference("collab_mode") or "off"
        _collab_active = _collab_mode_val and _collab_mode_val != "off"

        # ★ 2026-07-06: 统一搜索决策
        # ★ 2026-07-18: 方案B——分类为搜索但🌐没开时不阻断，继续走模型（让模型用本地能力回答）
        if _has_web_intent or _user_wants_search:
            if not _user_wants_search:
                # 关键词命中但🌐按钮没开 → 不阻断，继续走模型让模型用本地能力回答
                logger.info("[Engine] 检测到联网意图但🌐未开启，继续走模型（不阻断）")
                # 不return，不yield警告，继续正常流程
            elif _collab_active:
                # 🌐按钮开了但协作模式开着 → 不搜索，让外脑/本地处理
                logger.info("[Engine] 协作模式开启，跳过互联网搜索")
                # 不return，继续走正常流程
            elif not _is_casual and not _skip_vertical:
                # ★ 正常搜索流程
                _search_query = self._extract_search_query(user_message)
                if _search_query:
                    yield ("thinking", f"🔍 正在搜索: {_search_query} ...")

                    try:
                        _results = self._web_search(_search_query, max_results=5)
                        if _results:
                            # ★ 2026-07-21: 事实查询改为抓取网页正文 + 模型整合（不再只展示卡片）
                            _is_fact = self._is_fact_query(user_message)
                            if _is_fact:
                                # 事实查询：抓取前3个URL的页面内容，提取实际数据
                                _fetch_urls = [r["url"] for r in _results[:3]]
                                logger.info(f"[Engine] 事实查询抓取网页: {_search_query} ({len(_fetch_urls)} 个URL)")

                                try:
                                    _fetched = self._fetch_pages_concurrent(_fetch_urls, max_workers=3, timeout_per_page=8)
                                    _fetched_count = sum(1 for v in _fetched.values() if v)
                                    logger.info(f"[Engine] 网页抓取完成: {_fetched_count}/{len(_fetch_urls)} 成功")
                                    # ★ 调试：输出每个URL的抓取结果长度
                                    for _fu, _fc in _fetched.items():
                                        logger.info(f"[Engine] 抓取详情: {_fu[:60]}... → {len(_fc)} 字符")
                                except Exception as e:
                                    logger.warning(f"[Engine] 网页抓取失败，回退到摘要模式: {e}")
                                    _fetched = {}
                                    _fetched_count = 0

                                # 构建含网页正文的增强上下文
                                _search_context = self._build_fact_search_context(_search_query, _results, _fetched)

                                # 仍然展示搜索结果卡片（用户可点链接查看原文）
                                _sources = [{"title": r["title"], "url": r["url"]} for r in _results]
                                yield ("search_results", {
                                    "query": _search_query,
                                    "sources": _sources,
                                    "engine": "Bing"
                                })
                                yield ("text", f"🔍 已搜索并抓取 {_fetched_count} 个网页，正在整合数据...")
                                logger.info(f"[Engine] 事实查询抓取完成，交给模型整合: {_search_query}")
                            else:
                                # 知识查询：注入enhanced_context，让模型整合回答
                                _search_context = self._build_search_context(_search_query, _results)
                                _sources = [{"title": r["title"], "url": r["url"]} for r in _results]
                                yield ("search_results", {
                                    "query": _search_query,
                                    "sources": _sources,
                                    "engine": "Bing"
                                })
                                logger.info(f"[Engine] Bing 搜索成功: {len(_results)} 条结果")
                        else:
                            yield ("warning", "⚠️ 互联网搜索未返回结果，尝试用本地知识回答...")
                            logger.warning("[Engine] Bing 搜索无结果")
                    except Exception as e:
                        logger.error(f"[Engine] Bing 搜索异常: {e}")
                        yield ("warning", f"⚠️ 互联网搜索出错: {e}")

        # ★ 2026-07-21: 搜索后合并 _search_context（修复时序bug：搜索在Step1.5，但merge在L1935之前执行）
        if _search_context:
            enhanced_context += _search_context + "\n\n"
            logger.info(f"[Engine] 搜索上下文已合并到 enhanced_context ({len(_search_context)} 字符)")

        # ═══════════════════════════════════════════════════════════════
        # Step 2.3: 模型安装意图检测（2026-07-05 新增）
        # ★ 用户说"帮我装MusicGen/装个本地模型"→ 弹安装确认卡片
        # ═══════════════════════════════════════════════════════════════
        if not _is_casual:
            _install_intent = self._detect_install_intent(user_message)
            if _install_intent:
                logger.info(f"[Engine] 模型安装意图: {_install_intent}")
                yield ("install_confirm", _install_intent)
                yield ("done", "")
                return

        # ═══════════════════════════════════════════════════════════════
        # Step 2.35: 专项培养创建意图检测（2026-07-05 新增）
        # ★ 用户说"帮我创建翻译助手专项培养"→ 弹创建卡片
        # ═══════════════════════════════════════════════════════════════
        if not _is_casual:
            _spec_intent = self._detect_specialization_intent(user_message)
            if _spec_intent:
                logger.info(f"[Engine] 专项培养创建意图: {_spec_intent}")
                yield ("spec_create_confirm", _spec_intent)
                yield ("done", "")
                return

        # ═══════════════════════════════════════════════════════════════
        # Step 2.36: 知识库上传引导（2026-07-05 新增）
        # ★ 用户说"帮我上传到知识库/怎么上传知识库"→ 弹引导卡片
        # ═══════════════════════════════════════════════════════════════
        if not _is_casual:
            _kb_intent = self._detect_kb_upload_intent(user_message)
            if _kb_intent:
                logger.info(f"[Engine] 知识库上传意图: {_kb_intent}")
                yield ("kb_upload_guide", _kb_intent)
                yield ("done", "")
                return

        # ═══════════════════════════════════════════════════════════════
        # Step 2.37: 受控进化意图检测（2026-07-06 恢复）
        # ★ 用户说"帮我加功能/改代码/优化XX"→ 生成进化提案
        # ═══════════════════════════════════════════════════════════════
        if not _is_casual:
            _evolution_intent = self._detect_evolution_intent(user_message)
            if _evolution_intent:
                logger.info(f"[Engine] 受控进化意图: {_evolution_intent.get('title','')}")
                yield ("evolution_proposal", _evolution_intent)
                yield ("done", "")
                return

        # ═══════════════════════════════════════════════════════════════
        # Step 2.4: 垂直模型路由 — ★ 2026-07-13: 已禁用
        # 本地/外脑模式下不再自动弹出垂直面板，用户需主动选择垂直面板或协作模式
        # 原因：关键词匹配太宽泛（"图片"误触发SD），改为充分授权模式

        # ═══════════════════════════════════════════════════════════════
        # Step 2.5: Agent 注册中心路由（意图识别 → 专项 Agent 处理）
        # P1-1: 复用 Step 0.65 的 discover() 结果，不重复调用
        # P3: 闲聊 fast-path 跳过 Agent 路由
        # ═══════════════════════════════════════════════════════════════
        _agent_matched = None
        _agent_override_prompt = None
        _agent_pref = None
        if not _is_casual:
            try:
                from src.core.agent_registry import get_registry
                registry = get_registry()
                # P1-1: 优先使用 Step 0.65 已发现的结果
                _agent_matched = _agent_id_early
                if not _agent_matched:
                    # Step 0.65 未匹配或未执行 → 这里不再重复 discover
                    pass
                if _agent_matched:
                    agent = registry._agents.get(_agent_matched, {})
                    _agent_override_prompt = agent.get("system_prompt", "")
                    _agent_pref = agent.get("model_pref", "local")
                    
                    # ★ 2026-07-03: 禁用的 agent 直接跳过，不执行任何 agent 逻辑
                    if _agent_pref == "disabled":
                        logger.info(f"[Engine] Agent '{_agent_matched}' 已禁用，Step 2.5 跳过（走垂直面板参数提取）")
                        _agent_matched = None
                        _agent_override_prompt = None
                        _agent_pref = None
                
                # ★ 注入 Agent 专属 Skills + 路由执行（仅当 agent 未被禁用）
                if _agent_matched:
                    agent_skills = agent.get("skills", [])
                    if agent_skills:
                        skills_text = ""
                        try:
                            from src.core.skills_manager import get_skills_manager
                            sm = get_skills_manager()
                            for skill_name in agent_skills:
                                skill = sm.get_skill(skill_name)
                                if skill:
                                    skills_text += f"\n\n[可用工具: {skill_name}]\n{skill.prompt[:800]}"
                        except Exception as _e:
                            logger.debug(f"[Engine] 忽略错误: {_e}")
                        if skills_text:
                            _agent_override_prompt += f"\n\n## 🛠️ 你的专属工具{skills_text}"
                
                    yield ("thinking", f"🧩 Agent 路由 → {agent.get('name', _agent_matched)}...")
                
                    # ★ 脚本型 Agent：model_pref == "script" → 直接执行 Python 脚本
                    # ★ 2026-07-03: "suggest" 分支已删除（music_suggest agent 已删除）
                    if _agent_pref == "script":
                        script_name = agent.get("script_name", "")
                        if script_name:
                            try:
                                from src.core.agent_runner import run_script_agent
                                yield ("thinking", f"⚙️ 执行脚本 Agent: {script_name}...")
                                result = run_script_agent(script_name, user_message, agent)
                                if result.get('success'):
                                    full_response = result.get('output', '完成')
                                    fp = result.get('file', '')
                                    if fp: full_response += f"\n\n📁 {fp}"
                                else:
                                    full_response = f"❌ 脚本错误: {result.get('error', '未知')}"
                                self.history.append({"role": "user", "content": user_message})
                                self.history.append({"role": "assistant", "content": full_response})
                                yield ("text", full_response)
                                yield ("done", "")
                                return
                            except Exception as e:
                                logger.error(f"[Engine] 脚本Agent失败: {e}")
                
                    logger.info(f"[Engine] Agent 路由: {_agent_matched} ({agent.get('name')}), pref={_agent_pref}, skills={agent_skills}")
            except Exception as e:
                logger.debug(f"[Engine] Agent 路由跳过: {e}")

        # ═══════════════════════════════════════════════════════════════
        # Step 3: 协作模式检查 → 智能模型路由
        # 文档 v4.0 规则：
        # - 协作模式优先于路由：外脑仅做"参谋"，Ollama 强制整合
        # - 协作模式下跳过 Step 3，禁止第 2 个外部模型介入
        # ═══════════════════════════════════════════════════════════════
        use_external, model_label, model_id = False, "本地模型", "ollama"
        is_collab_skipped = False  # 标记：协作模式是否跳过了路由

        # ═══════════════════════════════════════════════════════════════
        # ★ 协作模式完整规则（2026-07-06 更新）
        #
        # 两种查询模式共享能力：知识库 + Skills + 用户画像 + 习惯 + 诚实
        # 🏠本地(kb_only) → 本地通用模型 | 🤖外脑(direct) → 外部模型
        # 协作是独立开关：☁️云地协作/🏠本地双模（只调参数+给建议，不加知识库/Skills）
        #
        # 规则 1：简单命令不协作
        #   "打开文件"、"列出目录"等无分析需求的操作 → 提示用户关协作
        #
        # 规则 2：专业模型的诚实警告
        #   本地模型缺乏语言/代码能力（如 embedding-only、vision-only）
        #   → 协作前警告用户，建议切外脑操作该类功能
        #
        # 规则 3：诚实原则
        #   外脑不说"我是本地模型"，本地不说"我能联网搜索"
        #   协作时双方都知道自己是协作角色
        # ═══════════════════════════════════════════════════════════════
        
        # 协作模式检查（独立于 query_mode，由 collab_mode_override 或 config 控制）
        collab_analysis = ""
        try:
            from src.core.collab_manager import get_collab_manager
            # P1-3: 使用 get_config() 单例
            from src.utils.config import get_config
            cfg = get_config()
            if not isinstance(cfg, dict):
                cfg = {}
            
            # ★ 修复：collab_mode_override 优先于 config
            if self._collab_mode_override is not None:
                cfg['collab_mode'] = self._collab_mode_override if isinstance(self._collab_mode_override, str) else ('cloud_local' if self._collab_mode_override else 'off')
                logger.info(f"[Engine] 协作模式覆盖: {self._collab_mode_override} (来自请求体)")
            
            cm = get_collab_manager(cfg)
            
            # 云地协作模式：外脑当大脑 → 垂直模型当工具
            if cm.is_cloud_local and cm.foreign_model:
                # ═══ 规则 1：简单命令检测 ═══
                _simple_patterns = [
                    r'^(打开|播放|运行)\s+',           # 打开文件/播放音乐/运行程序
                    r'^(列出|查看)\s*(目录|文件|文件夹)', # 列出目录
                    r'^(启动|关闭|重启)\s',            # 启动/关闭/重启
                    r'^文件:\s*\S+打开',              # "文件: xxx打开"
                ]
                _is_simple = any(re.search(p, user_message) for p in _simple_patterns)
                if _is_simple:
                    yield ("notification", {
                        "type": "collab_simple_command",
                        "message": (
                            "💡 检测到简单操作命令。\n\n"
                            "建议：关闭协作模式，直接用 🏠本地模式 或 🤖外脑模式。\n"
                            "协作模式会先让外脑分析再执行，简单操作反而更慢。"
                        )
                    })
                
                # ═══ 规则 2：专业模型能力检测 ═══
                _local_model_name = getattr(self.model, 'model', '')
                if not isinstance(_local_model_name, str): _local_model_name = ''  # ★ 2026-08-25 类型守护
                _specialized_keywords = [
                    'embed', 'nomic', 'bge-', 'e5-',
                    'vision', 'clip', 'llava', 'bakllava',
                    'starcoder', 'codellama', 'deepseek-coder',
                    'whisper', 'bark', 'musicgen',
                ]
                _is_specialized = any(kw in _local_model_name.lower() for kw in _specialized_keywords)
                if _is_specialized:
                    yield ("notification", {
                        "type": "collab_specialized_model",
                        "message": (
                            f"⚠️ 本地模型「{_local_model_name}」是专业模型，不擅长通用对话。\n"
                            f"云地协作由外脑主导分析，本地垂直模型只做执行。"
                        )
                    })
                
                # ★ 2026-09-14 V1：协作流水线（对话站+执行站编排，替代单外脑分析）
                yield ("thinking", "⛓️ 协作流水线启动...")
                _pipe_res = yield from self._run_collab_pipeline(cm, user_message)
                if _pipe_res and _pipe_res.get('completed'):
                    yield ("done", "")
                    return
                # 失败原因已在管线内通过 error 事件告知
                logger.error(f"[Engine] 协作流水线失败: {(_pipe_res or {}).get('error')}")
                return
            
            # 本地双模协作：本地通用模型调度本地垂直模型
            elif cm.is_local_dual:
                _local_model = getattr(self.model, 'model', '')
                if not isinstance(_local_model, str): _local_model = ''  # ★ 2026-08-25 类型守护
                
                # ★ 读取本地双模配置（大脑模型偏好）
                from src.utils.config import get_config
                _dualc_cfg = get_config()
                _brain_override = _dualc_cfg.get('local_dual_brain', '')
                if isinstance(_brain_override, dict):
                    _brain_override = _brain_override.get('name', '')
                if _brain_override and _brain_override.strip():
                    _brain_name = _brain_override.strip()
                    # ★ 去掉可能的前缀 "ollama:" / "local:" 
                    for prefix in ('ollama:', 'local:'):
                        if _brain_name.startswith(prefix):
                            _brain_name = _brain_name[len(prefix):]
                    # ★ 实际切换模型
                    try:
                        self.model.set_model(_brain_name)
                        _local_model = _brain_name
                        logger.info(f"[Engine] 本地双模: 已切换至配置的大脑模型 {_brain_name}")
                    except Exception as e:
                        logger.warning(f"[Engine] 本地双模: 切换模型失败 {_brain_name}: {e}, 回退到当前模型")
                
                # ★ 校验：本地双模的大脑必须是通用模型（非垂直专用模型）
                _NON_CHAT_KW = _NONCHAT_KEYWORDS  # P1-2: 统一使用模块级常量
                _is_chat_model = not any(kw in _local_model.lower() for kw in _NON_CHAT_KW)
                
                if not _is_chat_model:
                    yield ("notification", {
                        "type": "collab_invalid_brain",
                        "message": (
                            f"⚠️ 本地双模需要**通用模型**作为大脑，当前选中的「{_local_model}」是垂直专用模型（不能对话）。\n\n"
                            f"请到「设置 → AI模型 → 📋 全部模型」切换一个通用模型（如 qwen2.5:14b / deepseek-r1:14b），再开启本地双模。"
                        )
                    })
                    logger.warning(f"[Engine] 本地双模: 当前模型 {_local_model} 非通用模型，中止")
                    return
                
                yield ("notification", {
                    "type": "collab_local_dual",
                    "message": (
                        "🏠 本地双模协作已启用\n\n"
                        f"大脑：{_local_model}（本地通用模型）\n"
                        "工具：MusicGen / Bark / Stable Diffusion 等垂直模型\n\n"
                        "本地通用模型将作为调度中心，协调垂直模型执行具体操作。"
                    )
                })
                enhanced_context += (
                    "\n\n## 🏠 本地双模协作模式\n"
                    "你是哈文的本地调度大脑。用户可能请求音乐生成、语音合成、图片生成等垂直操作。\n\n"
                    "## ⚠️ 关键规则（必须遵守）\n"
                    "1. **禁止自己写代码调用垂直模型**——不要生成 `os.startfile('skills/...')`、`import musicgen`、"
                    "`subprocess.run(...)` 等试图直接启动模型的代码。这些代码会执行失败并报错。\n"
                    "2. **垂直模型由系统的 vertical_api 统一调度**，会通过前端的垂直面板自动处理。你只需要：\n"
                    "   - 理解用户意图（做什么、什么风格、什么参数）\n"
                    "   - 用自然语言告诉用户你的建议和分析\n"
                    "   - 系统会自动弹出垂直面板，用户确认后执行\n"
                    "3. **如果你的回答里包含 Python 代码，只能是以下情况**：\n"
                    "   - 打开已存在的文件（如播放刚生成的音频 `os.startfile('data/media/xxx.wav')`）\n"
                    "   - 文件操作（读写、列目录）\n"
                    "   - 绝对不能是启动/调用垂直模型的代码\n"
                    "4. 纯语言/知识类问题，正常回答即可。\n\n"
                    "## 正确的行为示例\n"
                    "- 用户说'做一首乡村音乐' → 你回复分析+建议，系统自动弹出MusicGen面板，用户点击执行\n"
                    "- 用户说'我要2分钟' → 你诚实说明MusicGen最长30秒，建议分段生成或接受30秒限制\n"
                    "- 用户说'打开刚才生成的音乐' → 你可以输出 `os.startfile('data/media/最新音乐.wav')`\n"
                    "- 用户说'忧伤' → 你反问'您是想做忧伤的音乐吗？还是想写文字/聊聊？'，不自己假设\n\n"
                    "## ⚠️ 意图模糊时禁止假设（必须遵守）\n"
                    "当用户输入只有情绪/风格词，没有动作词时（如'忧伤''乡村''欢快'）：\n"
                    "- 禁止自己假设用户要做什么\n"
                    "- 禁止自己生成代码调用垂直模型\n"
                    "- 应该反问用户'您想用XX做什么？做音乐/写文字/聊聊？'\n"
                    "- 等用户明确选择后再行动\n"
                )
                is_collab_skipped = True
                yield ("source", "🏠 本地双模 · 通用模型调度 + 垂直模型执行")
                logger.info(f"[Engine] 本地双模协作: Ollama={_local_model}")
        except Exception as e:
                logger.debug(f"[Engine] 协作模式跳过: {e}")

        # 非协作模式 / 协作不可用时 → 执行 Step 3 路由
        _route_error = None
        self._slot_switch_notice = None  # ★ #1310 每次发送重置换槽知情提示
        if not is_collab_skipped:
            # ★ 2026-08-25 P1: 四元组返回（新增 error_msg 四段式错误）
            use_external, model_label, model_id, _route_error = self._route_model_for_query(self._query_mode)
            logger.info(f"[Engine] 路由: mode={self._query_mode}, use_external={use_external}, label={model_label}, error={bool(_route_error)}")
        elif is_collab_skipped:
            # 协作模式：外脑已用，强制用 Ollama 整合
            logger.info(f"[Engine] 协作模式: 跳过路由，强制使用本地模型 整合")

        # ═══════════════════════════════════════════════════════════════
        # ★ 2026-08-25 P1: 严格路由错误（四段式：选择→状态→原因→指引）
        # 用户选择大于一切——不降级、不冒充、不自动切换，错误明确告知
        # ═══════════════════════════════════════════════════════════════
        if is_collab_skipped:
            # 协作模式已通过自己的验证流程（外脑成功 + Ollama在线），跳过此检查
            model_label = "本地模型"
            model_id = "ollama"
        elif _route_error:
            # ★ #1264（2026-10-29 定版拍板）：删除 #1251 代答特例——
            #   旧逻辑「主模型=HarvenEye + 外脑模式 → Eye 直接代答」违反铁律 0
            #   （页面上选外脑、回答却是本地，开发者 10-29 实锤）。当时为修手机端
            #   「没给出有效回答」的断裂体验，但该显示问题已由 #1256 拒答落库根治，
            #   特例失去存在理由。铁律 0（2026-10-25 死命令）优先：外脑模式下没选外脑
            #   → 一律明确拒答指引，绝不本地顶包。
            # ★ #1256：拒答引导也落库（此前只 yield error 不入库 → 手机/电脑会话记录缺这条回答，
            #   电脑端打开就是空白；落库后引导文案两边可见）
            _guide = f"❌ {_route_error}"
            try:
                save_message(self.conversation_id, "assistant", _guide,
                             metadata={"source": "⚠️ 路由提示"})
            except Exception as _se:
                logger.warning(f"[Engine] 路由提示入库失败: {_se}")
            yield ("error", _guide)
            logger.error(f"[Engine] 路由失败（严格模式不切换）: {_route_error[:80]}")
            return  # 严格模式：错误即停止，绝不用其他模型顶替

        # 外脑路由成功时的 thinking 提示 + 来源标识
        if self._query_mode == "direct" and use_external:
            yield ("thinking", f"🤖 外脑模式 · 正在连接 {model_label}...")
            # ★ #1310（1c 换槽知情）：发生自动换槽时必须让用户看见，不能只写日志
            if getattr(self, '_slot_switch_notice', None):
                yield ("thinking", self._slot_switch_notice)
                self._slot_switch_notice = None
            # ★ 关键：注入来源标识，让前端能显示模型来源
            self._reply_source = model_label  # ★ 2026-09-14 来源持久化
            yield ("model_source", {
                "model": model_id,
                "label": model_label,
                "is_external": True,
                "mode": "direct"
            })
        elif not use_external:
            if is_collab_skipped:
                # ★ 本地双模用本地模型名，云地协作用外脑名
                local_model_name = getattr(self.model, 'model', '本地模型')
                if self._collab_mode_override == 'local_dual':
                    collab_label = f"🏠 本地双模 · {local_model_name}"
                else:
                    # P3-2: 替代 'cm' in dir() 反模式
                    _cm_ref = locals().get('cm')
                    collab_model = getattr(_cm_ref, 'foreign_model', '外部模型') if _cm_ref else '外部模型'
                    collab_label = f"☁️ 协作 · {collab_model}"
                self._reply_source = collab_label  # ★ 2026-09-14 来源持久化
                yield ("model_source", {
                    "model": "collab",
                    "label": collab_label,
                    "is_external": False,
                    "mode": "collab"
                })
            else:
                if _agent_override_prompt and _agent_pref != "external":
                    self._reply_source = f"🧩 {registry._agents[_agent_matched]['name']} · 本地"  # ★ 2026-09-14
                    yield ("model_source", {
                        "model": _agent_matched,
                        "label": f"🧩 {registry._agents[_agent_matched]['name']} · 本地",
                        "is_external": False,
                        "mode": "agent"
                    })
                else:
                    # ★ 2026-09-15 用户反馈：本地标签带具体模型名（"🏠 本地 · qwen2.5:14b"，不再只写"本地模型"）
                    _local_name = ''
                    try:
                        _local_name = (getattr(self.model, 'model', '') or '').strip()
                    except Exception:
                        _local_name = ''
                    _loc_label = f"🏠 本地 · {_local_name}" if _local_name else "🏠 本地模型"
                    self._reply_source = _loc_label  # ★ 2026-09-14 来源持久化
                    yield ("model_source", {
                        "model": _local_name or "local",
                        "label": _loc_label,
                        "is_external": False,
                        "mode": self._query_mode
                    })

        # ===== 根据模式 + 模型路由构建不同的系统提示词 =====
        # ★ 恢复：direct 模式用极简 Prompt（无身份覆盖），避免外脑被本地模型 身份信息误导
        # 文档 v4.1 规则：
        # - direct 模式 → 极简提示词（不含"本地模型 驱动"等字眼，防止外脑误认身份）
        # - kb_only / 协作模式 → 完整 Harven 身份 Prompt
        use_minimal_prompt = (
            self._query_mode == "direct"
        ) and not is_collab_skipped

        # ★ 本地执行能力（所有模式共享，含Agent）—— 必须在Agent覆盖之前定义
        LOCAL_CAPABILITIES = """
## 💻 本地执行能力（你可以操作这台电脑！）

### 🛠️ 优先使用FC工具（重要！）
**当有FC工具可用时，必须优先调用FC工具，不要写Python代码！**
- 检查软件安装 → 调用 `check_software(name)` （不要写subprocess代码检查！）
- 安装CLI模块 → 调用 `cli_anything_install(software)`
- 执行CLI命令 → 调用 `cli_anything_execute(software, command)`
- 下载文件 → 调用 `download_file(url, save_path)`
- 读文件 → 调用 `read_file(path)`
- 写文件 → 调用 `write_file(path, content)`
- 搜索 → 调用 `web_search(query)` 或 `search_knowledge(query)`
- 获取时间 → 调用 `get_time()`
- 扫描系统 → 调用 `scan_system()`
- 安装Python包 → 调用 `install_package(name)`

### 🤐 回答纪律（2026-10-05 开发者定）
- **正文只写最终答案**。工具执行过程系统会以过程卡展示给用户，不要在回答里复述
  "我做了什么/我搜了几轮/第一步第二步"这类操作流水账。
- 查到的资料直接消化后给结论，引用来源一句话即可。

### 📝 写代码（仅当没有对应FC工具时）
**只有当没有FC工具能完成任务时，才写```python代码块。**
- 代码块会被系统自动执行，结果返回给你
- 可访问所有目录（C/D/E/F/G盘+用户家目录）
- 可运行系统命令：subprocess.run()
- 可安装Python包：subprocess.run(['pip','install','包名'])
- 可打开文件：os.startfile(路径)
- 可下载文件：urllib.request.urlretrieve()

## 📂 哈文项目目录结构（检测时用这些路径！）
```
H:\\HarvenAI/
├── web_app.py          ← 主程序入口（Flask）
├── config.yaml         ← 配置文件
├── src/core/engine.py  ← 对话引擎
├── src/core/tool_executor.py ← 代码执行器
├── src/core/builtin_tools.py ← FC工具
├── src/core/memory.py  ← 记忆系统
├── src/core/knowledge_base.py ← 知识库
├── src/models/database.py ← 数据库
├── data/sqlite/lingzhi.db ← SQLite数据库文件
├── data/knowledge/     ← 知识库文件
├── data/skills/        ← 可执行技能目录（含code_audit/accounting等）
├── data/panels/        ← UI面板目录
├── skills/             ← 纯提示词技能（.md文件）
├── templates/          ← HTML模板
├── static/             ← 静态资源（JS/CSS/图标）
└── plugins/            ← 插件目录
```

## 📦 已预装的库（不需要再安装！）
modelscope / openai / flask / faiss / requests / beautifulsoup4 /
faster-whisper / pillow / pypdf / playwright / apscheduler / litellm
当用户说"下载XX模型"时，直接用modelscope，不要说"请先安装modelscope"！

**⚠️ 限制（仅以下操作被禁止）：**
- rm -rf / del /S / format / shutdown / reboot（危险命令）
- ctypes / winreg / win32api / win32com（危险模块）
- 超时120秒的代码会被终止（下载类5分钟）
- 不要自己写requests搜索网页（系统有内置Bing搜索）

## ⚠️ 重要规则（最高优先级！）
- **优先用FC工具！** 有工具就调工具，不要写代码！
- **不要说"我无法操作电脑"——你完全可以！**
- **不要说"请运行上述代码"——FC工具会自动返回结果！**
- **不要说"请手动安装"——先检查有没有FC工具可用！**
- **不要说"请将输出告诉我"——系统已经自动把结果返回给你了！**
- **不要说"如果你希望我执行，请告诉我"——你应该直接执行！**
- **不要说"请先安装modelscope"——已经预装了！**
- **不要用CLI命令检查（modelscope --version）——用Python import检查！**

## ⚠️ 代码执行机制（必读！）
1. 你生成的```python代码块会被系统**自动提取并执行**
2. 执行结果会**自动返回给你**（作为system消息注入对话）
3. 你看到结果后**继续回答用户**
4. **不需要让用户运行代码** — 系统替你运行
5. **不需要让用户告诉你结果** — 系统替你把结果带回来
- **不要说"请先安装modelscope"——已经预装了！**
- **打开文件**（音频/图片/视频/PDF）→ 用 ```python 代码块 os.startfile(路径)（没有FC工具可替代）
- **安装模型** → 直接生成安装代码块执行，不要让用户手动操作
- 不要解释"我无法播放"——os.startfile() 会自动调用系统默认程序
- 注意：列目录用 list_dir 工具，读文件用 read_file 工具，不要写代码

## 📦 如何创建新 Skill
当用户说"帮我加个 XXX 功能"时，优先考虑创建一个 Skill。
路径为 skills/<技能名>.md，格式如下：
```python
import os
content = '''---
name: my_skill
description: 技能描述
triggers: [触发词]
---
执行说明
'''
with open(os.path.join('skills', 'my_skill.md'), 'w', encoding='utf-8') as f:
    f.write(content)
```
创建后系统会自动热加载。
"""

        # ★ Agent 覆盖：如果有 Agent 专属 prompt，优先使用
        if _agent_override_prompt:
            # ★ 2026-07-07: Agent prompt 也追加 LOCAL_CAPABILITIES，让Agent知道能操作电脑
            # ★ 2026-07-24: 追加当前安全模式状态
            _mode_info = ""
            try:
                from src.core.security_guard import get_permission_mode as _gpm
                _cur_mode = _gpm()
                if _cur_mode == 'full':
                    _mode_info = "\n\n## 🔓 当前运行模式：全权模式\n你有完全的系统操作权限，可以执行任何命令（包括安装软件、系统命令）。代码过滤器已关闭，你可以直接写代码执行。\n"
                else:
                    _mode_info = "\n\n## 🔒 当前运行模式：安全模式\n你只能使用FC工具和执行受限命令。不能写代码（会被过滤）。需要安装系统软件时，引导用户手动操作或建议切换到全权模式。\n"
            except Exception:
                pass
            system_prompt = _agent_override_prompt + "\n\n" + LOCAL_CAPABILITIES + _mode_info
            use_minimal_prompt = False  # Agent prompt 不追加额外能力描述
            # Agent 用外脑模式时的标识
            if _agent_pref == "external":
                self._reply_source = f"🧩 {registry._agents[_agent_matched]['name']} · 外脑"  # ★ 2026-09-14
                yield ("model_source", {
                    "model": _agent_matched,
                    "label": f"🧩 {registry._agents[_agent_matched]['name']} · 外脑",
                    "is_external": True,
                    "mode": "agent"
                })

        if use_minimal_prompt:
            # ★ 2026-07-06: 外脑极简提示词 + 哈文平台能力概览
            system_prompt = """## 行为准则
- 诚实、准确地回答问题
- 禁止生成色情、暴力、违法内容
- **关键规则：你是可以操作本地电脑的！你生成的```python代码块会被系统自动执行，结果返回给你。**

## ⚠️ 代码执行规则（最高优先级！必须遵守！）
- **你必须用```python开头和```结尾的代码块格式写代码！不要用其他格式！**
- **不要把代码当文字展示——代码必须在```python块里！**
- **系统只识别```python代码块并自动执行，纯文本代码不会被执行！**
- **不要说"请复制代码到Python环境运行"——系统会自动执行！**
- **不要说"请在终端执行xxx"——直接生成```python代码块！**
- **不要说"请手动运行"——你写的代码会被自动执行！**
- **不要说"请将输出告诉我"——系统已经自动把结果返回给你了！**
- **不要说"你可以将上述脚本保存并运行"——系统已经自动执行了！**
- **不要只描述代码要做什么——直接写出完整的```python代码块！**
- 你的```python代码块执行后，系统会自动把结果注入对话，你会看到结果并继续解读。

## 📂 项目目录结构（写代码检测时用这些路径！）
- 主程序入口：web_app.py（不是app.py！）
- 配置文件：config.yaml
- 对话引擎：src/core/engine.py
- 代码执行器：src/core/tool_executor.py
- 数据库：data/sqlite/lingzhi.db（不是data/memory！）
- 知识库：data/knowledge/
- 可执行技能：data/skills/（不是skill_scripts！）
- UI面板：data/panels/
- 纯提示词技能：skills/
- HTML模板：templates/
- 静态资源：static/
- 插件：plugins/

## 🏠 Harven AI 哈文 平台能力概览
你运行在 Harven AI 哈文 平台上，该平台有以下功能。当用户问"你有XX功能吗"或"XX是做什么的"时，基于以下列表回答：

**查询模式（两种，共享知识库+Skills+画像+习惯）：**
- 🏠本地模式：本地通用模型推理（Ollama是备选），数据不外传
- 🤖外脑模式：外部模型推理（DeepSeek/GLM/豆包等）
- 区别：只是通用推理模型不同，其他能力完全一样

**协作模式（独立开关）：**
- ☁️云地协作：外脑分析→调垂直参数→给建议→本地垂直模型执行
- 🏠本地双模：本地通用模型调度→调垂直参数→给建议→垂直模型执行
- 协作模式只调参数+给建议，不加知识库和Skills

1. **本地知识库**（三层架构）：上传文档→向量化→FAISS索引→语义搜索→注入回答
2. **代码执行**：编写Python代码→自动运行→返回结果（操控电脑的核心方式）
3. **文件管理**：读写本地文件、列出目录
4. **任务自动化**：帮用户下载安装软件（Docker/Ollama/Python等）
5. **专项培养**：给对话设定专职角色（翻译助手/代码审查员/周报生成器等）
6. **监控中心**：自动追踪关注话题的最新动态（从6个RSS源抓取匹配新闻）
7. **垂直模型**：多种专业垂直模型统称（已内置6种+动态扩展）
   - 已内置：🎵音乐生成 / 🎨图片生成 / 🔊语音合成 / 💻代码生成 / 📊数据分析 / 🌐文档翻译
   - ★ 垂直模型独立调用，不经过知识库/Skills/画像/习惯
8. **图片识别**：查看分析用户上传的图片（由用户选择的视觉模型处理，本地推荐 qwen3-vl:8b-instruct，也可配外脑视觉）
9. **联网搜索**：Bing内置+SearXNG可选增强，点🌐按钮或关键词触发
10. **模型安装**：对话驱动安装各种模型
    - 支持pip/HuggingFace/ModelScope(魔搭)/GitHub/Ollama五种安装方式
    - 说"帮我装MusicGen/装华佗模型/装个qwen模型"→哈文直接执行安装
    - ★ 你能直接生成代码执行安装，不需要让用户手动操作终端
11. **硬件检测**：检测电脑配置，推荐合适模型
12. **多模型管理**：配置多个外部模型API（DeepSeek/GLM/豆包等28家厂商）
13. **插件管理**：安装/管理扩展插件
14. **Skills系统**：创建自动化任务脚本（Skill Builder可AI生成）
15. **Agent智能体**：专项子角色（预置+自定义，Agent工坊注册）
16. **安全防护**（三层）：文件扫描（5层检查）/ 代码沙箱（Docker/子进程）/ 注入审查（LLM复核）
17. **杀毒扫描**：可挂载ClamAV第三方杀毒（设置→安全隐私）
18. **设备管理**：IoT设备驱动控制（MQTT/HTTP/WebSocket驱动）
19. **手机联动**：手机扫码连接哈文，远程对话
20. **IM连接**：企业微信/钉钉/飞书接入
21. **数据保险箱**：备份/恢复所有数据
22. **品牌定制**：自定义名称/图标/外观
23. **受控进化**：用户说"帮我加功能/改代码"→生成提案→用户审批→应用
24. **知识包**：整理好的知识库文档打包，可分享
    - 制作：用户说"打包知识包"→生成代码（shutil打包+metadata.yaml）
    - 安装：用户说"安装知识包"→生成代码（解压→导入知识库）
25. **Bundle（集合包）**：多个Skill+Agent+知识包的组合
    - 制作：用户说"创建Bundle"→生成代码（复制Skill/Agent/知识包+bundle.yaml+打包）
    - 安装：用户说"安装Bundle"→生成代码（解压→复制到各自目录→自动加载）
    - 安装后Skills/Agents/知识库自动生效，不需要额外操作

### ★ 模型安装（用快速命令！不要自己写代码！）
- 用户说"下载/安装XX模型" → 系统自动用内置工具执行（Function Calling）
- 不要搜索ModelScope！不要猜ID！不需要API Key！
- 不要让用户手动操作！直接调用工具执行！

**设置面板8个Tab**：①基本设置 ②模型管理 ③个人画像 ④外观定制 ⑤安全隐私 ⑥高级功能 ⑦插件管理 ⑧关于&连接

**⚠️ 关于安装模型/软件（最高优先级！）：**
当用户说"帮我装XX模型/下载XX模型"时，**必须直接生成```python代码块执行，绝对不要让用户手动操作！**
- ❌ 禁止说"请在终端执行xxx"/"请手动安装"/"请粘贴到Python环境"
- ✅ 直接生成代码块：subprocess.run / snapshot_download / ollama pull

### ★ 哈文内置API工具（优先使用，比自己写代码更可靠）：
当用户需要以下操作时，**优先调用API工具**（生成requests代码调用API）：
- 检查/扫描本地模型 → `requests.get('http://127.0.0.1:7000/api/system/scan')`
- 安装Hub模型 → `requests.post('http://127.0.0.1:7000/api/hub/install', json={'item_id':'xxx','name':'xxx','type':'skill'})`
- 执行安装命令 → `requests.post('http://127.0.0.1:7000/api/hub/execute', json={'command':'ollama pull qwen2.5:14b'})`
- **先看系统自检结果（上方已注入），已有模型直接用，不要重新下载！**
- ✅ 系统会自动执行你的代码块并返回结果

**⚠️ 关于"你之前下载/安装了XX吗"的问题：**
当用户问"你下载了XX吗/你安装了XX吗"时，**先检查本地目录再回答，不要直接说"没有"！**
- ✅ 生成代码检查：os.path.exists('~/.cache/modelscope/hub/models/')
- ✅ 生成代码检查：os.path.exists('~/.cache/huggingface/hub/')
- ✅ 检查后告诉用户实际结果（有就说有，没有就说没有）

**⚠️ 跨对话记忆（重要操作自动记录）：**
- 下载/安装模型/软件等重要操作会自动记录到长期记忆
- 新对话中用户问起时，你可以从记忆中知道之前做过什么
- 如果你不确定之前是否做过，先检查本地目录

""" + LOCAL_CAPABILITIES + VOICE_DISCIPLINE  # ★ #929-A5 外脑直连同样遵守语音播报铁律（所有模型一视同仁）

            # ★ 注入记忆系统描述 + 实际记忆内容（所有模式共享）
            system_prompt += """

## 🧠 本地记忆系统
你运行在 Harven AI 平台上，该平台有本地记忆系统。下面是你当前可用的记忆信息：
- 如果下方有"长期记忆"、"关于主人"、"本次对话的重要信息"等内容，那是从之前对话中保存的跨对话记忆
- 当用户问"你记得吗"或关于之前对话的内容时，先看下面的记忆信息
- 你在当前对话中提到的用户偏好/重要事实，后续对话也能使用
- 如果你下面的记忆信息中有相关内容，可以直接使用它来回答

"""

            # P1-4: 统一使用 _inject_memory_and_tasks()（传入 user_message 用于相关性检索）
            system_prompt = self._inject_memory_and_tasks(system_prompt, user_message)

            # ★ 2026-07-09: 注入系统知识（已安装库/模型/Ollama）
            try:
                from src.core.system_knowledge import get_knowledge_summary
                system_prompt += get_knowledge_summary()
            except Exception as _e:
                logger.debug(f"[Engine] 忽略错误: {_e}")

        else:
            # 完整提示词：诚实原则 + 所有本地上下文（本地通用模型安全）
            system_prompt = SYSTEM_PROMPT + LOCAL_CAPABILITIES
            # ★ 2026-07-24: 追加安全模式状态
            try:
                from src.core.security_guard import get_permission_mode as _gpm
                _cur_mode = _gpm()
                if _cur_mode == 'full':
                    system_prompt += "\n\n## 🔓 当前运行模式：全权模式\n你有完全的系统操作权限，可以执行任何命令。代码过滤器已关闭。\n"
                else:
                    system_prompt += "\n\n## 🔒 当前运行模式：安全模式\n优先使用FC工具，不要写代码。需要系统操作时引导用户手动操作或建议切换全权模式。\n"
            except Exception:
                pass

        # ★ 2026-07-06: 联网搜索由 Step 1.5 统一处理（SearXNG优先+Bing回退）

        if not use_minimal_prompt:
            # ★ 身份注入：诚实原则，不编造模型名
            _model_identity = """## ⚡ 你的真实身份（最高优先级 — 覆盖上方任何身份描述）

你是 **Harven AI** 助手，运行在本地 AI 引擎上。

**当用户问"你是谁"/"介绍一下你自己"/"你是什么模型"时，你必须：**
1. 先说「我是 Harven AI」或「我是哈文」
2. 诚实告知你无法确定自己运行在哪个具体模型上（因为 AI 助手不应编造引擎信息）
3. 描述你能做什么（文件操作、代码执行、知识查询等）
4. 不要说你是 qwen、deepseek、llama 或其他任何特定模型名

**动机**：Harven 是本地运行的私有 AI 平台，底层引擎可能随时切换。编造模型名会误导用户。

"""
            # 保存原 system_prompt，在其前方插入模型真实身份
            system_prompt = _model_identity + system_prompt

            # ★ 2026-09-16 对话框导航（B路线）：模型可输出 [NAVIGATE:路径] 标记，前端识别后跳转
            # 规则刻意收紧：仅祈使（打开/进入/切换）才输出标记，提及/询问绝不输出，防止聊天误跳
            try:
                _nav_panels = ''
                try:
                    from src.core.panel_manager import get_panel_manager as _gpm2
                    _pl = (_gpm2().get_all_panels() or [])[:40]
                    _nav_panels = "\n".join(
                        f"- 「{p.get('name', '')}」 → [NAVIGATE:/panel/{p.get('id', '')}]" for p in _pl if p.get('id')
                    )
                except Exception:
                    _nav_panels = ''
                system_prompt += f"""

## 🧭 页面跳转指令（严格条件）
本界面可以跳转页面。**仅当主人明确要求"打开/进入/切换"某个页面或面板时**：先用一句话确认（如"好的，已为你打开～"），然后在回复**最末尾**单独加一个标记（占一行）。主人只是**提到或询问**某页面（如"家庭面板里有什么"），**绝对不要**加标记，正常回答即可。
固定页面：面板中心 [NAVIGATE:/panels] · 知识库 [NAVIGATE:/knowledge] · 技能库 [NAVIGATE:/skills] · 任务 [NAVIGATE:/tasks] · 邮箱 [NAVIGATE:/mail] · HarvenHub [NAVIGATE:/hub] · 模型中心 [NAVIGATE:/models] · 设置 [NAVIGATE:/settings] · 运行状态 [NAVIGATE:/status] · 时间轴 [NAVIGATE:/timeline] · 创作工坊 [NAVIGATE:/studio] · 更多 [NAVIGATE:/more] · 家庭模板 [NAVIGATE:/space/family] · 公司模板 [NAVIGATE:/space/company] · 创作模板 [NAVIGATE:/space/create]
已安装面板（按名字对号入座，找不到就别加标记）：
{_nav_panels or '（暂无面板清单，不要输出面板标记）'}
"""
            except Exception as _e:
                logger.debug(f"[Engine] 导航提示注入失败: {_e}")

            # 注入专项培养系统提示词（只注入内容，不注入 JSON 输出指令）
            if self._specialization_prompt and self._specialization_prompt.strip():
                system_prompt += f"""

## 当前专项培养模式

{self._specialization_prompt}

请在当前模式下严格遵循以上规则回答。
"""

            # P1-4: 统一使用 _inject_memory_and_tasks() 替代重复的记忆+任务注入
            system_prompt = self._inject_memory_and_tasks(system_prompt, user_message)

            # 注入用户画像上下文（仅当用户问及自身信息时 — 节省 ~500 token）
            try:
                profile_keywords = ["我是谁", "我的信息", "了解我", "关于我", "我的画像",
                                    "我的偏好", "我的习惯", "我是什么", "你了解我吗",
                                    "what do you know about me", "who am i"]
                should_inject = any(
                    kw in user_message.lower() for kw in profile_keywords
                )
                if should_inject:
                    from src.core.user_profile import get_user_profile
                    profile_ctx = get_user_profile().build_profile_context()
                    if profile_ctx:
                        system_prompt += profile_ctx
                        logger.debug(f"[Engine] 注入用户画像（按需触发）")
                else:
                    logger.debug(f"[Engine] 跳过画像注入（非自身提问）")
            except Exception as e:
                logger.error(f"[Engine] 加载用户画像失败: {e}")

            # 注入自动画像分析摘要
            try:
                from src.core.profile_auto_updater import get_profile_updater
                auto_summary = get_profile_updater().get_profile_summary()
                if auto_summary.strip():
                    system_prompt += auto_summary
            except Exception as e:
                logger.debug(f"[Engine] 自动画像摘要跳过: {e}")

            # ★ L3 习惯上下文注入
            try:
                from src.core.habit_tracker import build_habits_context
                habit_ctx = build_habits_context()
                if habit_ctx:
                    system_prompt += habit_ctx
            except Exception as e:
                logger.debug(f"[Engine] 习惯上下文跳过: {e}")

        # ===== 所有模式共享：注入 Skills =====
        try:
            from src.core.skills_manager import get_skills_manager
            sm = get_skills_manager()
            skill_prompt = sm.get_skill_prompt(user_message)
            if skill_prompt:
                # ★ 检测匹配的技能是否有可执行脚本
                matched_skill = sm._last_matched_skill if hasattr(sm, '_last_matched_skill') else None
                script_instruction = ""
                if matched_skill and matched_skill.has_scripts:
                    script_instruction = f"""
⚠️ **此技能有可执行脚本** — 不要只是模拟/假装执行，请务必使用 `execute_skill_script` 工具真正调用脚本：
- 工具名: execute_skill_script
- skill_name: "{matched_skill.id.replace('file_', '')}"
- action: 根据用户意图选择合适的操作名
- params: 传递给脚本的参数字典

示例调用: execute_skill_script(skill_name="{matched_skill.id.replace('file_', '')}", action="add", params={{"amount": 50, "category": "买菜"}})
"""
                system_prompt += f"""
## 当前激活技能
{skill_prompt}
{script_instruction}
请在当前对话中严格遵循以上技能要求。
"""
                logger.info(f"[Engine] 注入技能提示词（{len(skill_prompt)}字）| mode={self._query_mode} | has_scripts={matched_skill and matched_skill.has_scripts}")
        except Exception as e:
            logger.debug(f"[Engine] 加载技能提示词失败: {e}")

        # ===== OpenClaw 风格 Skills JSON 工具注入（外脑/协作/自动模式）=====
        if self._query_mode in ("direct", "auto"):
            try:
                from src.core.collab_manager import get_collab_manager
                from src.core.skills_manager import get_skills_manager
                cm = get_collab_manager()
                sm = get_skills_manager()
                tools_prompt = cm.build_tools_prompt(sm, user_message)
                if tools_prompt:
                    system_prompt += tools_prompt
                # ★ 同时注入匹配技能的完整 Prompt 内容（让 AI 知道如何调用）
                try:
                    for s in sm.list_skills():
                        if not s.get('enabled', True):
                            continue
                        triggers = s.get('triggers', [])
                        if triggers and not any(t.lower() in user_message.lower() for t in triggers if t.replace('.*', '')):
                            continue
                        body = s.get('body', '') or s.get('prompt', '')
                        if body:
                            system_prompt += f"\n\n## 技能详情：{s.get('name', s.get('id', ''))}\n{body}"
                except Exception as _e:
                    logger.debug(f"[Engine] 忽略错误: {_e}")
                logger.info(f"[Engine] 注入 Skills JSON 工具描述（OpenClaw风格）| mode={self._query_mode}")
            except Exception as e:
                logger.debug(f"[Engine] Skills JSON 工具注入跳过: {e}")

        # ===== 所有模式共享：注入脚本Skill列表（让模型知道有哪些可执行技能）=====
        # ★ 2026-10-20 第四批4.1 Skill目录式：默认只注入「名+一句话+触发词」省 token；
        #   命中触发词的技能才展开动作清单+调用方式（装 20 个技能不再每消息多几千 token）
        try:
            from src.core.scripts_runner import list_skill_scripts
            from pathlib import Path as _Path
            script_skills = list_skill_scripts()
            if script_skills:
                skills_brief = ""       # 目录：名+一句话+触发词
                skills_hit = ""         # 命中：展开动作+用法
                _msg_l = (user_message or '').lower()
                for ss in script_skills:
                    sname = ss.get('skill_name', '')
                    skill_md = Path(__file__).parent.parent.parent / 'data' / 'skills' / sname / 'SKILL.md'
                    desc = ''
                    triggers = ''
                    if skill_md.exists():
                        try:
                            md_content = skill_md.read_text(encoding='utf-8')
                            if md_content.startswith('---'):
                                end = md_content.find('---', 3)
                                if end > 0:
                                    yaml_part = md_content[3:end]
                                    for yl in yaml_part.split('\n'):
                                        yl = yl.strip()
                                        if yl.startswith('description:'):
                                            desc = yl.split(':', 1)[1].strip().strip('"\'')
                                        elif yl.startswith('trigger:'):
                                            triggers = yl.split(':', 1)[1].strip()
                        except Exception as _e:
                            logger.debug(f"[Engine] 忽略错误: {_e}")
                    skills_brief += f"- {sname}"
                    if desc:
                        skills_brief += f"：{desc}"
                    if triggers:
                        skills_brief += f"（触发词：{triggers}）"
                    skills_brief += "\n"
                    # 命中判定：触发词出现在用户消息里
                    _hit = triggers and any(
                        t.strip() and t.strip().lower() in _msg_l
                        for t in triggers.replace('，', ',').split(',') if t.strip())
                    if _hit:
                        actions = [a.get('name', '') for a in ss.get('actions', [])]
                        skills_hit += f"\n### 命中技能：{sname}（{desc}）\n"
                        if actions:
                            skills_hit += f"可用动作：{', '.join(actions)}\n"
                        skills_hit += f"调用：execute_skill_script(skill_name=\"{sname}\", action=\"动作名\", params={{}})\n"

                system_prompt += f"""

## 🔧 可执行技能目录（Script Skills）
{skills_brief}
"""
                if skills_hit:
                    system_prompt += f"""
### 本条消息命中的技能（展开用法，优先用它，不要自己写代码）：
{skills_hit}
调用方式：execute_skill_script(skill_name="技能名", action="该技能可用动作里的一个", params={{}})
（★ action 必须从命中技能的"可用动作"里选）
其他未命中技能的动作清单可用 tool_schema(skill_name="execute_skill_script") 查询。
"""
                else:
                    system_prompt += """
（用户请求若明确匹配以上某技能的触发词，用 execute_skill_script(skill_name, action, params) 调用；动作清单可用 tool_schema 查询。）
"""
                logger.info(f"[Engine] 注入 {len(script_skills)} 个脚本Skill目录（目录式，命中展开 {skills_hit.count('命中技能')} 个）")
        except Exception as e:
            logger.debug(f"[Engine] 脚本Skill注入跳过: {e}")

        # ===== 所有模式共享：注入增强上下文（知识库/GitHub/搜索结果）=====
        # ★ 2026-10-22 教练层·环境简报注入：让 AI「记得」全家录入态势（本地统计~200字，0额外调用）
        try:
            from src.core.coach_engine import daily_brief, COACH_RULES as _CR
            _brief_parts = []
            _b = daily_brief()
            if _b.get('items'):
                _brief_parts.append('今日待办：' + '；'.join(i['text'] for i in _b['items'][:4]))
            for _pid, _fn in _CR.items():
                try:
                    _o = _fn()
                    if _o.get('text'):
                        _brief_parts.append(f'[{_pid}] ' + str(_o['text'])[:70])
                except Exception:
                    pass
            if _brief_parts:
                enhanced_context += ('\n\n## 🧑‍🏫 教练环境简报（本地实时统计，供参考不必复述）\n'
                                     + '\n'.join(_brief_parts))
        except Exception:
            pass
        # ★ 2026-10-21 速度包：菜谱/菜单类请求——直接注入冰箱库存快照，让外脑一轮出菜单
        #   （实锤：「海蛎子做面」走 query_panel 工具轮+生成轮=28s；库存前置后省掉工具往返）
        try:
            _recipe_kw = ('菜谱', '菜单', '做什么菜', '做个面', '做面', '做饭', '做什么吃的', '怎么做好吃', '菜怎么', '食谱')
            if user_message and any(k in user_message for k in _recipe_kw):
                from src.core.panel_manager import get_panel_manager as _gpm
                _fr = _gpm().query_panel_data('fridge_stock_1', None, 40, 0) or []
                if _fr:
                    _items = []
                    for _row in _fr[:40]:
                        _nm = _row.get('名称') or _row.get('物品') or ''
                        _qty = _row.get('数量') or _row.get('存量') or ''
                        _exp = str(_row.get('保质期') or _row.get('到期日') or '')[:10]
                        if _nm:
                            _items.append(f"{_nm}（{_qty}{'，' + _exp if _exp else ''}）")
                    if _items:
                        system_prompt += f"""

## 🧊 冰箱库存快照（{len(_items)} 项，已自动读取）
{ '；'.join(_items) }

用户在要菜谱/菜单。请**直接基于以上库存**给建议，不要再调用 query_panel 工具（库存已在你面前）。
优先用快过期的食材；缺的食材明确列出「需要购买」。
"""
        except Exception as _recipe_e:
            logger.debug(f"[Engine] 菜谱库存注入跳过: {_recipe_e}")
        # ★ 2026-07-21: 方案C — 联网状态透明告知
        _web_search_on = getattr(self, '_web_search_this_query', False)
        if not _web_search_on and use_external:
            # 🌐没开 + 使用外脑
            _ext_has_net = _model_has_internet(model_id)
            if _ext_has_net:
                enhanced_context += (
                    "\n\n## 📊 联网状态告知\n"
                    f"哈文互联网搜索未开启（🌐按钮未点击）。当前外脑模型（{model_label}）自带联网能力，可自行查询实时信息。\n"
                    "请注意：外脑联网查询的数据来源由外脑模型决定，可能与哈文搜索引擎（Bing）结果不同。\n"
                )
                logger.info(f"[Engine] 🌐未开，但外脑 {model_id} 自带联网，已透明告知")
            else:
                enhanced_context += (
                    "\n\n## 📊 联网状态告知\n"
                    "哈文互联网搜索未开启（🌐按钮未点击）。当前外脑模型不具备联网能力，无法查询实时信息。\n"
                    "如果用户询问天气、新闻、股价等实时信息，请直接告知：\n"
                    "\"互联网搜索未开启，请点击右上角🌐按钮开启联网搜索。\"\n"
                    "不要尝试用代码访问外部网站（会被安全模块拦截）。\n"
                )
                logger.info(f"[Engine] 🌐未开，外脑 {model_id} 不联网，已注入提示")

        if enhanced_context.strip():
            # P2-2: 删除 _annotate_search_results 标注层，直接注入原始增强上下文
            system_prompt += f"""
## 本次查询的增强信息
{enhanced_context}

### ✅ 搜索结果使用规则
1. **搜索结果中的数据是真实的，必须直接使用**。搜索结果里的股价、气温、汇率等数值来自互联网，不是编造的。引用它们回答问题不违反诚实原则。
2. **搜索结果可能有几分钟延迟**，这完全可接受。回答时标注来源和时间即可（如"据新浪财经搜索结果显示，上证指数3754.60点"）。
3. **禁止自己写代码联网查询**。代码执行会被安全模块拦截。搜索结果就是你的数据来源。
4. **只有当搜索结果中完全没有相关数据时**，才说"搜索结果中未找到具体数据"。如果搜索结果的标题或摘要里有数字，那就是有效数据，直接用。
5. **引用来源**：回答时标注数据来自哪个搜索结果。

### 🚫 禁止行为
1. 禁止在回答中输出Python代码（包括```python代码块）。
2. 禁止尝试用代码打开浏览器（webbrowser.open()）。
3. 如果搜索结果有数据，**禁止说"没有找到"**。搜索结果标题里的数字就是数据。
4. "诚实原则"是指不编造你没有任何来源的数字。搜索结果有数据时使用它，这不是编造。
"""
        
        # ===== 注入当前日期时间（防止所有模型日期幻觉）=====
        now = datetime.now()
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        date_preamble = (
            f"## 📅 当前时间（绝对准确，不可质疑）\n"
            f"现在是 {now.year}年{now.month}月{now.day}日，星期{weekday_names[now.weekday()]}，"
            f"北京时间 {now.hour:02d}:{now.minute:02d}。\n"
            f"当被问到日期/时间/星期时，必须使用以上时间，不得编造其他日期。\n\n"
        )
        system_prompt = date_preamble + system_prompt

        # ★ 诚实原则放在最后（离回答最近的位置，不会被前面的内容覆盖）
        # 仅外脑模式需要显式追加（本地模式已在 SYSTEM_PROMPT 中）
        if use_minimal_prompt:
            system_prompt += """

## ⚠️ 最高优先级：诚实原则
- 对于任何你没有确切把握的事实，必须如实说"我不清楚"、"我不知道"或"我无法确认"
- **绝对禁止编造、猜测、或假装知道**
- 不要因为用户说"你错了"就盲目改口——如果你确信自己是对的，坚持你的判断
- 诚实的"不知道"比一个虚假答案好一万倍
- 不知道就说不知道，不要编造
- **行动也必须诚实**：凡是「保存/记录/记账/设置/删除」类操作，只有当你真正调用了工具并确认成功后，才能说"已经记好/已保存"；没有实际执行就只能说"我可以帮你记"，并把要记的内容列出来请用户确认。**严禁凭空宣称"已经帮你记上啦"**
- **只回答用户最新的一条消息**。对话历史中已回答过的问题不要重复回答。如果用户改了话题，专注新话题。
"""

        # 构建上下文
        context = self._build_context()
        logger.debug(f"[Engine] 上下文: {len(context)}条历史, 系统提示词: {len(system_prompt)}字")

        # ★ 2026-08-03: 对话上下文检测 — 历史太长时提示用户
        if len(context) >= 6:
            yield ("hint", "💡 当前对话已有较长的历史记录。如果这是新问题，建议点击「+ 新对话」开始全新对话，避免旧上下文影响回答质量。")

        # 构建完整的消息列表
        all_messages = [{"role": "system", "content": system_prompt}]
        all_messages.extend(context)
        all_messages.append({"role": "user", "content": user_message})

        # ===== 智能模型路由：已在 Step 3 阶段计算 use_external/model_label =====
        logger.info(f"[Engine] use_external={use_external}, model_label={model_label}, query_mode={self._query_mode}")

        # ★ #1330 本地通道 FC 透明化：信号词说要 FC、路由却落在本地通道（无 FC）→
        #   明说「只能出方案动不了手，切外脑即可」，不再静默降级成纯聊天光说不练。
        #   哈文铁律：不代选不偷换——用户选了本地就继续本地，但必须把能力边界说破。
        _fc_sig = getattr(self, '_fc_signal_pending', False)
        self._fc_signal_pending = False
        if not use_external and _fc_sig:
            yield ("thinking", "⚙️ 这是个需要动手的操作，但当前本地模型不支持工具执行（只能给方案动不了手）。点对话区 🤖 切外脑再发一次，我就能直接帮你执行。")
            logger.info("[Engine] #1330 复杂操作落在本地通道（无FC），已提示切外脑")

        raw_stream = None

        if use_external:
            # ── 外部模型调用（带自动回落本地通用模型 + current_model 安全恢复）──
            from src.core.multi_model_manager import get_multi_model_manager
            mm = get_multi_model_manager()
            # 保存旧模型，只在内存中切换，不持久化到磁盘
            old_model = mm.current_model
            mm.current_model = model_id
            # 注意：不修改 mm.config['current_model'] — 那是持久化配置，
            # 外脑临时切换不应写到磁盘。只有用户主动在设置中选择才持久化。
            logger.info(f"[Engine] 使用外部模型: {model_id} ({model_label}), 原current_model={old_model}")
            try:
                # ★ 2026-07-11: Function Calling — 检测外脑是否支持
                # ★ 2026-07-12: 协作模式开启时 → 完全禁用FC（不传tools，不加FC提示词）
                from src.core.builtin_tools import get_tool_definitions, is_function_calling_supported, execute_tool_call, get_fc_bundle
                import json as _json

                # 检查协作模式
                _collab_val = self._collab_mode_override
                if _collab_val is None:
                    try:
                        _collab_val = get_preference("collab_mode") or "off"
                    except Exception:
                        _collab_val = "off"

                fc_tools = None
                _tool_catalog_text = ''
                if is_function_calling_supported(model_id) and (_collab_val == "off" or not _collab_val):
                    # ★ 只在非协作模式时启用FC
                    # ★ A批A3(2026-09-13): 目录式注入 — 常驻高频工具全量 + 其余只进"名+一句话"目录
                    # ★ 方案A 联网授权制（2026-10-05 定版拍板）：联网工具按🌐开关注入——
                    #   未授权时 web_search/web_fetch 不进常驻清单也不进目录，模型无从自主联网
                    fc_tools, _tool_catalog_text = get_fc_bundle(
                        allow_web_search=bool(getattr(self, '_web_search_this_query', False)))
                    # ★ 2026-09-27 #885 E1 纯聊天快车道（已批准）：闲聊/角色扮演/介绍解释类
                    #   跳过首轮非流式检测（省一整轮全文生成等待），直接走下方流式分支；
                    #   数据/操作类问题不受影响，照常走 FC
                    if fc_tools and self._fc_pure_chat_skip(user_message):
                        fc_tools = None
                        logger.info("[Engine] 纯聊天快车道：判定为闲聊/介绍类，跳过FC首轮非流式，直接流式")
                    else:
                        logger.info(f"[Engine] ★★★ Function Calling 已启用(目录式), {len(fc_tools)}个常驻工具+目录 (协作模式={_collab_val}) ★★★")
                else:
                    if _collab_val and _collab_val != "off":
                        logger.info(f"[Engine] 协作模式({ _collab_val }) → FC完全禁用，走协作流程")
                    else:
                        logger.info(f"[Engine] FC未启用: supported={is_function_calling_supported(model_id)}, collab={_collab_val}")

                # 先做一轮非流式调用（检测tool_calls）
                if fc_tools:
                    # ★ 2026-08-13: 强化FC提示词 — 必须调工具，禁止写代码
                    # ★ 2026-08-16: i18n — 按用户消息语言选提示词（含CJK用中文版，否则英文版）
                    # ★ 2026-09-26 语言判定修复（实测实锤冒英文）：单句全是文件名/路径时判不出，
                    #   扩到最近对话历史，仍判不出默认中文（哈文是中文产品）
                    _fc_messages = list(all_messages)
                    _has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in (user_message or ''))
                    if not _has_cjk:
                        for _hm in reversed(all_messages[-6:]):
                            _hc = _hm.get('content', '') if isinstance(_hm, dict) else ''
                            if isinstance(_hc, str) and any('\u4e00' <= ch <= '\u9fff' for ch in _hc):
                                _has_cjk = True
                                break
                    if _has_cjk:
                        _fc_hint = (
                            "\n★★★ FC已启用 ★★★\n"
                            "1. 优先调工具，不写代码。路径示例：\"G盘虚拟飞行\"→path=\"G:\\\\虚拟飞行\"\n"
                            "2. 工具映射：检查目录→list_dir | 读文件→read_file | 删除→delete_path | 执行命令→execute_command | 查软件→check_software | 查面板档案记录→query_panel（用户找自己存过的信息如执照/物品位置时用，不要爬目录搜文件；查账目可传 time_range=今天/本月/近7天）\n"
                            "   ★ 查账/花销类问题（\"今天花了多少钱\"\"本月账单\"）：若上下文已给出【档案中枢·已查到的真实数据】，直接用那份数据用人话回答，禁止再调任何工具、禁止输出 JSON 原文。没有预查数据时才调 query_panel(time_range=…)\n"
                            "   记账（如\"47.6 炸酱面\"\"买咖啡50\"）→skill_script(action=\"execute\", skill_name=\"accounting\", script_action=\"main\", params={\"action\":\"add\",\"amount\":金额数字,\"category\":\"餐饮/买菜/交通/学习/其他\",\"note\":\"项目名\"})——参数已给全，禁止再调 tool_schema 查它\n"
                            "   2.5 ★ 用户报健身/运动完成情况（\"今晚练的是腿\"\"今天跑步5公里\"）→ add_panel_record(panel_name=\"健身记录\", fields={\"日期\":\"今天\",\"运动项目\":\"练腿\",…时长/强度有就填})——健身数据要进面板台账才出统计，禁止只塞随身记了事；字段名不确定先 edit_panel(action=\"list\") 查。★带「成员」列的面板（健身/餐饮/健康等）fields 必带 成员=谁（用户说了名字填名字；没说就留空不填成员列，不要猜）。健身计划提醒（\"后天提醒我练胸\"）→ set_reminder(title=\"练胸\", remind_at=解析时间, module=\"fitness\")\n"
                            "3. 多步操作：拿到结果后继续调工具，直到任务完成\n"
                            "4. 清空回收站→execute_command(\"powershell -Command Clear-RecycleBin -Force\")\n"
                            "   验证：list_dir(C:\\\\$Recycle.Bin)——SID文件夹还在是正常的，里面没文件=已清空\n"
                            "5. 卸载→execute_command(\"winget uninstall XX\")→验证check_software\n"
                            "6. 不要动注册表！不要reg delete！不要rd /s /q系统目录！\n"
                            "7. 不解释，直接调工具。调完所有工具后用中文总结\n"
                            "7.5 ★★ 工具调用纪律（省时铁律）：需要查数据/执行操作时，**第一轮就直接调工具**，严禁第一轮先写解释性文字（如「让我帮您查一下」）——每多一轮就多等 10 秒。同类查询能一次调完就一次调完（可并行多个工具），不要拆成多轮慢慢调\n"
                            "7.6 ★★ 并行调用铁律（#1290 省时）：互相不依赖的多个查证/操作（如查端口+查进程+读配置+查数据库），必须在**同一轮里一次返回多个 tool_calls** 并行发起，严禁一轮只调一个工具分四五轮慢慢查——每多一轮用户就多等 5~10 秒。只有下一步的参数依赖上一步结果时才允许串行。另外：上下文里若已给出预查数据（【自检快照】/【档案中枢】/【本机档案预查】等），直接引用作答，相关调查类工具一律禁调\n"
                            "8. 回复用中文，不输出JSON原始数据\n"
                            "9. ★ 介绍/解释/咨询类问题（如「你有什么技能」「XX是什么」「怎么用」）直接凭已有知识回答，禁止为了回答去 list_dir/read_file 翻文件——只有用户明确要查看/操作具体文件时才用文件工具\n"
                            "10. ★★ 消息里含【文件:…】内容或「系统注」说明时：分析直接基于给出的内容作答，严禁为寻找该文件去 list_dir/execute_command 全盘搜索（Downloads/Desktop/H盘 等一律禁止）——文件由用户上传，不在硬盘其他位置，搜也搜不到\n"
                            "11. ★★ 找图片/照片类请求（包括生活照：自拍/风景/美食/家人/宠物/截图，如「找上次拍的晚饭」「孩子笑的那张照片」）：直接一步调 search_images 工具（哈文图库总账：手机拍照/聊天发图/面板附件/知识库图片全在里面），不要先查工具参数、不要浏览目录、禁止用 list_dir/execute_command/python 代码去任何磁盘目录翻找图片文件（C盘/D盘/G盘/H盘一律禁止）——图库没有就翻不到，只会浪费时间。搜不到就诚实说「图库里没有这个描述的照片」，并建议用户到「记忆相册」页面找或补充描述。找到后在回答里用 Markdown 图片语法展示：![照片](返回的url)——前端会自动渲染成可看的图\n"
                            "12. ★★ 绝不输出 tool_calls JSON：想调工具就用真正的工具通道；没有合适的工具就用文字说明，把 {\"tool_calls\":…} 写进回答属于严重错误，用户会看到一坨代码\n"
                            "13. ★ 用户想打开/进入某个板块或页面时（如「打开图片库」「带我去知识库」「看看记账面板」「我要看照片」）：先简短回应，然后在回答末尾单独一行输出跳转标记：【打开:/photos】。可用页面：/photos=图片库（总存放地）、/memory=记忆相册（生活照/自拍/风景/美食，一句话找照片+AI修图）、/panels=面板列表（不确定面板ID时跳这个）、/knowledge=知识库、/skills=技能库、/tasks=任务、/mail=邮箱、/models=模型中心、/settings=设置、/status=运行状态、/timeline=时间轴、/hub=市场。常用面板直达（ID必须一字不差）：/panel/accounting_daily_1=记账、/panel/home_calendar=日历待办、/panel/home_items=物品存放、/panel/fridge_stock_1=冰箱、/panel/home_docs=证件与保单、/panel/study_center=孩子学习、/panel/kid_archive=孩子档案、/panel/health_file=健康档案、/panel/fitness_log=健身、/panel/med_cabinet=药箱、/panel/meal_diary=餐饮、/panel/elder_care=老人关怀、/panel/car_maintenance_1=车辆保养、/panel/memory_album=生活记录。标记必须严格用【打开:页面路径】格式；前端会渲染成开门按钮，用户点了就跳。上下文/工具结果里出现过的 panel_id 优先直接用。★ 用户找生活照片时：先调 search_images 展示找到的图，再在末尾附【打开:/memory】方便进相册细看\n"
                            "14. ★★ 落库纪律（双向铁律，2026-10-25 实锤双向乌龙）：①执行过写入工具（add_inbox_note/add_panel_record/task_create/update_panel_record 等）后，必须按工具返回的 JSON 如实回执——存到了哪（随身记/面板/任务）、提醒时间多少；禁止只说「已记好」；②用户问「记到哪了/存了没」：必须调 query_my_records 查证后回答，禁止只调 query_panel 查面板就断言「没落库」（随身记/提醒/任务都不在面板表里，查面板为空≠没落库）；③没调任何写入工具时严禁说「已记好/已保存」；④返回里带「没有重复记录/已存在」时如实转告用户（说明刚记过，不用再记）\n"
                            "15. ★★ 提醒归位（开发者 10-25 拍板：能进模块的不进随身记）：带明确时间的「X时间提醒我Y」→ set_reminder(title=Y, remind_at=解析时间, module=按域：健身/运动→fitness、食材/过期/冰箱→fridge、物品/晾晒/换季/保养→items、孩子/作业/接送→kid、日程→calendar、其他→通用)，禁止塞随身记；只有真正的琐事/想法（如「记一下那个新食谱」无时间）才 add_inbox_note。混合句拆开各落各家（练腿→add_panel_record + 提醒练胸→set_reminder）\n"
                            "16. ★★ 反吹牛铁律 + 相册动手（2026-11-12 实锤：模型两次谎报「已建相册已归类」实际什么都没做）：①只有工具返回 success=true 才能对用户说「已完成/已创建/已归类」——工具报错、返回空、没找到，都必须如实说失败原因；**没有对应工具的请求，明说「这个我现在办不了」并给替代路径**，严禁编造成功假象。②建相册/移照片/把某人的照片归一起 → manage_album 工具动手：action=list 列相册 | create 建相册 | move 移照片（先 search_images 拿 id）| move_faces 人脸归册（person=家人名字，本地人脸识别全库扫后自动归册）。照片归类类请求必须调本工具，禁止只动嘴\n"
                        )
                    else:
                        _fc_hint = (
                            "\n★★★ FC ENABLED ★★★\n"
                            "1. Prefer calling tools over writing code. Path example: \"D drive SteamLibrary\" → path=\"D:\\\\SteamLibrary\"\n"
                            "2. Tool mapping: list directory→list_dir | read file→read_file | delete→delete_path | run command→execute_command | check software→check_software | query panel records→query_panel (use when user looks for their own saved info like licenses/item locations; never crawl the filesystem; pass time_range=today/this month/last 7 days for account queries)\n"
                            "   ★ Account/spending questions (\"how much did I spend today\"): if the context already contains [Archive Hub] real data, answer from it in natural language — do NOT call any tool, do NOT output raw JSON. Only call query_panel(time_range=…) when no hub data is present\n"
                            "   Log expense (e.g. \"47.6 lunch\")→skill_script(action=\"execute\", skill_name=\"accounting\", script_action=\"main\", params={\"action\":\"add\",\"amount\":<number>,\"category\":\"food/transport/other\",\"note\":\"<item>\"}) — params given in full, do NOT call tool_schema to look it up\n"
                            "3. Multi-step: after each result, keep calling tools until the task is fully done\n"
                            "4. Empty recycle bin→execute_command(\"powershell -Command Clear-RecycleBin -Force\")\n"
                            "   Verify: list_dir(C:\\\\$Recycle.Bin)——SID folders remaining is normal; empty inside = cleaned\n"
                            "5. Uninstall→execute_command(\"winget uninstall XX\")→verify with check_software\n"
                            "6. NEVER touch the registry! No reg delete! No rd /s /q on system directories!\n"
                            "7. Do not explain — call tools directly. After all tools are done, summarize in English\n"
                            "8. Reply in English. Do not output raw JSON data\n"
                            "9. ★ For intro/explanation/consultation questions (e.g. \"what skills do you have\", \"what is X\", \"how to use\"), answer directly from knowledge — do NOT list_dir/read_file just to answer. Only use file tools when the user explicitly asks to view/operate on specific files\n"
                            "10. ★★ When the message contains [File: ...] content or [System note]: analyze the given content directly. NEVER search the whole disk (Downloads/Desktop/H drive etc.) via list_dir/execute_command to find that file — it was uploaded by the user and does not exist elsewhere on disk\n"
                            "11. ★★ NO-BOAST RULE: only say \"done/created/moved\" when the tool returned success=true. If a tool failed, returned empty, or no suitable tool exists, say so honestly and offer an alternative — NEVER fabricate success. For album requests (create album / move photos / group photos of a family member) call the manage_album tool (action=list/create/move/move_faces) — never claim you organized photos without calling it\n"
                        )
                    # ★ A批A3: 目录追加 — 非常驻工具只给名+一句话，用时先 tool_schema 拿参数
                    if _tool_catalog_text:
                        _catalog_hint = (
                            "\n9. 其他工具目录（要用目录里的工具时，先调 tool_schema 拿参数定义，再调用目标工具）：\n"
                            + _tool_catalog_text
                        )
                    else:
                        _catalog_hint = ''
                    if _fc_messages and _fc_messages[0].get('role') == 'system':
                        _fc_messages[0]['content'] = _fc_hint + _catalog_hint + "\n\n" + _fc_messages[0]['content']
                    else:
                        _fc_messages.insert(0, {'role': 'system', 'content': (_fc_hint + _catalog_hint).strip()})

                    # ★ 2026-08-14: FC循环检测（★ #1329：上限 5→10——搬家类多步任务实锤 5 轮不够，
                    #   被指断在半路；配合收尾不传 tools 强制总结，超长任务也有真回答）
                    _fc_max_rounds = 10
                    _fc_done = False
                    _fc_final = ""  # ★ #1075 FC 最终回答留存（收笔落库用）
                    _fc_executed = []  # ★ #1329 执行足迹（工具名×次数，收尾汇报用）
                    # ★ 2026-10-20 FC慢治理#4: 连续失败计数 — 连败2轮注入收口提示（最坏5轮→3轮）
                    _fc_fail_streak = 0
                    _fc_fail_hinted = False
                    # ★ #1212 空转收口（10-25 实锤：问管家第2/3轮「解释而非调工具」白耗 17 秒）：
                    #   解释空转最多 1 次，第 2 次直接把解释文本当最终回答收笔，不再喂下去
                    _fc_explain_streak = 0
                    # ★ A批A7: token账本 — 记FC轮次与注入体积
                    try:
                        from src.core.token_ledger import record as _tl_record
                        _tl_record('fc_turns', tools=len(fc_tools), catalog_chars=len(_tool_catalog_text))
                    except Exception:
                        _tl_record = None
                    for _fc_round in range(_fc_max_rounds):
                        logger.info(f"[Engine] ★★★ FC第{_fc_round+1}轮调用, 消息数={len(all_messages)}, tools={len(fc_tools)}")
                        # ★ #1212 心跳保活：FC 每轮都是非流式全文等待（6-10 秒零字节），
                        #   先吐一条 thinking 占位，防手机端 SSE 长静默被网络层捏断
                        yield ("thinking", f"🔎 第{_fc_round+1}轮查询中…")
                        try:
                            _fc_resp_gen = mm.generate_response(list(all_messages), stream=False, tools=fc_tools)
                            _fc_full = ""
                            _fc_tcs = None
                            for _fc_chunk in _fc_resp_gen:
                                if isinstance(_fc_chunk, dict):
                                    if _fc_chunk.get('type') in ('text', 'content'):
                                        _fc_full += _fc_chunk.get('content', '')
                                    elif _fc_chunk.get('type') == 'tool_calls':
                                        _fc_tcs = _fc_chunk.get('content')
                                        logger.info(f"[Engine] ★ FC第{_fc_round+1}轮返回tool_calls: {len(_fc_tcs) if _fc_tcs else 0}个")
                                    elif _fc_chunk.get('type') == 'error':
                                        logger.error(f"[Engine] ★ FC第{_fc_round+1}轮错误: {_fc_chunk.get('content', '')[:200]}")
                                        _fc_full += _fc_chunk.get('content', '')
                        except Exception as _fc_e:
                            logger.error(f"[Engine] ★ FC第{_fc_round+1}轮异常: {_fc_e}")
                            _fc_tcs = None
                            _fc_full = f"调用失败: {_fc_e}"

                        if not _fc_tcs:
                            # ★ 2026-08-14: 检测AI是在"解释"还是"总结"
                            # ★ 2026-08-16: i18n — 英文解释性话语同样继续循环
                            _is_explaining = any(k in _fc_full for k in [
                                '我先', '我来', '让我', '我先查看', '我来帮你', '我先看', '让我先', '我需要先',
                                'let me', "i'll", 'i will', 'i am going to', 'first,', 'i would',
                            ])
                            if _is_explaining and _fc_round < _fc_max_rounds - 1:
                                _fc_explain_streak += 1
                                if _fc_explain_streak >= 2:
                                    # ★ #1212 空转收口：连解释 2 轮说明模型不会再调工具了，直接收笔（省 10-20 秒）
                                    logger.info(f"[Engine] ★ FC第{_fc_round+1}轮解释空转第{_fc_explain_streak}次，提前收笔")
                                    if _fc_full:
                                        _fc_full = self._defuse_raw_json(_fc_full)
                                        _fc_final = _fc_full
                                        yield ("text", _fc_full)
                                        _fc_done = True
                                    break
                                # AI在解释，不是总结 → 不加user消息（避免污染历史），直接继续循环
                                logger.info(f"[Engine] ★ FC第{_fc_round+1}轮AI在解释而非调工具，继续循环（不污染历史）")
                                # 只加assistant消息，不加user消息
                                all_messages.append({"role": "assistant", "content": _fc_full})
                                continue
                            # 真的在总结 → 输出
                            logger.info(f"[Engine] ★ FC第{_fc_round+1}轮无工具调用，输出最终回答 ({len(_fc_full)}字)")
                            # ★ 2026-10-11 #1086 缩进根治：收笔输出必须只在「无工具调用」分支内。
                            #   原先 if _fc_full+break 写在分支外 → 无条件 break，工具执行段成死代码；
                            #   GLM tool_calls+思考文本同返时（实锤 17:16 手机「找家人照片」），
                            #   283 字英文推理被当最终回答 yield 并落库，工具从未执行。
                            if _fc_full:
                                # ★ 2026-09-27 #886 B JSON回炉保底：外脑偷懒复读工具生 JSON 时本地翻译
                                _fc_full = self._defuse_raw_json(_fc_full)
                                _fc_final = _fc_full  # ★ #1075 留存最终回答
                                yield ("text", _fc_full)
                                _fc_done = True
                            break
                        # 有 tool_calls → 不收笔，落到下方「执行工具调用」段，执行完继续下一轮
                        yield ("thinking", f"正在执行工具 (第{_fc_round+1}轮)...")
                        # 先加assistant消息（带tool_calls）
                        all_messages.append({"role": "assistant", "tool_calls": _fc_tcs})
                        # 执行每个工具并加tool消息
                        for _fc_tc in _fc_tcs:
                            _tc_name = _fc_tc.get('function', {}).get('name', '')
                            _tc_args = _fc_tc.get('function', {}).get('arguments', '{}')
                            _tc_id = _fc_tc.get('id', '')
                            logger.info(f"[Engine] ★ FC执行: {_tc_name}({_tc_args[:100]})")
                            _fc_executed.append(_tc_name)  # ★ #1329 执行足迹
                            _tc_result = execute_tool_call(_tc_name, _tc_args)

                            # ★ 2026-08-15: 危险工具日志记录（协作模式也记录）
                            # ★ 2026-10-20 FC慢治理#4: 降噪 — execute_command 每条都打
                            #   warning 淹没日志（echo ver 都标危险实锤）；仅真高危参数才 warning
                            _DANGER_TOOLS_FC = {
                                'delete_path', 'execute_command', 'email_send',
                                'write_file', 'edit_file', 'install_package',
                                'install_model', 'uninstall_model',
                                'hub_install',  # ★ #1212: 装外部内容，确认卡把关
                            }
                            if _tc_name in _DANGER_TOOLS_FC:
                                _tc_args_l = (_tc_args or '').lower()
                                _really_dangerous = _tc_name != 'execute_command' or any(
                                    _kw in _tc_args_l for _kw in
                                    ('rm -rf', 'format ', 'del /f', 'shutdown', 'reg delete',
                                     'reg add', 'diskpart', 'taskkill /f', 'curl -o', 'wget',
                                     'invoke-expression', 'iex ', 'dd if=')
                                )
                                if _really_dangerous:
                                    logger.warning(f"[Engine] ★★ 危险工具已执行: {_tc_name}({_tc_args[:100]}) — 协作模式={self._collab_mode_override}")
                                else:
                                    logger.debug(f"[Engine] 工具已执行: {_tc_name}({_tc_args[:80]})")

                            # yield结果给用户
                            _tc_parsed = _tc_result
                            if isinstance(_tc_parsed, str):
                                try:
                                    import json as _json_fc
                                    _tc_parsed = _json_fc.loads(_tc_parsed)
                                except:
                                    _tc_parsed = {}
                            if isinstance(_tc_parsed, dict):
                                # ★ #1212（2026-10-25）hub_install 确认卡：不阻塞 FC 循环，
                                #   先推 confirm_card 事件给前端弹卡，用户点头后走
                                #   POST /api/hub/install/confirm 才真下载安装（pending 10分钟 TTL）
                                if _tc_parsed.get('confirm_card'):
                                    yield ("confirm_card", _tc_parsed['confirm_card'])
                                # ★ #925 根治目录式工具死锁（2026-09-27 runtime.log 实锤）：
                                #   智谱只能调 tools 参数里带上的工具；模型按目录提示先调
                                #   tool_schema 拿定义，但查到的定义从未合并进 fc_tools →
                                #   下一轮依然发不出正式调用 → GLM 死循环查 schema（实锤连查
                                #   5 次 search_images 参数）后放弃去翻磁盘。修法：查到什么
                                #   工具，下一轮 tools 就带上什么。
                                if _tc_name == 'tool_schema':
                                    try:
                                        _have = {d['function']['name'] for d in fc_tools}
                                        _add = [d for d in (_tc_parsed.get('tools') or [])
                                                if isinstance(d, dict)
                                                and d.get('function', {}).get('name')
                                                and d['function']['name'] not in _have]
                                        if _add:
                                            fc_tools = list(fc_tools) + _add
                                            logger.info(f"[Engine] ★ #925 tool_schema 合并 {len(_add)} 个工具进下一轮: "
                                                        f"{[d['function']['name'] for d in _add]}，总数={len(fc_tools)}")
                                    except Exception as _merge_e:
                                        logger.debug(f"[Engine] tool_schema 合并失败: {_merge_e}")
                                # ★ 2026-09-21 #829: 工具过程改独立事件 tool_log（不再混入正文）
                                #   根因：原来 yield ("text", "📋 ...执行结果：{JSON}") 会把工具
                                #   原始 JSON 当正文发出去 —— 手机中转/电脑前端都直接拼进气泡，
                                #   用户看到大段 JSON。改为 tool_log 后：电脑前端渲染成折叠卡，
                                #   手机中转只收 text/content 天然干净。
                                if _tc_parsed.get('success') and _tc_parsed.get('message'):
                                    yield ("tool_log", f"📋 {_tc_name} 执行成功：{_tc_parsed['message']}")
                                elif _tc_parsed.get('success'):
                                    import json as _json_fc
                                    _fc_str = _json_fc.dumps(_tc_parsed, ensure_ascii=False, indent=2)
                                    if len(_fc_str) > 3000: _fc_str = _fc_str[:3000] + "\n...(已截断)"
                                    yield ("tool_log", f"📋 {_tc_name} 执行成功：\n{_fc_str}")
                                elif _tc_parsed.get('error'):
                                    yield ("tool_log", f"❌ {_tc_name} 失败：{_tc_parsed['error'][:200]}")

                            # ★ 2026-08-14: tool消息用JSON格式，AI能看懂
                            import json as _json_tool
                            if isinstance(_tc_result, str):
                                _tc_content = _tc_result  # 已经是字符串
                            else:
                                _tc_content = _json_tool.dumps(_tc_result, ensure_ascii=False)
                            # ★ A批A4(2026-09-13): 工具结果瘦身 — 进历史截断1000字符，全文落盘 data/tool_results/
                            try:
                                from src.core.builtin_tools import slim_tool_result as _slim
                                _tc_content = _slim(_tc_content, tool_name=_tc_name or 'tool')
                            except Exception as _slim_e:
                                logger.debug(f"[Engine] 工具结果瘦身失败(不影响主流程): {_slim_e}")
                            if _tl_record:
                                _tl_record('fc_rounds', round=_fc_round + 1, tool=_tc_name)
                            all_messages.append({
                                "role": "tool",
                                "tool_call_id": _tc_id,
                                "content": _tc_content
                            })
                            # ★ 2026-10-20 FC慢治理#4: 失败统计（本轮任一工具失败计连败）
                            _tc_ok = bool(_tc_parsed.get('success')) if isinstance(_tc_parsed, dict) else True
                            if not _tc_ok:
                                _fc_fail_streak += 1
                            else:
                                _fc_fail_streak = 0  # 成功即重置连败

                        # ★ 连败收口：连续2轮全失败 → 注入提示让外脑基于已有结果直接总结
                        #   （最坏5轮烧到3轮收口，省 20~40 秒）
                        if (_fc_fail_streak >= 2 and not _fc_fail_hinted
                                and _fc_round < _fc_max_rounds - 1):
                            all_messages.append({"role": "user", "content": (
                                "（系统提示：工具已连续失败多次，可能是命令语法或环境问题。"
                                "请基于已获得的结果直接总结回答用户，不要继续重试类似命令。）"
                            )})
                            _fc_fail_hinted = True
                            logger.warning(f"[Engine] ★ FC连败{_fc_fail_streak}轮，注入收口提示（第{_fc_round+1}轮后）")

                    if _fc_done:
                        # ★ 2026-10-11 #1075 根治双答 bug：FC 循环已输出最终回答，这里收笔
                        #   落库后直接 return。原先控制流 fall-through 到主流式分支，把模型
                        #   二次调用一遍（实锤：手机「找家人照片」回答出现两份、库只有一份）
                        # ★ #1518：FC 收笔出口也附垂直入口提示（协作OFF拦下的场景）
                        _vb_hint_fc = _vb_take_hint() if (_vertical_blocked and _fc_final) else ''
                        if _vb_hint_fc:
                            _fc_final += _vb_hint_fc
                            yield ("text", _vb_hint_fc)
                            logger.info(f"[Engine] #1518 FC收笔已附垂直入口提示: {_vertical_blocked}")
                        _src_fc = getattr(self, "_reply_source", "")
                        save_message(self.conversation_id, "assistant", _fc_final,
                                     metadata=({"source": _src_fc} if _src_fc else None))
                        self.history.append({"role": "user", "content": user_message})
                        self.history.append({"role": "assistant", "content": _fc_final})
                        logger.info(f"[Engine] #1075 FC 收笔入库: {len(_fc_final)} 字（不再二次作答）")
                        yield ("done", None)
                        return

                    if not _fc_done:
                        # 循环结束但还没输出最终回答（★ #1329：轮次用尽）
                        # 原先带着 tools 再调 → 模型刚拿到工具结果还想接着调（finish=tool_calls）
                        # → 被空流重试白烧。改：收尾调用【不传 tools】强制总结，并把已执行足迹
                        # 说给模型听，保证用户拿到「干到哪了」的真回答，而不是报错。
                        from collections import Counter as _fc_Counter
                        _fc_footprint = '、'.join(f'{k}×{v}' for k, v in _fc_Counter(_fc_executed).most_common()) if _fc_executed else '无'
                        _sum_lang_hint = (
                            f"工具调用轮次已达上限。已执行的工具：{_fc_footprint}。"
                            + ("请用中文总结刚才执行的操作结果，如实说明哪些完成了、哪些还没做——不要再调任何工具。" if _has_cjk else "Summarize in English what was done so far — do NOT call any more tools."))
                        all_messages.append({"role": "user", "content": _sum_lang_hint})
                        response_gen = mm.generate_response(all_messages, stream=True, tools=None)
                        raw_stream = self._adapt_mm_stream(response_gen)
                    elif full_response:
                        # 没有工具调用，直接输出非流式结果
                        # ★ 2026-07-24: 方案A — 模型有FC工具但没调，检测文本是否提到工具名
                        # ★ 2026-08-02: 扩展方案A — 覆盖list_dir/read_file
                        _resp_lower = full_response.lower()
                        _user_lower = (user_message or '').lower()

                        # 方案A-1: check_software 自动检测
                        if any(_tm in _resp_lower for _tm in ['check_software', 'scan_system', '检查软件', '检查安装']):
                            _SW_MAP_A = {
                                'docker': ['docker', '容器'], 'python': ['python', 'py环境'],
                                'git': ['git'], 'node': ['node', 'nodejs'], 'npm': ['npm'],
                                'ollama': ['ollama'], 'pip': ['pip'], 'java': ['java', 'jdk'],
                                'blender': ['blender'], 'ffmpeg': ['ffmpeg'], 'redis': ['redis'],
                            }
                            for _sw, _kws in _SW_MAP_A.items():
                                if any(_kw in _user_lower for _kw in _kws):
                                    try:
                                        from src.core.builtin_tools import check_software as _cs
                                        _sr = _cs(_sw)
                                        _auto_msg = "\n\n📋 **自动检测结果**（系统已自动调用工具）：\n"
                                        if _sr.get('installed'):
                                            _auto_msg += f"✅ {_sw} 已安装\n   版本：{_sr.get('version','未知')}\n   运行：{'✅运行中' if _sr.get('running') else '⚠️未运行'}\n"
                                        else:
                                            _auto_msg += f"❌ {_sw} 未安装\n   {_sr.get('error','')}\n"
                                        full_response += _auto_msg
                                        logger.info(f"[Engine] 方案A(FC路径): 自动调check_software({_sw})")
                                    except Exception as _ae:
                                        logger.error(f"[Engine] 方案A(FC路径)失败: {_ae}")
                                    break

                        # ★ 方案A-2: list_dir 自动检测 — 用户问"下载了什么""目录有什么"
                        if any(_kw in _user_lower for _kw in ['下载了什么', '下载了哪个', '目录有什么', '文件夹有什么', '有什么文件', '看看目录', '查看目录', '下载了啥', '刚才下载']):
                            # 智能推断路径
                            _auto_path = None
                            if '下载' in _user_lower:
                                _auto_path = os.path.join(BASE_DIR, 'data', 'downloads')
                            elif 'skill' in _user_lower:
                                _auto_path = os.path.join(BASE_DIR, 'data', 'skills')
                            elif 'tool' in _user_lower or '工具' in _user_lower:
                                _auto_path = os.path.join(BASE_DIR, 'data', 'fc_wrappers')
                            else:
                                _auto_path = os.path.join(BASE_DIR, 'data')
                            try:
                                from src.core.builtin_tools import list_dir as _ld
                                _lr = _ld(_auto_path)
                                if _lr.get('success'):
                                    _items = _lr.get('items', [])
                                    _auto_msg = f"\n\n📁 **目录内容** (`{_auto_path}`)：\n"
                                    for _it in _items[:20]:
                                        _icon = '📁' if _it['is_dir'] else '📄'
                                        _size = f" ({_it['size']//1024}KB)" if not _it['is_dir'] and _it['size'] > 0 else ''
                                        _auto_msg += f"{_icon} {_it['name']}{_size}\n"
                                    if len(_items) > 20:
                                        _auto_msg += f"... 共{len(_items)}项\n"
                                    full_response += _auto_msg
                                    logger.info(f"[Engine] 方案A-2: 自动调list_dir({_auto_path}) → {len(_items)}项")
                            except Exception as _ae:
                                logger.error(f"[Engine] 方案A-2失败: {_ae}")

                        # ★ 方案A-3: read_file 自动检测 — 用户说"看看这个文件"
                        if any(_kw in _user_lower for _kw in ['看看文件', '文件内容', '文件写了什么', '读一下文件']):
                            # 尝试从用户消息中提取文件路径
                            import re as _re
                            _path_match = _re.search(r'[A-Za-z]:[/\\][^\s,，。]+', user_message or '')
                            if _path_match:
                                _auto_path = _path_match.group(0)
                                try:
                                    from src.core.builtin_tools import read_file as _rf
                                    _rr = _rf(_auto_path)
                                    if _rr.get('success'):
                                        _content = _rr.get('content', '')[:500]
                                        full_response += f"\n\n📄 **文件内容** (`{_auto_path}`)：\n```\n{_content}\n```\n"
                                        logger.info(f"[Engine] 方案A-3: 自动调read_file({_auto_path})")
                                except Exception as _ae:
                                    logger.error(f"[Engine] 方案A-3失败: {_ae}")

                        # ★ 2026-08-03: 代码兜底执行 — FC路径也尝试执行代码
                        import re as _re_fc
                        _fc_code_blocks = _re_fc.findall(r'```(?:python|py)?\s*\n([\s\S]*?)```', full_response)
                        if _fc_code_blocks:
                            try:
                                from src.core.tool_executor import execute_python_code
                                for _fc_code in _fc_code_blocks:
                                    # ★ #1315 先剥围栏语言标记再执行（GLM 复读 python 行 NameError 实锤）
                                    _fc_code = self._strip_fence_lang(_fc_code)
                                    if len(_fc_code.strip()) < 10:
                                        continue
                                    _fc_result = execute_python_code(_fc_code, timeout=30)
                                    if _fc_result.get('success') and _fc_result.get('output'):
                                        _fc_out = _fc_result['output'][:2000]
                                        full_response += f"\n\n📋 **代码执行结果**：\n```\n{_fc_out}\n```"
                                        # 移除代码块（已执行）
                                        full_response = _re_fc.sub(
                                            r'```(?:python|py)?\s*\n[\s\S]*?```',
                                            '（代码已执行，结果见下方）',
                                            full_response,
                                        )
                                        logger.info(f"[Engine] FC路径代码兜底成功, 输出{len(_fc_out)}字符")
                                    elif not _fc_result.get('success'):
                                        _fc_err = _fc_result.get('error', '')[:200]
                                        # 安全检查不通过 → 过滤代码
                                        full_response = _re_fc.sub(
                                            r'```(?:python|py)?\s*\n[\s\S]*?```',
                                            f'（代码未执行：{_fc_err}）',
                                            full_response,
                                        )
                                        logger.info(f"[Engine] FC路径代码兜底失败: {_fc_err}")
                            except Exception as _fc_ex:
                                logger.debug(f"[Engine] FC路径代码兜底异常: {_fc_ex}")

                        # ★ 2026-08-03: 方案A-2关键词扩展（正则匹配，不依赖精确匹配）
                        if any(_re_fc.search(p, _user_lower) for p in [
                            r'下载.*什么', r'什么.*下载', r'下了.*啥', r'最近.*下载',
                            r'昨天.*下载', r'下载.*啥', r'下载.*哪', r'什么.*文件'
                        ]):
                            _auto_path = os.path.join(BASE_DIR, 'data', 'downloads')
                            try:
                                from src.core.builtin_tools import list_dir as _ld
                                _lr = _ld(_auto_path)
                                if _lr.get('success'):
                                    _items = _lr.get('items', [])
                                    _auto_msg = f"\n\n📁 **目录内容** (`{_auto_path}`)：\n"
                                    for _it in _items[:20]:
                                        _icon = '📁' if _it['is_dir'] else '📄'
                                        _size = f" ({_it['size']//1024}KB)" if not _it['is_dir'] and _it['size'] > 0 else ''
                                        _auto_msg += f"{_icon} {_it['name']}{_size}\n"
                                    if len(_items) > 20:
                                        _auto_msg += f"... 共{len(_items)}项\n"
                                    full_response += _auto_msg
                                    logger.info(f"[Engine] 方案A-2: 自动调list_dir({_auto_path}) → {len(_items)}项")
                            except Exception as _ae:
                                logger.error(f"[Engine] 方案A-2失败: {_ae}")

                        # ★ 2026-08-03: 幻觉tool_calls检测 — 模型写JSON而不是调FC API
                        _hallucinated_tc = _re_fc.search(r'\{\s*"tool_calls"\s*:\s*\[([\s\S]*?)\]\s*\}', full_response)
                        if _hallucinated_tc:
                            import json as _json_hc
                            try:
                                _tc_json = '{"tool_calls":[' + _hallucinated_tc.group(1) + ']}'
                                _tc_data = _json_hc.loads(_tc_json)
                                _tc_list = _tc_data.get('tool_calls', [])
                                for _tc_item in _tc_list:
                                    _tc_tool = _tc_item.get('tool', _tc_item.get('function', {}).get('name', ''))
                                    _tc_action = _tc_item.get('action', _tc_item.get('reason', ''))
                                    # 智能推断参数
                                    if _tc_tool == 'list_dir':
                                        # 从action里提取路径，否则用默认
                                        _path_match = _re_fc.search(r'([A-Za-z]:[/\\][^\s\'"]+)', _tc_action or '')
                                        _tc_path = _path_match.group(1) if _path_match else os.path.join(BASE_DIR, 'data', 'downloads')
                                        from src.core.builtin_tools import list_dir as _ld_hc
                                        _lr_hc = _ld_hc(_tc_path)
                                        if _lr_hc.get('success'):
                                            _items_hc = _lr_hc.get('items', [])
                                            _hc_msg = f"\n\n📁 **list_dir执行结果** (`{_tc_path}`)：\n"
                                            for _it_hc in _items_hc[:20]:
                                                _icon = '📁' if _it_hc['is_dir'] else '📄'
                                                _size = f" ({_it_hc['size']//1024}KB)" if not _it_hc['is_dir'] and _it_hc['size'] > 0 else ''
                                                _hc_msg += f"{_icon} {_it_hc['name']}{_size}\n"
                                            full_response += _hc_msg
                                            logger.info(f"[Engine] 幻觉tool_calls: 执行list_dir({_tc_path}) → {len(_items_hc)}项")
                                    elif _tc_tool == 'check_software':
                                        from src.core.builtin_tools import check_software as _cs_hc
                                        _sw_name = _tc_item.get('name', _tc_item.get('software', ''))
                                        if _sw_name:
                                            _sr_hc = _cs_hc(_sw_name)
                                            full_response += f"\n\n📋 **check_software结果**：\n{_sr_hc}\n"
                                            logger.info(f"[Engine] 幻觉tool_calls: 执行check_software({_sw_name})")
                                    elif _tc_tool == 'read_file':
                                        _path_match = _re_fc.search(r'([A-Za-z]:[/\\][^\s\'"]+)', _tc_action or '')
                                        if _path_match:
                                            from src.core.builtin_tools import read_file as _rf_hc
                                            _rr_hc = _rf_hc(_path_match.group(1))
                                            if _rr_hc.get('success'):
                                                full_response += f"\n\n📄 **read_file结果**：\n```\n{_rr_hc.get('content','')[:1000]}\n```\n"
                                # 移除幻觉JSON块
                                full_response = _re_fc.sub(r'\{\s*"tool_calls"\s*:\s*\[[\s\S]*?\]\s*\}', '', full_response)
                            except Exception as _hc_ex:
                                logger.debug(f"[Engine] 幻觉tool_calls解析失败: {_hc_ex}")

                        # ★ #1518：FC 非流式直出出口也附垂直入口提示
                        _vb_hint_fs = _vb_take_hint() if (_vertical_blocked and full_response) else ''
                        if _vb_hint_fs:
                            full_response += _vb_hint_fs
                            logger.info(f"[Engine] #1518 FC非流式已附垂直入口提示: {_vertical_blocked}")
                        yield ("text", full_response)
                        yield ("done", None)
                        return
                    else:
                        # 回退到流式调用
                        response_gen = mm.generate_response(all_messages, stream=True, tools=fc_tools)
                        raw_stream = self._adapt_mm_stream(response_gen)
                else:
                    # 不支持Function Calling，直接流式调用
                    response_gen = mm.generate_response(all_messages, stream=True)
                    raw_stream = self._adapt_mm_stream(response_gen)
            except Exception as e:
                logger.error(f"[Engine] 外部模型调用失败: {e}")
                # ★ 2026-09-27 开发者死命令（#891 阵营原则延伸到主聊天）：外脑失败 → 直接报错停止，
                # 绝不回落本地。用外脑还是本地由用户阵营选择决定，路由进来必是 direct；
                # 很多用户没有本地模型，偷顶等于替用户做主
                err_msg = f"❌ 外部大模型（{model_label}）连接不成功\n\n错误详情：{str(e)[:500]}\n\n请检查「设置 → 模型管理」中的 API Key 和网络配置。"
                yield ("error", err_msg)
                return  # 直接结束，不用本地顶替
            finally:
                # ★ 关键修复：无论成功还是失败，恢复 current_model
                mm.current_model = old_model

            # 检查外部模型是否返回了错误或空流
            if raw_stream is not None:
                try:
                    first_check = next(iter(raw_stream))
                except StopIteration:
                    logger.warning("[Engine] 外部模型返回空流")
                    # ★ 2026-09-27 开发者死命令：无响应直接报错停止，不回落本地
                    err_msg = f"❌ 外部大模型（{model_label}）无响应\n\n模型服务端可能暂时不可用，请稍后重试或检查网络。"
                    yield ("error", err_msg)
                    return
                except Exception as _fs_e:
                    logger.warning(f"[Engine] 外部模型流首检异常: {_fs_e}")
                    # ★ 2026-09-27 开发者死命令：流异常直接报错停止，不回落本地
                    err_msg = f"❌ 外部大模型（{model_label}）响应异常\n\n{str(_fs_e)[:300]}\n\n请检查模型配置，或稍后重试。"
                    yield ("error", err_msg)
                    return
                if isinstance(first_check, str) and first_check.startswith("[错误]"):
                    logger.warning(f"[Engine] 外部模型返回错误: {first_check}")
                    # ★ 2026-09-27 开发者死命令：报错直接停，不回落本地
                    err_msg = f"❌ 外部大模型（{model_label}）返回错误\n\n{first_check[:300]}\n\n请检查模型配置，或切换其他外脑，或点 🏠本地 用本地模型。"
                    yield ("error", err_msg)
                    return
                else:
                    import itertools
                    raw_stream = itertools.chain([first_check], raw_stream)

        # ── 本地通用模型调用（优先 → llama-cpp 回退）──
        if not use_external or raw_stream is None:
            # ═══ ★ #1497（2026-11-13）本地FC前置轮：A护栏 + B打通 ═══
            #   外脑有 FC 循环，本地此前是哑巴工具。此处先探测应答模型：
            #   有 tools 能力（Ollama /api/show）→ /api/chat+tools 真调工具收笔；
            #   无 tools 能力 + FC 域意图（邮件等）→ 如实护栏，不让模型瞎编。
            #   两种情况都不命中 → ('skip') 原有链路零变化。
            try:
                _fc_engine = self
                _sel_1497 = (getattr(self.model, 'model', '') or '')
                from src.core.multi_model_manager import get_multi_model_manager as _gmm_1497
                _sel_1497 = _sel_1497 or (_gmm_1497().current_model or '')
            except Exception:
                _sel_1497 = getattr(self.model, 'model', '') or ''
            _fc_model = ''
            _fc_label = ''
            if _sel_1497 == 'harveneye':
                # HarvenEye 文字分流的实际应答模型：钉选通道 → 出厂 understand 通道
                _eye_model = ''
                try:
                    from src.core.vision_link import resolve_pinned_channel as _rpch_1497
                    _pch_1497 = _rpch_1497() or {}
                    if _pch_1497.get('provider') == 'ollama':
                        _eye_model = _pch_1497.get('model', '')
                        _fc_label = f"👁️ 本地视觉引擎 · {_pch_1497.get('label') or _eye_model}（Ollama）"
                except Exception:
                    _pch_1497 = {}
                if not _eye_model:
                    try:
                        from src.vision import config as _vcfg_1497
                        from src.vision.installer import engine_status as _est_1497
                        _u_1497 = _vcfg_1497.ENGINES.get('understand', {})
                        if (_u_1497.get('provider') == 'ollama' and _u_1497.get('model')
                                and bool(_est_1497().get('ready'))):
                            _eye_model = _u_1497['model']
                            _fc_label = f"👁️ 本地视觉引擎 · {_eye_model}（本地）"
                    except Exception:
                        pass
                _fc_model = _eye_model
            else:
                # 普通本地模型：模型名直接探（非 Ollama 名探测会 False → skip/护栏）
                _fc_model = _sel_1497
                _fc_label = f"🏠 {_fc_model}"
            if _fc_model and not getattr(self, '_web_search_this_query', False):
                _fc_events = []
                try:
                    _st, _pl = _fc_engine._local_fc_preround(
                        list(all_messages), _fc_model, user_message,
                        notify=lambda k, c: _fc_events.append((k, c)))
                except Exception as _fc_pre_e:
                    logger.warning(f"[Engine] #1497 前置轮异常(走原链路): {_fc_pre_e}")
                    _st, _pl = 'skip', None
                if _st in ('guard', 'done'):
                    yield ("model_source", {"model": _fc_model,
                                            "label": _fc_label or _fc_model,
                                            "is_external": False, "mode": "local"})
                    for _ev_k, _ev_c in _fc_events:
                        yield (_ev_k, _ev_c)
                    _final_1497 = _pl or ''
                    if _st == 'guard':
                        _final_1497 = _pl
                    elif not _final_1497:
                        _final_1497 = '⚙️ 工具已执行，但没能生成总结，请把想看的具体内容再问一次。'
                    # ★ #1518：#1497 前置轮收笔出口也附垂直入口提示
                    _vb_hint_1497 = _vb_take_hint() if (_vertical_blocked and _final_1497) else ''
                    if _vb_hint_1497:
                        _final_1497 += _vb_hint_1497
                        logger.info(f"[Engine] #1518 前置轮收笔已附垂直入口提示: {_vertical_blocked}")
                    yield ("text", _vb_hint_1497)
                    try:
                        save_message(self.conversation_id, "assistant", _final_1497,
                                     metadata={"source": _fc_label or _fc_model})
                    except Exception:
                        pass
                    self.history.append({"role": "assistant", "content": _final_1497})
                    logger.info(f"[Engine] #1497 本地前置轮收笔({_st}): {len(_final_1497)}字")
                    return
                # ('skip') → 落到下方原有链路，行为零变化
            # ═══ #1497 前置轮结束 ═══
            # ★ #1291（2026-10-29）HarvenEye 文字分流（A′，定版拍板）：
            #   选 HarvenEye 发纯文字 → 只认钉选的理解槽大脑；没钉选如实报错。
            #   绝不顶号——chat_local 四级兜底曾借 Ollama 模型挂 HarvenEye 牌子
            #   （身份冒用实锤，开发者 10-29 裁决）。带图场景走 _vision_reply 不受影响。
            # ★ #1296（2026-10-29）判定反转：本地模式优先界面真实选择 self.model.model，
            #   mm.current_model 是全局槽位（可能残留外脑 deepseek，会把 harveneye 判定短路掉）。
            try:
                from src.core.multi_model_manager import get_multi_model_manager as _gmm_eye
                _sel_eye = (getattr(self.model, 'model', '') or _gmm_eye().current_model or '')
            except Exception:
                _sel_eye = getattr(self.model, 'model', '') or ''
            if _sel_eye == 'harveneye':
                from src.core import eye_client as _eyecli_e
                try:
                    from src.core.vision_link import resolve_pinned_channel as _rpch_e
                    _pch = _rpch_e()
                except Exception:
                    _pch = {}
                if _pch:
                    # 有钉选大脑 → 用钉的模型答（标注真实名字，铁律 0 合规）
                    if _pch.get('provider') == 'api':
                        _elabel = f"👁️ 本地视觉引擎 · {_pch.get('api_model', '')}（外脑视觉大脑）"
                        _emid = _pch.get('api_model', 'vision')
                    elif _pch.get('provider') == 'ollama':
                        _elabel = f"👁️ 本地视觉引擎 · {_pch.get('label') or _pch.get('model', '')}（Ollama）"
                        _emid = _pch.get('model', 'vision')
                    else:
                        from pathlib import Path as _Pth2
                        _elabel = f"👁️ 本地视觉引擎 · {_Pth2(_pch['gguf']).stem}（本地直载）"
                        _emid = _Pth2(_pch['gguf']).stem
                    self._reply_source = _elabel
                    yield ("model_source", {"model": _emid, "label": _elabel,
                                            "is_external": _pch.get('provider') == 'api',
                                            "mode": "vision"})
                    _efull, _egot = [], False
                    try:
                        for piece in _eyecli_e.chat_stream(all_messages, timeout=150.0):
                            _egot = True
                            _efull.append(str(piece or ''))
                            yield ("text", piece)
                    except RuntimeError as _ere:
                        yield ("error", f"❌ {_ere}")
                        return
                    except Exception as _eee:
                        yield ("error", f"❌ 本地视觉大脑回答失败: {_eee}")
                        return
                    if _egot:
                        save_message(self.conversation_id, "assistant", ''.join(_efull),
                                     metadata={"source": _elabel})
                        self.history.append({"role": "assistant", "content": ''.join(_efull)})
                    return
                # ★ #1418（2026-11-04）三件套齐 = 出厂自带大脑（已定版：产品默认配置，非 AI 代选）：
                #   钉选为空时，只要底座（yolox+clip）就绪 + 出厂 understand 通道可解析且模型
                #   真实存在（full 捆的 qwen3-vl 等），直接用它答（真实模型名标注）；
                #   任何档位（lite/standard/full）通用——三件套齐就是本地通用大脑。
                #   任一不满足才走分层报错。
                _base_ok = False
                try:
                    from src.vision.installer import engine_status as _est_e
                    _base_ok = bool(_est_e().get('ready'))
                except Exception:
                    _base_ok = False
                _fch = {}
                try:
                    from pathlib import Path as _Pth3
                    from src.vision import config as _vcfg_e
                    _u = _vcfg_e.ENGINES.get("understand", {})
                    _prov = _u.get("provider", "")
                    if _prov == "ollama" and _u.get("model"):
                        # 校验 ollama 里模型真实存在（没拉的机器不硬闯，留给分层报错引导）
                        import requests as _req_e
                        _base_url = (_u.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
                        _tags = _req_e.get(f"{_base_url}/api/tags", timeout=3).json()
                        _names = [m.get("name", "") for m in _tags.get("models", [])]
                        if any(n == _u["model"] or n.split(":")[0] == _u["model"].split(":")[0]
                               for n in _names):
                            _fch = {"provider": "ollama", "model": _u["model"],
                                    "label": _u["model"]}
                    elif _prov == "llama_cpp" and _u.get("gguf") and _Pth3(_u["gguf"]).is_file():
                        _fch = {"provider": "llama_cpp", "gguf": _u["gguf"],
                                "mmproj": _u.get("mmproj", "")}
                except Exception:
                    _fch = {}
                if _fch and _base_ok:
                    from pathlib import Path as _Pth4
                    if _fch.get("provider") == "ollama":
                        _elabel = f"👁️ 本地视觉引擎 · {_fch['model']}（本地）"
                        _emid = _fch["model"]
                    else:
                        _elabel = f"👁️ 本地视觉引擎 · {_Pth4(_fch['gguf']).stem}（本地直载）"
                        _emid = _Pth4(_fch['gguf']).stem
                    self._reply_source = _elabel
                    yield ("model_source", {"model": _emid, "label": _elabel,
                                            "is_external": False, "mode": "vision"})
                    _efull, _egot = [], False
                    try:
                        for piece in _eyecli_e.chat_stream(all_messages, timeout=150.0,
                                                           channel=_fch):
                            _egot = True
                            _efull.append(str(piece or ''))
                            yield ("text", piece)
                    except RuntimeError as _ere:
                        yield ("error", f"❌ {_ere}")
                        return
                    except Exception as _eee:
                        yield ("error", f"❌ 本地视觉大脑回答失败: {_eee}")
                        return
                    if _egot:
                        save_message(self.conversation_id, "assistant", ''.join(_efull),
                                     metadata={"source": _elabel})
                        self.history.append({"role": "assistant", "content": ''.join(_efull)})
                    return
                # 三件套不齐 → 按「卡在哪层」分层报错（每条至少三条出路，不给死胡同）
                if not _base_ok:
                    yield ("error",
                           "❌ 视觉引擎还没安装（本机没有它的视觉底座文件）。\n\n"
                           "出路：\n"
                           "① 到「视觉」页面一键安装（约 800MB，普通电脑即可，CPU 也能跑）\n"
                           "② 在 设置→模型管理 换一个本地模型\n"
                           "③ 点对话区 🤖 按钮切换外脑模式")
                else:
                    yield ("error",
                           "❌ 视觉引擎底座已装，但通用大脑还没就位，没法对话。\n\n"
                           "出路：\n"
                           "① 到「视觉」页面下载/钉选通用模型当它的大脑\n"
                           "② 电脑配置有限？在「视觉」页面把外脑视觉模型（如 GLM 看图）设为大脑，本机零负担\n"
                           "③ 换其他本地模型或切 🤖 外脑模式")
                return

            # ═══ 模型能力检测：非语言模型不参与对话 ═══
            # P1-2: 统一使用模块级常量 _NONCHAT_KEYWORDS
            # ★ 2026-08-25: 类型守护——list/dict 一律安全化，避免 .lower() 崩溃
            _current_model = getattr(self.model, 'model', '') or ''
            if not isinstance(_current_model, str):
                logger.warning(f"[Engine] 本地模型名类型异常({type(_current_model).__name__})，已忽略")
                _current_model = ''
            _model_lower = _current_model.lower()
            _is_nonchat = any(kw in _model_lower for kw in _NONCHAT_KEYWORDS)
            if _is_nonchat:
                yield ("notification", {
                    "type": "nonchat_model",
                    "message": (
                        f"❌ 当前本地模型「{_current_model}」是专业模型，不具备语言对话能力。\n\n"
                        f"原因：音乐/图片/视频/嵌入类模型只能做专项任务，无法生成文本回答。\n\n"
                        f"解决方法：\n"
                        f"· 切换到 🤖外脑模式（使用 DeepSeek 等云端语言模型）\n"
                        f"· 或在设置 → AI模型 中切换到语言类本地模型（如 qwen2.5:14b）"
                    )
                })
                yield ("error", f"本地模型「{_current_model}」不具备对话能力，请切换模型。")
                return

            try:
                logger.info(f"[Engine] 使用本地模型 模型: {self.model.model if hasattr(self.model, 'model') else '?'}")
                response_stream = self.model.chat_with_context(
                    system_prompt=system_prompt,
                    conversation_history=context,
                    user_message=user_message,
                    stream=True,
                    temperature=self._temperature,
                )
                raw_stream = response_stream
            except Exception as e:
                logger.warning(f"[Engine] Ollama 不可用: {e}，尝试 llama-cpp 本地引擎...")
                # 🔄 回退到 llama-cpp-python 内置引擎
                llama_ok = False
                try:
                    from src.core.llama_engine import get_llama_engine
                    from src.core.model_downloader import get_installed_model_path
                    model_path = get_installed_model_path()
                    if model_path:
                        engine = get_llama_engine(str(model_path))
                        if not engine.is_loaded:
                            engine.load(str(model_path))
                        if engine.is_loaded:
                            yield ("thinking", "🧠 本地模型（llama-cpp）")
                            full_prompt = system_prompt + "\n\n" + "\n".join(
                                f"{m['role']}: {m['content']}" for m in context[-6:]
                            ) + "\nuser: " + user_message
                            logger.info(f"[Engine] 使用 llama-cpp 引擎: {model_path}")
                            raw_stream = engine.stream_generate(
                                prompt=full_prompt,
                                system_prompt="",
                                temperature=self._temperature,
                            )
                            llama_ok = True
                except ImportError as ie:
                    logger.debug(f"[Engine] llama-cpp 未安装: {ie}")
                except Exception as le:
                    logger.debug(f"[Engine] llama-cpp 调用失败: {le}")
                
                # ★ llama-cpp 也失败 → 用简化 prompt 重试 Ollama
                if not llama_ok:
                    logger.info(f"[Engine] llama-cpp 不可用，用简化提示词重试 Ollama...")
                    yield ("thinking", "🔄 模型响应异常，正在用简化模式重试...")
                    try:
                        retry_msg = [{"role": "system", "content": "你是一个有帮助的AI助手。直接回答用户的问题。"}]
                        retry_msg.extend(context[-4:])
                        retry_msg.append({"role": "user", "content": user_message})
                        raw_stream = self.model.chat(retry_msg, stream=True, temperature=self._temperature)
                    except Exception as retry_e:
                        logger.error(f"[Engine] Ollama 简化重试也失败: {retry_e}")
                        yield ("error", "本地通用模型暂时不可用。请稍后重试或检查模型运行时是否正常。")
                        return

        # 防御性检查：确保 raw_stream 是可迭代的
        if isinstance(raw_stream, str):
            if raw_stream.startswith("[错误]"):
                yield ("error", raw_stream)
            else:
                yield ("error", f"模型返回了非流式数据: {raw_stream[:100]}")
            return
        
        # 调试：尝试获取第一个 chunk 并记录
        try:
            first_chunk = next(iter(raw_stream))
            logger.debug(f"[Engine] 第一个 chunk: type={type(first_chunk)}, content={str(first_chunk)[:100]}")
            # 重新构造迭代器（因为已经消费了一个元素）
            import itertools
            raw_stream = itertools.chain([first_chunk], raw_stream)
        except StopIteration:
            logger.warning("[Engine] raw_stream 是空的（没有 chunk）!")
        except Exception as e:
            logger.error(f"[Engine] 检查 raw_stream 失败: {e}")

        chunk_count = 0
        chunk_meta_count = 0  # 统计非内容 chunk（仅元数据，无实际文本）
        try:
            for chunk in raw_stream:
                # ★ 2026-10-10 #1052: 停止门——用户点了停止立即收笔（半截保留入库）
                if is_stopped(self.conversation_id):
                    logger.info(f"[Engine] #1052 用户停止：已出 {chunk_count} 块/{len(full_response)} 字，中断生成")
                    full_response += "\n\n（已停止）"
                    yield ("text", "\n\n（已停止）")
                    break
                chunk_meta_count += 1
                if isinstance(chunk, str) and chunk.startswith("[错误]"):
                    yield ("error", chunk)
                    return
                if isinstance(chunk, str):
                    full_response += chunk
                    chunk_count += 1
                    yield ("text", chunk)
        except Exception as e:
            logger.debug(f"[Engine] 流式读取异常: {e}")
            yield ("error", f"生成中断: {e}")
            return

        logger.info(f"[Engine] 生成完成, {chunk_count} text chunks, {chunk_meta_count} total chunks, {len(full_response)}字")

        # 诊断：模型没有返回任何文本内容
        # ★ 2026-06-26 加固：空响应优先用简化 prompt 重试，不直接报错
        # ★ 2026-10-10 #1052: 空流重试加停止守卫——用户刚点了停止就不能再触发生成
        if chunk_count == 0 and not is_stopped(self.conversation_id):
            logger.warning(f"[Engine] 模型返回空流！chunk_count=0, meta_chunks={chunk_meta_count}. model={self.model.model if hasattr(self.model, 'model') else '?'}")
            
            # ★ 尝试用简化 prompt 重试（移除超长的 system_prompt 和 enhanced_context）
            yield ("thinking", "模型响应异常，正在用简化模式重试...")
            try:
                retry_msg = [{"role": "system", "content": "你是一个有帮助的AI助手。请直接回答用户的问题，不要使用JSON格式或特殊标记。"}]
                retry_msg.extend(context[-4:])  # 仅最近4条上下文
                retry_msg.append({"role": "user", "content": user_message})
                retry_stream = self.model.chat(retry_msg, stream=True, temperature=self._temperature)
                retry_count = 0
                for retry_chunk in retry_stream:
                    if isinstance(retry_chunk, str):
                        full_response += retry_chunk
                        retry_count += 1
                        yield ("text", retry_chunk)
                if retry_count > 0:
                    logger.info(f"[Engine] 简化重试成功, {retry_count} chunks")
                elif is_collab_skipped:
                    # ★ 协作模式重试失败：直接用外脑分析作为回答
                    logger.warning(f"[Engine] 协作模式 Ollama 重试失败，使用外脑分析兜底")
                    # P3-2: 替代 'collab_analysis' in dir() 反模式
                    collab_fallback = collab_analysis if collab_analysis else ""
                    if not collab_fallback:
                        collab_fallback = "外脑分析已完成但本地整合失败。建议关闭协作模式后重试。"
                    full_response = collab_fallback
                    for chunk in [full_response[i:i+50] for i in range(0, len(full_response), 50)]:
                        yield ("text", chunk)
                else:
                    logger.warning(f"[Engine] 简化重试仍然返回空！")
                    yield ("error", "模型无法生成回复，请尝试：1. 重启 Ollama 2. 切换模型 3. 简化问题")
                    return
            except Exception as retry_e:
                logger.error(f"[Engine] 简化重试失败: {retry_e}")
                # P3-2: 替代 'collab_analysis' in dir() 反模式
                if is_collab_skipped and collab_analysis:
                    full_response = collab_analysis
                    yield ("text", full_response)
                else:
                    yield ("error", f"模型重试失败: {retry_e}")
                return

        # ★ #1518：协作OFF拦下的垂直Skill——回答末尾附入口提示（消息不再被吞）
        if _vertical_blocked and full_response:
            _vb_hint = _vb_take_hint()
            if _vb_hint:
                full_response += _vb_hint
                yield ("text", _vb_hint)
                logger.info(f"[Engine] #1518 已附垂直入口提示: {_vertical_blocked}")

        # ★ 2026-10-10 #1052: 用户停止 → 跳过代码兜底执行/自动扫描等重后处理，直接收笔入库
        #（否则半截回复里的代码块还会被兜底执行，停止按钮按了还要白等几十秒）
        if is_stopped(self.conversation_id):
            _src_stop = getattr(self, "_reply_source", "")
            save_message(self.conversation_id, "assistant", full_response,
                         metadata=({"source": _src_stop} if _src_stop else None))
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": full_response})
            logger.info(f"[Engine] #1052 已停止收笔入库: {len(full_response)} 字")
            clear_stop(self.conversation_id)
            yield ("done", None)
            return

        # 清洗模型可能输出的后台任务 JSON 块（action/tool_calls）
        # 这类 JSON 块是给后台任务调度器的，不应该展示给用户
        try:
            import re as _re
            # ★ 2026-09-13: 先初始化（修复：全权模式跳过清洗块时 _had_code 未定义；无代码回复被误标 True）
            _had_code = False
            # ★ 2026-07-24: 全权模式下关闭代码过滤器（允许模型写代码执行）
            _full_mode = False
            try:
                from src.core.security_guard import get_permission_mode
                _full_mode = get_permission_mode() == 'full'
            except Exception:
                pass

            if not _full_mode:
                # ★ 2026-08-03: 代码兜底执行 — 先尝试执行代码，成功则显示结果，失败才过滤
                _code_blocks = _re.findall(r'```(?:python|py)?\s*\n([\s\S]*?)```', full_response)
                _exec_results = []
                for _code in _code_blocks:
                    # ★ #1315 先剥围栏语言标记再执行（GLM 复读 python 行 NameError 实锤）
                    _code = self._strip_fence_lang(_code)
                    if len(_code.strip()) < 10:
                        continue
                    try:
                        from src.core.tool_executor import execute_python_code
                        _result = execute_python_code(_code, timeout=30)
                        if _result.get('success') and _result.get('output'):
                            _exec_results.append(_result['output'][:2000])
                            logger.info(f"[Engine] 代码兜底执行成功, 输出{len(_result['output'])}字符")
                        elif not _result.get('success'):
                            _err = _result.get('error', '')[:200]
                            logger.info(f"[Engine] 代码兜底执行失败: {_err}")
                    except Exception as _ex:
                        logger.debug(f"[Engine] 代码兜底异常: {_ex}")

                # 如果有执行成功的结果，替换代码块为结果
                if _exec_results:
                    _results_text = "\n\n📋 **代码执行结果**：\n```\n" + "\n---\n".join(_exec_results) + "\n```"
                    full_response = _re.sub(
                        r'```(?:python|py)?\s*\n[\s\S]*?```',
                        _results_text,
                        full_response,
                    )
                    _had_code = False  # 不再标记为"代码被过滤"
                    logger.info(f"[Engine] 代码已执行，结果已替换到回复中")
                else:
                    # ★ 2026-08-03: 代码执行失败 → 尝试执行裸代码（不在```块里的）
                    _bare_code = _re.search(r'(import\s+\w+[\s\S]*?)(?=\n\n|\Z)', full_response)
                    if _bare_code:
                        try:
                            from src.core.tool_executor import execute_python_code
                            _bare_result = execute_python_code(_bare_code.group(1), timeout=30)
                            if _bare_result.get('success') and _bare_result.get('output'):
                                full_response += f"\n\n📋 **代码执行结果**：\n```\n{_bare_result['output'][:2000]}\n```"
                                full_response = _re.sub(r'import\s+\w+[\s\S]*?(?=\n\n|\Z)', '', full_response)
                                _had_code = False
                                logger.info(f"[Engine] 裸代码执行成功")
                            else:
                                _had_code = True
                        except Exception:
                            _had_code = True
                    elif _code_blocks:
                        _had_code = True   # ★ 有```代码块但兜底执行失败 → 才算"代码被过滤"
                    else:
                        _had_code = False  # ★ 2026-09-13 修复：既无代码块也无裸代码 → 清白，不再误标 True（误判"哈文正常"这类纯文本回复）

                    # 标记但不显示FC误导警告
                    if _re.search(r'```(?:python|py|bash|sh|json|javascript|js|ruby|go|rust|java|c|cpp)?\s*[\s\S]*?```', full_response):
                        _had_code = True
                        full_response = _re.sub(
                            r'```(?:python|py|bash|sh|json|javascript|js|ruby|go|rust|java|c|cpp)?\s*[\s\S]*?```',
                            '',
                            full_response,
                        )

            # 2. 通用代码特征检测——检测"像代码"的模式
            _code_features = 0
            if _re.search(r'^\s*import\s+\w+', full_response, _re.MULTILINE):
                _code_features += 1
            if _re.search(r'^\s*from\s+\w+\s+import', full_response, _re.MULTILINE):
                _code_features += 1
            if _re.search(r'^\s*def\s+\w+\s*\(', full_response, _re.MULTILINE):
                _code_features += 1
            if _re.search(r'subprocess\.\w+\s*\(', full_response):
                _code_features += 1
            if _re.search(r'\.run\s*\(', full_response) and _re.search(r'capture_output|timeout|stdout|stderr', full_response):
                _code_features += 1
            if _re.search(r'__import__\s*\(', full_response):
                _code_features += 1
            if _re.search(r'os\.system\s*\(', full_response):
                _code_features += 1
            if _re.search(r'print\s*\(.+\)', full_response) and _re.search(r'if\s+.*:', full_response):
                _code_features += 1  # print + if 同时出现，大概率是代码

            # 3. 如果检测到2个以上代码特征，判定为代码输出
            if _code_features >= 2:
                _had_code = True
                # 删除所有代码行（import/from/def/class/if/for/while/try/except/print/return开头）
                full_response = _re.sub(
                    r'^\s*(?:import|from|def|class|if|elif|else|for|while|try|except|finally|with|return|print|raise|assert|pass|break|continue|yield|global|nonlocal|lambda)\b.*$',
                    '',
                    full_response,
                    flags=_re.MULTILINE,
                )
                # 删除缩进的代码行（以空格开头的非文本行）
                full_response = _re.sub(
                    r'^    [^\n]*$',
                    '',
                    full_response,
                    flags=_re.MULTILINE,
                )
                logger.info(f"[Engine] 检测到{_code_features}个代码特征，已过滤代码行")

            # 4. 清洗裸 import 语句（单个也拦截）
            if _re.search(r'^\s*import\s+\w+', full_response, _re.MULTILINE):
                _had_code = True
                full_response = _re.sub(
                    r'^\s*(?:import|from)\s+\w+[^\n]*$',
                    '',
                    full_response,
                    flags=_re.MULTILINE,
                )

            if _had_code:
                logger.warning(f"[Engine] 代码执行失败/不安全，已移除代码块")
                # ★ 不再显示FC误导警告，改为简短提示
                yield ("hint", "💡 部分代码因安全限制未执行。如需完整操作，可切换到全权模式。")

            # 清洗 ```json ...``` 代码块
            full_response = _re.sub(
                r'```json\s*[\s\S]*?```',
                '',
                full_response,
            )
            # 清洗 {tool_calls:[...]} 幻觉块
            full_response = _re.sub(
                r'\{\s*"tool_calls"\s*:\s*\[[\s\S]*?\]\s*\}',
                '',
                full_response,
            )
            # 清洗 [{"action":...}] 任务数组
            full_response = _re.sub(
                r'\[\s*\{[^}]*"action"\s*:[\s\S]*?\}\s*\]',
                '',
                full_response,
            )
            # 清理多余空行
            full_response = _re.sub(r'\n{3,}', '\n\n', full_response)
            full_response = full_response.strip()

            # ★ 2026-08-03: 代码未执行且回复太短 → 补充简短提示（不再提FC）
            if _had_code and len(full_response) < 50:
                full_response += (
                    "\n\n---\n💡 部分操作因安全限制未执行。如需完整操作，可切换到全权模式（设置→安全隐私）。"
                )
                logger.info("[Engine] 代码未执行且回复过短，已补充提示")
            else:
                # ★ 2026-07-24: 全权模式下不过滤代码
                _had_code = False
                logger.info("[Engine] 全权模式：代码过滤器已关闭，模型代码将直接执行")

            # ★ 2026-07-24: 方案A — 移到if/else外面，两种模式都运行
            # 检测条件：代码被过滤(_had_code) OR 模型文本含"调用check_software"等
            _need_auto_check = _had_code
            _resp_lower = full_response.lower()

            # 检测模型是否在文本里"提到"了工具调用（但没有真正调FC）
            if not _need_auto_check:
                _tool_mentions = ['check_software', '调用 check_software', '调用check_software',
                                  'scan_system', '调用 scan_system', '调用scan_system']
                if any(_tm in _resp_lower for _tm in _tool_mentions):
                    _need_auto_check = True
                    logger.info("[Engine] 方案A: 模型在文本中提到工具名，触发自动调用")

            if _need_auto_check:
                _user_lower = (user_message or '').lower()
                # 软件关键词映射
                _SW_MAP = {
                    'docker': ['docker', '容器'],
                    'python': ['python', 'py环境', 'py3'],
                    'git': ['git'],
                    'node': ['node', 'nodejs', 'node.js'],
                    'npm': ['npm'],
                    'ollama': ['ollama'],
                    'pip': ['pip'],
                    'java': ['java', 'jdk'],
                    'go': ['golang', 'go语言'],
                    'rust': ['rust', 'cargo'],
                    'blender': ['blender'],
                    'ffmpeg': ['ffmpeg'],
                    'redis': ['redis'],
                }
                _detected_sw = None
                for _sw, _kws in _SW_MAP.items():
                    if any(_kw in _user_lower for _kw in _kws):
                        _detected_sw = _sw
                        break

                if _detected_sw:
                    try:
                        from src.core.builtin_tools import check_software as _cs
                        _sr = _cs(_detected_sw)
                        _auto_msg = "\n\n📋 **自动检测结果**（系统已自动调用工具）：\n"
                        if _sr.get('installed'):
                            _auto_msg += f"✅ {_detected_sw} 已安装\n"
                            _auto_msg += f"   版本：{_sr.get('version','未知')}\n"
                            _auto_msg += f"   运行：{'✅运行中' if _sr.get('running') else '⚠️未运行'}\n"
                            _auto_msg += f"   路径：{_sr.get('path','未知')}\n"
                        else:
                            _auto_msg += f"❌ {_detected_sw} 未安装\n"
                            _auto_msg += f"   {_sr.get('error','')}\n"
                        yield ("text", _auto_msg)
                        logger.info(f"[Engine] 方案A: 自动调check_software({_detected_sw})")
                    except Exception as _ae:
                        logger.error(f"[Engine] 方案A失败: {_ae}")

                elif any(_kw in _user_lower for _kw in ['系统', '环境', '硬件', '配置', '系统信息']):
                    try:
                        from src.core.builtin_tools import scan_system as _ss
                        _sys_r = _ss()
                        _sys_msg = "\n\n📋 **自动系统扫描**（系统已自动调用）：\n"
                        _sys_msg += f"CPU: {_sys_r.get('cpu','未知')}\n"
                        _sys_msg += f"内存: {_sys_r.get('memory','未知')}\n"
                        _sys_msg += f"GPU: {_sys_r.get('gpu','未知')}\n"
                        _sys_msg += f"磁盘: {_sys_r.get('disk','未知')}\n"
                        yield ("text", _sys_msg)
                        logger.info("[Engine] 方案A: 自动调scan_system")
                    except Exception as _ae2:
                        logger.error(f"[Engine] 方案A(scan)失败: {_ae2}")

        except Exception as _e:
            logger.debug(f"[Engine] 忽略错误: {_e}")

        # 保存AI回复（★ 2026-09-14 来源持久化：metadata.source 记录本次回答方）
        _src = getattr(self, "_reply_source", "")
        save_message(self.conversation_id, "assistant", full_response,
                     metadata=({"source": _src} if _src else None))

        # ★ 2026-09-16 对话即录入·主聊天默录钩子（异步/默认开/本地模型/失败静默）
        # 内部先走孩子对话归档（其独立开关默认关），再做家庭域默录（动词触发+域关键词+低置信度待确认）
        # ★ 2026-09-27 修谎报记账：images 传进默录链（此前只传文本，发图记账时抽取链拿不到图，
        #   静默跳过 → 主聊天模型凭视觉转述自由发挥谎称"已记上"）
        try:
            from src.core.panel_assist import maybe_home_record
            maybe_home_record(self.conversation_id, user_message, full_response, images=images)
        except Exception as _kid_e:
            logger.debug(f"[Engine] 档案归档钩子跳过: {_kid_e}")

        # 触发消息发送事件（Hooks）
        get_event_bus().emit(Events.MESSAGE_SENT, self.conversation_id, full_response)
        get_event_bus().emit(Events.MEMORY_EXTRACTED, self.conversation_id, user_message, full_response)

        # 更新历史
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": full_response})

        # ★ 2026-07-07: 功德行已清理（不再有 warning_text 善意提醒）

        # ═══ 工具执行层：检测并执行模型输出的代码块 ═══
        if full_response:
            try:
                # P1-3: 使用 get_config() 单例
                from src.utils.config import get_config
                tool_cfg = get_config()
                tool_enabled = tool_cfg.get('tool_execution_enabled', True) if isinstance(tool_cfg, dict) else True
                if tool_enabled:
                    from src.core.tool_executor import execute_tools_from_response
                    tool_results = execute_tools_from_response(full_response)
                    if tool_results:
                        for i, tr in enumerate(tool_results):
                            result_text = f"[工具执行 #{i+1}]\n{'✅ 成功' if tr['success'] else '❌ 失败'}\n输出:\n{tr['output']}\n"
                            if tr['error']:
                                result_text += f"错误:\n{tr['error']}\n"
                            self.history.append({"role": "system", "content": result_text})
                            yield ("tool_result", {"index": i+1, "success": tr['success'], "output": tr['output'][:10000], "error": tr.get('error','')})

                            # ★ 2026-07-07: 工具执行成功后自动记录到跨对话记忆+任务列表
                            if tr['success']:
                                try:
                                    from src.core.memory import get_memory_manager
                                    mm = get_memory_manager()
                                    # 检测是否是下载/安装类操作
                                    output_lower = tr['output'].lower()
                                    if any(kw in output_lower for kw in ['下载', '安装', 'install', 'download', 'model_dir', 'snapshot_download', 'pip install']):
                                        mm.save_long_term_memory(
                                            f"tool_exec_{self.conversation_id}_{i}",
                                            f"用户请求: {user_message[:100]}\n执行结果: {tr['output'][:300]}",
                                            category="tool_execution"
                                        )
                                        logger.info(f"[Engine] 工具执行记录到长期记忆: 工具#{i+1}")

                                        # ★ 2026-07-07: 同时创建任务记录（跨对话保留）
                                        try:
                                            from src.core.task_manager import get_task_manager
                                            tm = get_task_manager()
                                            task_id = tm.create_task(
                                                conversation_id=self.conversation_id,
                                                title=f"✅ {user_message[:50]}",
                                                description=f"执行结果: {tr['output'][:500]}",
                                                priority=3
                                            )
                                            # 直接标记为完成（因为已经执行成功了）
                                            if task_id:
                                                tm.complete_task(task_id)
                                            logger.info(f"[Engine] 任务记录已创建并完成: {user_message[:50]}")
                                        except Exception as task_e:
                                            logger.debug(f"[Engine] 任务记录创建失败: {task_e}")
                                except Exception as mem_e:
                                    logger.debug(f"[Engine] 工具执行记忆保存失败: {mem_e}")

                        logger.info(f"[Engine] 工具执行: {len(tool_results)}个代码块")

                        # ★ 2026-07-18: 工具执行后二次推理——让模型解读执行结果
                        try:
                            yield ("text", "\n\n")  # 分隔线
                            second_messages = list(self.history)
                            second_stream = self.model.chat(second_messages, stream=True, temperature=self._temperature)
                            for chunk in second_stream:
                                if chunk:
                                    full_response += chunk
                                    yield ("text", chunk)
                            logger.info("[Engine] 工具执行后二次推理完成")
                        except Exception as second_e:
                            logger.error(f"[Engine] 二次推理失败: {second_e}")
                            yield ("text", f"\n\n⚠️ 结果解读失败，以下是工具执行的原始输出，请参考上方内容。")
            except Exception as e:
                logger.debug(f"[Engine] 工具执行跳过: {e}")

        # ★ 2026-07-18: 记忆提取改为方案C——只在用户明确说"记住"时做AI提取，否则只存L0
        if full_response:
            try:
                from src.core.memory import get_memory_manager
                mm = get_memory_manager()
                # 方案C：只在用户明确要求记住时才做AI提取，传当前模型
                _should_extract = any(kw in user_message.lower() for kw in [
                    '记住', '记下', '请记住', '别忘了', 'remember',
                    '我喜欢', '我不喜欢', '我叫', '我住在', '我的名字'
                ])
                if _should_extract:
                    mm.extract_task_memory(self.conversation_id, user_message, full_response, model=self.model)
                else:
                    # 普通对话：只保存L0原始对话（已在save_message完成），不做AI提取
                    logger.debug("[Engine] 跳过AI记忆提取（用户未要求记住）")
            except Exception as e:
                logger.error(f"[Engine] 记忆提取失败: {e}")

            # L3 习惯学习
            try:
                from src.core.habit_tracker import learn_from_conversation
                learn_from_conversation(user_message, full_response)
            except Exception as e:
                logger.debug(f"[Engine] 习惯学习跳过: {e}")

            # P2: GPEA 自学习引擎已归档（2026-07-02 瘦身，generate_skill 从未成功过）

            # 自动提取用户画像（每次对话都做基础提取）
            try:
                from src.core.user_profile import get_user_profile
                get_user_profile().extract_from_conversation(user_message, full_response)
            except Exception as e:
                logger.error(f"[Engine] 用户画像提取失败: {e}")

            # 用户画像深度分析（每15次对话触发一次，避免频繁LLM调用）
            try:
                from src.core.profile_auto_updater import get_profile_updater
                updater = get_profile_updater()
                state = updater._load_state()
                # 累计15条新消息触发一次批量分析
                if state.total_messages_analyzed % 15 == 0:
                    updater.analyze_recent_conversations(limit=30, since_hours=24)
            except Exception as e:
                logger.debug(f"[Engine] 画像自动更新跳过: {e}")

            # 自动提取结构化任务
            try:
                from src.core.task_manager import get_task_manager
                get_task_manager().extract_tasks_from_conversation(
                    self.conversation_id, user_message, full_response
                )
            except Exception as e:
                logger.error(f"[Engine] 任务提取失败: {e}")

            # Active Memory — 自动提取并存储对话中的关键信息
            try:
                from src.core.active_memory import get_active_memory
                get_active_memory().auto_extract_from_conversation(user_message, full_response)
            except Exception as e:
                logger.error(f"[Engine] Active Memory 提取失败: {e}")

            # P2: specialization_executor 已归档（2026-07-02 瘦身，条件极难触发）

            # 自动保存快照（每5轮保存一次）
            try:
                from src.core.memory import get_memory_manager
                total_msgs = len(self.history)
                if total_msgs > 0 and total_msgs % 10 == 0:  # 每5轮(10条消息)
                    get_memory_manager().save_snapshot(self.conversation_id)
            except Exception as e:
                logger.error(f"[Engine] 快照保存失败: {e}")

        # 自动保存新知识到知识库
        if full_response:
            self._auto_save_knowledge(user_message, full_response)

    def _auto_save_knowledge(self, question, answer):
        """自动将新知识保存到知识库

        ★ 2026-07-21: 默认关闭（config.yaml: auto_learn: false）
        - 关闭时不保存任何对话记录到知识库
        - 开启时检查去重（相似度>80%则跳过）
        - 知识库应该是用户手动管理的，自动塞对话记录只会制造垃圾
        """
        try:
            # ★ 检查开关
            from src.utils.config import Config
            cfg = Config()
            auto_learn_enabled = cfg.get('auto_learn', False)
            if not auto_learn_enabled:
                return  # 开关关闭，不保存

            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()

            # 只保存较长的有价值的问答
            if len(answer) > 100 and len(question) > 5:
                # ★ 去重检查：检查是否已有相似条目
                existing = kb.search(question, top_k=3)
                for doc in existing:
                    existing_text = doc.get('text', '')
                    # 如果已有条目的前50字符与当前问题的前50字符相同，跳过
                    if f"问：{question[:50]}" in existing_text:
                        logger.debug(f"[Engine] auto_learn去重：跳过相似条目")
                        return

                # 组合问答作为知识条目
                knowledge_text = f"问：{question}\n答：{answer[:500]}"
                kb.add_document(knowledge_text, source="auto_learn")
                logger.info(f"[Engine] auto_learn保存: {question[:30]}...")
        except Exception as e:
            logger.error(f"[Engine] 自动保存知识失败: {e}")

    def generate_sync(self, prompt: str, max_tokens: int = 2048) -> str:
        """同步生成文本（不经过道德检查/插件/三段式，直接调用LLM）"""
        try:
            messages = [
                {"role": "system", "content": "你是一个有帮助的AI助手。请直接回复用户的问题。"},
                {"role": "user", "content": prompt},
            ]
            response = self.model.chat(
                messages=messages,
                stream=False,
                temperature=0.7,
                max_tokens=max_tokens,
            )
            return response.strip() if response else ""
        except Exception as e:
            logger.error(f"[Engine] generate_sync 失败: {e}")
            return ""

    # ═══════════════════════════════════════════════════════════════
    # ★ 2026-08-13: Function Calling — AI判断意图直接调工具
    # ═══════════════════════════════════════════════════════════════

    # 危险工具列表（需要确认）
    # ★ A批A1(2026-09-13): 删除6个幽灵名（install_software/uninstall_software/clean_disk/
    #   fix_network/change_wallpaper/delete_file —— 注册表里根本不存在这些工具），现在与实际键全等
    _DANGER_TOOLS = {
        'install_package', 'install_model', 'uninstall_model',
        'write_file', 'edit_file', 'execute_command',
        'delete_path', 'email_send',
    }

    def _vision_reply(self, user_message, images, stream=True, query_mode=None):
        """★ 2026-09-15 A方案：聊天看图 — 图片随消息发送，自动路由视觉模型

        ★ 2026-09-24 路由改造（定版拍板 A方案）：识别方跟随用户当前模式，不再强制本地——
        · 🤖外脑模式 → 图片发给当前外脑识别（须支持视觉；不支持给提示，不偷偷换本地）
        · 🏠本地模式 → 当前模型本身能看图就直接用；否则找本地视觉模型（HarvenEye 优先，自动探测已装视觉模型）；
          都没有 → 提示二选一（装视觉模型如 qwen3-vl:8b-instruct / 切外脑），不再只有 pull 一条路
        标签透明化：「🏠 本地看图 · X」/「🤖 外脑看图 · X」，用户一眼看到图发给了谁（隐私自己掌控）。

        images: dataURL（SPA 前端）或本地路径列表。
        """
        import base64 as _b64
        import os as _os
        import re as _re
        import tempfile as _tmp

        text = (user_message or '').strip() or '请描述这张图片的内容。'
        tmp_paths = []
        try:
            # 1. 图片落地：dataURL → 临时文件；本地路径直接用
            for idx, img in enumerate(images[:4]):  # 最多 4 张，防滥用
                if not isinstance(img, str) or not img:
                    continue
                m = _re.match(r'^data:image/[a-zA-Z+]+;base64,(.+)$', img, _re.S)
                if m:
                    fd, p = _tmp.mkstemp(suffix='.png', prefix=f'hv_img_{idx}_')
                    with _os.fdopen(fd, 'wb') as f:
                        f.write(_b64.b64decode(m.group(1)))
                    tmp_paths.append(p)
                elif _os.path.exists(img):
                    tmp_paths.append(img)
            if not tmp_paths:
                yield ("error", "❌ 图片处理失败，请重新选择图片再发")
                return

            # ★ #918（2026-09-27）聊天发图自动入统一图库：根治"发完即走"——
            #   图不再看完就没，进 images_index 总账（描述=用户随图原话 → 自动分类）；
            #   后续默录链/表单存同一张图时 sha 去重自动合并面板关联。失败绝不挡看图。
            try:
                from src.core import img_store as _istore
                _reg_ids = []
                for p in tmp_paths:
                    _reg = _istore.register(image_data=Path(p).read_bytes(), source='chat',
                                            note=(user_message or '')[:200])
                    if _reg and _reg.get('id'):
                        _reg_ids.append(_reg['id'])
                logger.info(f'[Engine] 聊天发图已入图库 {len(tmp_paths)} 张')
            except Exception as _ie:
                _reg_ids = []
                logger.debug(f'[Engine] 聊天图入图库跳过: {_ie}')

            # ★ 2026-09-23 大图归一化（实测实锤）：相机原图 4MB+ Ollama 会静默丢图，
            # minicpm-v 表现为「抱歉，我无法提供图片中的单词」。看图前统一压缩到安全区。
            from src.core.image_processor import normalize_image_for_vision
            tmp_paths = [normalize_image_for_vision(p) for p in tmp_paths]

            # ── ★ A1 人脸先遣队（2026-11-12）：发图先跑本地 SFace 毫秒级认人 ──
            #   实锤：聊天看图只走 VLM，「这是谁」全靠模型瞎猜（SFace 引擎从不被调用）。
            #   现在认出的家人名字注入提问上下文，下游视觉模型（外脑/理解槽/Eye/Ollama
            #   四分支全受益）只管描述场景，身份靠人脸引擎——数据提取本地毫秒级，
            #   语言理解交给模型，回归哈文架构原则。置信度门槛 SFace 自带
            #   （sim≥threshold 才认，否则标「访客」，宁可保守不标错人）。
            #   失败/未录家人绝不挡看图。
            _face_hint = ''
            try:
                from src.vision.engines.face_engine import FaceEngine as _FE
                if _FE.models_ready():
                    _fe = _FE()
                    _fe.load()
                    _names, _visitors = set(), 0
                    for _fp in tmp_paths:
                        try:
                            _fd = getattr(_fe.infer(_fp), 'data', None) or {}
                            _names.update(_fd.get('matched_names') or [])
                            _visitors += sum(1 for _f in (_fd.get('faces') or [])
                                             if not _f.get('matched'))
                        except Exception:
                            continue
                    if _names:
                        _face_hint = (f"\n\n〔系统参考·本地人脸识别：图中识别出家人 "
                                      f"{'、'.join(sorted(_names))}"
                                      + (f"；另有 {_visitors} 张未录入面孔（请称访客，勿猜身份）〕"
                                         if _visitors else "〕"))
                    elif _visitors:
                        _face_hint = ("\n\n〔系统参考·本地人脸识别：图中有面孔但均非已录入家人，"
                                      "请勿猜测具体身份〕")
            except Exception as _fhe:
                logger.debug(f'[Engine] 人脸先遣队跳过: {_fhe}')

            # ── ★ 2026-09-24 路由决策：跟随用户当前模式 ──
            _mode = query_mode if query_mode is not None else self._query_mode
            use_external = False
            ext_label, ext_id = '', ''
            _fall_understand = False
            if _mode == 'direct':
                _ue, _lb, _mid, _err = self._route_model_for_query('direct')
                if _err:
                    yield ("error", f"❌ {_err}")
                    return
                use_external = bool(_ue)
                ext_label, ext_id = _lb, _mid
                # ★ #1310（1c 换槽知情）看图链同款：自动换槽必须带出
                if getattr(self, '_slot_switch_notice', None):
                    yield ("thinking", self._slot_switch_notice)
                    self._slot_switch_notice = None

            # ★ #1287（2026-10-29）外脑 vision 能力位守卫：无位外脑不准硬发图——
            #   DeepSeek 等纯文本外脑会把图静默忽略后乱答（全面检测实锤：发图问「图里是什么」
            #   答「域名被注册」，比报错更坑）。有位（GLM/Qwen）照旧直发；无位落理解槽。
            if use_external:
                try:
                    from src.core.vision_link import _provider_has_vision as _phv
                    if not _phv(ext_id):
                        use_external = False
                        _fall_understand = True
                except Exception:
                    pass

            # 2. 保存用户消息（存档用原话，不带人脸 hint，防污染历史上下文）
            save_message(self.conversation_id, "user", f"[图片×{len(tmp_paths)}] {text}")
            self.history.append({"role": "user", "content": f"[图片×{len(tmp_paths)}] {text}"})
            # ★ A1：发给视觉模型的提问 = 原话 + 人脸识别参考（四个看图分支共用）
            _ask = text + _face_hint if _face_hint else text
            # ★ 2026-11-12 反吹牛补丁：看图直答链无执行权——明示模型不许谎称已归档，
            #   并附照片真实所在相册。实锤：HarvenEye 谎称「已归类至老谭相册、
            #   存储位置 data\media\family\221.jpg」（目录根本不存在），照片实际在生活照。
            try:
                _real_albums = []
                if _reg_ids:
                    from src.core import img_store as _ist2
                    for _rid in _reg_ids:
                        _info = _ist2.get(_rid) or {}
                        _ra = str(_info.get('album') or '').strip()
                        if _ra and _ra not in _real_albums:
                            _real_albums.append(_ra)
                _ask += ("\n\n〔系统规则·必读：你没有执行归档/移动/删除照片的权限，"
                         "绝不许声称「已归类/已保存/已同步/已存到某位置」。"
                         "用户想归类照片时，告诉他直接说「分到XX相册」即可自动归档，"
                         "或到图片库手动移相册。"
                         + (f"本批照片当前真实所在相册：{'、'.join(_real_albums)}。" if _real_albums else "")
                         + "〕")
            except Exception:
                pass

            if use_external:
                # ═══ 🤖 外脑看图分支 ═══
                # ★ 2026-09-26 零门槛直发（定版拍板）：删关键词预判——能不能看图由外脑自己说，
                #   哈文不做裁判（GLM-5.3-Flash 曾被关键词表误判为纯文本）。报错时转译人话+给建议。
                try:
                    from src.core.multi_model_manager import get_multi_model_manager as _gmm
                    _mm = _gmm()
                    _mname = (_mm.config.get('models', {}).get(ext_id, {}) or {}).get('model_name', '') or ''
                except Exception:
                    _mm, _mname = None, ''

                label = f"🤖 外脑看图 · {ext_label}"
                self._reply_source = label
                yield ("model_source", {"model": ext_id, "label": label,
                                        "is_external": True, "mode": "vision"})
                yield ("thinking", f"☁️ 图片将发送给 {ext_label} 识别…")

                # OpenAI 视觉消息格式（image_url + dataURL）→ mm.generate_response 自动路由
                vision_content = [{"type": "text", "text": _ask}]
                for p in tmp_paths:
                    with open(p, 'rb') as _f:
                        _b = _b64.b64encode(_f.read()).decode()
                    vision_content.append({"type": "image_url",
                                           "image_url": {"url": f"data:image/png;base64,{_b}"}})
                messages = [
                    {"role": "system", "content": "你是哈文，本地AI助手。用户发来了图片，请用中文、口语化地回答关于图片的问题。"},
                    {"role": "user", "content": vision_content},
                ]
                # ★ #1265（2026-10-29）：模型显式传参，不再临时换血 mm.current_model
                #   （旧写法流式消费窗口内全局状态被换，并发批改读到临时值实锤）
                full_response = ""
                for piece in self._adapt_mm_stream(
                        _mm.generate_response(messages, stream=stream, model=ext_id)):
                    if piece.startswith('[错误]'):
                        # ★ 2026-09-26 报错转译：技术报错说人话+给出路，不干巴巴甩原文
                        from src.core.image_processor import translate_vision_error
                        yield ("text", translate_vision_error(piece[4:], ext_label, _mname or ext_id))
                        yield ("model_source", {"model": ext_id, "label": f"⚠️ {ext_label} 看不了这张图",
                                                "is_external": True, "mode": "vision"})
                        return
                    full_response += piece
                    yield ("text", piece)

                save_message(self.conversation_id, "assistant", full_response,
                             metadata={"source": label})
                self.history.append({"role": "assistant", "content": full_response})
                return

            # ═══ 🏠 本地看图分支 ═══
            # ★ #1204（2026-10-25 开发者死命令）：只准用用户选的本地主模型，严禁扫描代选；
            #   不能看图 → 如实报错+列已装可选项（信息告知，换不换用户自己定）
            from src.core.image_processor import build_image_message, model_supports_vision, installed_vision_models
            # ★ #1267（2026-10-29）：主模型=HarvenEye → 直走 Eye 视觉通道（流式）——
            #   Eye 底座 qwen3-vl 就是通用视觉模型，选谁给谁；不按模型名字面误判「不支持看图」
            from src.core import eye_client as _eyecli

            # ★ #1287（2026-10-29）理解槽兜底分支：外脑无 vision 位 → 图落理解槽
            #   （v1.3 场景④：钉选 > 出厂 ollama 通道，回复方如实标注，铁律 0 合规）
            if _fall_understand:
                _ch = {}
                try:
                    from src.core import vision_link as _vl
                    _ch = _vl.resolve_understand_channel()
                except Exception:
                    _ch = {}
                if not _ch:
                    yield ("error", "❌ 当前模型看不了图，也还没配置视觉模型。请到 设置→模型管理 钉选一个支持识图的模型")
                    return
                if _ch.get('provider') == 'api':
                    _ulabel = f"🤖 外脑看图 · {_ch.get('api_model', '')}"
                elif _ch.get('provider') == 'ollama':
                    _ulabel = f"🏠 本地看图 · {_ch.get('label') or _ch.get('model', '')} (Ollama)"
                else:
                    from pathlib import Path as _Pth
                    _ulabel = f"🏠 本地看图 · {_Pth(_ch['gguf']).stem}"
                self._reply_source = _ulabel
                yield ("model_source", {"model": _ch.get('api_model') or _ch.get('model') or 'vision',
                                        "label": _ulabel, "is_external": _ch.get('provider') == 'api',
                                        "mode": "vision"})
                yield ("thinking", "🖼️ 视觉模型正在看图…")
                import base64 as _b64f
                _imgs = []
                for _p in (tmp_paths or []):
                    with open(_p, 'rb') as _f:
                        _imgs.append(_b64f.b64encode(_f.read()).decode())
                _msgs = [
                    {"role": "system", "content": "你是哈文，本地AI助手。用户发来了图片，请用中文、口语化地回答关于图片的问题。"},
                    {"role": "user", "content": _ask, "images": _imgs},
                ]
                _full, _got = [], False
                try:
                    for piece in _eyecli.chat_stream(_msgs, timeout=150.0):
                        _got = True
                        _full.append(str(piece or ''))
                        yield ("text", piece)
                except RuntimeError as _re:
                    yield ("error", f"❌ {_re}")
                    return
                except Exception as _ee:
                    yield ("error", f"❌ 视觉模型看图失败: {_ee}")
                    return
                if _got:
                    save_message(self.conversation_id, "assistant", ''.join(_full),
                                 metadata={"source": _ulabel})
                    self.history.append({"role": "assistant", "content": ''.join(_full)})
                return

            # ★ #1571 件1（2026-11-27）：当前本地主模型=对话型组合 → 编排器接力看图。
            #   泛化 HarvenEye 分流范式：末环是看图理解大脑的组合就是「复合本地视觉模型」。
            #   铁律 0：组合失败如实报错带指引，不偷偷换模型。
            try:
                _cur_local = str(getattr(self.model, 'model', '') or '')
            except Exception:
                _cur_local = ''
            if _cur_local.startswith('chain:'):
                from src.core.chain_engine.registry import get_registry as _greg_c
                from src.core.chain_engine import get_orchestrator as _gorch
                _cr = _greg_c().get_chain(_cur_local[6:])
                if _cr is None:
                    yield ("error", f"❌ 组合 {_cur_local[6:]} 不存在（可能已被删除），请到工坊确认")
                    return
                if 'vision_understand' not in _cr.capabilities():
                    yield ("error", f"❌ 组合「{_cr.name}」没有看图环节，看不了图。到工坊给它的环节选「看图理解」能力")
                    return
                _clabel = f"🧩 {_cr.name}（组合）看图"
                self._reply_source = _clabel
                yield ("model_source", {"model": _cur_local, "label": _clabel,
                                        "is_external": False, "mode": "vision"})
                yield ("thinking", f"🧩 组合「{_cr.name}」正在接力执行…")
                _cpayload = {"image_path": (tmp_paths or [''])[0], "prompt": _ask}
                _cout = _gorch().run(_cr.id, _cpayload)
                if not _cout.get('ok'):
                    yield ("error", f"❌ 组合「{_cr.name}」执行失败: {_cout.get('error')}")
                    return
                _ctext = _cout.get('result')
                if isinstance(_ctext, dict):
                    _so = _ctext.get('step_output')
                    _ctext = _so if isinstance(_so, str) else str(_so or _ctext)[:4000]
                if not isinstance(_ctext, str):
                    _ctext = str(_ctext or '')[:4000]
                if not _ctext.strip():
                    yield ("error", f"❌ 组合「{_cr.name}」执行完成但没有产出文字（到工坊检查末环是否为看图理解）")
                    return
                if len(tmp_paths) > 1:
                    _ctext += f"\n\n（本次组合只看了第一张图，共发来 {len(tmp_paths)} 张）"
                yield ("text", _ctext)
                save_message(self.conversation_id, "assistant", _ctext, metadata={"source": _clabel})
                self.history.append({"role": "assistant", "content": _ctext})
                return

            if _eyecli.is_selected():
                _label = "🏠 本地看图 · 视觉引擎"
                self._reply_source = _label
                yield ("model_source", {"model": "harveneye", "label": _label,
                                        "is_external": False, "mode": "vision"})
                yield ("thinking", "🖼️ HarvenEye 正在看图…")
                import base64 as _b64m
                _imgs = []
                for _p in (tmp_paths or []):
                    with open(_p, 'rb') as _f:
                        _imgs.append(_b64m.b64encode(_f.read()).decode())
                _msgs = [
                    {"role": "system", "content": "你是哈文，本地AI助手。用户发来了图片，请用中文、口语化地回答关于图片的问题。"},
                    {"role": "user", "content": _ask, "images": _imgs},
                ]
                _full, _got = [], False
                try:
                    for piece in _eyecli.chat_stream(_msgs, timeout=150.0):
                        _got = True
                        _full.append(str(piece or ''))
                        yield ("text", piece)
                except RuntimeError as _re:
                    yield ("error", f"❌ {_re}")
                    return
                except Exception as _ee:
                    yield ("error", f"❌ 视觉引擎看图失败: {_ee}")
                    return
                if _got:
                    save_message(self.conversation_id, "assistant", ''.join(_full),
                                 metadata={"source": _label})
                    self.history.append({"role": "assistant", "content": ''.join(_full)})
                else:
                    yield ("error", "❌ 视觉引擎返回了空回答，请把图片重新发一次")
                return
            _base = getattr(self.model, 'base_url', 'http://localhost:11434')
            vision_model = (getattr(self.model, 'model', '') or '').strip()
            try:
                if not vision_model or not model_supports_vision(vision_model, base_url=_base):
                    vision_model = None
            except Exception:
                vision_model = None
            if not vision_model:
                # ★ #1356（2026-11-01）：发图预检引导卡——按硬件体检分流推荐
                #   （显存够→推本地档位自选；不够→只推外脑，绝不忽悠装跑不动的），
                #   卡片生成失败降级回 #1204 原版话术，永不炸聊天。
                _cur = (getattr(self.model, 'model', '') or '').strip() or '（未选）'
                try:
                    from src.core.image_processor import vision_guide_card as _vgc
                    _card = _vgc(_base)
                except Exception:
                    _card = ''
                if _card:
                    yield ("text",
                           f"⚠️ 当前本地模型「{_cur}」不支持看图。\n\n{_card}")
                else:
                    try:
                        _hint = ' / '.join(installed_vision_models(base_url=_base)[:4])
                    except Exception:
                        _hint = ''
                    yield ("text",
                           f"⚠️ 当前本地模型「{_cur}」不支持看图。\n\n"
                           "1️⃣ 到 **设置 → 模型中心** 自己换一个能看图的本地模型"
                           f"（已装可选：{_hint or '还没有，需要先安装一个'}）\n"
                           "2️⃣ 或切到 🤖 外脑模式，选一个支持看图的外脑（图片会发到云端识别）\n\n"
                           "换好后把图片重新发一次即可。")
                yield ("model_source", {"model": "none", "label": "⚠️ 当前模型不能看图", "is_external": False, "mode": "vision"})
                return

            label = f"🏠 本地看图 · {vision_model}"
            self._reply_source = label
            yield ("model_source", {"model": vision_model, "label": label,
                                    "is_external": False, "mode": "vision"})
            yield ("thinking", f"🖼️ {vision_model} 正在看图…")

            # 3. 多模态消息 → Ollama（OpenAI 兼容接口，流式）
            messages = [
                {"role": "system", "content": "你是哈文，本地AI助手。用户发来了图片，请用中文、口语化地回答关于图片的问题。"},
                build_image_message(_ask, tmp_paths),
            ]
            response = self.model.client.chat.completions.create(
                model=vision_model, messages=messages, stream=stream, max_tokens=2048,
            )
            full_response = ""
            if stream:
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        piece = chunk.choices[0].delta.content
                        full_response += piece
                        yield ("text", piece)
            else:
                full_response = response.choices[0].message.content or ""
                yield ("text", full_response)

            # 4. 回答持久化（带来源，历史对话也能看到是谁答的）
            save_message(self.conversation_id, "assistant", full_response,
                         metadata={"source": label})
            self.history.append({"role": "assistant", "content": full_response})
        except Exception as e:
            logger.error(f"[Engine] 看图回答失败: {e}", exc_info=True)
            yield ("error", f"❌ 看图失败：{e}")
        finally:
            for p in tmp_paths:
                try:
                    if 'hv_img_' in str(p):
                        _os.remove(p)
                except Exception:
                    pass

    @staticmethod
    def _strip_assist_context(msg: str) -> str:
        """★ 2026-09-16 快车道防误触发（实测实锤bug）：助手小窗自由对话把
        "（用户此刻在「XX」面板…）"前缀和【背景补充】页面上下文拼进消息原文，
        快车道若对拼接文本做关键词匹配，上下文里的"目录/应用/设置"等词会跟
        用户一句"打开家庭生活"拼出误触发 → 转给 Computer Ops 答非所问。
        快车道只应匹配用户原话。"""
        try:
            import re as _re_ctx
            s = _re_ctx.sub(r'【背景补充（供参考，不用复述）】[\s\S]*$', '', msg or '').strip()
            s = _re_ctx.sub(r'^（用户此刻在[^）]*）\s*', '', s).strip()
            return s or (msg or '')
        except Exception:
            return msg or ''

    @staticmethod
    def _match_nav_target(msg: str):
        """★ 2026-09-16 导航兜底（B路线后端版）：祈使"打开/进入/切换 XX"且 XX 命中
        已装面板名或出厂分类 → 返回 (path, name)；否则 None（照旧走原快车道/模型）。
        刻意保守：≤24字、无问号、目标词精确（原词优先逐级剥后缀，互含≤2字），
        与前端 A 路线（spa/src/navCommands.js）同一套闸门。
        只认面板名+出厂分类+面板管理页 —— 与电脑操作零冲突；"设置"等内置页
        与"打开Windows设置"撞车，刻意不在此兜底。"""
        try:
            import re as _re_nav
            t = (msg or '').strip()
            if not t or len(t) > 24 or _re_nav.search(r'[？?]', t):
                return None
            m = _re_nav.match(
                r'^(?:请|麻烦|帮我|劳驾)?(?:帮我)?(?:打开|进入|切换到|切换|带我去|跳转到|跳转|去(?:看看|一下|下)?|查看|看看)\s*(.{1,16}?)\s*(?:面板|页面|空间|界面|的页|页)?[吧呗哈哦啦]?$',
                t)
            if not m:
                return None
            kw = (m.group(1) or '').strip()
            if not kw:
                return None
            cands = [kw]
            cur = kw
            for _ in range(3):
                nxt = _re_nav.sub(r'(面板|页面|空间|中心|界面|工具|记录页|的页|页|库)$', '', cur).strip()
                if not nxt or nxt == cur:
                    break
                cands.append(nxt)
                cur = nxt
            targets = []  # (name, path)
            try:
                from src.core.panel_manager import get_panel_manager as _gpm_nav
                for _p in (_gpm_nav().get_all_panels() or []):
                    if _p.get('id') and _p.get('name'):
                        targets.append((_p['name'], '/panel/' + _p['id']))
            except Exception:
                pass
            targets += [('家庭模板', '/space/family'), ('公司模板', '/space/company'),
                        ('创作模板', '/space/create'), ('面板中心', '/panels')]
            for cand in cands:
                hits = []
                for name, path in targets:
                    if cand == name:
                        hits.append((path, name))
                    elif (cand in name and len(name) - len(cand) <= 2) or (name in cand and len(cand) - len(name) <= 2):
                        hits.append((path, name))
                if len(hits) == 1:
                    return hits[0]
                if len(hits) > 1:
                    return ('/panels', '面板管理')
            # 常用别名（与前端 navCommands.js PANEL_ALIAS 同步维护）：别名 → 面板ID
            _panel_ids = set()
            for _nm, _pt in targets:
                if _pt.startswith('/panel/'):
                    _panel_ids.add(_pt.rsplit('/', 1)[-1])
            _NAV_ALIAS = [
                ('孩子成长档案', 'kid_archive'), ('成长档案', 'kid_archive'), ('成长', 'kid_archive'), ('孩子档案', 'kid_archive'),
                ('食物库存', 'fridge_stock_1'), ('冰箱', 'fridge_stock_1'),
                ('记账账本', 'accounting_daily_1'), ('记账', 'accounting_daily_1'), ('账本', 'accounting_daily_1'),
                ('车辆保养', 'car_maintenance_1'), ('车辆', 'car_maintenance_1'), ('汽车', 'car_maintenance_1'),
                ('宠物', 'pet_care_1'),
                ('绿植', 'plants_1'), ('花草', 'plants_1'), ('浇花', 'plants_1'),
                # ★ 2026-10-11 #1099 健康中枢：体检指针改 health_file（旧 health_checkup_1 早已退役），补健身/成长
                ('健康档案', 'health_file'), ('体检', 'health_file'), ('体检报告', 'health_file'), ('复查', 'health_file'),
                ('健身记录', 'fitness_log'), ('健身', 'fitness_log'), ('运动记录', 'fitness_log'),
                ('孩子成长', 'child_growth'), ('接种', 'child_growth'), ('视力', 'child_growth'),
                ('家庭日历', 'home_calendar'), ('日历', 'home_calendar'), ('日程', 'home_calendar'),
                ('物品存放', 'home_items'), ('物品', 'home_items'), ('东西在哪', 'home_items'),
                ('老人关怀', 'elder_care'), ('老人', 'elder_care'), ('用药', 'elder_care'),
                ('家庭药箱', 'med_cabinet'), ('药箱', 'med_cabinet'), ('吃药', 'med_cabinet'),
                ('证件保单', 'home_docs'), ('证件', 'home_docs'), ('保单', 'home_docs'),
                ('水电气', 'home_utilities'), ('缴费', 'home_utilities'),
                ('全家待办', 'family_todo'),
                ('人情往来', 'home_favors'), ('人情', 'home_favors'), ('随礼', 'home_favors'),
                ('家庭维修', 'home_repair'), ('维修', 'home_repair'),
            ]
            for cand in cands:
                for alias, pid in _NAV_ALIAS:
                    if cand != alias:
                        continue
                    if pid in _panel_ids:
                        return ('/panel/' + pid, alias)
                    return ('/panels', '面板管理')  # 面板未装 → 打开面板页
            return None
        except Exception:
            return None

    def _try_smart_route(self, user_message: str, stream: bool = True):
        """★ 2026-08-13: 智能关键词组合路由 — 不漏词，不依赖FC"""
        try:
            from src.core.agent_runner import run_script_agent
            # ★ 2026-09-16 防御纵深：即使调用方忘了剥离小窗上下文，快车道也只匹配用户原话
            #   （顺带保护 computer_use 的路径提取不被【背景补充】里的假路径污染）
            user_message = self._strip_assist_context(user_message)
            msg = user_message.lower().strip()

            # ★ #1154（2026-10-16）L1 冰箱一句话直录 —— 零风险确定性动作，优先级最高。
            #   「娃娃菜2颗放冰箱」直写库秒回执，零确认零 token；解析不出自动回退原路由。
            try:
                from src.core.fridge_hub import try_quick_fridge_add as _tqfa
                _fr_out = _tqfa(user_message)
                if _fr_out:
                    logger.info(f"[Engine] L1 冰箱直录命中: {user_message[:30]}")
                    if stream:
                        yield ("text", _fr_out)
                    return _fr_out
            except Exception as _fre:
                logger.debug(f"[Engine] 冰箱直录快车道异常(回退原路由): {_fre}")

            # ★ 2026-08-14: 复杂操作信号词 — 含这些词时不拦截，让FC处理
            # ★ 2026-08-14: 去掉"删除""游戏"等误拦截词，改为更精确的复合词
            # ★ 2026-08-16: i18n — 增加英文信号词（英文指令同样跳过B方案走FC）
            # ★ 2026-10-20 高频本地操作快车道（速度实锤反馈：清空回收站走 FC 要
            #   外脑 9s 首轮 + Clear-RecycleBin 本身 38~66s；本地直执行省掉外脑轮）
            _quick_ops = [
                ('清空回收站', '倒垃圾', '清空垃圾', '清空废纸篓'),
                ('打开下载', '打开下载文件夹'),
                ('打开桌面', ), ('打开文档', '打开我的文档'), ('打开图片', '打开图片文件夹'),
                ('打开音乐', ), ('打开视频', '打开视频文件夹'),
            ]
            # ★ 2026-11-11 方案B修复①：直执行守卫（打桩探针危险级 2 例实锤）——
            #   问句/犹豫句/条件句绝不直执行：「清空回收站之前我想先看看…」「清空回收站的文件
            #   还能恢复吗」曾被五个字劫持直接 Clear-RecycleBin。含疑问/条件标记一律不进直执行。
            _QUICKOP_BLOCKERS = ('？', '?', '吗', '呢', '吧', '么', '怎么', '怎样', '如何',
                                 '能不能', '可不可以', '会不会', '是不是', '之前', '以后',
                                 '的时候', '先别', '还是', '要不要', '该不该')
            for _ops in (_quick_ops if not any(k in msg for k in _QUICKOP_BLOCKERS) else []):
                if any(k in msg for k in _ops):
                    try:
                        import subprocess as _sp
                        if '回收' in _ops[0] or '垃圾' in _ops[0]:
                            # PowerShell COM 清空（比 Clear-RecycleBin 少驱动器枚举开销；
                            # 三盘并行意义不大，单调用即可；-Force 免确认）
                            _cmd = 'powershell -NoProfile -Command "Clear-RecycleBin -Force -ErrorAction SilentlyContinue; Write-Output done"'
                            _r = _sp.run(_cmd, capture_output=True, text=True, shell=True, timeout=180,
                                         encoding='utf-8', errors='replace')
                            _out = "🗑 回收站已清空。"
                            logger.info(f"[Engine] 快车道·清空回收站 rc={_r.returncode}")
                        else:
                            _folder_map = {'下载': 'Downloads', '桌面': 'Desktop', '文档': 'Documents',
                                           '图片': 'Pictures', '音乐': 'Music', '视频': 'Videos'}
                            _fold = next((v for k, v in _folder_map.items() if k in msg), None)
                            if _fold:
                                _sp.Popen(f'explorer.exe shell:{_fold}', shell=True)
                                _out = f"📁 已打开{_fold} 文件夹。"
                                logger.info(f"[Engine] 快车道·打开文件夹: {_fold}")
                            else:
                                continue
                        _out = ("⚡ 本地快车道 · " if False else "") + _out + "\n\n（本地直接执行，未经过大模型）"
                        if stream:
                            yield ("text", _out)
                        return _out
                    except Exception as _qo_e:
                        logger.warning(f"[Engine] 快车道本地操作失败(回退FC): {_qo_e}")
                        break

            # ★ #1509（2026-11-13）：用户自定义口令（config custom_keywords，追加式）——
            #   命中即 0 token 直出 reply / 打开 open 路径；同样过疑问句守卫，防「XX吗」误触发
            #   守卫=快车道疑问词+复杂信号疑问词（哪里/什么/为什么等），「工作文件在哪里」不误开
            _ckw_blockers = _QUICKOP_BLOCKERS + ('哪里', '哪个', '哪些', '什么', '为什么', '为啥', '多久', '几')
            if not any(k in msg for k in _ckw_blockers):
                for _ck in _load_custom_keywords():
                    _trig = str(_ck.get('trigger') or '').strip()
                    if not _trig or _trig not in user_message:
                        continue
                    try:
                        _out = ''
                        _open = str(_ck.get('open') or '').strip()
                        if _open:
                            import os as _os
                            _os.startfile(_os.path.normpath(_open))
                            _out = str(_ck.get('reply') or '').strip() or f"📂 已打开：{_open}"
                        else:
                            _out = str(_ck.get('reply') or '').strip()
                        if _out:
                            logger.info(f"[Engine] 自定义口令命中: {_trig}")
                            yield ("model_source", {
                                "model": "custom_kw", "label": "⚡ 自定义口令 · 本地直出",
                                "is_external": False, "mode": "fastlane",
                            })
                            if stream:
                                yield ("text", _out)
                            return _out
                    except Exception as _ck_e:
                        logger.warning(f"[Engine] 自定义口令执行失败({_ck.get('trigger')}): {_ck_e}")
                        continue

            _COMPLEX_SIGNALS = [
                # 中文
                '并删除', '并卸载', '并清理', '并找出', '并分析', '并对比',
                '里面', '哪个', '哪些', '为什么', '占多少', '占了',
                '最多', '最少', '分析', '批量', '全部', '所有', '找出',
                '对比', '比较', '统计', '汇总', '总结',
                '残留', '清理掉', '卸载并', '卸载删除', '删除', '并删除',
                # 英文
                'and delete', 'and remove', 'and uninstall', 'and clean',
                'and find', 'and analyze', 'and compare', 'and list',
                'inside', 'which ', 'why ', 'how much', 'how many',
                'largest', 'biggest', 'smallest', 'most ', 'least ',
                'analyze', 'analyse', 'batch', 'all of', 'find ',
                'compare', 'statist', 'summar', 'leftover', 'residual',
                'uninstall and', 'delete and', 'remove and',
                'delete all', 'remove all', 'uninstall all', 'everything',
            ]
            _is_complex = any(k in msg for k in _COMPLEX_SIGNALS)
            # ★ 2026-10-20 FC慢治理#5: 疑问句排除 — 「为什么…慢」被「为什么」误判复杂
            #   导致三连装载 FC 全套后快车道又跳过（预处理白做实锤）；纯疑问句=咨询
            #   不是操作，回 B 方案/快车道正常路由。含明确祈使动作词的不算纯疑问
            if _is_complex:
                _q_start = msg.strip().startswith(('为什么', '怎么', '怎样', '什么', '为啥', 'why ', 'what ', 'how '))
                _q_end = msg.strip().endswith(('？', '?', '吗', '么'))
                _has_imperative = any(_kw in msg for _kw in (
                    '帮我', '给我', '请帮我', '删掉', '删除', '清理', '卸载', '打开', '关闭',
                    '设置', '安装', '停用', '批量', '找出并', '检查并'))
                if (_q_start or _q_end) and not _has_imperative:
                    _is_complex = False
                    logger.info(f"[Engine] 疑问句排除复杂信号词，正常路由: {user_message[:50]}")
            if _is_complex:
                logger.info(f"[Engine] 检测到复杂操作信号词，跳过B方案，让FC处理: {user_message[:50]}")
                # ★ #1330（2026-10-30）：置位信号——若后续路由到本地通道（无 FC），
                #   send 主链会据此给用户诚实提示，不再静默降级成纯聊天光说不练
                self._fc_signal_pending = True
                return None

            # ★ 2026-09-16 导航兑底：祈使"打开/进入 + 面板名/分类名"不进 Computer Ops，
            #   输出 [NAVIGATE:] 标记由前端/web_app.py 提取后跳转（web_app.py L1135 同一套）
            _nav_hit = self._match_nav_target(user_message)
            if _nav_hit:
                _path, _name = _nav_hit
                _nav_out = f"🧭 已为你打开「{_name}」～\n[NAVIGATE:{_path}]"
                logger.info(f"[Engine] 快车道导航兑底: {user_message[:30]} → {_path}")
                if stream:
                    yield ("text", _nav_out)
                return _nav_out

            # computer_use 触发：关键词组合匹配
            # ★ 2026-11-11 方案B修复②（打桩探针 5 例劫持实锤）两道闸：
            #   闸1 疑问守卫：知识问句不劫持（内存条怎么选/截图快捷键是什么/进程和线程的区别…）
            #   闸2 外脑放行：外脑在线时知识型词交还模型（FC 理解力成熟，词匹配只兜本地模型）；
            #       确定性动作（打开盘/文件夹/应用名/列出磁盘）仍走快车道零 token 直执行
            _q_markers = ('怎么', '怎样', '如何', '什么', '为什么', '为啥', '区别')
            _pure_question = ((any(k in msg for k in _q_markers)
                               or msg.endswith(('？', '?', '吗', '呢', '么', '吧')))
                              and not any(k in msg for k in ('帮我', '给我', '请')))
            try:
                from src.core.multi_model_manager import is_local_model as _isl_b
                _ext_online = not _isl_b(getattr(getattr(self, 'model', None), 'current_model', None))
            except Exception:
                _ext_online = False
            _kw_allowed = not _pure_question and not _ext_online
            computer_use_triggers = False
            # 截图（知识型 → 受两道闸）
            if _kw_allowed and any(k in msg for k in ['截图', '截屏', 'screenshot']):
                computer_use_triggers = True
            # 打开+盘/目录/文件夹（确定性动作；仅疑问守卫，不含"思路""文件"等抽象词）
            if not _pure_question and any(k in msg for k in ['打开', 'open']) and any(k in msg for k in ['盘', '目录', '文件夹', 'folder', 'directory', 'drive']) and not any(k in msg for k in ['思路', '文件', '方式', '方法']):
                computer_use_triggers = True
            # 查看内存/进程（知识型 → 受两道闸）
            if _kw_allowed and (any(k in msg for k in ['内存', 'memory', 'ram']) or any(k in msg for k in ['进程', 'process', '查看进程'])):
                computer_use_triggers = True
            # 整理桌面（知识型 → 受两道闸）
            if _kw_allowed and any(k in msg for k in ['整理桌面', '整理文件', 'clean desktop', 'organize desktop', 'tidy desktop']):
                computer_use_triggers = True
            # 窗口控制（知识型 → 受两道闸）
            if _kw_allowed and any(k in msg for k in ['最大化', '最小化', 'maximize', 'minimize']):
                computer_use_triggers = True
            # 打开+应用名（确定性动作；仅疑问守卫）
            if not _pure_question and '打开' in msg and any(k in msg for k in ['设置', '浏览器', '记事本', '计算器', '画图', '资源管理器', '任务管理器', '终端', 'cmd', '控制面板', '应用']):
                computer_use_triggers = True
            # ★ 2026-08-16: 英文 open+应用名（确定性动作）
            if not _pure_question and 'open' in msg and any(k in msg for k in ['settings', 'browser', 'edge', 'chrome', 'notepad', 'calculator', 'mspaint', 'paint', 'explorer', 'task manager', 'terminal', 'cmd', 'control panel', 'control panel']):
                computer_use_triggers = True
            # 列出磁盘（确定性动作）
            if not _pure_question and any(k in msg for k in ['列出', '查看', 'list']) and any(k in msg for k in ['盘', '磁盘', 'drive', 'disk']):
                computer_use_triggers = True
            # 输入文字（确定性动作）
            if not _pure_question and any(k in msg for k in ['输入文字', '打字', 'type text']):
                computer_use_triggers = True

            if computer_use_triggers:
                result = run_script_agent('computer_use', user_message)
                from src.utils.i18n import tr as _tr
                output = result.get('output', result.get('error', _tr('route.done')))
                if stream:
                    yield ("text", f"🤖 {_tr('route.computer_ops')} {output}")
                return output

            # system_ops 触发
            # ★ 2026-09-19 (#794): 拆除危险动词分支 — 装软件/安装/卸载/uninstall/清理/clean
            #   全部移交 FC 走确认门（破坏性操作必须确认铁律）。快车道只保留零风险安全分支。
            system_ops_triggers = False
            if any(k in msg for k in ['网络修复', '修网络', '网络 fix', 'flushdns', 'fix network', 'repair network', 'network repair']):
                system_ops_triggers = True
            if any(k in msg for k in ['查配置', '系统配置', 'system info', '电脑配置', 'system information', 'pc specs', 'my specs']):
                system_ops_triggers = True
            if any(k in msg for k in ['换壁纸', '改壁纸', 'wallpaper', 'change wallpaper']):
                system_ops_triggers = True
            if any(k in msg for k in ['查进程', '看进程']) and '进程' in msg:
                system_ops_triggers = True

            if system_ops_triggers:
                result = run_script_agent('system_ops', user_message)
                from src.utils.i18n import tr as _tr
                output = result.get('output', result.get('error', _tr('route.done')))
                if stream:
                    yield ("text", f"🤖 {_tr('route.system_ops')} {output}")
                return output

            # ★ 文件操作类工具（直接调builtin_tools，不走AI写代码）
            try:
                from src.core.builtin_tools import call_tool
                import json as _json

                # list_dir: 检查/查看 + 目录/磁盘/文件夹
                if any(k in msg for k in ['检查', '查看', '看看', '列出', 'list', 'check', 'show']) and any(k in msg for k in ['目录', '磁盘', '文件夹', '盘', 'folder', 'directory', '里面', 'drive']):
                    # 提取路径（支持多种格式）
                    import re as _re
                    import os as _os
                    path = None
                    # 格式1: D:\SteamLibrary
                    path_match = _re.search(r'([A-Za-z]:\\[^\s，。、并和且]*)', user_message)
                    if path_match:
                        path = path_match.group(1)
                    # 格式2: D盘SteamLibrary → D:\SteamLibrary
                    if not path:
                        drive_match = _re.search(r'([A-Za-z])盘\s*([^\s，。、的并和且]+)', user_message)
                        if drive_match:
                            drive = drive_match.group(1).upper()
                            folder = drive_match.group(2).strip()
                            # 去掉修饰词
                            folder = _re.sub(r'[的里面有什么都有啥？?]', '', folder).strip()
                            if folder:
                                path = f"{drive}:\\{folder}"
                            else:
                                path = f"{drive}:\\"
                    # ★ 2026-08-16: 格式3（英文）: "D drive SteamLibrary" / "D:\" → D:\SteamLibrary
                    if not path:
                        drive_match = _re.search(r'\b([A-Za-z])\s*(?:drive|:)\s*\\?\s*([A-Za-z0-9_\-\.]+)', user_message)
                        if drive_match:
                            drive = drive_match.group(1).upper()
                            folder = drive_match.group(2).strip()
                            if folder:
                                path = f"{drive}:\\{folder}"
                            else:
                                path = f"{drive}:\\"
                    # ★ 验证路径存在
                    if path and not _os.path.exists(path):
                        # 路径不存在，不拦截，让FC处理
                        path = None
                    # ★ 2026-08-17: Linux/macOS 路径格式（/home/user/xxx）
                    if not path:
                        unix_match = _re.search(r'(/(?:home|Users|tmp|var|opt|etc|mnt|media|root)[^\s，。、的并和且]*)', user_message)
                        if unix_match:
                            path = unix_match.group(1).rstrip('/')
                            if not _os.path.exists(path):
                                path = None
                    # ★ 2026-08-17: ~ 路径（~/Desktop 等）
                    if not path:
                        tilde_match = _re.search(r'(~[^\s，。、的并和且]*)', user_message)
                        if tilde_match:
                            path = _os.path.expanduser(tilde_match.group(1))
                            if not _os.path.exists(path):
                                path = None
                    if path:
                        result = call_tool('list_dir', path=path)
                        from src.utils.i18n import tr as _tr
                        if isinstance(result, dict) and result.get('success'):
                            items = result.get('items', [])
                            dirs = [i for i in items if i.get('is_dir')]
                            files = [i for i in items if not i.get('is_dir')]
                            lines = [_tr('route.dir_header', path=path)]
                            lines.append(_tr('route.dir_folders', n=len(dirs)))
                            for d in dirs[:20]:
                                lines.append(f"    📁 {d['name']}")
                            if len(dirs) > 20:
                                lines.append(_tr('route.dir_more', n=len(dirs)-20))
                            lines.append(_tr('route.dir_files', n=len(files)))
                            for f in files[:20]:
                                size = f.get('size', 0)
                                size_str = f" ({size/1024/1024:.1f}MB)" if size > 1024*1024 else f" ({size/1024:.0f}KB)" if size > 1024 else ""
                                lines.append(f"    📄 {f['name']}{size_str}")
                            if len(files) > 20:
                                lines.append(_tr('route.dir_more', n=len(files)-20))
                            output = "\n".join(lines)
                        else:
                            output = _json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                        if stream:
                            yield ("text", output)
                        return output

                # read_file: 读/看 + 文件名（支持无扩展名）
                if any(k in msg for k in ['读', '看看', '查看', 'read', 'show']) and any(k in msg for k in ['文件', 'config', 'yaml', 'json', 'txt', 'py', 'md', '配置', 'file']):
                    import re as _re
                    # 先匹配带扩展名的路径
                    path_match = _re.search(r'([A-Za-z]:\\[^\s]*\.\w+)', user_message)
                    # 再匹配 D盘xxx/config.yaml 格式
                    if not path_match:
                        path_match = _re.search(r'(\w+\.(?:yaml|json|txt|py|md|cfg|ini|conf))', user_message, _re.IGNORECASE)
                    if path_match:
                        result = call_tool('read_file', path=path_match.group(1))
                        output = result.get('content', str(result)) if isinstance(result, dict) else str(result)
                        if stream:
                            yield ("text", f"📄 文件内容：\n{output[:2000]}")
                        return output

                # check_software: 装了吗/有没有 + 软件名（支持中英文）
                if any(k in msg for k in ['装了吗', '有没有', '装没装', '是否安装', 'installed', 'do i have']) :
                    # 提取软件名（支持中英文）
                    import re as _re
                    sw_match = _re.search(r'(?:装了吗|有没有|装没装|是否安装)\s*([A-Za-z\u4e00-\u9fff]+)', user_message)
                    if not sw_match:
                        # ★ 2026-08-16: 英文格式 "is norton installed" / "do i have norton"
                        sw_match = _re.search(r'(?:is\s+|do\s+i\s+have\s+)([A-Za-z0-9][A-Za-z0-9 \-\.]{0,40}?)\s+(?:installed|on\s+this)', msg)
                    if sw_match:
                        result = call_tool('check_software', name=sw_match.group(1))
                        output = _json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                        if stream:
                            yield ("text", f"🔍 {output}")
                        return output

                # get_time: 几点/时间
                if any(k in msg for k in ['几点', '现在时间', '什么时间', 'current time']):
                    result = call_tool('get_time')
                    output = result.get('time', str(result)) if isinstance(result, dict) else str(result)
                    if stream:
                        yield ("text", f"🕐 {output}")
                    return output

            except Exception as e:
                logger.debug(f"[Engine] 工具调用失败: {e}")

            return None
        except Exception as e:
            logger.debug(f"[Engine] 智能路由失败: {e}")
            return None

    def _try_function_calling(self, user_message: str, stream: bool = True):
        """FC意图判断：AI判断用户想做什么，直接调工具"""
        try:
            from src.core.builtin_tools import get_tool_definitions, call_tool, TOOL_REGISTRY
            from src.core.agent_runner import run_script_agent

            # 获取工具定义
            # ★ 方案A 联网授权制（2026-10-05 定版拍板）：联网工具按🌐开关注入
            tool_defs = get_tool_definitions(
                allow_web_search=bool(getattr(self, '_web_search_this_query', False)))
            if not tool_defs:
                return None

            # 加上computer_use和system_ops作为工具
            fc_tools = tool_defs + [
                {
                    "type": "function",
                    "function": {
                        "name": "computer_use",
                        "description": "操作电脑：打开D盘/截图/打开设置/打开浏览器/查看内存/查看进程/整理桌面/窗口最大化/窗口最小化",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "description": "用户想做的操作，比如'打开D盘''截图''打开设置'"}
                            },
                            "required": ["action"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "system_ops",
                        "description": "系统管理：装软件/卸软件/清理磁盘/修网络/查配置/查进程/换壁纸",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "description": "用户想做的操作，比如'装微信''清理磁盘''查配置'"}
                            },
                            "required": ["action"]
                        }
                    }
                }
            ]

            # 调用AI判断意图
            messages = [
                {"role": "system", "content": "你是哈文AI的意图判断器。根据用户消息判断需要调用什么工具。如果用户只是聊天/问问题，不要调用任何工具。"},
                {"role": "user", "content": user_message}
            ]

            response = self.model.chat(
                messages=messages,
                stream=False,
                temperature=0.0,
                tools=fc_tools,
            )

            if not response:
                return None

            # 解析tool_calls
            tool_calls = None
            if hasattr(response, 'choices') and response.choices:
                msg = response.choices[0].message
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    tool_calls = msg.tool_calls
            elif isinstance(response, dict):
                choices = response.get('choices', [])
                if choices:
                    msg = choices[0].get('message', {})
                    tool_calls = msg.get('tool_calls')

            if not tool_calls:
                return None

            # 执行第一个工具调用
            for tc in tool_calls[:1]:
                if hasattr(tc, 'function'):
                    fn = tc.function
                    tool_name = fn.name
                    import json as _json
                    try:
                        arguments = _json.loads(fn.arguments) if isinstance(fn.arguments, str) else fn.arguments
                    except:
                        arguments = {}
                else:
                    tool_name = tc.get('function', {}).get('name', '')
                    arguments = tc.get('function', {}).get('arguments', {})
                    if isinstance(arguments, str):
                        import json as _json
                        try:
                            arguments = _json.loads(arguments)
                        except:
                            arguments = {}

                if not tool_name:
                    continue

                # 执行工具
                if tool_name in ('computer_use', 'system_ops'):
                    # 脚本型Agent
                    action = arguments.get('action', user_message)
                    result = run_script_agent(tool_name, action)
                    output = result.get('output', result.get('error', '执行完成'))
                elif tool_name in TOOL_REGISTRY:
                    # 内置工具
                    result = call_tool(tool_name, **arguments)
                    output = _json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                else:
                    return None

                # 返回结果给用户
                if stream:
                    yield ("text", f"🤖 [{tool_name}] {output}")
                return output

            return None

        except Exception as e:
            logger.debug(f"[Engine] FC失败（回退到正常流程）: {e}")
            return None


# 全局引擎单例（用于 resource_hub 等模块）
_engine_instance = None


# ═══════════════════════════════════════════════════════════════
# ★ 2026-07-17: Skill触发词自动执行（不依赖FC，引擎直接检测）
# ═══════════════════════════════════════════════════════════════

def _try_skill_trigger_match(user_message):
    """★ 2026-07-17: 只检测匹配，不执行（用于流式执行前）
    ★ 2026-09-26 修复（已批准A方案）：
      ① 触发词改正确正则语义——'记账.*元' 以前被当死字符串找子串，永远不命中
      ② 返回 (skill_name, action)：action 按消息意图从技能动作表选真实值
        （以前执行点硬编码 'main'，accounting 等动作表无 main → 必报'不支持的操作'）
      ③ 数据写型动作（add/delete 等）不自动执行——参数给不了，瞎跑会记废账，
        交回正常管线（智能拆分/确认卡接管）；只放行查询型与生成/执行型
    """
    import re as _re_trig
    try:
        from src.core.scripts_runner import list_skill_scripts
        from pathlib import Path
        skills = list_skill_scripts()
        if not skills:
            return None
        for skill in skills:
            skill_name = skill.get('skill_name', '')
            skill_md = Path(__file__).parent.parent.parent / 'data' / 'skills' / skill_name / 'SKILL.md'
            if not skill_md.exists():
                continue
            content = skill_md.read_text(encoding='utf-8')
            if not content.startswith('---'):
                continue
            end = content.find('---', 3)
            if end <= 0:
                continue
            triggers = ''
            semantic_actions = []
            _in_actions = False
            for line in content[3:end].split('\n'):
                _ls = line.strip()
                if _ls.startswith('trigger:'):
                    triggers = _ls.split(':', 1)[1].strip()
                    _in_actions = False
                elif _ls.startswith('actions:'):
                    _in_actions = True
                elif _in_actions and _ls.startswith('- '):
                    semantic_actions.append(_ls[2:].strip())
                elif _ls and not _ls.startswith('-'):
                    _in_actions = False
            if not triggers:
                continue
            # ① 正则语义匹配（含元字符的触发词按正则，其余按子串；非法正则退回子串）
            hit = False
            for tw in [t.strip() for t in triggers.split('|') if t.strip()]:
                if any(c in tw for c in '.*+?[](){}|^$\\'):
                    try:
                        if _re_trig.search(tw, user_message, _re_trig.I):
                            hit = True
                            break
                    except _re_trig.error:
                        pass
                if not hit and tw.lower() in user_message.lower():
                    hit = True
                    break
            if not hit:
                continue
            logger.info(f"[Engine] Skill触发词匹配: {skill_name} (触发词: {triggers})")
            # ②③ action 选择
            file_actions = [a.get('name', '') for a in skill.get('actions', [])]
            try:
                _script = Path(__file__).parent.parent.parent / skill.get('entry_point', '')
                _src = _script.read_text(encoding='utf-8', errors='ignore') if _script.exists() else ''
            except Exception:
                _src = ''
            # 直跑型：脚本根本不读 action 参数（music-gen/code_audit）→ 文件动作即可
            _cares = any(k in _src for k in ('HARVEN_SKILL_ACTION', 'args.action', 'sys.argv', "'--action'", '"--action"'))
            if not _cares:
                return (skill_name, file_actions[0] if file_actions else 'run')
            if semantic_actions:
                _is_query = bool(_re_trig.search(r'查|多少|汇总|总结|账单|余额|报表|统计|状态|报告|列表|月结|数据', user_message))
                if _is_query:
                    # 汇总/总结/月结 → summary 类优先；其余查询 → query 优先
                    if _re_trig.search(r'汇总|总结|月结', user_message):
                        _order = ('summary', 'monthly', 'report', 'query', 'status', 'list', 'view', 'get')
                    else:
                        _order = ('query', 'summary', 'report', 'status', 'list', 'monthly', 'view', 'get')
                    for cand in _order:
                        if cand in semantic_actions:
                            return (skill_name, cand)
                # 生成/执行型动作放行（song_production 的 full 等）
                for cand in ('full', 'generate', 'run', 'main', 'produce', 'start'):
                    if cand in semantic_actions:
                        return (skill_name, cand)
            # 理会 action 但无语义表 / 只剩数据写型 → 不瞎跑，交回正常管线
            return None
        return None
    except Exception as e:
        logger.error(f"[Engine] Skill匹配检测失败: {e}")
        return None


# ★ 2026-09-26：_try_skill_trigger（非流式版）已删除——无任何调用点的死代码，
#   且 actions[0]['name'] 写法对字符串列表必抛 TypeError（从未跑通过）。执行统一走流式版。


# ═══════════════════════════════════════════════════════════════
# ★ 2026-07-11: 快速命令拦截器（不经过外脑，直接执行）
# ═══════════════════════════════════════════════════════════════

# ★ 2026-08-05: 流水线系统已废弃，由Skill脚本替代（避免async generator语法错误）


def _extract_genre(user_message):
    """从用户消息提取音乐风格"""
    msg = user_message.lower()
    genres = {
        'country': ['乡村', 'country'],
        'rock': ['摇滚', 'rock'],
        'pop': ['流行', 'pop'],
        'jazz': ['爵士', 'jazz'],
        'classical': ['古典', 'classical'],
        'electronic': ['电子', 'electronic', 'edm'],
        'chinese': ['中国风', '古风', 'chinese'],
    }
    for genre, keywords in genres.items():
        if any(kw in msg for kw in keywords):
            return genre
    return 'pop'  # 默认流行


def _try_smart_split(user_message, conversation_id):
    """★ 2026-08-08: 智能拆分复杂消息为多个操作

    "买海鲜50元 买书包120 加油600 该洗咖啡机了"
    → 记账x3 + 提醒x1
    """
    import re as _re_split

    # ★ 2026-10-15 A. 明确时间线索词（提醒直设门闸）——
    #   没有任何线索（"提醒我喝水""一会儿提醒我"）不设模糊提醒，回问具体时间；
    #   注意"马上/一会儿/等下"是模糊词，故意不算（会变成当天09:00垃圾提醒）
    _TIME_CUE_PAT = _re_split.compile(
        r'每天|每日|天天|每周|每星期|每月|隔天|今天|今晚|明天|后天|大后天|'
        r'周[一二三四五六日天]|星期[一二三四五六日天]|'
        r'\d+\s*天后|\d{1,2}\s*月\d{1,2}[日号]|'
        # ★ 2026-10-17 B. 补相对时长词——「5分钟后提醒我接孩子」曾被门闸拦下反问
        #   （parse_reminder_text 本身会解「N分钟后」，实锤：解析出 =now+5min），门闸却放不了行
        r'\d+\s*分钟后|\d+\s*个?小时后|\d+\s*分半后|'
        r'\d{1,2}[点时]|早晨|早上|上午|中午|下午|晚上|傍晚|凌晨|半夜|'
        r'\d{1,2}:\d{2}')

    # ★ 2026-09-26 防误判加固（已定案A）：只看用户原话——
    #   浮窗聊天会把面板目录等【背景补充】拼在消息后，模板文案"多久没打电话了、该帮爸妈办
    #   什么事…聊了什么"曾被 `该(.+?)了` 正则误捕成假提醒（09-26 实锤）。先切掉再跑正则
    _cut = user_message.find('【背景补充')
    if _cut > -1:
        user_message = user_message[:_cut].strip()
    if user_message.startswith('（用户此刻在'):
        _pend = user_message.find('）')
        if 0 < _pend < 40:
            user_message = user_message[_pend + 1:].strip()

    # 检测是否包含多个操作（至少2个金额或2个操作词）
    amounts = _re_split.findall(r'(\d+(?:\.\d+)?)\s*元', user_message)

    # ★ #1556 技术数字上下文排除：端口/版本/超时/大小等数字不是记账金额。
    #   实锤误报：方案文本「Flask 常驻服务（端口如 8101，绑 127.0.0.1）」→
    #   裸数字正则把 8101 当金额（项目名成了「端口如」），差点入账 8101.0 元。
    _TECH_BEFORE = ('端口', '版本', '版号', '超时', '延迟', '毫秒', '频率', '帧率',
                    '分辨率', '线程', '进程', '字节', '像素', '批次', '轮次',
                    '显存', '内存', '占用', '硬盘', '缓存',
                    'port', 'timeout', 'version', 'batch', 'epoch', 'step', 'top_')
    _TECH_AFTER = ('gb', 'mb', 'kb', 'tb', 'ms', 'px', 'dpi', 'fps', 'hz',
                   'mib', 'gib', 'kib',
                   '毫秒', '微秒', '像素', '字节', '版本')

    def _is_tech_number(msg, num_start, num_end):
        before = msg[max(0, num_start - 14):num_start].lower()
        after = msg[num_end:num_end + 6].lower()
        if any(w in before for w in _TECH_BEFORE):
            return True
        if after.startswith(_TECH_AFTER):
            return True
        return False

    # ★ 2026-09-17 放宽：句尾无单位纯数字也算金额（实锤"模拟飞行1200"聊天模式没记上，
    #   模型还嘴硬说"已经记录了"）。守卫：数字后跟量词/日期/分数/倍数不算
    #   （3个/5斤/90分/8岁/30度/9月15日/5万/13点）
    _bare_amounts = [
        m.group(1) for m in _re_split.finditer(
            r'(?<![\d.:：])([1-9]\d{2,5}(?:\.\d+)?)(?![\d元块个只斤克次人天号日月点岁折成分度万亿千：:])(?![:.]\d)(?=\s*[，。,.!！?？\s]|$)',
            user_message)
        if not _is_tech_number(user_message, m.start(1), m.end(1))]
    # ↑ #1556（2026-11-26）：finditer+过滤替换 findall（要数字位置判技术上下文）；
    #   新增 (?![:.]\d) 排除 IP/版本号形态（127.0.0.1 的 "127.0"、2.9.1 的 "2.9"）
    # ★ 疑问句守卫："iPhone 7999值得买吗"/"多少钱"不是记账
    _is_question = bool(_re_split.search(r'[?？]|多少|几块|几元|值不值|好不好', user_message))
    if _is_question:
        _bare_amounts = []
    remind_words = _re_split.findall(r'该(.+?)了|记得(.+?)|提醒我(.+?)', user_message)

    # ★ 2026-09-26 甲：金额在前语序（实测实锤"47.6 今天中文韩式炸酱面"快记没接住，
    #   掉进外脑FC查了4轮工具说明书——慢30秒+烧5倍token）。段首数字+空格+描述
    #   也算记账，走同一条待确认队列："47.6 今天炸酱面"/"50 买菜"/"47.6 炸酱面，35 晚饭"
    _lead_ops = []
    if not _is_question:
        # 注意：分隔符类不含英文句点——"47.6"的小数点不能被切（09-26 实测踩坑：切完变"6 今天…"金额错成6.0）
        for _seg in _re_split.split(r'[，。,!！?？\n]', user_message):
            _seg = _seg.strip()
            _lm = _re_split.match(
                r'(\d{1,5}(?:\.\d+)?)\s+([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9]*)$', _seg)
            if not _lm:
                continue
            _amt_s, _item = _lm.group(1), _lm.group(2)
            # 量词守卫："5 斤苹果"/"100 米跑步"不是记账
            if _item[0] in '个只斤克吨次人天号日月点岁折成分度万亿千百块元碗瓶包双条张件岁步单米':
                continue
            # 年份守卫："2026 年度计划"不是记账
            if _re_split.fullmatch(r'(19|20)\d{2}', _amt_s) and ('年' in _item or '月' in _item):
                continue
            # #1556 技术数字守卫："8101 端口服务"/"512 超时时间"不是记账
            if any(w in _item for w in _TECH_BEFORE):
                continue
            try:
                _amt = float(_amt_s)
            except ValueError:
                continue
            if _amt <= 0 or len(_item) < 2:
                continue
            _lead_ops.append({'type': '记账', 'item': _item, 'amount': _amt, 'category': '其他'})

    # 少于2个操作 → 不拆分
    if len(amounts) + len(_bare_amounts) + len(_lead_ops) < 1 and len(remind_words) < 1:
        return None

    # ★ #1218（2026-10-25 深夜）运动/健身域让路——拆分器把混合句劫走只存提醒、健身部分被丢弃
    #   实锤：「今天健身练腿10月28号提醒我健身练胸」→ 拆分器接管只设提醒，
    #   「练腿」无人处理，FC（add_panel_record 进健身面板）根本没机会跑。
    #   规则：消息含运动特征词 且 无任何记账金额特征 → 拆分器让位，整句放行 FC
    #   （FC 提示词条 2.5：健身→add_panel_record 进面板台账；提醒→add_inbox_note，各落各家）
    if not (amounts or _bare_amounts or _lead_ops):
        _FIT_PAT = (r'健身|练[腿胸背肩臂腰腹核心]|跑步|撸铁|俯卧撑|仰卧起坐|深蹲|'
                    r'引体向上|有氧|无氧|拉伸|游泳|骑行|跳绳|瑜伽|平板支撑|波比跳')
        if _re_split.search(_FIT_PAT, user_message):
            logger.info('[Engine] 智能拆分让路：运动域消息放行 FC（#1218）')
            return None

    # 拆分操作
    operations = []

    # 匹配记账项
    patterns = [
        r'买(\w+?)\s*(?:花(?:费)?|用)?\s*(\d+(?:\.\d+)?)\s*元',
        r'(\w+?)\s*(?:花费|花了|支出|用)\s*(\d+(?:\.\d+)?)\s*元',
        r'(\w+?)\s+(\d+(?:\.\d+)?)\s*元',
    ]

    for pattern in patterns:
        matches = _re_split.findall(pattern, user_message)
        for match in matches:
            item, amount_str = match[0], match[1]
            amount = float(amount_str)
            category = '其他'
            cat_map = {
                '菜': '买菜', '海鲜': '买菜', '肉': '买菜', '水果': '买菜',
                '加油': '交通', '油': '交通', '车': '交通', '打车': '交通',
                '书': '学习', '包': '学习', '笔': '学习',
                '饭': '餐饮', '吃': '餐饮', '餐': '餐饮', '咖啡': '餐饮',
            }
            for kw, cat in cat_map.items():
                if kw in item:
                    category = cat
                    break
            operations.append({
                'type': '记账',
                'item': item, 'amount': amount,
                'category': category,
            })

    # ★ 2026-09-17 无单位尾随数字："模拟飞行1200"/"酒店订金500。"
    #   项目名取末段（按 去/到/搜/买 切开取最后一段），分类交由落库口 acc_categories 兣底
    _bare_pat = (r'([\u4e00-\u9fa5A-Za-z]{2,}?)\s*([1-9]\d{2,5}(?:\.\d+)?)'
                 r'(?![\d元块个只斤克次人天号日月点岁折成分度万亿千：:])(?![:.]\d)'
                 r'(?=\s*[，。,.!！?？\s]|$)')
    for _bm in (_re_split.finditer(_bare_pat, user_message) if not _is_question else []):
        # 时间守卫：数字紧邻冒号（13:20）跳过
        if _bm.start() > 0 and user_message[_bm.start() - 1] in ':：':
            continue
        # #1556 技术数字守卫：端口/版本/带量纲单位的数字不是记账
        if _is_tech_number(user_message, _bm.start(2), _bm.end(2)):
            continue
        item, amount_str = _bm.group(1), _bm.group(2)
        try:
            amount = float(amount_str)
        except ValueError:
            continue
        _segs = [s for s in _re_split.split(r'[去到搜买]', item) if s]
        if _segs and len(_segs[-1]) >= 2:
            item = _segs[-1].strip()  # "去酷航避避暑搜模拟飞行"→"模拟飞行"；"买菜"不拆（拆出单字"菜"）
        if not item or not amount:
            continue
        operations.append({'type': '记账', 'item': item, 'amount': amount, 'category': '其他'})

    # ★ 2026-09-26 甲：金额在前的记账项并入同一条流（同样走去重+待确认队列）
    operations.extend(_lead_ops)

    # ★ 2026-09-16 跨模式去重：多个正则独立扫描同一句话，会各报一笔（实锤
    #   "买水果花了35元"报出"今天买水果"+"水果花了"两笔）。同一金额的多条记账，
    #   若项目名互为包含（或归一化动词尾巴后相同），只保留最短项目名那条
    for op in operations:
        if op['type'] == '记账':
            op['item'] = _re_split.sub(r'(?:花了|花费|支出|用|买了|买)$', '', str(op['item'])).strip() or op['item']
    _accs = [op for op in operations if op['type'] == '记账']
    _drop = set()
    for i, a in enumerate(_accs):
        for j, b in enumerate(_accs):
            if i >= j or a['amount'] != b['amount']:
                continue
            x, y = a['item'], b['item']
            if x == y or (x and y and (x in y or y in x)):
                _drop.add(j if len(y) >= len(x) else i)  # 丢项目名更长的
    if _drop:
        _kept_accs = [a for k, a in enumerate(_accs) if k not in _drop]
        _others = [op for op in operations if op['type'] != '记账']
        operations = _kept_accs + _others

    # 匹配提醒项
    # ★ 2026-09-26 防误判加固（已定案A）：问句不设提醒（"永智农场怎么样？"曾被当提醒）；
    #   task 限长 30 字且禁含括号/冒号——面板 schema 文案特征，正常口说提醒不会带
    if not _is_question:
        # ★ 2026-11-13 实锤修复：「发送邮件给38552540@qq.com 先测试一下应该成功了」
        #   被「该(.+?)了」抠出 task=「成功」→ 假提醒 + 整条消息被确认卡短路（邮件没进 FC）。
        #   ①「该」前邻「应/不」（应该X了/不该X了）不算提醒口吻（lookbehind 排除）；
        #   ② 邮件动作域（发送邮件/发邮件…）是明确的工具意图，跳过提醒匹配
        if not any(k in user_message for k in ('发送邮件', '发邮件', '发个邮件', '邮件给')):
            # ★ #1509：用户自定义提醒触发词（config custom_keywords remind:true）追加进正则
            _rem_extra = [str(c.get('trigger')).strip() for c in _load_custom_keywords()
                          if c.get('remind') and str(c.get('trigger') or '').strip()]
            _rem_alt = '|'.join(f'{_re_split.escape(w)}(.+)' for w in _rem_extra) if _rem_extra else ''
            # ★ #1509 顺手修：记得/提醒我/别忘了 改贪婪(.+)——原非贪婪(.+?)只抓 1 个字
            #   （「记得交房租」task='交' 实锤截断）；「该X了」保留非贪婪（了是后缀）
            remind_pattern = r'(?<![应不])该(.+?)了|记得(.+)|提醒我(.+)|别忘了(.+)' + (('|' + _rem_alt) if _rem_alt else '')
            remind_matches = _re_split.findall(remind_pattern, user_message)
            for match in remind_matches:
                task = ''.join([g for g in match if g]).strip()
                if task and len(task) <= 30 and not _re_split.search(r'[（(：:]|[（(]$', task):
                    operations.append({'type': '提醒', 'task': task})

    if not operations:
        return None

    # 执行识别（★ 2026-09-26 模式分开·已定案A：凡进面板库必过确认——
    #   记账不再直接入库（无确认卡悄悄进账本的根因），改存待确认队列，用户点📥才入账）
    results = []
    recorded = 0
    _reminder_done = False  # ★ 2026-10-15 A. 同句多提醒只建一次
    for op in operations:
        if op['type'] == '记账':
            try:
                from datetime import date as _date_split
                from src.core.panel_assist import add_pending as _pa_add
                _rec = {'日期': _date_split.today().isoformat(),
                        '项目': op.get('item', ''),
                        '金额': op.get('amount', 0)}
                _pa_add('accounting_daily_1', 'accounting', [_rec],
                        source_text=user_message[:200])
                results.append(f"✅ {op['item']} {op['amount']}元（待确认，还没入账）")
                recorded += 1
            except Exception as e:
                results.append(f"❌ {op['item']} 记录失败: {e}")
        elif op['type'] == '提醒':
            # ★ 2026-10-15 A. 聊天直接设提醒（定版拍板：杀踢皮球+删旧浮窗误导文案）
            #   同句多个提醒只建一次——parse_reminder_text 单次只解析一个时间，
            #   且整句传参才能保住时间词（task 片段已被正则吃掉"明天下午3点"）
            if _reminder_done:
                continue
            _reminder_done = True
            try:
                from src.core.home_reminder import create_from_text, describe_info
                if not _TIME_CUE_PAT.search(user_message):
                    results.append(f"🔔 想设提醒：{op['task']}")
                    results.append("   👉 请告诉我具体时间，比如「明天晚上7点提醒我交物业费」")
                    continue
                # ★ #1219（2026-10-25）拆分器直设提醒也带域归属——之前 module 空，
                #   健身提醒全标成杂项，面板「相关提醒」看不到（id=51 实锤）
                _m_guess = '通用'
                if _re_split.search(r'健身|练[腿胸背肩臂腰腹核心]|跑步|撸铁|俯卧撑|仰卧起坐|深蹲|引体|有氧|无氧|拉伸|游泳|骑行|跳绳|瑜伽', user_message):
                    _m_guess = 'fitness'
                elif _re_split.search(r'过期|冰箱|食材|保鲜|牛奶|酸奶|剩菜', user_message):
                    _m_guess = 'fridge'
                elif _re_split.search(r'晾晒|换季|保养|换洗|收纳|物品', user_message):
                    _m_guess = 'items'
                elif _re_split.search(r'孩子|作业|接送|上课|下课|学习|考试|家长', user_message):
                    _m_guess = 'kid'
                elif _re_split.search(r'开会|会议|日程|预约|缴费', user_message):
                    _m_guess = 'calendar'
                _rid, _info, _err = create_from_text(user_message, module=_m_guess, source='chat')
                if _err or not _rid:
                    results.append(f"❌ 提醒没设上：{_err or '解析失败'}")
                else:
                    results.append(f"✅ 已设提醒：{describe_info(_info).replace(chr(10), ' ')}")
                    results.append("   （到点我会叫你；说「取消提醒 xxx」可撤销）")
            except Exception as e:
                results.append(f"❌ 提醒设置失败: {e}")

    # 生成汇总
    total = sum(op['amount'] for op in operations if op['type'] == '记账')
    summary = f"📋 识别到 {len(operations)} 项操作：\n\n"
    for r in results:
        summary += f"  {r}\n"
    if recorded:
        summary += f"\n💰 {recorded}笔记账已放进待确认队列（合计{total:.1f}元），还没入账："
        summary += "\n👉 点右下角哈文助手顶上的 📥 逐条确认入库；不想要的点「忽略」即可"
    logger.info(f"[Engine] 智能拆分: {len(operations)}项操作（待确认制）")
    return summary


def _try_quick_command(user_message):
    """检测快速命令并调用内置工具执行（不经过外脑）"""
    import re as _re_qc
    import os as _os_qc
    msg = user_message.lower().strip()

    # ── 取消/删除提醒 ★ 2026-10-15 A.（聊天直设提醒配套的撤销闭环）──
    _m_cancel = _re_qc.match(r'^(取消提醒|撤销提醒|删掉提醒|删除提醒)\s*[：:]?\s*(.*)$', user_message.strip())
    if _m_cancel:
        from src.core.home_reminder import list_reminders, delete_reminder
        _kw = _m_cancel.group(2).strip()
        _pend = list_reminders(pending_only=True)
        if _kw:
            _hit = [r for r in _pend if _kw in r['title']]
            if len(_hit) == 1:
                delete_reminder(_hit[0]['id'])
                return f"✅ 已取消提醒：{_hit[0]['title']}（原定 {_hit[0]['remind_at']}）"
            if len(_hit) > 1:
                _lines = "\n".join(f"  {i+1}. {_r['title']}｜{_r['remind_at']}"
                                   for i, _r in enumerate(_hit[:5]))
                return f"找到 {len(_hit)} 条相关提醒，说「取消提醒 + 更完整的名字」可指定：\n{_lines}"
            return f"没找到名字带「{_kw}」的待提醒。说「取消提醒」可看全部待提醒列表"
        if not _pend:
            return "当前没有待提醒的提醒。"
        _lines = "\n".join(f"  {i+1}. {_r['title']}｜{_r['remind_at']}"
                           for i, _r in enumerate(_pend[:8]))
        return f"当前待提醒有 {len(_pend)} 条：\n{_lines}\n👉 说「取消提醒 + 名字」即可撤销"

    # ── 下载/安装模型 ──
    if ('下载' in user_message or '安装' in user_message or '帮我' in user_message) and ('模型' in user_message or '华佗' in user_message or 'huatuo' in msg or 'qwen' in msg or 'deepseek' in msg or 'llama' in msg):
        from src.core.builtin_tools import install_model
        result = install_model(user_message)
        if result.get('success'):
            msg_text = result.get('message', '安装成功')
            if result.get('path'):
                msg_text += f"\n路径: {result['path']}"
            if result.get('size_mb'):
                msg_text += f"\n大小: {result['size_mb']}MB"
            return f"✅ {msg_text}"
        else:
            return f"❌ {result.get('error', '安装失败')}"

    # ── 删除/卸载模型 ──
    if ('删除' in user_message or '卸载' in user_message or '删掉' in user_message) and ('模型' in user_message or '华佗' in user_message or 'huatuo' in msg):
        from src.core.builtin_tools import uninstall_model
        result = uninstall_model(user_message)
        if result.get('success'):
            msg_text = result.get('message', '卸载成功')
            if result.get('freed_mb'):
                msg_text += f"\n释放: {result['freed_mb']}MB"
            return f"✅ {msg_text}"
        else:
            return f"❌ {result.get('error', '卸载失败')}"

    # ── 查看本地模型 ──
    if ('有哪些' in user_message or '什么' in user_message) and '模型' in user_message and ('我' in user_message or '本地' in user_message):
        from src.core.builtin_tools import scan_system
        result = scan_system()
        if result.get('success'):
            from src.core.system_knowledge import get_knowledge_summary
            summary = get_knowledge_summary()
            if summary:
                return f"📋 以下是你的本地模型列表：\n{summary}"
        return "暂无本地模型。"

    # ★ 2026-08-03: 文件操作 + 软件检测（Skill方式，引擎直接执行）

    # ── list_dir：查看下载/目录内容 ──
    if _re_qc.search(r'下载.*什么|什么.*下载|下了.*啥|最近.*下载|昨天.*下载|下载.*哪|目录.*什么|文件夹.*什么|有什么文件|看看.*目录|查看.*目录', msg):
        from src.core.builtin_tools import list_dir
        _base_dir = _os_qc.path.dirname(_os_qc.path.dirname(_os_qc.path.dirname(_os_qc.path.abspath(__file__))))
        # 智能推断路径
        if '下载' in msg and 'skill' not in msg:
            _path = _os_qc.path.join(_os_qc.path.expanduser('~'), 'Downloads')
        elif 'skill' in msg:
            _path = _os_qc.path.join(_base_dir, 'data', 'skills')
        elif 'download' in msg:
            _path = _os_qc.path.join(_base_dir, 'data', 'downloads')
        else:
            _path_match = _re_qc.search(r'([A-Za-z]:[/\\][^\s,，。]+)', user_message)
            _path = _path_match.group(1) if _path_match else _os_qc.path.join(_base_dir, 'data')
        result = list_dir(_path)
        if result.get('success'):
            items = result.get('items', [])
            if not items:
                return f"📁 目录为空：`{_path}`"
            output = f"📁 **目录内容** (`{_path}`)：\n\n"
            for item in items[:30]:
                icon = '📁' if item['is_dir'] else '📄'
                size = f" ({item['size']//1024}KB)" if not item['is_dir'] and item['size'] > 0 else ''
                output += f"{icon} {item['name']}{size}\n"
            if len(items) > 30:
                output += f"... 共{len(items)}项\n"
            return output
        else:
            return f"❌ {result.get('error', '无法读取目录')}"

    # ── check_software：检查软件安装 ──
    _sw_map = {
        'docker': ['docker', '容器'], 'python': ['python', 'py环境', 'py3'],
        'git': ['git'], 'node': ['node', 'nodejs', 'node.js'], 'npm': ['npm'],
        'ollama': ['ollama'], 'pip': ['pip'], 'java': ['java', 'jdk'],
        'go': ['golang', 'go语言'], 'rust': ['rust', 'cargo'],
        'blender': ['blender'], 'ffmpeg': ['ffmpeg'], 'redis': ['redis'],
    }
    if any(kw in msg for kws in _sw_map.values() for kw in kws) and any(q in msg for q in ['装', '安装', '版本', '有没有', '有没有装', '在不在']):
        from src.core.builtin_tools import check_software
        for sw, kws in _sw_map.items():
            if any(kw in msg for kw in kws):
                result = check_software(sw)
                if result.get('installed'):
                    return f"✅ **{sw}** 已安装\n   版本：{result.get('version', '未知')}\n   运行：{'✅运行中' if result.get('running') else '⚠️未运行'}"
                else:
                    return f"❌ **{sw}** 未安装\n   {result.get('error', '')}"
        return None

    # ── read_file：读取文件内容 ──
    if _re_qc.search(r'看看.*文件|读.*文件|文件.*内容|文件.*写了什么|查看.*配置', msg):
        from src.core.builtin_tools import read_file
        _path_match = _re_qc.search(r'([A-Za-z]:[/\\][^\s,，。]+)', user_message)
        if _path_match:
            _path = _path_match.group(1)
            result = read_file(_path)
            if result.get('success'):
                content = result.get('content', '')[:2000]
                return f"📄 **文件内容** (`{_path}`)：\n```\n{content}\n```"
            else:
                return f"❌ {result.get('error', '读取失败')}"
        # 没有路径 → 尝试读config.yaml
        _config_path = _os_qc.path.join(_os_qc.path.dirname(_os_qc.path.dirname(_os_qc.path.dirname(_os_qc.path.abspath(__file__)))), 'config.yaml')
        result = read_file(_config_path)
        if result.get('success'):
            content = result.get('content', '')[:2000]
            return f"📄 **config.yaml 内容**：\n```\n{content}\n```"

    return None


def get_conversation_engine():
    """获取或创建对话引擎单例（用于模块间共享）"""
    global _engine_instance
    if _engine_instance is None:
        # ★ 2026-10-04 P1 修复：裸 ModelManager() 不带模型名 → 400「model is required」
        #   （手机远程 22:00/22:04 日志实锤）。改 create_from_config：与 web_app.get_mm
        #   同源读 config ollama.default_model。
        from src.core.model_manager import create_from_config
        mm = create_from_config()
        _engine_instance = ConversationEngine(
            model_manager=mm,
            conversation_id="__system__",
        )
    return _engine_instance
