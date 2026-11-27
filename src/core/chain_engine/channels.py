# -*- coding: utf-8 -*-
"""通道适配层（骨架核心 · B2 定版施工）。

四种发动机各配一个统一插头，四接口 = 加载 / 推理 / 保活 / 释放；
驻留池只对 adapter 说话，不对具体引擎说话（B2 定版）。
迁移期原则「搬家不装修」：adapter 包装**现有引擎单例**，不重写推理。
"""
from __future__ import annotations

import threading
import time


class ChannelError(Exception):
    pass


class BaseAdapter:
    """统一四接口。channel 名即 registry 里的 channel 字段。"""
    channel = "base"

    def load(self, model_id: str):
        """加载并返回底层引擎句柄（实现方保证单例复用）。"""
        raise NotImplementedError

    def infer(self, handle, payload: dict) -> dict:
        raise NotImplementedError

    def keepalive(self, handle, seconds: int):
        """保活钩子（本地引擎常驻无需动作；云通道可预热线程）。"""

    def release(self, handle):
        """释放（底座/进程内引擎常驻不真释放，仅记账）。"""


# ══ internal：底座内部件（dhash/YOLOX/CLIP/SFace，进程内单例）═══
class InternalAdapter(BaseAdapter):
    channel = "internal"

    def load(self, model_id: str):
        import sys
        if r"H:\HarvenAI" not in sys.path:
            sys.path.insert(0, r"H:\HarvenAI")
        if r"H:\HarvenAI\src" not in sys.path:
            sys.path.insert(0, r"H:\HarvenAI\src")
        from src.vision.engines.yolox_engine import YoloxEngine
        from src.vision.engines.clip_engine import ClipEngine
        from src.vision.engines.face_engine import FaceEngine
        base = model_id.rsplit(".", 1)[-1]
        if base == "yolox":
            return _SingletonSlot("chain.yolox", YoloxEngine)
        if base == "clip":
            return _SingletonSlot("chain.clip", ClipEngine)
        if base == "mnv4":
            # 快速分类（2026-11-26）：MobileNetV4 Conv Small，timm 通道，毫秒级 Top-K
            from src.vision.engines.mnv4_engine import Mnv4Engine
            return _SingletonSlot("chain.mnv4", Mnv4Engine)
        if base == "locate":
            # 目标定位（2026-11-26 #1555）：LocateAnything-3B 独立服务 http 通道
            from src.vision.engines.locate_engine import LocateHttpEngine
            return _SingletonSlot("chain.locate", LocateHttpEngine)
        if base == "sface":
            if not FaceEngine.models_ready():
                raise ChannelError("SFace 模型未安装（设置页可下载）；人脸支线缺失不瘫主链")
            return _SingletonSlot("chain.face", FaceEngine)
        if base == "dhash":
            return _NoopSlot("chain.dhash")
        raise ChannelError(f"未知底座部件: {model_id}")

    def infer(self, handle, payload: dict) -> dict:
        return handle.infer(payload)


# ══ llama_cpp：进程内 GGUF（视觉目录条目，走 vlm 引擎工厂单例）══
class LlamaCppAdapter(BaseAdapter):
    channel = "llama_cpp"

    def load(self, model_id: str):
        import sys
        if r"H:\HarvenAI" not in sys.path:
            sys.path.insert(0, r"H:\HarvenAI")
        if r"H:\HarvenAI\src" not in sys.path:
            sys.path.insert(0, r"H:\HarvenAI\src")
        from src.vision.engines.vlm_engine import build_understand_engine
        # 迁移期：理解环工厂已实现「用户选谁用谁」三通道；组合环声明 llama_cpp
        # 时复用同一工厂单例（共享已加载权重，显存单例）。
        return _SingletonSlot("chain.vlm", build_understand_engine)

    def infer(self, handle, payload: dict) -> dict:
        return handle.infer(payload)


# ══ ollama：本地 REST ══════════════════════════════════════════
class OllamaAdapter(BaseAdapter):
    channel = "ollama"

    def load(self, model_id: str):
        name = model_id.split(":", 1)[1] if ":" in model_id else model_id
        try:
            import urllib.request, json as _json
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.5) as r:
                names = [m.get("name") for m in _json.loads(r.read()).get("models", [])]
            if not any(n == name or (n or "").startswith(name + ":") for n in names):
                raise ChannelError(f"Ollama 未安装模型 {name}（B10：缺模型如实报错，不偷换）")
        except ChannelError:
            raise
        except Exception as e:
            raise ChannelError(f"Ollama 不可达: {e}")
        return _OllamaSlot(name)

    def infer(self, handle, payload: dict) -> dict:
        return handle.infer(payload)


