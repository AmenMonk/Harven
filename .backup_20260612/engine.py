# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
AI对话核心引擎 v2.0
整合模型调用 + 功德行 + 三段式查询（知识库→联网搜索→推理）+ 记忆持久化
"""
import re
import json
from datetime import datetime
from src.core.model_manager import ModelManager
from src.core.morality import SYSTEM_PROMPT, analyze_intent
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


def _to_bool(val):
    """兼容字符串/布尔/整型等多种输入，统一转为 bool"""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on", "enabled")
    if isinstance(val, (int, float)):
        return bool(val)
    return False


class ConversationEngine:
    """对话引擎：管理一次对话的完整流程"""

    # ── 三种模式 ──
    #   auto    — 本地知识库 + 互联网 + 大模型 + Ollama，由 Ollama 最终综合分析给出答案
    #   kb_only — 本地知识库 + Ollama，Ollama 整合分析后给出答案
    #   direct  — Ollama 记录提问 → 外接大模型给出答案 → Ollama 整合分析后给出答案
    QUERY_MODES = {
        "auto": "自动（本地KB+搜索+外脑→Ollama整合）",
        "kb_only": "本地（知识库+Ollama整合分析）",
        "direct": "大模型（外脑回答+Ollama整合）",
    }

    def __init__(self, model_manager: ModelManager, conversation_id: str, specialization_prompt=None, query_mode=None):
        self.model = model_manager
        self.conversation_id = conversation_id
        self.history = []
        self._specialization_prompt = specialization_prompt  # 专项培养系统提示词
        self._load_history()

        # 加载用户偏好
        self._context_rounds = 8  # 默认8轮=16条消息，降低token消耗；用户可在设置中调整
        self._temperature = 0.7
        self._stream_output = True
        self._web_search_enabled = True
        self._kb_search_enabled = True
        self._load_preferences()

        # 查询模式（优先使用传入参数，否则从配置加载）
        if query_mode and query_mode in self.QUERY_MODES:
            self._query_mode = query_mode
        else:
            mode = get_preference("query_mode")
            self._query_mode = mode if mode in self.QUERY_MODES else "auto"

    def _load_preferences(self):
        """加载用户设置（数据库优先，无记录时回退 config.yaml）"""
        import yaml
        from pathlib import Path
        cfg = {}
        try:
            cfg_path = Path(__file__).parent.parent.parent / "config.yaml"
            if cfg_path.exists():
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning(f"[Engine] config.yaml 加载失败: {e}")
        web_cfg = cfg.get("web_search", {})
        model_cfg = cfg.get("model", {})

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

        stream = get_preference("stream_output")
        if stream is not None:
            self._stream_output = _to_bool(stream)

        web = get_preference("web_search_enabled")
        if web is not None:
            self._web_search_enabled = _to_bool(web)
        else:
            self._web_search_enabled = web_cfg.get("enabled", True)

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

    def send(self, user_message, stream=True, query_mode=None):
        """
        发送用户消息，执行三段式查询

        Args:
            query_mode: 可覆盖实例默认模式 (auto/kb_only/direct)

        Returns:
            stream=True: 生成器 (type, content)
                type: "thinking" | "text" | "warning" | "error" | "source"
            stream=False: 完整回复文本
        """
        # 允许本次调用临时覆盖查询模式
        if query_mode and query_mode in self.QUERY_MODES:
            self._query_mode = query_mode
        # 0. 检查是否处于锁机状态
        from src.core.morality import is_lockdown
        if is_lockdown():
            if stream:
                yield ("error", "Harven AI当前处于强制休眠状态，请等待恢复或重新启动。")
                return  # 必须阻止后续执行
            return "Harven AI当前处于强制休眠状态，请等待恢复或重新启动。"

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

        # 0.6 检查图片/视频生成意图
        try:
            from src.core.image_gen import process_image_gen_message
            gen_result = process_image_gen_message(user_message)
            if gen_result is not None:
                save_message(self.conversation_id, "user", user_message)
                from src.core.image_gen import get_image_generator
                formatted = get_image_generator().format_result_for_chat(gen_result)
                save_message(self.conversation_id, "assistant", formatted)
                self.history.append({"role": "user", "content": user_message})
                self.history.append({"role": "assistant", "content": formatted})
                if stream:
                    yield ("text", formatted)
                return formatted
        except Exception as e:
            logger.debug(f"[Engine] 图片生成跳过: {e}")

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

        # 2. 功德行分析（v2.0: LLM意图复核 + block机制）
        morality = analyze_intent(user_message)

        # 2.5 处理阻止执行（harmful确认后不执行）
        if morality.get("block"):
            save_message(self.conversation_id, "user", user_message)
            warning = morality.get("warning_text", "")
            if stream:
                yield ("warning", warning)
            return warning

        # 2.6 处理讨论模式（ambiguous：和主人对话确认意图）
        if morality["level"] == "discuss":
            save_message(self.conversation_id, "user", user_message)
            discussion = morality.get("warning_text", "")
            save_message(self.conversation_id, "assistant", discussion or "...")
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": discussion or "..."})
            if stream:
                yield ("text", discussion or "...")
            return discussion or "..."

        # 2. 保存用户消息
        save_message(self.conversation_id, "user", user_message)

        if stream:
            yield from self._stream_three_stage(user_message, morality)
        else:
            return self._sync_three_stage(user_message, morality)

    def _build_context(self):
        """
        构建发送给模型的上下文（动态摘要策略）
        - 最近8轮(16条)：全量保留
        - 更早的历史：只保留user消息（去掉assistant长回复以节省token）
        - 最早的用户消息最多保留5条，避免无限增长
        """
        all_msgs = self.history
        n_recent = 16  # 8轮 × 2

        if len(all_msgs) <= n_recent:
            return all_msgs

        # 最近8轮全量
        recent = all_msgs[-n_recent:]

        # 更早的：只取user消息，最多5条
        older = all_msgs[:-n_recent]
        early_user = [m for m in older if m["role"] == "user"]
        early_summary = early_user[-5:]  # 最多5条最早的用户消息

        return early_summary + recent

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

    def _classify_query(self, message):
        """
        判断查询类型（关键词快速匹配 + LLM 兜底）

        策略：关键词匹配覆盖 80% 常见场景（毫秒级），
        剩余 20% 模糊场景交给 LLM 分类。

        Returns:
            dict: {"need_kb": bool, "need_web": bool, "query_type": str}
        """
        msg = message.strip()
        msg_lower = msg.lower()

        # 短消息快速跳过
        if len(msg) <= 4 and all(c not in "？?。.！!;；" for c in msg):
            return {"need_kb": False, "need_web": False, "query_type": "casual"}

        # ── 关键词快速匹配 ──
        casual_patterns = [
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
        ]
        for p in casual_patterns:
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

        用最小的 prompt 快速分类，1-2 秒内完成。
        """
        try:
            prompt = f"""你是一个查询分类器。请判断以下用户消息的意图类型，只返回 JSON：
{{"need_kb": true/false, "need_web": true/false, "type": "knowledge/realtime/casual/coding/creative"}}

规则：
- need_kb=true: 需要查询本地知识库（涉及已知知识、教程、文档）
- need_web=true: 需要联网搜索（涉及实时信息、新闻、最新数据）
- 闲聊/情感/问候: need_kb=false, need_web=false, type="casual"
- 写代码/技术问题: need_kb=true, type="coding"
- 创作/写作: need_kb=false, type="creative"

用户消息: {message[:200]}

JSON:"""


            full_text = ""
            try:
                # 意图分类强制使用本地 Ollama（10字prompt，不值得调云）
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
                return {
                    "need_kb": result.get("need_kb", False),
                    "need_web": result.get("need_web", False),
                    "query_type": result.get("type", "general"),
                }
        except Exception as e:
            logger.warning(f"[Engine] LLM分类器异常（将使用关键词匹配）: {e}")
        return None

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
        Returns: (use_external: bool, model_label: str)

        路由规则：
        - direct → 云端大模型（用户主动选直答，要强模型）
        - auto/kb_only → 本地 Ollama（综合整合用本地足够）
        - 如果未配置外部模型，全部回落 Ollama
        """
        if query_mode == "direct":
            try:
                from src.core.multi_model_manager import get_multi_model_manager
                mm = get_multi_model_manager()
                if mm is not None and mm.current_model != "ollama":
                    all_providers = mm._get_all_providers()
                    name = all_providers.get(mm.current_model, {}).get("name", mm.current_model)
                    return True, name
            except Exception as e:
                logger.debug(f"[Engine] 多模型管理器检测失败（将使用本地Ollama）: {e}")
        return False, "本地 Ollama"

    def _resolve_query_mode(self, query_info):
        """
        根据 _query_mode 决定实际搜索行为
        Returns: (do_kb, do_web)

        三种模式：
        - direct  ：纯云端大模型，不做任何搜索
        - kb_only ：只查本地知识库
        - auto    ：由AI判断是否需要联网/查知识库（默认）
        """
        mode = self._query_mode
        if mode == "direct":
            return False, False
        elif mode == "kb_only":
            return self._kb_search_enabled, False
        else:  # auto（默认）
            return (
                query_info["need_kb"] and self._kb_search_enabled,
                query_info["need_web"] and self._web_search_enabled,
            )

    def _stream_three_stage(self, user_message, morality):
        """流式三段式查询（支持 query_mode 切换）"""
        full_response = ""

        # ========== direct 模式：直通模型，不做任何加工 ==========
        if self._query_mode == "direct":
            # 智能路由：直答模式 → 云端大模型（如果已配置）
            use_external, model_label = self._route_model_for_query("direct")

            yield ("thinking", f"🤖 大模型 · {model_label} 外脑回答...")
            # 只保留对话历史上下文，不注入任何系统提示词、记忆、用户画像
            context = self._build_context()

            try:
                if use_external:
                    # 直答模式 + 外部模型：空系统提示词，纯透传
                    from src.core.multi_model_manager import get_multi_model_manager
                    mm = get_multi_model_manager()
                    all_messages = [{"role": "system", "content": ""}]
                    all_messages.extend(context)
                    all_messages.append({"role": "user", "content": user_message})
                    response_gen = mm.generate_response(all_messages, stream=True)
                    raw_stream = self._adapt_mm_stream(response_gen)
                else:
                    # 直答模式 + 本地 Ollama
                    response_stream = self.model.chat_with_context(
                        system_prompt="",  # 空系统提示词 = 原始模型行为
                        conversation_history=context,
                        user_message=user_message,
                        stream=True,
                        temperature=self._temperature,
                    )
                    raw_stream = response_stream

                for chunk in raw_stream:
                    if isinstance(chunk, str) and chunk.startswith("[错误]"):
                        yield ("error", chunk)
                        return
                    if isinstance(chunk, str):
                        full_response += chunk
                        yield ("text", chunk)
            except Exception as e:
                logger.error(f"[Engine] direct 模式模型调用失败: {e}")
                yield ("error", f"模型调用失败: {e}")
                return
            # 保存到数据库
            save_message(self.conversation_id, "assistant", full_response)
            self.history.append({"role": "assistant", "content": full_response})
            return
        # ========================================================

        # 判断查询类型（仅 auto 模式使用分类结果）
        query_info = self._classify_query(user_message)

        # 根据模式决定搜索行为
        do_kb, do_web = self._resolve_query_mode(query_info)

        # 收集增强上下文
        enhanced_context = ""
        kb_results = []
        web_results = []

        # Step 0: 检测 GitHub URL 并自动下载（所有模式都执行，很快）
        github_context = self._check_github_url(user_message)
        if github_context:
            enhanced_context += github_context
            yield ("source", "📦 已从 GitHub 下载并导入知识库")

        # Step 0b: 浏览器自动化检测（仅 auto/full 模式触发）
        if self._query_mode == "auto":
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

        # Step 0c: Active Memory（所有模式都执行，本地极快）
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

        # Step 2: 联网搜索
        if do_web:
            yield ("thinking", "🌐 正在联网搜索...")
            try:
                from src.core.web_search import get_web_searcher
                searcher = get_web_searcher()
                web_results = searcher.search_and_fetch(user_message, max_results=3, fetch_top=2)
                if web_results:
                    search_date = datetime.now().strftime("%Y年%m月%d日 %H:%M")
                    web_text = ""
                    for r in web_results:
                        title = r.get("title") or r.get("url", "(无标题)")
                        content = r.get('content') or r.get('snippet') or '(无摘要)'
                        if not content.strip() and not title.strip():
                            continue
                        web_text += f"[网络-{title}]: {content}\n"
                    if web_text.strip():
                        enhanced_context += f"\n\n[增强信息——联网搜索，{search_date}]\n{web_text}"
                        yield ("source", f"🌐 从网络搜索到 {len(web_results)} 条相关结果")
                else:
                    # 搜索引擎返回空结果 — 告知AI搜索失败
                    enhanced_context += "\n\n[增强信息——联网搜索] ⚠️ 已尝试联网搜索但未返回有效结果。请基于你的知识和本地数据库回答主人，并如实告知：本次联网搜索未能获取到相关信息。不要编造或暗示已获取网络数据。"
                    yield ("source", "⚠️ 联网搜索未获取到有效结果")
                    logger.warning(f"[Engine] 联网搜索返回空结果")
            except Exception as e:
                enhanced_context += "\n\n[增强信息——联网搜索] ⚠️ 联网搜索功能本次不可用（可能原因：网络问题、搜索引擎无法访问、或被反爬虫拦截）。请基于你的知识和本地数据库回答主人，并明确告诉主人：联网搜索暂时无法使用。"
                yield ("source", "⚠️ 联网搜索暂时不可用")
                logger.error(f"[Engine] 联网搜索失败: {e}")

        # Step 3: AI推理整合
        # 智能路由：决定用本地还是云端模型
        use_external, model_label = self._route_model_for_query(self._query_mode)
        logger.info(f"[Engine] 路由: mode={self._query_mode}, use_external={use_external}, label={model_label}")

        if self._query_mode == "direct":
            yield ("thinking", f"🤖 模型直答 · {model_label} 直接回答...")
        else:
            yield ("thinking", f"🧠 正在整合分析 · {model_label}...")

        # 构建增强系统提示词
        # 注意：不再注入绝对日期断言，因为联网搜索结果日期可能与本地系统日期不一致，
        # 导致模型不信任搜索结果。日期信息由搜索结果中的 search_date 提供。
        # 注意：SYSTEM_PROMPT 中不再使用 {current_model} 占位符，
        # 因为实际推理模型由 _route_model_for_query() 动态决定。
        system_prompt = SYSTEM_PROMPT

        # 注入专项培养系统提示词
        if self._specialization_prompt and self._specialization_prompt.strip():
            system_prompt += f"""

