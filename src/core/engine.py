# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
AI对话核心引擎 v2.0
整合模型调用 + 查询流程（知识库→推理）+ 记忆持久化
"""
import re
import json
import yaml
from pathlib import Path
from datetime import datetime
from src.core.model_manager import ModelManager
from src.core.morality import SYSTEM_PROMPT, IDENTITY_PROMPT
from src.models.database import save_message, get_messages, get_preference
from src.core.event_bus import get_event_bus, Events
import logging
logger = logging.getLogger(__name__)

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
            self._query_mode = mode if mode in self.QUERY_MODES else "kb_only"

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

    def send(self, user_message, stream=True, query_mode=None, web_search=False):
        """
        发送用户消息，执行三段式查询

        Args:
            query_mode: 可覆盖实例默认模式 (kb_only/direct)
            web_search: ★ 2026-07-05: 用户是否点了🌐互联网按钮，True=强制搜索

        Returns:
            stream=True: 生成器 (type, content)
                type: "thinking" | "text" | "warning" | "error" | "source"
        """
        # ★ 2026-07-29: 方案N — 注入系统状态（模型回答前先查好）
        self._system_status = ''

        # ★ 2026-08-13: 智能意图匹配 — 关键词组合，不漏词
        # 不依赖FC（本地模型不支持tools参数），用关键词组合直接路由
        if user_message and not user_message.startswith('/help') and '[CONFIRM_AGENT:' not in user_message:
            fc_result = yield from self._try_smart_route(user_message, stream)
            if fc_result is not None:
                return fc_result

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
        return system_prompt

    def _adapt_mm_stream(self, mm_gen):
        """
        适配多模型管理器的输出流为纯文本生成器
        mm_gen yields {"type": "text/error", "content": str}
        我们需要 yield 纯文本字符串（与 ModelManager.chat 的输出格式一致）
        """
        for item in mm_gen:
            if not isinstance(item, dict):
                continue
            t = item.get("type", "")
            c = item.get("content", "")
            if t == "text" and c:
                yield c
            elif t == "error":
                yield f"[错误]{c}"

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
        """检测用户消息是否匹配垂直模型任务，返回模型ID或None"""
        try:
            from src.core.vertical_api import _BUILTIN_MODELS
            msg_lower = user_message.lower()
            best_model = None
            best_score = 0
            for model in _BUILTIN_MODELS:
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
ADVISE:好的！我帮您规划。\\n\\nMusicGen 可以生成 5-30 秒纯伴奏。\\n为了做出满意曲子，我需要了解：\\n1. 什么风格？（乡村/爵士/古典/摇滚/电子...）\\n2. 什么情绪？（欢快/忧伤/激昂/浪漫...）\\n3. 什么场景？（背景音乐/游戏配乐/手机铃声...）

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
        智能模型路由：根据查询模式选择最合适的模型。
        Returns: (use_external: bool, model_label: str, model_id: str)

        路由规则（2026-06-12 更新）：
        - direct / auto → 优先云端大模型（DeepSeek等），未配置则回落 Ollama
        - kb_only → 本地模型（纯知识库检索不需要云端）
        - 如果未配置外部模型，全部回落 Ollama
        - 【安全回落】外部模型 API key 缺失或调用失败时，自动回落 Ollama
        """
        if query_mode in ("direct", "auto"):
            try:
                from src.core.multi_model_manager import get_multi_model_manager
                mm = get_multi_model_manager()
                if mm is None:
                    logger.warning(f"[Engine] ⚠️ 多模型管理器为None！回退Ollama")
                    return False, "本地模型", "ollama"

                all_providers = mm._get_all_providers()

                # 优先使用用户设定的当前模型（如果不是 ollama）— 但检查 API key
                if mm.current_model != "ollama":
                    model_cfg = mm.config.get('models', {}).get(mm.current_model, {})
                    provider_info = all_providers.get(mm.current_model, {})
                    needs_key = provider_info.get('needs_key', False)
                    logger.info(f"[Engine] 🔍 当前模型={mm.current_model}, enabled={model_cfg.get('enabled')}, needs_key={needs_key}, has_api_key={bool(model_cfg.get('api_key'))}")
                    # API key 缺失 → 检查 multi_models.json 和 config.yaml 两个来源
                    if needs_key and not model_cfg.get('api_key'):
                        # 1. multi_models.json 的 model_keys
                        global_keys = mm.config.get('model_keys', {})
                        if global_keys.get(mm.current_model):
                            model_cfg['api_key'] = global_keys[mm.current_model]
                        # 2. config.yaml 的 model_keys（★ 修复：回退到 config.yaml）
                        if not model_cfg.get('api_key'):
                            cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
                            try:
                                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                                yaml_keys = cfg.get('model_keys', {})
                                if yaml_keys.get(mm.current_model):
                                    model_cfg['api_key'] = yaml_keys[mm.current_model]
                            except Exception as _e: logger.debug(f'[Engine] 忽略错误: {_e}')
                        # 3. ★ 新增：也检查 provider_info 中可能有的 key
                        if not model_cfg.get('api_key'):
                            prov_cfg = mm.config.get('models', {}).get(mm.current_model, {})
                            if prov_cfg.get('api_key'):
                                model_cfg['api_key'] = prov_cfg['api_key']
                    
                    if needs_key and not model_cfg.get('api_key'):
                        logger.warning(
                            f"[Engine] 模型 {mm.current_model} 需要 API key 但未配置，"
                            f"强制回落 Ollama（current_model={mm.current_model}）"
                        )
                    elif model_cfg.get('enabled'):
                        name = provider_info.get("name", mm.current_model)
                        logger.info(f"[Engine] {query_mode}模式使用用户选定模型: {mm.current_model} ({name})")
                        return True, name, mm.current_model
                    else:
                        logger.warning(f"[Engine] 模型 {mm.current_model} 未启用，回落 Ollama")

                # current_model 是 ollama 或未启用/无API key → 自动查找最佳外部模型
                all_models = mm.get_all_models()
                logger.info(f"[Engine] 搜索可用外部模型，共 {len(all_models)} 个提供者，当前current_model={mm.current_model}")
                for m in all_models:
                    if m['id'] == 'ollama':
                        continue
                    if not m['enabled']:
                        logger.debug(f"[Engine] 跳过 {m['id']}: 未启用")
                        continue
                    # 如果需要 API key，检查是否有（三层回退：mm.config → config.yaml）
                    if m['needs_key']:
                        cfg_entry = mm.config.get('models', {}).get(m['id'], {})
                        api_key = cfg_entry.get('api_key', '')
                        if not api_key:
                            # 回退：检查 config.yaml model_keys
                            try:
                                cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
                                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                                api_key = cfg.get('model_keys', {}).get(m['id'], '')
                            except Exception as _e: logger.debug(f'[Engine] 忽略错误: {_e}')
                        if not api_key:
                            logger.debug(f"[Engine] 跳过 {m['id']}: 需要 API key 但未配置")
                            continue
                        # 将找到的 key 写入内存，防止后续重复查询
                        cfg_entry['api_key'] = api_key
                    # 找到了！临时切换并返回
                    name = m['name']
                    model_id = m['id']
                    logger.info(f"[Engine] {query_mode}模式自动切换到外部模型: {model_id} ({name})")
                    return True, name, model_id

                logger.debug(f"[Engine] {query_mode}模式：无可用外部模型（未启用或无API key），回退Ollama")
            except Exception as e:
                logger.warning(f"[Engine] ⚠️ 多模型管理器检测异常（回退Ollama）: {type(e).__name__}: {e}")
        return False, "本地模型", "ollama"

    def _invoke_collab_foreign(self, collab_manager, user_message: str) -> str:
        """
        调用协作外脑获取分析结果（非流式，同步模式）
        
        协作模式三步骤：
        1. 隐私过滤（根据 collab.privacy_level）
        2. 构建包含 Skills JSON 工具的 system prompt
        3. 调用外部模型获取分析结果
        
        Returns: 外脑分析文本，失败返回空字符串
        """
        # 【保密模式】禁止任何外部调用（包括协作外脑）
        if self._privacy_mode:
            logger.warning("[Engine] 保密模式下禁止协作外脑调用")
            return ""

        try:
            from src.core.multi_model_manager import get_multi_model_manager
            from src.core.skills_manager import get_skills_manager

            mm = get_multi_model_manager()
            foreign_model = collab_manager.foreign_model

            # 检查外脑是否可用
            all_providers = mm._get_all_providers()
            provider_info = all_providers.get(foreign_model, {})
            model_cfg = mm.config.get('models', {}).get(foreign_model, {})
            needs_key = provider_info.get('needs_key', False)

            if needs_key and not model_cfg.get('api_key'):
                # 从 multi_models.json 和 config.yaml 两个来源回退读取 API key
                global_keys = mm.config.get('model_keys', {})
                if global_keys.get(foreign_model):
                    model_cfg['api_key'] = global_keys[foreign_model]
                if not model_cfg.get('api_key'):
                    cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
                    try:
                        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                        yaml_keys = cfg.get('model_keys', {})
                        if yaml_keys.get(foreign_model):
                            model_cfg['api_key'] = yaml_keys[foreign_model]
                    except Exception as _e: logger.debug(f'[Engine] 忽略错误: {_e}')
                if not model_cfg.get('api_key'):
                    logger.warning(f"[Engine] 协作外脑 {foreign_model} 无 API key，跳过")
                    return ""

            if not model_cfg.get('enabled', True):
                logger.warning(f"[Engine] 协作外脑 {foreign_model} 未启用，跳过")
                return ""

            # 1. 隐私过滤
            filtered_msg = collab_manager.filter_user_data(user_message)
            logger.debug(f"[Engine] 协作外脑隐私过滤: {collab_manager.privacy_level}, "
                        f"原长度={len(user_message)}, 过滤后={len(filtered_msg)}")

            # 2. 构建 system prompt
            system_prompt = (
                "你是 Harven AI 的协作外脑。你的任务是分析用户问题并提供关键见解。\n"
                "回答要求：简洁、结构化、300字以内。\n"
                "你的分析结果将交给本地模型 模型做最终整合回答。\n"
                "不要回答最终答案，只提供：相关知识点、关键事实、分析角度、注意事项。\n"
            )

            # 注入 Skills JSON 工具描述
            try:
                sm = get_skills_manager()
                tools_prompt = collab_manager.build_tools_prompt(sm, user_message)
                if tools_prompt:
                    system_prompt += tools_prompt
            except Exception as e:
                logger.debug(f"[Engine] 协作外脑 Skills 注入跳过: {e}")

            # 3. 调用外部模型
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": filtered_msg},
            ]

            old_model = mm.current_model
            mm.current_model = foreign_model
            # ★ 修复：不修改 mm.config['current_model']（避免持久化到磁盘）
            logger.info(f"[Engine] 协作外脑调用: {foreign_model}, privacy={collab_manager.privacy_level}")

            try:
                response_gen = mm.generate_response(messages, stream=False)
                result = ""
                # generate_response 始终返回生成器，yield dict: {'type':'text'/'error','content':'...'}
                for chunk in response_gen:
                    if isinstance(chunk, dict):
                        if chunk.get('type') == 'text':
                            result += chunk.get('content', '')
                        elif chunk.get('type') == 'error':
                            logger.warning(f"[Engine] 协作外脑返回错误: {chunk.get('content', '')}")
                            return ""
                    elif isinstance(chunk, str):
                        result += chunk
                
                # ★ 安全清洗：剥离外脑可能产生的幻觉 tool_calls JSON 块
                result = self._clean_collab_response(result)
                result = result.strip()[:1500]
                if result:
                    logger.info(f"[Engine] 协作外脑返回 {len(result)} 字")
                return result
            finally:
                mm.current_model = old_model

        except Exception as e:
            logger.error(f"[Engine] 协作外脑调用失败: {e}")
            return ""

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
            yield ("text", quick_result)
            yield ("done", None)
            return

        # ★ 2026-08-03: 流水线已废弃，由Skill脚本替代

        # ★ 2026-08-08: 协作模式=安全开关
        _collab_val_sk = self._collab_mode_override
        if _collab_val_sk is None:
            _collab_val_sk = get_preference("collab_mode") or "off"
        _collab_on = _collab_val_sk and _collab_val_sk != "off"

        # ★ 2026-07-17: Skill触发词自动执行
        _skill_match = _try_skill_trigger_match(user_message)
        if _skill_match:
            _skill_name = _skill_match
            # ★ 协作OFF时阻止垂直Skill
            _vertical_skills = ['music-gen', 'musicgen', 'bark', 'stable-diffusion', 'huatuo']
            if not _collab_on and _skill_name and any(v in _skill_name.lower() for v in _vertical_skills):
                yield ("text", f"⚠️ 【{_skill_name}】需要开启协作模式才能执行。\n\n请点击右上角协作模式按钮开启。")
                yield ("done", None)
                return
            yield ("text", f"🔧 检测到【{_skill_name}】触发词，正在执行...\n\n")

            # ★ 2026-08-08: 流式执行 — 实时显示进度
            from src.core.scripts_runner import run_skill_script_streaming
            import json as _json
            full_output = ''
            final_result = None
            for item in run_skill_script_streaming(_skill_name, 'main', {}, timeout=300):
                if isinstance(item, str):
                    # 进度行
                    full_output += item + '\n'
                    yield ("text", item + '\n')
                elif isinstance(item, dict):
                    final_result = item

            _skill_result = final_result.get('output', '') if final_result else full_output

            # 解析输出格式
            full_response = _skill_result[:5000]
            try:
                data = _json.loads(_skill_result)
                if 'stats' in data and 'issues' in data:
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
            except (ValueError, TypeError):
                pass  # 纯文本输出已在流式yield中显示
            # ★ 2026-08-06: Skill执行也存消息到数据库
            try:
                save_message(self.conversation_id, "user", user_message)
                save_message(self.conversation_id, "assistant", full_response)
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

        # ★ 2026-07-29: 强化提示词 — 告诉模型有哪些FC工具可用
        try:
            from src.core.builtin_tools import get_tool_definitions as _gtd
            _fc_count = len(_gtd())
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
                "- web_search(query) / web_fetch(url) — 联网搜索\n"
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
                
                yield ("thinking", f"☁️ 云地协作 · 外脑 {cm.foreign_model} 分析中...")

                collab_analysis = self._invoke_collab_foreign(cm, user_message)
                
                if not collab_analysis:
                    err_msg = f"❌ 外脑（{cm.foreign_model}）连接失败\n\n云地协作依赖外脑。外脑无法连接，协作已中止。\n\n建议：\n· 检查外脑 API Key\n· 切换到 🏠本地双模 或 关闭协作"
                    yield ("error", err_msg)
                    logger.error(f"[Engine] 云地协作: 外脑连接失败")
                    return
                
                # 外脑成功 → 直接由外脑输出结果（不经过本地通用模型）
                yield ("source", f"☁️ {cm.foreign_model} · 外脑主导回答")
                enhanced_context += f"\n\n## ☁️ 云地协作 · 外脑分析（{cm.foreign_model}）\n{collab_analysis}\n\n你是外脑，请直接回答用户。如果需要调用本地垂直模型（音乐/图片/语音），请描述需求参数。"
                yield ("thinking", "☁️ 云地协作 · 外脑回答中...")
                is_collab_skipped = True
                logger.info(f"[Engine] 云地协作: 外脑={cm.foreign_model} 主导")
            
            # 本地双模协作：本地通用模型调度本地垂直模型
            elif cm.is_local_dual:
                _local_model = getattr(self.model, 'model', '')
                
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
        if not is_collab_skipped:
            use_external, model_label, model_id = self._route_model_for_query(self._query_mode)
            logger.info(f"[Engine] 路由: mode={self._query_mode}, use_external={use_external}, label={model_label}")
        elif is_collab_skipped:
            # 协作模式：外脑已用，强制用 Ollama 整合
            logger.info(f"[Engine] 协作模式: 跳过路由，强制使用本地模型 整合")

        # ═══════════════════════════════════════════════════════════════
        # 外脑连接规则（2026-07-07 更新）：
        # - direct 模式（用户明确选了外脑）：连接失败 → 报错停止，绝不偷偷用本地顶替
        # - 协作模式：外脑+本地缺一不可。任何一个失败 → 报错停止，不降级、不冒充
        # ═══════════════════════════════════════════════════════════════
        if is_collab_skipped:
            # ★ 协作模式已通过自己的验证流程（外脑成功 + Ollama在线），跳过此检查
            model_label = "本地模型"
            model_id = "ollama"
            pass
        elif not use_external and self._query_mode in ("direct", "auto"):
            if self._query_mode == "direct":
                # ★ 2026-07-05: direct 模式外脑连不上，友好提示+引导配置
                err_msg = (
                    f"❌ 外脑（{model_label}）连接不成功\n\n"
                    f"可能原因：\n"
                    f"· API Key 未配置或已失效\n"
                    f"· 模型未启用\n"
                    f"· 网络无法访问模型服务端\n\n"
                    f"📋 配置外脑步骤：\n"
                    f"1. 点击右上角 ⚙️ 设置按钮\n"
                    f"2. 进入「🤖 模型管理」标签\n"
                    f"3. 点击子导航的「🔀 多模型」按钮\n"
                    f"4. 找到想要使用的外部模型（如 DeepSeek/GLM/豆包）\n"
                    f"5. 填入 API Key 并保存\n"
                    f"6. 确保模型已启用（开关打开）\n"
                    f"7. 回到对话框重新发送消息\n\n"
                    f"💡 如果您的电脑配置较高（有独立显卡），也可以切换到「🏠本地模式」使用本地模型。"
                )
                yield ("error", err_msg)
                logger.error(f"[Engine] direct模式外脑不可用: {model_label}, 中止请求")
                return  # 直接结束，不用本地模型 顶替
            # ★ 2026-07-07: auto模式已取消，direct模式外脑失败直接中止

        # 外脑路由成功时的 thinking 提示 + 来源标识
        if self._query_mode == "direct" and use_external:
            yield ("thinking", f"🤖 外脑模式 · 正在连接 {model_label}...")
            # ★ 关键：注入来源标识，让前端能显示模型来源
            yield ("model_source", {
                "model": model_id,
                "label": model_label,
                "is_external": True,
                "mode": "direct"
            })
        # ★ 2026-07-07: auto模式已取消，只有direct模式使用外脑
            yield ("model_source", {
                "model": model_id,
                "label": model_label,
                "is_external": True,
                "mode": "auto"
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
                yield ("model_source", {
                    "model": "collab",
                    "label": collab_label,
                    "is_external": False,
                    "mode": "collab"
                })
            else:
                if _agent_override_prompt and _agent_pref != "external":
                    yield ("model_source", {
                        "model": _agent_matched,
                        "label": f"🧩 {registry._agents[_agent_matched]['name']} · 本地",
                        "is_external": False,
                        "mode": "agent"
                    })
                else:
                    yield ("model_source", {
                        "model": "local",
                        "label": "本地模型",
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
8. **图片识别**：查看分析用户上传的图片（用minicpm-v:8b视觉模型）
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

""" + LOCAL_CAPABILITIES

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
        try:
            from src.core.scripts_runner import list_skill_scripts
            from pathlib import Path as _Path
            script_skills = list_skill_scripts()
            if script_skills:
                skills_list_text = ""
                for ss in script_skills:
                    sname = ss.get('skill_name', '')
                    # 读SKILL.md获取描述和触发词
                    skill_md = Path(__file__).parent.parent.parent / 'data' / 'skills' / sname / 'SKILL.md'
                    desc = ''
                    triggers = ''
                    if skill_md.exists():
                        try:
                            md_content = skill_md.read_text(encoding='utf-8')
                            # 解析YAML frontmatter
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
                    actions = [a.get('name', '') for a in ss.get('actions', [])]
                    skills_list_text += f"- {sname}"
                    if desc:
                        skills_list_text += f"：{desc}"
                    if triggers:
                        skills_list_text += f"（触发词：{triggers}）"
                    if actions:
                        skills_list_text += f"（可用动作：{', '.join(actions)}）"
                    skills_list_text += "\n"

                system_prompt += f"""

## 🔧 可执行技能（Script Skills）
以下技能有可执行脚本，当用户请求匹配触发词时，**优先用 execute_skill_script 工具调用对应技能**，不要自己写代码：

{skills_list_text}
调用方式：execute_skill_script(skill_name="技能名", action="main", params={{}})
"""
                logger.info(f"[Engine] 注入 {len(script_skills)} 个脚本Skill到系统提示词")
        except Exception as e:
            logger.debug(f"[Engine] 脚本Skill注入跳过: {e}")

        # ===== 所有模式共享：注入增强上下文（知识库/GitHub/搜索结果）=====
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
                from src.core.builtin_tools import get_tool_definitions, is_function_calling_supported, execute_tool_call
                import json as _json

                # 检查协作模式
                _collab_val = self._collab_mode_override
                if _collab_val is None:
                    try:
                        _collab_val = get_preference("collab_mode") or "off"
                    except Exception:
                        _collab_val = "off"

                fc_tools = None
                if is_function_calling_supported(model_id) and (_collab_val == "off" or not _collab_val):
                    # ★ 只在非协作模式时启用FC
                    fc_tools = get_tool_definitions()
                    logger.info(f"[Engine] ★★★ Function Calling 已启用, {len(fc_tools)}个工具 (协作模式={_collab_val}) ★★★")
                else:
                    if _collab_val and _collab_val != "off":
                        logger.info(f"[Engine] 协作模式({ _collab_val }) → FC完全禁用，走协作流程")
                    else:
                        logger.info(f"[Engine] FC未启用: supported={is_function_calling_supported(model_id)}, collab={_collab_val}")

                # 先做一轮非流式调用（检测tool_calls）
                if fc_tools:
                    # ★ 2026-08-13: 强化FC提示词 — 必须调工具，禁止写代码
                    # ★ 2026-08-16: i18n — 按用户消息语言选提示词（含CJK用中文版，否则英文版）
                    _fc_messages = list(all_messages)
                    _has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in user_message)
                    if _has_cjk:
                        _fc_hint = (
                            "\n★★★ FC已启用 ★★★\n"
                            "1. 优先调工具，不写代码。路径示例：\"G盘虚拟飞行\"→path=\"G:\\\\虚拟飞行\"\n"
                            "2. 工具映射：检查目录→list_dir | 读文件→read_file | 删除→delete_path | 执行命令→execute_command | 查软件→check_software\n"
                            "3. 多步操作：拿到结果后继续调工具，直到任务完成\n"
                            "4. 清空回收站→execute_command(\"powershell -Command Clear-RecycleBin -Force\")\n"
                            "   验证：list_dir(C:\\\\$Recycle.Bin)——SID文件夹还在是正常的，里面没文件=已清空\n"
                            "5. 卸载→execute_command(\"winget uninstall XX\")→验证check_software\n"
                            "6. 不要动注册表！不要reg delete！不要rd /s /q系统目录！\n"
                            "7. 不解释，直接调工具。调完所有工具后用中文总结\n"
                            "8. 回复用中文，不输出JSON原始数据\n"
                        )
                    else:
                        _fc_hint = (
                            "\n★★★ FC ENABLED ★★★\n"
                            "1. Prefer calling tools over writing code. Path example: \"D drive SteamLibrary\" → path=\"D:\\\\SteamLibrary\"\n"
                            "2. Tool mapping: list directory→list_dir | read file→read_file | delete→delete_path | run command→execute_command | check software→check_software\n"
                            "3. Multi-step: after each result, keep calling tools until the task is fully done\n"
                            "4. Empty recycle bin→execute_command(\"powershell -Command Clear-RecycleBin -Force\")\n"
                            "   Verify: list_dir(C:\\\\$Recycle.Bin)——SID folders remaining is normal; empty inside = cleaned\n"
                            "5. Uninstall→execute_command(\"winget uninstall XX\")→verify with check_software\n"
                            "6. NEVER touch the registry! No reg delete! No rd /s /q on system directories!\n"
                            "7. Do not explain — call tools directly. After all tools are done, summarize in English\n"
                            "8. Reply in English. Do not output raw JSON data\n"
                        )
                    if _fc_messages and _fc_messages[0].get('role') == 'system':
                        _fc_messages[0]['content'] = _fc_hint + "\n\n" + _fc_messages[0]['content']
                    else:
                        _fc_messages.insert(0, {'role': 'system', 'content': _fc_hint.strip()})

                    # ★ 2026-08-14: FC循环检测 — 最多5轮工具调用
                    _fc_max_rounds = 5
                    _fc_done = False
                    for _fc_round in range(_fc_max_rounds):
                        logger.info(f"[Engine] ★★★ FC第{_fc_round+1}轮调用, 消息数={len(all_messages)}, tools={len(fc_tools)}")
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
                                # AI在解释，不是总结 → 不加user消息（避免污染历史），直接继续循环
                                logger.info(f"[Engine] ★ FC第{_fc_round+1}轮AI在解释而非调工具，继续循环（不污染历史）")
                                # 只加assistant消息，不加user消息
                                all_messages.append({"role": "assistant", "content": _fc_full})
                                continue
                            # 真的在总结 → 输出
                            logger.info(f"[Engine] ★ FC第{_fc_round+1}轮无工具调用，输出最终回答 ({len(_fc_full)}字)")
                            if _fc_full:
                                yield ("text", _fc_full)
                                _fc_done = True
                            break

                        # 执行工具调用
                        yield ("thinking", f"正在执行工具 (第{_fc_round+1}轮)...")
                        # 先加assistant消息（带tool_calls）
                        all_messages.append({"role": "assistant", "tool_calls": _fc_tcs})
                        # 执行每个工具并加tool消息
                        for _fc_tc in _fc_tcs:
                            _tc_name = _fc_tc.get('function', {}).get('name', '')
                            _tc_args = _fc_tc.get('function', {}).get('arguments', '{}')
                            _tc_id = _fc_tc.get('id', '')
                            logger.info(f"[Engine] ★ FC执行: {_tc_name}({_tc_args[:100]})")
                            _tc_result = execute_tool_call(_tc_name, _tc_args)

                            # ★ 2026-08-15: 危险工具日志记录（协作模式也记录）
                            _DANGER_TOOLS_FC = {
                                'delete_path', 'execute_command', 'email_send',
                                'write_file', 'edit_file', 'install_package',
                                'install_model', 'uninstall_model',
                            }
                            if _tc_name in _DANGER_TOOLS_FC:
                                logger.warning(f"[Engine] ★★ 危险工具已执行: {_tc_name}({_tc_args[:100]}) — 协作模式={self._collab_mode_override}")

                            # yield结果给用户
                            _tc_parsed = _tc_result
                            if isinstance(_tc_parsed, str):
                                try:
                                    import json as _json_fc
                                    _tc_parsed = _json_fc.loads(_tc_parsed)
                                except:
                                    _tc_parsed = {}
                            if isinstance(_tc_parsed, dict):
                                if _tc_parsed.get('success') and _tc_parsed.get('message'):
                                    yield ("text", f"\n📋 **{_tc_name}** 执行结果：\n{_tc_parsed['message']}\n")
                                elif _tc_parsed.get('success'):
                                    import json as _json_fc
                                    _fc_str = _json_fc.dumps(_tc_parsed, ensure_ascii=False, indent=2)
                                    if len(_fc_str) > 3000: _fc_str = _fc_str[:3000] + "\n...(已截断)"
                                    yield ("text", f"\n📋 **{_tc_name}** 执行结果：\n{_fc_str}\n")
                                elif _tc_parsed.get('error'):
                                    yield ("text", f"\n❌ **{_tc_name}** 失败：{_tc_parsed['error'][:200]}\n")

                            # ★ 2026-08-14: tool消息用JSON格式，AI能看懂
                            import json as _json_tool
                            if isinstance(_tc_result, str):
                                _tc_content = _tc_result  # 已经是字符串
                            else:
                                _tc_content = _json_tool.dumps(_tc_result, ensure_ascii=False)
                            all_messages.append({
                                "role": "tool",
                                "tool_call_id": _tc_id,
                                "content": _tc_content
                            })

                    if not _fc_done:
                        # 循环结束但还没输出最终回答，流式输出（传tools让AI还能调）
                        # ★ 2026-08-15: 加提示让AI总结，避免返回空
                        # ★ 2026-08-16: i18n — 按用户消息语言要求总结
                        _sum_lang_hint = "请用中文总结刚才执行的操作结果。" if _has_cjk else "Please summarize in English what you just did and the results."
                        all_messages.append({"role": "user", "content": _sum_lang_hint})
                        response_gen = mm.generate_response(all_messages, stream=True, tools=fc_tools)
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
                if self._query_mode == "direct":
                    # ★ direct 模式：连接失败 → 报错停止，不回落
                    err_msg = f"❌ 外部大模型（{model_label}）连接不成功\n\n错误详情：{str(e)[:500]}\n\n请检查「设置 → 模型管理」中的 API Key 和网络配置。"
                    yield ("error", err_msg)
                    return  # 直接结束，不用本地顶替
                else:
                    # ★ 2026-07-07: auto模式已取消，此处不会到达（direct模式已在上面return）
                    fallback_msg = f"⚠️ 外脑（{model_label}）连接不成功，已使用本地模型"
                    yield ("notification", {
                        "type": "foreign_model_fallback",
                        "message": fallback_msg,
                    })
                    yield ("thinking", f"⚠️ 外脑回落本地 · {fallback_msg}")
                    use_external = False
            finally:
                # ★ 关键修复：无论成功还是失败，恢复 current_model
                mm.current_model = old_model

            # 检查外部模型是否返回了错误或空流
            if raw_stream is not None:
                try:
                    first_check = next(iter(raw_stream))
                    if isinstance(first_check, str) and first_check.startswith("[错误]"):
                        logger.warning(f"[Engine] 外部模型返回错误: {first_check}")
                        if self._query_mode == "direct":
                            err_msg = f"❌ 外部大模型（{model_label}）返回错误\n\n{first_check[:300]}\n\n请检查模型配置或尝试其他外部模型。"
                            yield ("error", err_msg)
                            return
                        else:
                            fallback_msg = f"⚠️ 外脑（{model_label}）返回错误，已使用本地模型"
                            yield ("notification", {
                                "type": "foreign_model_fallback",
                                "message": fallback_msg,
                            })
                            yield ("thinking", f"⚠️ 外脑回落本地 · {fallback_msg}")
                            raw_stream = None
                            use_external = False
                    else:
                        import itertools
                        raw_stream = itertools.chain([first_check], raw_stream)
                except StopIteration:
                    logger.warning("[Engine] 外部模型返回空流")
                    if self._query_mode == "direct":
                        err_msg = f"❌ 外部大模型（{model_label}）无响应\n\n模型服务端可能暂时不可用，请稍后重试或检查网络。"
                        yield ("error", err_msg)
                        return
                    else:
                        fallback_msg = f"⚠️ 外脑（{model_label}）无响应，已使用本地模型"
                        yield ("notification", {
                            "type": "foreign_model_fallback",
                            "message": fallback_msg,
                        })
                        yield ("thinking", f"⚠️ 外脑回落本地 · {fallback_msg}")
                        raw_stream = None
                        use_external = False

        # ── 本地通用模型调用（优先 → llama-cpp 回退）──
        if not use_external or raw_stream is None:
            # ═══ 模型能力检测：非语言模型不参与对话 ═══
            # P1-2: 统一使用模块级常量 _NONCHAT_KEYWORDS
            _current_model = getattr(self.model, 'model', '') or ''
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
        if chunk_count == 0:
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

        # 清洗模型可能输出的后台任务 JSON 块（action/tool_calls）
        # 这类 JSON 块是给后台任务调度器的，不应该展示给用户
        try:
            import re as _re
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
                    else:
                        _had_code = True

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

        # 保存AI回复
        save_message(self.conversation_id, "assistant", full_response)

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
    _DANGER_TOOLS = {
        'install_software', 'uninstall_software', 'clean_disk',
        'fix_network', 'change_wallpaper', 'install_package',
        'install_model', 'uninstall_model', 'write_file', 'edit_file',
        'execute_command', 'delete_path', 'email_send', 'delete_file',
    }

    def _try_smart_route(self, user_message: str, stream: bool = True):
        """★ 2026-08-13: 智能关键词组合路由 — 不漏词，不依赖FC"""
        try:
            from src.core.agent_runner import run_script_agent
            msg = user_message.lower().strip()

            # ★ 2026-08-14: 复杂操作信号词 — 含这些词时不拦截，让FC处理
            # ★ 2026-08-14: 去掉"删除""游戏"等误拦截词，改为更精确的复合词
            # ★ 2026-08-16: i18n — 增加英文信号词（英文指令同样跳过B方案走FC）
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
            if _is_complex:
                logger.info(f"[Engine] 检测到复杂操作信号词，跳过B方案，让FC处理: {user_message[:50]}")
                return None

            # computer_use 触发：关键词组合匹配
            computer_use_triggers = False
            # 截图
            if any(k in msg for k in ['截图', '截屏', 'screenshot']):
                computer_use_triggers = True
            # 打开+盘/目录/文件夹（不含"思路""文件"等抽象词）
            if any(k in msg for k in ['打开', 'open']) and any(k in msg for k in ['盘', '目录', '文件夹', 'folder', 'directory', 'drive']) and not any(k in msg for k in ['思路', '文件', '方式', '方法']):
                computer_use_triggers = True
            # 查看内存/进程
            if any(k in msg for k in ['内存', 'memory', 'ram']) or any(k in msg for k in ['进程', 'process', '查看进程']):
                computer_use_triggers = True
            # 整理桌面
            if any(k in msg for k in ['整理桌面', '整理文件', 'clean desktop', 'organize desktop', 'tidy desktop']):
                computer_use_triggers = True
            # 窗口控制
            if any(k in msg for k in ['最大化', '最小化', 'maximize', 'minimize']):
                computer_use_triggers = True
            # 打开+应用名（组合匹配，不漏词）
            if '打开' in msg and any(k in msg for k in ['设置', '浏览器', '记事本', '计算器', '画图', '资源管理器', '任务管理器', '终端', 'cmd', '控制面板', '应用']):
                computer_use_triggers = True
            # ★ 2026-08-16: 英文 open+应用名
            if 'open' in msg and any(k in msg for k in ['settings', 'browser', 'edge', 'chrome', 'notepad', 'calculator', 'mspaint', 'paint', 'explorer', 'task manager', 'terminal', 'cmd', 'control panel', 'control panel']):
                computer_use_triggers = True
            # 列出磁盘
            if any(k in msg for k in ['列出', '查看', 'list']) and any(k in msg for k in ['盘', '磁盘', 'drive', 'disk']):
                computer_use_triggers = True
            # 输入文字
            if any(k in msg for k in ['输入文字', '打字', 'type text']):
                computer_use_triggers = True

            if computer_use_triggers:
                result = run_script_agent('computer_use', user_message)
                from src.utils.i18n import tr as _tr
                output = result.get('output', result.get('error', _tr('route.done')))
                if stream:
                    yield ("text", f"🤖 {_tr('route.computer_ops')} {output}")
                return output

            # system_ops 触发
            system_ops_triggers = False
            if any(k in msg for k in ['装软件', '安装软件', '装个', '装一']) and not any(k in msg for k in ['思路', '想法', '方案', '办法']):
                system_ops_triggers = True
            # ★ 2026-08-16: 英文装软件（无"思路"类排除词，用复合词防误伤）
            if any(k in msg for k in ['install software', 'install app', 'install application', 'install a program']):
                system_ops_triggers = True
            if any(k in msg for k in ['卸软件', '卸载', 'uninstall']):
                system_ops_triggers = True
            if any(k in msg for k in ['清理磁盘', '清理垃圾', 'clean', 'disk cleanup', 'cleanup']):
                system_ops_triggers = True
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
            tool_defs = get_tool_definitions()
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
    """★ 2026-08-08: 只检测匹配，不执行（用于流式执行前）"""
    try:
        from src.core.scripts_runner import list_skill_scripts
        from pathlib import Path
        skills = list_skill_scripts()
        if not skills:
            return None
        msg_lower = user_message.lower()
        for skill in skills:
            skill_name = skill.get('skill_name', '')
            skill_md = Path(__file__).parent.parent.parent / 'data' / 'skills' / skill_name / 'SKILL.md'
            if not skill_md.exists():
                continue
            content = skill_md.read_text(encoding='utf-8')
            triggers = ''
            if content.startswith('---'):
                end = content.find('---', 3)
                if end > 0:
                    for line in content[3:end].split('\n'):
                        line = line.strip()
                        if line.startswith('trigger:'):
                            triggers = line.split(':', 1)[1].strip()
                            break
            if not triggers:
                continue
            trigger_words = [t.strip().lower() for t in triggers.split('|') if t.strip()]
            if any(tw in msg_lower for tw in trigger_words):
                return skill_name
        return None
    except Exception as e:
        logger.error(f"[Engine] Skill匹配检测失败: {e}")
        return None


def _try_skill_trigger(user_message, return_name=False):
    """检测用户消息是否匹配Skill触发词，匹配则自动执行脚本

    Returns:
        str or tuple: 脚本输出结果（None表示没有匹配）
        如果return_name=True，返回(skill_name, output)元组
    """
    try:
        from src.core.scripts_runner import list_skill_scripts, run_skill_script
        from pathlib import Path

        skills = list_skill_scripts()
        if not skills:
            return (None, None) if return_name else None

        msg_lower = user_message.lower()

        for skill in skills:
            skill_name = skill.get('skill_name', '')
            skill_md = Path(__file__).parent.parent.parent / 'data' / 'skills' / skill_name / 'SKILL.md'
            if not skill_md.exists():
                continue

            content = skill_md.read_text(encoding='utf-8')
            triggers = ''
            if content.startswith('---'):
                end = content.find('---', 3)
                if end > 0:
                    yaml_part = content[3:end]
                    for line in yaml_part.split('\n'):
                        line = line.strip()
                        if line.startswith('trigger:'):
                            triggers = line.split(':', 1)[1].strip()
                            break

            if not triggers:
                continue

            trigger_words = [t.strip().lower() for t in triggers.split('|') if t.strip()]
            matched = any(tw in msg_lower for tw in trigger_words)

            if matched:
                logger.info(f"[Engine] Skill触发词匹配: {skill_name} (触发词: {triggers})")
                actions = skill.get('actions', [])
                action = actions[0]['name'] if actions else 'main'
                result = run_skill_script(skill_name, action, {}, timeout=120)
                if result.get('success'):
                    output = result.get('output', '')
                    logger.info(f"[Engine] Skill {skill_name} 执行成功，输出 {len(output)} 字符")
                    return (skill_name, output) if return_name else output
                else:
                    error = result.get('stderr', '') or result.get('error', '未知错误')
                    logger.error(f"[Engine] Skill {skill_name} 执行失败: {error}")
                    err_out = f"脚本执行失败: {error}"
                    return (skill_name, err_out) if return_name else err_out

        return (None, None) if return_name else None
    except Exception as e:
        logger.error(f"[Engine] Skill触发检测失败: {e}")
        return (None, None) if return_name else None
        return None


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

    # 检测是否包含多个操作（至少2个金额或2个操作词）
    amounts = _re_split.findall(r'(\d+(?:\.\d+)?)\s*元', user_message)
    remind_words = _re_split.findall(r'该(.+?)了|记得(.+?)|提醒我(.+?)', user_message)

    # 少于2个操作 → 不拆分
    if len(amounts) < 1 and len(remind_words) < 1:
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

    # 匹配提醒项
    remind_pattern = r'该(.+?)了|记得(.+?)|提醒我(.+?)|别忘了(.+?)'
    remind_matches = _re_split.findall(remind_pattern, user_message)
    for match in remind_matches:
        task = ''.join([g for g in match if g])
        if task:
            operations.append({'type': '提醒', 'task': task})

    if not operations:
        return None

    # 执行记账操作
    results = []
    recorded = 0
    for op in operations:
        if op['type'] == '记账':
            try:
                import sqlite3 as _sqlite_split
                import os as _os_split
                db_path = _os_split.path.join(
                    _os_split.path.dirname(_os_split.path.dirname(_os_split.path.dirname(_os_split.path.abspath(__file__)))),
                    'data', 'sqlite', 'lingzhi.db'
                )
                conn = _sqlite_split.connect(db_path)
                conn.execute(
                    "INSERT INTO skill_accounting (type, amount, category, date, note) VALUES (?, ?, ?, date('now'), ?)",
                    ('支出', op['amount'], op['category'], op['item'])
                )
                conn.commit()
                conn.close()
                results.append(f"✅ {op['item']} {op['amount']}元（{op['category']}）")
                recorded += 1
            except Exception as e:
                results.append(f"❌ {op['item']} 记录失败: {e}")
        elif op['type'] == '提醒':
            results.append(f"🔔 提醒：{op['task']}（待办）")

    # 生成汇总
    total = sum(op['amount'] for op in operations if op['type'] == '记账')
    summary = f"📋 已处理{len(operations)}项操作：\n\n"
    for r in results:
        summary += f"  {r}\n"
    summary += f"\n💰 本次共记账{recorded}笔，合计{total:.1f}元"
    if any(op['type'] == '提醒' for op in operations):
        summary += "\n🔔 提醒已记录"

    logger.info(f"[Engine] 智能拆分: {len(operations)}项操作")
    return summary


def _try_quick_command(user_message):
    """检测快速命令并调用内置工具执行（不经过外脑）"""
    import re as _re_qc
    import os as _os_qc
    msg = user_message.lower().strip()

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
        from src.core.model_manager import ModelManager
        mm = ModelManager()
        _engine_instance = ConversationEngine(
            model_manager=mm,
            conversation_id="__system__",
        )
    return _engine_instance