# ══ cloud：外脑 HTTP ═══════════════════════════════════════════
class CloudAdapter(BaseAdapter):
    channel = "cloud"

    def load(self, model_id: str):
        from ..security_guard import get_permission_mode  # noqa: F401  确认可导入
        return _CloudSlot(model_id)

    def infer(self, handle, payload: dict) -> dict:
        return handle.infer(payload)


# ══ 句柄实现（包装现有引擎，保证单例）══════════════════════════
class _SingletonSlot:
    """惰性单例槽：首个调用者创建，其后全部复用（显存单例共享）。"""
    def __init__(self, key: str, factory):
        self._key, self._factory = key, factory
        self._lock = threading.Lock()
        self._obj = None
        self._prompt_ok = None    # ★ #1568 引擎 infer 是否接受 prompt 参数（惰性探测，缓存）

    def _get(self):
        with self._lock:
            if self._obj is None:
                self._obj = self._factory()
            return self._obj

    def _accepts_prompt(self, obj) -> bool:
        """★ #1568：探测引擎 infer 签名是否带 prompt（VLM 理解引擎有；YOLOX/CLIP 无）。
        探测失败按 False 处理（维持原行为，绝不因新特性弄坏底座环）。"""
        if self._prompt_ok is None:
            try:
                import inspect
                self._prompt_ok = "prompt" in inspect.signature(obj.infer).parameters
            except Exception:
                self._prompt_ok = False
        return self._prompt_ok

    def infer(self, payload: dict) -> dict:
        obj = self._get()
        t0 = time.perf_counter()
        if hasattr(obj, "infer"):
            if getattr(obj, "accepts_payload", False):
                # 定位等需要 prompt/target 的引擎：传完整 payload
                r = obj.infer(payload)
            else:
                img = payload.get("image_path", "")
                prompt = str(payload.get("prompt", "") or "")
                # ★ #1568：prompt 非空且引擎吃得下 → 透传（组合环提示词进 VLM 理解引擎）；
                #   底座检测/索引引擎照旧只吃图（prompt 对它们本来就没意义）
                if prompt and self._accepts_prompt(obj):
                    r = obj.infer(img, prompt=prompt)
                else:
                    r = obj.infer(img)
        else:                                   # callable factory 分支
            r = obj().infer(payload.get("image_path", ""))
        return {"ok": True, "result": r, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}

    def raw(self):
        return self._get()


class _NoopSlot:
    """零开销程序部件（dhash 等纯函数在编排器里直调）。"""
    def __init__(self, key):
        self._key = key

    def infer(self, payload: dict) -> dict:
        return {"ok": True, "result": {}, "elapsed_ms": 0.0}

    def raw(self):
        return None


class _OllamaSlot:
    def __init__(self, name: str):
        self.name = name

    def infer(self, payload: dict) -> dict:
        import urllib.request, json as _json, time as _t
        body = _json.dumps({
            "model": self.name,
            "messages": [{"role": "user",
                          "content": payload.get("prompt", ""),
                          **({"images": [payload["image_b64"]]} if payload.get("image_b64") else {})}],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        t0 = _t.perf_counter()
        with urllib.request.urlopen(req, timeout=payload.get("timeout_s", 120)) as r:
            data = _json.loads(r.read().decode("utf-8"))
        return {"ok": True, "result": data.get("message", {}).get("content", ""),
                "elapsed_ms": round((_t.perf_counter() - t0) * 1000, 1)}

    def raw(self):
        return self.name


class _CloudSlot:
    def __init__(self, model_id: str):
        self.model_id = model_id

    def infer(self, payload: dict) -> dict:
        # 迁移期：云推理统一走现有外脑通道（multi_models current / camp vision），
        # 通道适配在此只做记账与超时包装；直连改造随账单链迁移做。
        import sys
        if r"H:\HarvenAI" not in sys.path:
            sys.path.insert(0, r"H:\HarvenAI")
        if r"H:\HarvenAI\src" not in sys.path:
            sys.path.insert(0, r"H:\HarvenAI\src")
        from core import model_manager as mm
        t0 = time.perf_counter()
        text = mm.chat_cloud(payload.get("prompt", "")) if hasattr(mm, "chat_cloud") else ""
        return {"ok": bool(text), "result": text,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}

    def raw(self):
        return self.model_id


# ══ 适配器注册表 ═══════════════════════════════════════════════
ADAPTERS: dict[str, BaseAdapter] = {
    "internal": InternalAdapter(),
    "llama_cpp": LlamaCppAdapter(),
    "ollama": OllamaAdapter(),
    "cloud": CloudAdapter(),
}


def get_adapter(channel: str) -> BaseAdapter:
    a = ADAPTERS.get(channel)
    if a is None:
        raise ChannelError(f"未登记的引擎通道: {channel}（B2：四种发动机各一插头）")
    return a