## 当前专项培养模式

{self._specialization_prompt}

请在当前模式下严格遵循以上规则回答。

## 后台任务能力（专项培养模式专属）

你拥有自动执行后台任务的能力。当用户要求你搜集资料、下载资源、学习课程、安装工具等操作时，你**不需要**手动引导用户去操作，而是应该**直接在回复末尾输出一个JSON任务块**，系统会自动在后台执行这些任务，不会阻塞聊天。

### 支持的操作类型

| action | 说明 | 必需参数 | 可选参数 |
|--------|------|----------|----------|
| search | 联网搜索并自动保存结果到知识库 | query | - |
| download | 下载文件到本地 | url | - |
| knowledge | 直接保存知识到知识库 | content | - |
| install | 安装Python包 | package | - |

### JSON格式（在回复末尾用 ```json 包裹）

```json
[
  {{"action": "search", "query": "儿童发展心理学最新研究论文", "description": "搜索心理学最新研究"}},
  {{"action": "search", "query": "Python NLP自然语言处理入门教程", "description": "搜索NLP教程资料"}},
  {{"action": "download", "url": "https://example.com/paper.pdf", "description": "下载论文PDF"}},
  {{"action": "knowledge", "content": "关键知识点总结...", "description": "保存核心知识到知识库"}},
  {{"action": "install", "package": "transformers", "description": "安装NLP依赖包"}}
]
```

