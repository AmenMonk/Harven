"""槽位3: 理解引擎 — 无专属大脑，provider 三通道（v1.3 融合定版）。

★ 定版铁律（铁律 0）：理解走用户选的模型，本模块不硬编码任何偏好。
  ollama    = OllamaVlmEngine，本机 Ollama REST 适配（过渡兼容，模型管家时代退役）
  llama_cpp = LlamaCppVlmEngine，进程内直载 GGUF（主模型+mmproj，最终形态）
  api       = 外脑视觉 API（接线在 ② 节点：接哈文 multi_models 用户选择）

L3 只兜 L1/L2 搞不定的疑难图（分级入库制度）。
13.2 显存调度器: 请求级超时（默认 20s）→ 超时/失联自动降级，
调用方拿到 VlmDegraded 异常后回落 L1+L2 结果，管线永不为 VLM 卡死。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

from .. import config
from .base import BaseEngine, EngineResult


class VlmDegraded(Exception):
    """VLM 不可用/超时——调用方应降级使用 L1+L2 结果。"""


# ══ 通道1: Ollama REST 适配（过渡兼容通道）══════════════════════════

class OllamaVlmEngine(BaseEngine):
    name = "qwen3-vl"
    slot = "understand"

    @property
    def base_url(self) -> str:
        return config.ENGINES["understand"]["base_url"].rstrip("/")

    @property
    def model(self) -> str:
        return config.ENGINES["understand"]["model"]

    def load(self) -> None:
        """Ollama 常驻进程，无需进程内加载；健康检查即可。"""
        self.loaded = self.healthcheck()

    def healthcheck(self) -> bool:
        """GET /api/tags — Ollama 活着且模型在列。"""
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3) as r:
                models = [m.get("name", "") for m in json.load(r).get("models", [])]
                return any(m.startswith(self.model) or m.split(":")[0] == self.model
                           for m in models)
        except Exception:
            return False

    def _vram_guard(self):
        """★ 修④（2026-11-12）：Ollama 通道显存预检——llama_cpp 通道有 #1431 三梯队，
        本通道一直没有，实锤场景：桌面应用把 12GB 显存吃剩不足（11.9/12），Ollama 收到
        请求后想装载模型→等显存→无限挂→手机永远转圈。规则：
        · 目标模型已在驻留（/api/ps 有货）→ 直接放行（能跑）
        · 模型未驻留 + 可测得空闲显存 < 3GB → 不发请求，直接 VlmDegraded 带指引，
          宁可如实报错也不无限等。无 torch 测不了显存 → 放行（笔记本/无卡机不受影响）。"""
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/ps", timeout=3) as r:
                loaded = [m.get("name", "") for m in (json.load(r) or {}).get("models", [])]
            if any(m.startswith(self.model) or m.split(":")[0] == self.model for m in loaded):
                return   # 模型已驻留，能跑
        except Exception:
            return       # Ollama 没响应？后面请求自然会报错，不在这里误伤
        free = None
        try:
            import torch
            if torch.cuda.is_available():
                free, _t = torch.cuda.mem_get_info(0)
                free = free / 1024 ** 3
        except Exception:
            free = None
        if free is not None and free < 3.0:
            raise VlmDegraded(
                f"本地模型装不进显存（空闲仅 {free:.1f}GB，需 ≈3GB）：QQ/Edge/Office 等\n"
                "吃显存的应用把卡占满了。关掉几个再试，或在设置里把模型切成外脑（智谱等）")

    def infer(self, image_path: str, prompt: str = "", timeout_s: float | None = None,
              **kwargs) -> EngineResult:
        """图片+提示词 → VLM 描述。失败/超时抛 VlmDegraded（13.2 降级链）。"""
        t0 = self._now_ms()
        timeout_s = timeout_s or config.VLM_TIMEOUT_S
        self._vram_guard()   # ★ 修④：显存预检，装不进如实报错不无限等

        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        payload = json.dumps({
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": prompt or "请用中文简要描述这张图片里的内容。",
                "images": [b64],
            }],
            "stream": False,
            "keep_alive": config.CHAT_KEEP_ALIVE,   # ★ #1243 防闲置卸载冷加载
            "options": {"num_ctx": config.CHAT_NUM_CTX},   # ★ #1270 防 4096 默认上下文截断 400
        }).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                text = json.load(r)["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            # ★ #1270：400 的真实原因在响应体——旧版只透传「Bad Request」无法诊断
            detail = ''
            try:
                detail = e.read().decode('utf-8', 'replace')[:200]
            except Exception:
                pass
            raise VlmDegraded(f"VLM 降级: HTTP {e.code}: {detail or e.reason}") from e
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError, KeyError, ValueError) as e:
            # OSError 兜住裸 ConnectionResetError(如 WinError 10054)
            raise VlmDegraded(f"VLM 降级: {type(e).__name__}: {e}") from e

        if not text:
            raise VlmDegraded("VLM 返回空内容")

        elapsed = self._now_ms() - t0
        return EngineResult(
            source_layer="L3",
            confidence=0.9,            # VLM 自然语言无硬置信度，固定高位标注
            elapsed_ms=elapsed,
            data={"description": text, "model": self.model},
        )

    # ── 通用对话（#1237：理解槽对话通道，messages 原样转发）────

    def chat(self, messages: list[dict], stream: bool = False,
             timeout_s: float | None = None):
        """通用对话透传 Ollama /api/chat（纯文本多轮 / 可带图）。

        非流式 → 返回完整回答文本；流式 → 返回原始 response（逐行 NDJSON，调用方透传）。
        失败/超时抛 VlmDegraded（与 L3 降级链同一异常通道）。
        """
        if timeout_s is None:
            timeout_s = config.CHAT_STREAM_FIRST_S if stream else config.CHAT_TIMEOUT_S
        self._vram_guard()   # ★ 修④：显存预检，装不进如实报错不无限等
        payload = json.dumps({
            "model": self.model, "messages": messages, "stream": stream,
            "keep_alive": config.CHAT_KEEP_ALIVE,   # ★ #1243 防闲置卸载冷加载空流
            "options": {"num_ctx": config.CHAT_NUM_CTX},   # ★ #1270 防 4096 默认上下文截断 400
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=timeout_s)
        except urllib.error.HTTPError as e:
            # ★ #1270：透传 Ollama 400 响应体原文（上下文超限等原因可直读）
            detail = ''
            try:
                detail = e.read().decode('utf-8', 'replace')[:200]
            except Exception:
                pass
            raise VlmDegraded(f"对话降级: HTTP {e.code}: {detail or e.reason}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise VlmDegraded(f"对话降级: {type(e).__name__}: {e}") from e
        if stream:
            return resp
        try:
            with resp:
                text = json.load(resp)["message"]["content"].strip()
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise VlmDegraded(f"对话响应解析失败: {type(e).__name__}: {e}") from e
        if not text:
            raise VlmDegraded("对话返回空内容")
        return text

    def attach_image(self, messages: list[dict], image_path: str) -> list[dict]:
        """把图片以 base64 附加到最后一条 user 消息（Ollama images 字段）。"""
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        msgs = [dict(m) for m in messages]
        for m in reversed(msgs):
            if m.get("role") == "user":
                m["images"] = (m.get("images") or []) + [b64]
                return msgs
        msgs.append({"role": "user", "content": "", "images": [b64]})
        return msgs


# 兼容别名：旧管线/旧调用方写 VlmEngine 的地方无需改动
VlmEngine = OllamaVlmEngine


# ══ 通道2: llama-cpp 进程内直载 GGUF（最终形态）════════════════════
# ══ ★ #1431 显存守卫（2026-11-04 r48）════════════════════════════
# 开发者笔记本实锤：6GB 显存跑 qwen2.5:7b(Ollama ~4.8GB) + Qwen3VL(进程内 ~3GB) 必爆，
# 症状=聊天「连接中断，请重试」。三梯队自愈，用户零操作：
#   ① 余量不足 → 自动调 Ollama API keep_alive=0 卸载常驻模型（下次调用 Ollama
#      自动重载，无副作用）
#   ② 仍不足 → n_gpu_layers 按余量比例降层（其余 CPU，保底能跑）
#   ③ 加载仍炸 → 纯 CPU 再试一次；再炸 → 明确报错带指引，绝不静默崩

def _cuda_free_gb() -> float | None:
    """查 CUDA 空闲显存（GB）；无 NVIDIA 环境/无 torch → None（llama-cpp 自己回落 CPU）。"""
    try:
        import torch
        if torch.cuda.is_available():
            free, _total = torch.cuda.mem_get_info(0)
            return free / 1024 ** 3
    except Exception:
        pass
    return None


def _ollama_unload_all(base_url: str) -> int:
    """让 Ollama 立即卸载全部常驻模型（keep_alive=0）；返回卸载个数。失败返 0。"""
    n = 0
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/ps", timeout=3) as r:
            models = (json.load(r) or {}).get("models") or []
        for m in models:
            name = m.get("name")
            if not name:
                continue
            body = json.dumps({"model": name, "keep_alive": 0}).encode()
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/generate", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                urllib.request.urlopen(req, timeout=10).read()
                n += 1
            except Exception:
                pass
        if n:
            time.sleep(2)   # 给驱动一点显存回收时间
    except Exception:
        pass
    return n


def _heal_cuda_runtime() -> None:
    """★ #1449（2026-11-05 r50）CUDA 运行时自愈：cu124 wheel 不捆绑 CUDA 运行时
    DLL，ggml-cuda.dll 依赖缺失时静默回退 CPU（台式机 02:00 实锤：补拷
    cudart/cublas 三件套后 CUDA 后端立即激活，4070S 全层上 GPU）。
    有 torch 在旁就从 torch/lib 幂等补拷；失败静默（CPU 兜底能跑）。"""
    try:
        import shutil
        from pathlib import Path
        site = Path(sys.prefix) / "Lib" / "site-packages"
        ll = site / "llama_cpp" / "lib"
        tl = site / "torch" / "lib"
        if not (ll / "ggml-cuda.dll").is_file():
            return
        for name in ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"):
            dst, src = ll / name, tl / name
            if not dst.is_file() and src.is_file():
                shutil.copy2(src, dst)
    except Exception:
        pass


class LlamaCppVlmEngine(BaseEngine):
    """进程内直载 GGUF（主模型+mmproj），零外部服务依赖（v1.3 最终形态）。

    依赖: llama-cpp-python（需含 Qwen3VLChatHandler 的版本）+ 模型管家下载的
    GGUF 主模型与 mmproj 两个文件（config.ENGINES["understand"]["gguf"/"mmproj"]）。
    缺任一 → VlmDegraded 带安装指引（管线正常降级 L1+L2，不炸主流程）。
    """
    name = "llama-cpp-vlm"
    slot = "understand"

    def __init__(self):
        self._llm = None
        self._handler = None

    def _check_ready(self) -> None:
        """就绪检查：缺 wheel 或缺模型文件 → 带指引报错。"""
        cfg = config.ENGINES["understand"]
        try:
            import llama_cpp  # noqa: F401
        except ImportError as e:
            raise VlmDegraded(
                "视觉理解(llama_cpp通道)未就绪：哈文缺 llama-cpp-python 组件，"
                "请在哈文设置→视觉引擎 里安装，或先用外脑视觉模型") from e
        from pathlib import Path
        gguf, mmproj = cfg.get("gguf", ""), cfg.get("mmproj", "")
        if not gguf or not Path(gguf).exists():
            raise VlmDegraded(
                "视觉理解(llama_cpp通道)未就绪：未下载本地视觉模型，"
                "请到 设置→视觉引擎 下载安装（或设置 HARVENEYE_VLM_PROVIDER=ollama 走本机 Ollama）")
        if not mmproj or not Path(mmproj).exists():
            raise VlmDegraded(
                "视觉理解(llama_cpp通道)未就绪：缺视觉投影文件(mmproj)，"
                "请到 设置→视觉引擎 重新下载")

    def load(self) -> None:
        self._check_ready()
        _heal_cuda_runtime()   # ★ #1449 先补 CUDA 运行时 DLL 再 import llama_cpp
        from llama_cpp import Llama
        # Qwen3VLChatHandler 导入路径随版本漂移：新版在 llama_multimodal，旧版在 llama_chat_format
        Qwen3VLChatHandler = None
        try:
            from llama_cpp.llama_multimodal import Qwen3VLChatHandler  # type: ignore
        except ImportError:
            try:
                from llama_cpp.llama_chat_format import Qwen3VLChatHandler  # type: ignore
            except ImportError:
                pass
        if Qwen3VLChatHandler is None:
            raise VlmDegraded(
                "llama-cpp-python 版本不含 Qwen3VL 视觉支持（Qwen3VLChatHandler），"
                "请在 设置→视觉引擎 更新组件")
        cfg = config.ENGINES["understand"]
        self._handler = Qwen3VLChatHandler(clip_model_path=cfg["mmproj"])
        # ★ #1431 显存守卫三梯队：env 显式指定最高优先，自动调度兜底
        gpu_layers = int(os.environ.get("HARVENEYE_GPU_LAYERS", "-1"))
        need_gb = float(os.environ.get("HARVENEYE_VRAM_NEED_GB", "3.2"))
        free_gb = _cuda_free_gb()
        if gpu_layers == -1 and free_gb is not None and free_gb < need_gb:
            freed = _ollama_unload_all(cfg["base_url"])
            free_gb = _cuda_free_gb()
            if freed:
                print(f"[vlm] 显存不足，已自动卸载 Ollama 常驻模型 {freed} 个，现空闲 {free_gb:.1f}GB", flush=True)
            if free_gb is not None and free_gb < need_gb:
                # 梯队②：按余量比例降层（Qwen3-VL-4B ≈36 层，Q4 权重 ~2.7GB）
                ratio = max(0.0, min(1.0, (free_gb - 0.5) / need_gb))
                gpu_layers = int(round(36 * ratio))
                print(f"[vlm] 显存仍不足（{free_gb:.1f}GB/需{need_gb}GB），降层 GPU={gpu_layers}/36（其余 CPU）", flush=True)
        load_kwargs = dict(
            model_path=cfg["gguf"],
            chat_handler=self._handler,
            n_ctx=cfg.get("num_ctx", config.CHAT_NUM_CTX),
            verbose=False,
        )
        try:
            self._llm = Llama(n_gpu_layers=gpu_layers, **load_kwargs)
        except Exception as e:
            if gpu_layers != 0:
                # 梯队③：GPU/混合加载仍炸 → 纯 CPU 保底
                print(f"[vlm] GPU 加载失败({type(e).__name__})，改纯 CPU 加载", flush=True)
                self._llm = Llama(n_gpu_layers=0, **load_kwargs)
            else:
                raise VlmDegraded(
                    f"视觉模型加载失败（{type(e).__name__}）：显存/内存不足或权重损坏，"
                    "请关闭占用显存的程序后重试") from e
        self.loaded = True

    def infer(self, image_path: str, prompt: str = "", timeout_s: float | None = None,
              **kwargs) -> EngineResult:
        t0 = self._now_ms()
        self._check_ready()
        if not self.loaded:
            self.load()
        uri = self._image_to_uri(image_path)
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt or "请用中文简要描述这张图片里的内容。"},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}]
        try:
            out = self._llm.create_chat_completion(messages=messages, max_tokens=1024)
        except Exception as e:
            raise VlmDegraded(f"VLM 降级: {type(e).__name__}: {e}") from e
        text = (out.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            raise VlmDegraded("VLM 返回空内容")
        return EngineResult(
            source_layer="L3", confidence=0.9,
            elapsed_ms=self._now_ms() - t0,
            data={"description": text, "model": self.name},
        )

    def chat(self, messages: list[dict], stream: bool = False,
             timeout_s: float | None = None):
        """通用对话（进程内）。
        ★ #1446（2026-11-05 r50）：stream=True → 返回逐 token 生成器（打字机恢复，
        llama-cpp 原生 create_chat_completion(stream=True)，Qwen3VLChatHandler 兼容）。
        非 stream → 返回完整文本。失败抛 VlmDegraded。"""
        self._check_ready()
        if not self.loaded:
            self.load()
        if stream:
            return self._stream_chat(messages)
        try:
            out = self._llm.create_chat_completion(messages=messages)
        except Exception as e:
            raise VlmDegraded(f"对话降级: {type(e).__name__}: {e}") from e
        text = (out.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not text:
            raise VlmDegraded("对话返回空内容")
        return text

    def _stream_chat(self, messages: list[dict]):
        """逐 token 生成器（#1446）：yield 文本增量；中途异常统一转 VlmDegraded。
        注意：生成器体惰性执行，load() 已在 chat() 里提前完成，首 chunk 即有词。"""
        try:
            for chunk in self._llm.create_chat_completion(messages=messages, stream=True):
                piece = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                if piece:
                    yield piece
        except VlmDegraded:
            raise
        except Exception as e:
            raise VlmDegraded(f"对话降级: {type(e).__name__}: {e}") from e

    def attach_image(self, messages: list[dict], image_path: str) -> list[dict]:
        """把图片以 data URI 附到最后一条 user 消息（OpenAI 视觉格式）。"""
        uri = self._image_to_uri(image_path)
        msgs = [dict(m) for m in messages]
        for m in reversed(msgs):
            if m.get("role") == "user":
                content = m.get("content")
                part = {"type": "image_url", "image_url": {"url": uri}}
                if isinstance(content, str):
                    m["content"] = [{"type": "text", "text": content}, part]
                elif isinstance(content, list):
                    m["content"] = content + [part]
                return msgs
        msgs.append({"role": "user", "content": [
            {"type": "text", "text": ""}, {"type": "image_url", "image_url": {"url": uri}}]})
        return msgs

    @staticmethod
    def _image_to_uri(image_path: str) -> str:
        import base64
        import mimetypes
        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{b64}"

    def unload(self) -> None:
        """显存调度：释放进程内模型（13.2）。"""
        self._llm = None
        self._handler = None
        self.loaded = False


# ══ 通道3: api — 委托哈文模型体系（用户选的外脑视觉模型）════════════

class ApiVlmEngine(BaseEngine):
    """api 通道 — 经哈文 MultiModelManager 走用户选的视觉模型（v1.3 定版）。

    铁律 0：用谁由 vision_link 解析器决定（用户显式钉的视觉模型 >
    用户当前外脑且该外脑带 vision 能力位），本引擎绝不代选。
    messages 走 OpenAI 视觉格式（content 列表含 image_url dataURI），
    mm 侧 2026-09-24 起原生支持。
    """
    name = "api-vision"
    slot = "understand"

    def __init__(self, generate_fn=None):
        self._generate_fn = generate_fn   # 可注入；缺省Late-import哈文 mm

    @property
    def model(self) -> str:
        return config.ENGINES["understand"].get("api_model", "") or "(当前外脑)"

    def _call(self, messages: list[dict], timeout_s: float | None = None) -> str:
        if self._generate_fn is None:
            try:
                from src.core.multi_model_manager import get_multi_model_manager
                mm = get_multi_model_manager()
                _model = config.ENGINES["understand"].get("api_model") or None
                self._generate_fn = lambda msgs, stream=False: mm.generate_response(
                    msgs, stream=stream, model=_model)   # ★ #1265: model 显式参不换血全局
            except Exception as e:
                raise VlmDegraded(f"api 通道不可用: {type(e).__name__}: {e}") from e
        try:
            out = self._generate_fn(messages, stream=False)
        except Exception as e:
            raise VlmDegraded(f"api 降级: {type(e).__name__}: {e}") from e
        # mm 生成器统一 yield {'type': 'text'|'error'|'thinking'|'tool_calls', 'content':..}
        if hasattr(out, '__iter__') and not isinstance(out, (str, bytes)):
            text = ''
            for chunk in out:
                if not isinstance(chunk, dict):
                    text += str(chunk)
                    continue
                if chunk.get('type') == 'error':
                    raise VlmDegraded(f"api 降级: {chunk.get('content', '')}")
                if chunk.get('type') == 'text':
                    text += chunk.get('content', '') or ''
            return text.strip()
        return str(out).strip()

    def load(self) -> None:
        self.loaded = True           # 无进程内模型，每次调用即用

    def healthcheck(self) -> bool:
        return True                  # 真实可用性在调用时由 mm 如实报错（不预检不代选）

    def infer(self, image_path: str, prompt: str = "", timeout_s: float | None = None,
              **kwargs) -> EngineResult:
        t0 = self._now_ms()
        uri = LlamaCppVlmEngine._image_to_uri(image_path)
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt or "请用中文简要描述这张图片里的内容。"},
            {"type": "image_url", "image_url": {"url": uri}},
        ]}]
        text = self._call(messages, timeout_s)
        if not text:
            raise VlmDegraded("api 返回空内容")
        return EngineResult(
            source_layer="L3", confidence=0.9,
            elapsed_ms=self._now_ms() - t0,
            data={"description": text, "model": self.model},
        )

    def chat(self, messages: list[dict], stream: bool = False,
             timeout_s: float | None = None):
        if stream:
            raise VlmDegraded("api 通道流式由调用方直接走 mm.generate_response(stream=True)")
        return self._call(messages, timeout_s)

    def attach_image(self, messages: list[dict], image_path: str) -> list[dict]:
        return LlamaCppVlmEngine.attach_image(self, messages, image_path)


# ══ 工厂：按配置选通道（用户选谁用谁，铁律 0）══════════════════════

def build_understand_engine() -> BaseEngine:
    """理解槽工厂 — provider 决定通道；未知 provider 如实报错不兜底代选。"""
    provider = config.ENGINES["understand"].get("provider", "ollama")
    if provider == "ollama":
        return OllamaVlmEngine()
    if provider == "llama_cpp":
        return LlamaCppVlmEngine()
    if provider == "api":
        return ApiVlmEngine()
    raise VlmDegraded(f"未知理解通道 provider={provider!r}（可选 ollama/llama_cpp/api）")
