# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
本地模型后端 — Phase 3: ModelBackend 抽象层
支持多种本地推理引擎：Ollama / llama-cpp-python（GGUF 直跑）
通过统一接口 chat() / list_models() / load_model() 调用
"""
import requests
from openai import OpenAI
import logging
logger = logging.getLogger(__name__)

# 非对话模型关键词（统一常量）
_NONCHAT_KEYWORDS = [
    'embed', 'bge-', 'nomic', 'gte-', 'e5-',
    'musicgen', 'bark', 'riffusion', 'audiocraft',
    'stable-diffusion', 'sdxl', 'flux', 'kolors',
    'cogvideo', 'animatediff', 'opensora',
    'whisper',
]

# ★ #1238（2026-10-26）HarvenEye 本地混合模型引擎 id。
#   定位（定版拍板）：HarvenEye = 混合模型引擎（视觉 + 通用对话双能力），
#   进本地模型选项（不是垂直区）、用户自由选不定默认顶替、
#   模型未装不可出现（门禁 eye_client.models_ready）、失败回退 Ollama 本地。
EYE_MODEL_ID = 'harveneye'


def check_ollama(base_url="http://localhost:11434"):
    """
    检查本地引擎是否在运行

    Returns:
        (bool, str): (是否可用, 描述信息)
    """
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=8)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            return True, f"运行中，已安装 {len(models)} 个模型: {', '.join(model_names)}"
        return False, f"本地引擎返回状态码 {resp.status_code}"
    except requests.ConnectionError:
        return False, "本地引擎未运行，请先启动引擎服务"
    except Exception as e:
        return False, f"检测本地引擎失败: {e}"


def get_available_models(base_url="http://localhost:11434"):
    """获取已安装的模型列表"""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        if resp.status_code == 200:
            return [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as _e: logger.debug(f"[model_manager] 忽略: {_e}")
    return []


class ModelManager:
    """
    Phase 3: 本地模型后端管理器 — 支持多种引擎
    当前支持：Ollama (OpenAI 兼容 API) + llama-cpp-python (GGUF 直跑)
    未来可扩展：vLLM / MLX / TensorRT-LLM
    """

    def __init__(self, base_url="http://localhost:11434", model=""):
        self.base_url = base_url
        # ★ 2026-08-25: 类型守护——防止 list/dict 混入导致 .lower() 崩溃
        # ★ 2026-09-26 审计（开发者死命令：模型必须用户自选）：删除内定 qwen2.5:14b 兜底——
        #   非法类型 → 落「用户已装列表第一个通用模型」（用户机器上真实存在的）；
        #   空 → 保留空（诚实表示"未选择模型"，调用时报错引导，不偷用任何模型）
        if not isinstance(model, str) or not model.strip():
            if isinstance(model, str):
                logger.warning("[ModelManager] 未配置本地模型（default_model 为空）——用户尚未选择模型，不内定兜底")
                model = ""
            else:
                logger.warning(f"[ModelManager] 模型名类型异常({type(model).__name__})，改用已装列表第一个通用模型")
                _av = [m for m in get_available_models(base_url)
                       if 'embed' not in m.lower() and 'bge' not in m.lower() and 'minicpm' not in m.lower()]
                model = _av[0] if _av else ""
        self.model = model
        self._engine_type = None  # 'ollama' | 'llama_cpp' | 'harveneye' | 'chain' | None
        self._llama_engine = None
        self._using_gguf = False
        self._chain_id = ''      # ★ #1571 件1：对话型组合的户口 id

        # ★ #1571 件1（2026-11-27）：对话型组合（chain:<id>）当本地大脑——编排器接力执行。
        #   泛化 HarvenEye 当年的分流范式：末环是理解/生成大脑的组合就是一个「复合本地模型」。
        if model.strip().startswith('chain:'):
            self._engine_type = 'chain'
            self._chain_id = model.strip()[6:]
            logger.info(f"[ModelManager] 引擎已选定: 对话型组合 {self._chain_id}（组合即本地大脑，走编排器接力）")
            return

        # ★ #1238: HarvenEye 混合模型引擎（走 eye_client HTTP，进程内不碰其引擎代码）
        if model.strip() == EYE_MODEL_ID:
            self._engine_type = 'harveneye'
            logger.info("[ModelManager] 引擎已选定: HarvenEye 混合模型引擎（对话走 Eye /chat，失败回退 Ollama）")
            return

        # 尝试连接引擎（Ollama 优先，GGUF 回退）
        ok, _ = check_ollama(base_url)
        if ok:
            self._engine_type = 'ollama'
            self.client = OpenAI(
                base_url=f"{base_url}/v1",
                api_key="ollama",
                timeout=120.0,
            )
            logger.info(f"[ModelManager] 引擎已连接: {base_url}")
        else:
            # Ollama 不可用，尝试加载 GGUF 模型
            logger.info("[ModelManager] Ollama 引擎不可用，尝试 GGUF 直跑...")
            self._try_gguf_fallback(model)

    def set_model(self, model_name):
        """切换模型"""
        # ★ 2026-08-25: 类型守护
        if not isinstance(model_name, str) or not model_name.strip():
            logger.warning(f"[ModelManager] set_model 拒绝非法模型名({type(model_name).__name__}): {str(model_name)[:60]}")
            return False
        self.model = model_name
        # ★ #1322 选择即换血：用户选了本地模型，当场预载进显存（不写死模型名，通用机制）
        try:
            from src.core import local_model_swap
            local_model_swap.swap_in_async(model_name, reason='set_model')
        except Exception as _e:
            logger.debug(f"[ModelManager] 模型预载触发失败（不影响切换）: {_e}")
        if self._using_gguf:
            self._try_gguf_fallback(model_name)
        return True

    def _try_gguf_fallback(self, model_name=None):
        """尝试加载 GGUF 模型（魔搭下载的）"""
        try:
            from src.core.llama_engine import LlamaEngine, find_local_gguf
            gguf_path = find_local_gguf(model_name)
            if gguf_path:
                self._llama_engine = LlamaEngine(gguf_path)
                self._engine_type = 'llama_cpp'
                logger.info(f"[ModelManager] ✅ GGUF 引擎已加载: {gguf_path}")
                return True
        except ImportError:
            logger.warning("[ModelManager] llama-cpp-python 未安装，无法使用 GGUF")
        except Exception as e:
            logger.error(f"[ModelManager] GGUF 加载失败: {e}")
        self._engine_type = None
        return False

    @property
    def is_available(self):
        """模型是否可用"""
        return self._engine_type is not None

    def _chat_gguf(self, messages, stream=False, temperature=0.7, max_tokens=4096):
        """GGUF 引擎对话"""
        # 从 messages 提取 system prompt 和用户消息
        system_prompt = ""
        user_content = ""
        history_parts = []
        for m in messages:
            if m["role"] == "system":
                system_prompt = m["content"]
            elif m["role"] == "user":
                user_content = m["content"]
            elif m["role"] == "assistant":
                history_parts.append(f"Assistant: {m['content']}")

        history_text = "\n".join(history_parts) if history_parts else ""
        prompt = f"{history_text}\nUser: {user_content}\nAssistant:" if history_text else f"User: {user_content}\nAssistant:"

        if system_prompt:
            prompt = f"{system_prompt}\n\n{prompt}"

        if stream:
            return self._llama_engine.stream_generate(prompt, temperature=temperature, max_tokens=max_tokens)
        else:
            return self._llama_engine.generate(prompt, temperature=temperature, max_tokens=max_tokens)

    def warmup(self):
        """
        模型预热：发送一条极短消息加载模型到 GPU
        避免首次对话时的冷启动延迟（通常 8-15 秒）
        """
        # ★ #1238: HarvenEye 无需哈文侧预热（Eye 服务自己管模型加载），只探门禁
        if self._engine_type in ('harveneye', 'chain'):
            if self._engine_type == 'chain':
                # ★ #1571 件1：组合无需预热（编排器自管加载），只探户口在不在
                try:
                    from src.core.chain_engine.registry import get_registry as _greg
                    r = _greg().get_chain(self._chain_id)
                    logger.info(f"[ModelManager] 对话型组合{'就绪' if r else '不存在'}: {self._chain_id}")
                except Exception as e:
                    logger.debug(f"[ModelManager] 组合户口探测失败: {e}")
            else:
                def _eye_check():
                    try:
                        from src.core import eye_client
                        r = eye_client.models_ready()
                        if r.get('ready'):
                            logger.info(f"[ModelManager] HarvenEye 门禁就绪: {r.get('model')}")
                        else:
                            logger.warning(f"[ModelManager] HarvenEye 门禁未就绪: {r.get('error') or r.get('missing')}")
                    except Exception as e:
                        logger.debug(f"[ModelManager] HarvenEye 探测失败: {e}")
                threading.Thread(target=_eye_check, daemon=True).start()
            return
        import threading
        def _do_warmup():
            try:
                messages = [{"role": "user", "content": "hi"}]
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    stream=False,
                    temperature=0.1,
                    max_tokens=5,
                )
                content = response.choices[0].message.content
                logger.info(f"[ModelManager] 模型预热完成: {self.model} → {content[:30]}")
            except Exception as e:
                logger.error(f"[ModelManager] 模型预热失败（不影响使用）: {e}")
        t = threading.Thread(target=_do_warmup, daemon=True)
        t.start()
    
    def chat(self, messages, stream=False, temperature=0.7, max_tokens=4096):
        """发送对话请求 — 自动选择引擎（Ollama 或 GGUF）"""
        # ★ #1571 件1：对话型组合路径（先于专业模型拦截——组合 id 不会撞关键词，但顺序上更稳）
        if self._engine_type == 'chain':
            return self._chat_chain(messages, stream, temperature)

        # ═══ 非语言模型拦截 ═══
        _model_lower = (self.model or '').lower()
        if any(kw in _model_lower for kw in _NONCHAT_KEYWORDS):
            raise RuntimeError(
                f"「{self.model}」是专业模型，不具备语言对话能力。"
                "请切换到语言模型（如 qwen2.5:14b）或使用外脑模式。"
            )

        # ★ #1238: HarvenEye 路径（失败内部回退 Ollama，不外抛——engine 回退链会去
        #   找 llama-cpp/GGUF 反而绕远；定版拍板：Eye 失败回退 Ollama 本地）
        if self._engine_type == 'harveneye':
            return self._chat_eye(messages, stream, temperature)

        # GGUF 引擎路径
        if self._engine_type == 'llama_cpp' and self._llama_engine and self._llama_engine.is_loaded:
            return self._chat_gguf(messages, stream, temperature, max_tokens)

        # Ollama 路径
        if self._engine_type != 'ollama':
            raise RuntimeError("本地引擎不可用：既没有 Ollama 也没有 GGUF 模型")
        try:
            logger.debug(f"[ModelManager] 调用模型: {self.model}, 消息数: {len(messages)}, stream={stream}")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            if stream:
                return self._stream_response(response)
            else:
                return response.choices[0].message.content
        
        except Exception as e:
            logger.error(f"[ModelManager] 模型调用失败: {e}")
            raise RuntimeError(f"模型调用失败: {e}") from e
    
    def _chain_result_text(self, result) -> str:
        """组合产出 → 回复文本（str 直用；dict 取 step_output/常见文本字段；兜底 JSON）。"""
        import json as _json
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            so = result.get('step_output')
            if isinstance(so, str) and so.strip():
                return so
            _pool = so if isinstance(so, dict) else result
            for k in ('text', 'description', 'result', 'content', 'answer'):
                v = _pool.get(k) if isinstance(_pool, dict) else None
                if isinstance(v, str) and v.strip():
                    return v
            try:
                return _json.dumps(result, ensure_ascii=False)[:4000]
            except Exception:
                return str(result)[:4000]
        return str(result)[:4000]

    def _chat_chain(self, messages, stream, temperature):
        """★ #1571 件1：对话型组合执行——用户消息→编排器接力→末环输出即回复。
        组合编排是同步执行（无逐 token 流），stream 时一次性 yield，诚实不伪装打字机。
        铁律 0：组合失败如实报错带指引，绝不偷偷换模型回退。"""
        from src.core.chain_engine.registry import get_registry as _greg
        from src.core.chain_engine import get_orchestrator as _gorch
        r = _greg().get_chain(self._chain_id)
        if r is None:
            raise RuntimeError(f"组合 {self._chain_id} 不存在（可能已被删除），请到工坊确认")
        # 组装接力 prompt：最近几轮文字 + 本轮用户消息（组合无会话记忆，拼进提示词是最诚实的记忆替身）
        hist = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            c = m.get('content')
            if not isinstance(c, str) or not c.strip():
                continue
            if m.get('role') == 'user':
                hist.append(f"用户：{c}")
            elif m.get('role') == 'assistant':
                hist.append(f"助手：{c}")
        prompt = "\n".join(hist[-10:])
        out = _gorch().run(self._chain_id, {"prompt": prompt})
        if not out.get('ok'):
            _err = str(out.get('error') or '')
            if 'image_path' in _err:
                raise RuntimeError(f"组合「{r.name}」要看图才能干活——发一张照片（或截图）再问它")
            raise RuntimeError(f"组合「{r.name}」执行失败: {_err}（可到工坊看物流单定位哪一环）")
        text = self._chain_result_text(out.get('result'))
        if not text.strip():
            raise RuntimeError(f"组合「{r.name}」执行完成但没有产出文字（到工坊检查末环能力是否为看图理解/文字生成）")
        if stream:
            def _gen():
                yield text
            return _gen()
        return text

    def _eye_fallback_notice(self, reason: str) -> str:
        """Eye 失败回退 Ollama 时的诚实标注（回答开头一行，不冒充 Eye 在答）。"""
        fb = self._pick_ollama_fallback_model()
        return f"（⚠️ 视觉引擎暂不可用：{reason}；已回退本地模型 {fb} 回答）\n\n"

    def _pick_ollama_fallback_model(self) -> str:
        """回退用 Ollama 模型：ollama.default_model 优先，否则已装列表第一个通用模型。"""
        name = ''
        try:
            from src.utils.config import get_config
            name = (get_config() or {}).get('ollama', {}).get('default_model', '') or ''
        except Exception:
            pass
        if not name or name == EYE_MODEL_ID:
            avail = [m for m in get_available_models(self.base_url)
                     if 'embed' not in m.lower() and 'bge' not in m.lower()
                     and 'minicpm' not in m.lower()]
            name = avail[0] if avail else ''
        return name

    def _ensure_ollama_fallback(self) -> bool:
        """把当前实例从 harveneye 切到 Ollama 引擎（回退用，实例级一次性切换）。"""
        ok, _ = check_ollama(self.base_url)
        if not ok:
            return False
        fb = self._pick_ollama_fallback_model()
        if not fb:
            return False
        self._engine_type = 'ollama'
        self.model = fb
        self.client = OpenAI(base_url=f"{self.base_url}/v1", api_key='ollama', timeout=120.0)
        logger.warning(f"[ModelManager] HarvenEye 不可用 → 已回退 Ollama 模型: {fb}")
        return True

    def _chat_eye(self, messages, stream, temperature):
        """HarvenEye 对话：/chat 非流式或 SSE 流式；失败自动回退 Ollama（带诚实标注）。"""
        from src.core import eye_client
        msgs = [{'role': m.get('role', 'user'), 'content': m.get('content', '')}
                for m in messages if isinstance(m, dict) and m.get('content')]
        if not msgs:
            raise RuntimeError('HarvenEye 对话消息为空')
        notice_yielded = False
        try:
            if not stream:
                ok, content = eye_client.chat(msgs, timeout=150.0)
                if not ok:
                    # 非流式回退：切 Ollama 引擎后重发（同步返回，标注前置）
                    if not self._ensure_ollama_fallback():
                        raise RuntimeError(f'视觉引擎不可用且 Ollama 回退也失败: {content}')
                    retry_msgs = [{'role': 'system', 'content': '你是一个有帮助的AI助手。'}] + msgs[-8:]
                    fb_text = self.chat(retry_msgs, stream=False, temperature=temperature)
                    return self._eye_fallback_notice(str(content)[:80]) + (fb_text or '')
                return content

            def _gen():
                nonlocal notice_yielded
                try:
                    for piece in eye_client.chat_stream(msgs):
                        if not notice_yielded:
                            # 首包即成功，无回退——纯答案，不插标注
                            notice_yielded = True
                        yield piece
                    if not notice_yielded:
                        raise RuntimeError('视觉引擎返回空流')
                except RuntimeError as e:
                    if notice_yielded:
                        # 已吐过答案再断——诚实标注断点
                        yield f"\n\n（⚠️ 视觉引擎传输中断：{e}）"
                        return
                    if not self._ensure_ollama_fallback():
                        raise RuntimeError(f'视觉引擎不可用且 Ollama 回退也失败: {e}') from e
                    yield self._eye_fallback_notice(str(e)[:80])
                    notice_yielded = True
                    retry_msgs = [{'role': 'system', 'content': '你是一个有帮助的AI助手。'}] + msgs[-8:]
                    yield from self.chat(retry_msgs, stream=True, temperature=temperature)

            return _gen()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f'视觉引擎对话失败: {e}') from e

    def _stream_response(self, response):
        """处理流式响应，兼容普通模型和 thinking 模型（qwen3/deepseek-r1）
        
        thinking 模型的流式格式：
          - 思考阶段：delta.reasoning_content 有内容，delta.content 为空/None
          - 回答阶段：delta.content 有内容，delta.reasoning_content 为空/None
        
        Harven AI 不展示思考过程（show_thinking=False），直接等待 delta.content
        """
        total_chunks = 0
        content_chunks = 0
        thinking_chunks = 0
        for chunk in response:
            total_chunks += 1
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            
            # 普通内容（所有模型的最终回答都在这里）
            if delta.content:
                content_chunks += 1
                yield delta.content
            # thinking 模型的思考内容 —— 跳过不输出（show_thinking=False）
            elif hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                thinking_chunks += 1
                # 不 yield，静默跳过思考过程
        
        logger.debug(
            f"[ModelManager] 流结束: total={total_chunks}, content={content_chunks}, "
            f"thinking={thinking_chunks}, model={self.model}"
        )
        if total_chunks > 0 and content_chunks == 0:
            if thinking_chunks > 0:
                logger.warning(
                    f"[ModelManager] 模型只返回了思考内容 ({thinking_chunks} chunks) 但无最终回答！"
                    f"模型={self.model}，可能是 thinking 模式下回答被截断或超时"
                )
            else:
                logger.warning(f"[ModelManager] 模型返回 {total_chunks} 个 chunk 但全部无 content！模型={self.model}")
        elif total_chunks == 0:
            logger.warning(f"[ModelManager] 模型返回空流！模型={self.model}")
    
    def chat_with_context(self, system_prompt, conversation_history, user_message, 
                          stream=False, temperature=0.7):
        """
        带系统提示词和历史上下文的对话
        
        Args:
            system_prompt: 系统提示词
            conversation_history: [{"role": str, "content": str}]
            user_message: 当前用户消息
            stream: 是否流式
            temperature: 温度
        """
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_message})
        
        return self.chat(messages, stream=stream, temperature=temperature)


def create_from_config():
    """★ 2026-10-04 P1 修复：裸 ModelManager() 不读 config 默认模型 → 本地调用
    create(model='') 被 Ollama 拒绝 400「model is required」（手机远程 __system__
    单例 / 定时任务 / 看图分支三处实锤，22:00/22:04 日志）。
    统一从这里建实例：读 config.yaml 的 ollama.base_url / ollama.default_model，
    与 web_app.get_mm() 同源——设置页选过模型，这里就带得上。
    用户尚未选择模型时仍为空（诚实报错引导，不内定兜底——09-26 审计原则不变）。"""
    try:
        from src.utils.config import get_config
        cfg = get_config() or {}
        _ollama = cfg.get('ollama', {}) or {}
        base_url = _ollama.get('base_url', 'http://localhost:11434') or 'http://localhost:11434'
        model_name = _ollama.get('default_model', '') or ''
    except Exception as _e:
        logger.warning(f"[ModelManager] create_from_config 读配置失败（按缺省建）: {_e}")
        base_url, model_name = 'http://localhost:11434', ''
    return ModelManager(base_url, model_name)