### 使用规则
1. 当用户要求搜集、学习、下载、安装任何资源时，**主动**生成JSON任务块
2. 每个任务都要有清晰的description描述
3. search会自动联网搜索并将结果保存到知识库
4. download会下载文件到本地downloads目录
5. knowledge会直接将内容存入向量知识库，后续查询可检索
6. install仅支持Python包名（如numpy、transformers），会通过pip安装
7. 所有任务在后台并行执行，用户可以继续和你聊天，不受影响
8. 在回复中告诉用户"已在后台启动X项任务，可在任务管理中查看进度"
"""

        # 注入记忆上下文
        try:
            from src.core.memory import get_memory_manager
            mem_ctx = get_memory_manager().build_memory_context(self.conversation_id)
            if mem_ctx:
                system_prompt += mem_ctx
        except Exception as e:
            logger.error(f"[Engine] 加载记忆失败: {e}")

        # 注入用户画像上下文
        try:
            from src.core.user_profile import get_user_profile
            profile_ctx = get_user_profile().build_profile_context()
            if profile_ctx:
                system_prompt += profile_ctx
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

        # 注入任务上下文
        try:
            from src.core.task_manager import get_task_manager
            task_ctx = get_task_manager().build_task_context(self.conversation_id)
            if task_ctx:
                system_prompt += task_ctx
        except Exception as e:
            logger.error(f"[Engine] 加载任务上下文失败: {e}")

        if enhanced_context.strip():
            system_prompt += f"""
## 本次查询的增强信息
{enhanced_context}

请基于以上增强信息综合回答主人的问题。注意：[增强信息——联网搜索]标记的信息来自搜索引擎，是当前可获取的最新信息。如果标记中包含⚠️，说明本次搜索未能获取有效数据，请如实告知主人，不要假装已有网络数据。
"""

        # 注入 Skill 系统提示词（用户自定义技能）
        try:
            from src.core.skills_manager import get_skills_manager
            skill_prompt = get_skills_manager().get_skill_prompt(user_message)
            if skill_prompt:
                system_prompt += f"""
## 当前激活技能
{skill_prompt}

请在当前对话中严格遵循以上技能要求。
"""
                logger.info(f"[Engine] 注入技能提示词（{len(skill_prompt)}字）")
        except Exception as e:
            logger.debug(f"[Engine] 加载技能提示词失败: {e}")

        # 构建上下文
        context = self._build_context()
        logger.debug(f"[Engine] 上下文: {len(context)}条历史, 系统提示词: {len(system_prompt)}字")

        # 构建完整的消息列表
        all_messages = [{"role": "system", "content": system_prompt}]
        all_messages.extend(context)
        all_messages.append({"role": "user", "content": user_message})

        # ===== 智能模型路由：已在 Step 3 阶段计算 use_external/model_label =====
        logger.info(f"[Engine] use_external={use_external}, model_label={model_label}, query_mode={self._query_mode}")

        try:
            if use_external:
                # 使用多模型管理器的外部模型
                from src.core.multi_model_manager import get_multi_model_manager
                mm = get_multi_model_manager()
                logger.info(f"[Engine] 使用外部模型: {mm.current_model}")
                response_gen = mm.generate_response(all_messages, stream=True)
                # response_gen yields {"type": "text/error", "content": str}
                raw_stream = self._adapt_mm_stream(response_gen)
            else:
                # 使用本地 Ollama
                logger.info(f"[Engine] 使用本地 Ollama 模型: {self.model.model if hasattr(self.model, 'model') else '?'}")
                response_stream = self.model.chat_with_context(
                    system_prompt=system_prompt,
                    conversation_history=context,
                    user_message=user_message,
                    stream=True,
                    temperature=self._temperature,
                )
                raw_stream = response_stream
            
            # 调试：检查 raw_stream 类型
            logger.info(f"[Engine] raw_stream type: {type(raw_stream)}")
            if hasattr(raw_stream, '__iter__'):
                logger.info(f"[Engine] raw_stream is iterable")
            else:
                logger.error(f"[Engine] raw_stream is NOT iterable! Cannot iterate")
                
        except Exception as e:
            logger.error(f"[Engine] 模型调用异常: {e}")
            yield ("error", f"模型调用失败: {e}")
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
            logger.info(f"[Engine] 第一个 chunk: type={type(first_chunk)}, content={str(first_chunk)[:100]}")
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
        if chunk_count == 0 and chunk_meta_count == 0:
            logger.warning(f"[Engine] 模型返回了空流！模型可能超时或无响应。model={self.model.model if hasattr(self.model, 'model') else '?'}")
            yield ("error", "模型没有返回任何内容。可能原因：Ollama 服务异常、模型加载失败、或请求超时。建议：重启 Ollama 后重试。")
            return

        if chunk_count == 0 and chunk_meta_count > 0:
            logger.warning(f"[Engine] 模型返回 {chunk_meta_count} 个 chunk 但都无 content！可能是响应格式异常。")
            # 尝试用简化 prompt 重试一次
            logger.info(f"[Engine] 尝试用简化 prompt 重试...")
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
                if retry_count == 0:
                    logger.warning(f"[Engine] 简化重试仍然返回空！")
                    yield ("error", "模型无法生成回复，请尝试：1. 重启 Ollama 2. 切换模型 3. 简化问题")
                    return
                logger.info(f"[Engine] 简化重试成功, {retry_count} chunks")
            except Exception as retry_e:
                logger.error(f"[Engine] 简化重试失败: {retry_e}")
                yield ("error", f"模型重试失败: {retry_e}")
                return

        # 保存AI回复
        save_message(self.conversation_id, "assistant", full_response)

        # 触发消息发送事件（Hooks）
        get_event_bus().emit(Events.MESSAGE_SENT, self.conversation_id, full_response)
        get_event_bus().emit(Events.MEMORY_EXTRACTED, self.conversation_id, user_message, full_response)

        # 更新历史
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": full_response})

        # 功德行善意提醒（warn和guide级别都有可能带warning_text）
        if morality.get("warning_text") and morality["level"] in ("warn", "guide"):
            yield ("warning", morality["warning_text"])

        # 自动提取记忆
        if full_response:
            try:
                from src.core.memory import get_memory_manager
                mm = get_memory_manager()
                mm.extract_task_memory(self.conversation_id, user_message, full_response)
            except Exception as e:
                logger.error(f"[Engine] 记忆提取失败: {e}")

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

            # 自动检测并启动后台任务（专项培养模式）
            if self._specialization_prompt and self._specialization_prompt.strip():
                try:
                    from src.core.specialization_executor import (
                        parse_specialization_steps,
                        create_background_task_for_conversation,
                    )
                    bg_steps = parse_specialization_steps(full_response)
                    if bg_steps:
                        bg_title = f"专项培养: {user_message[:30]}"
                        create_background_task_for_conversation(
                            title=bg_title,
                            steps=bg_steps,
                            conversation_id=self.conversation_id,
                        )
                        logger.info(f"[Engine] 启动 {len(bg_steps)} 步后台任务")
                except Exception as e:
                    logger.error(f"[Engine] 后台任务启动失败: {e}")

            # 自动保存快照（每5轮保存一次）
            try:
                from src.core.memory import get_memory_manager
                total_msgs = len(self.history)
                if total_msgs > 0 and total_msgs % 10 == 0:  # 每5轮(10条消息)
                    get_memory_manager().save_snapshot(self.conversation_id)
            except Exception as e:
                logger.error(f"[Engine] 快照保存失败: {e}")

        # 自动保存新知识到知识库（简单实现）
        if web_results and full_response:
            self._auto_save_knowledge(user_message, full_response)

    def _sync_three_stage(self, user_message, morality):
        """同步三段式查询"""
        full_response = ""
        for msg_type, content in self._stream_three_stage(user_message, morality):
            if msg_type == "text":
                full_response += content
            elif msg_type == "error":
                return content
        if morality["level"] == "warn" and morality["warning_text"]:
            full_response += f"\n\n---\n💡 善意提醒：{morality['warning_text']}\n"
        return full_response

    def _auto_save_knowledge(self, question, answer):
        """自动将新知识保存到知识库"""
        try:
            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            # 只保存较长的有价值的问答
            if len(answer) > 100 and len(question) > 5:
                # 组合问答作为知识条目
                knowledge_text = f"问：{question}\n答：{answer[:500]}"
                kb.add_document(knowledge_text, source="auto_learn")
        except Exception as e:
            logger.error(f"[Engine] 自动保存知识失败: {e}")

    def send_with_images(self, user_message, image_paths, stream=True):
        """带图片的消息发送"""
        from src.core.image_processor import analyze_image
        from src.models.database import save_message as db_save

        morality = analyze_intent(user_message)

        # 处理阻止执行
        if morality.get("block"):
            db_save(self.conversation_id, "user", user_message or "[图片]")
            warning = morality.get("warning_text", "")
            if stream:
                def _block_gen():
                    yield ("warning", warning)
                return _block_gen()
            return warning

        # 处理讨论模式
        if morality["level"] == "discuss":
            db_save(self.conversation_id, "user", user_message or "[图片]")
            discussion = morality.get("warning_text", "")
            db_save(self.conversation_id, "assistant", discussion or "...")
            if stream:
                def _discuss_gen():
                    yield ("text", discussion or "...")
                return _discuss_gen()
            return discussion or "..."

        db_save(self.conversation_id, "user", user_message or "[图片]")

        if stream:
            return self._stream_image(user_message, image_paths, morality)
        else:
            response = ""
            for msg_type, content in self._stream_image(user_message, image_paths, morality):
                if msg_type == "text":
                    response += content
                elif msg_type == "done":
                    response = content
            return response

    def send_with_video(self, user_message, video_path, stream=True):
        """带视频的消息发送"""
        from src.core.video_processor import analyze_video
        from src.models.database import save_message as db_save

        morality = analyze_intent(user_message)

        # 处理阻止执行
        if morality.get("block"):
            db_save(self.conversation_id, "user", user_message or "[视频]")
            warning = morality.get("warning_text", "")
            if stream:
                def _block_gen():
                    yield ("warning", warning)
                return _block_gen()
            return warning

        # 处理讨论模式
        if morality["level"] == "discuss":
            db_save(self.conversation_id, "user", user_message or "[视频]")
            discussion = morality.get("warning_text", "")
            db_save(self.conversation_id, "assistant", discussion or "...")
            if stream:
                def _discuss_gen():
                    yield ("text", discussion or "...")
                return _discuss_gen()
            return discussion or "..."

        db_save(self.conversation_id, "user", user_message or "[视频]")

        if stream:
            return self._stream_video(user_message, video_path, morality)
        else:
            response = ""
            for msg_type, content in self._stream_video(user_message, video_path, morality):
                if msg_type == "text":
                    response += content
                elif msg_type == "done":
                    response = content
            return response

    def _stream_image(self, text, image_paths, morality):
        """流式图片识别"""
        from src.core.image_processor import analyze_image

        full_text = ""
        for msg_type, content in analyze_image(
            self.model, text, image_paths,
            system_prompt=SYSTEM_PROMPT, stream=True
        ):
            if msg_type == "text":
                yield ("text", content)
                full_text += content
            elif msg_type == "done":
                from src.models.database import save_message
                save_message(self.conversation_id, "assistant", content)
                self.history.append({"role": "user", "content": text or "[图片]"})
                self.history.append({"role": "assistant", "content": content})

    def _stream_video(self, text, video_path, morality):
        """流式视频识别"""
        from src.core.video_processor import analyze_video

        full_text = ""
        for msg_type, content in analyze_video(
            self.model, text, video_path,
            system_prompt=SYSTEM_PROMPT, stream=True
        ):
            if msg_type == "text":
                yield ("text", content)
                full_text += content
            elif msg_type == "done":
                from src.models.database import save_message
                save_message(self.conversation_id, "assistant", content)
                self.history.append({"role": "user", "content": text or "[视频]"})
                self.history.append({"role": "assistant", "content": content})
            elif msg_type == "error":
                yield ("error", content)
                return
            elif msg_type == "thinking":
                yield ("thinking", content)

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


# 全局引擎单例（用于 resource_hub 等模块）
_engine_instance = None


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
