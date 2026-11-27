"""视觉推理独立 worker 进程（★ #1584 显存双载根治 · 崩溃隔离）。

背景（2026-11-27 ggml abort 实锤）：进程内 llama_cpp 跑 qwen3-vl 期间，Ollama
通道同时驻留同一/另一视觉模型 → 12GB 显存双载 → ggml-cuda 断言 abort →
哈文主进程整进程秒死（无 traceback，日志无声中断）。

本 worker 把 llama_cpp 视觉推理整个挪出主进程：
  · 模型加载 / CUDA 推理全在本进程，崩了只崩 worker，主进程毫发无伤
  · 主进程通过 localhost HTTP 调用（/infer /chat /health）
  · 空闲 idle_exit 秒自动退出（默认 15 分钟），下次调用由主进程重新拉起
  · 端口由系统分配（绑定 :0），写入 port-file 供主进程发现

启动方式（由 LlamaCppVlmEngine 客户端拉起，不手工跑）：
  python -u src/vision/vision_worker.py --gguf ... --mmproj ... \
      --num-ctx 16384 --port-file <path> --idle-exit 900
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path

# 自举：把 H:\HarvenAI 放进 sys.path（file = src/vision/vision_worker.py → parents[2]）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.vision.engines.vlm_engine import (  # noqa: E402
    _cuda_free_gb, _heal_cuda_runtime, _ollama_unload_all)

_INF_LOCK = threading.Lock()      # llama 推理非线程安全，全局串行
_STATE = {"llm": None, "loaded": False, "last_request": time.time(), "err": "",
           "gguf": "", "notes": [], "gpu_layers": None}


def _note(text: str) -> None:
    """★ #1589 自愈动作不静默：记进 _STATE，主进程经 /health 读走并写 WARNING 日志。"""
    if text not in _STATE["notes"]:
        _STATE["notes"].append(text)


def _load_model(gguf: str, mmproj: str, num_ctx: int) -> None:
    """与 LlamaCppVlmEngine.load 同源的加载逻辑（三梯队显存守卫复用）。"""
    _heal_cuda_runtime()
    from llama_cpp import Llama
    Qwen3VLChatHandler = None
    try:
        from llama_cpp.llama_multimodal import Qwen3VLChatHandler  # type: ignore
    except ImportError:
        try:
            from llama_cpp.llama_chat_format import Qwen3VLChatHandler  # type: ignore
        except ImportError:
            pass
    if Qwen3VLChatHandler is None:
        raise RuntimeError("llama-cpp-python 版本不含 Qwen3VLChatHandler，无法视觉推理")
    base_url = os.environ.get("HARVENEYE_OLLAMA", "http://127.0.0.1:11434")
    gpu_layers = int(os.environ.get("HARVENEYE_GPU_LAYERS", "-1"))
    need_gb = float(os.environ.get("HARVENEYE_VRAM_NEED_GB", "3.2"))
    free_gb = _cuda_free_gb()
    if gpu_layers == -1 and free_gb is not None and free_gb < need_gb:
        freed = _ollama_unload_all(base_url)
        free_gb = _cuda_free_gb()
        if freed:
            _note(f"显存不足（当时空闲不足 {need_gb}GB），已卸载 Ollama 常驻模型 {freed} 个"
                  f"（下次用 Ollama 会自动重载）")
            print(f"[vworker] 显存不足，已卸载 Ollama 常驻 {freed} 个，现空闲 {free_gb:.1f}GB", flush=True)
        if free_gb is not None and free_gb < need_gb:
            ratio = max(0.0, min(1.0, (free_gb - 0.5) / need_gb))
            gpu_layers = int(round(36 * ratio))
            _note(f"显存仍不足（空闲 {free_gb:.1f}GB/需 {need_gb}GB），部分层改用 CPU 跑"
                  f"（显卡层数 {gpu_layers}/36，速度会慢一些）")
            print(f"[vworker] 显存仍不足（{free_gb:.1f}GB/需{need_gb}GB），降层 GPU={gpu_layers}/36", flush=True)
    handler = Qwen3VLChatHandler(clip_model_path=mmproj)
    load_kwargs = dict(model_path=gguf, chat_handler=handler,
                       n_ctx=num_ctx, verbose=False)
    try:
        llm = Llama(n_gpu_layers=gpu_layers, **load_kwargs)
    except Exception as e:
        if gpu_layers != 0:
            # 梯队③：GPU/混合加载炸 → 纯 CPU 保底
            _note(f"显卡加载失败（{type(e).__name__}），已改用纯 CPU 加载（速度会明显变慢）")
            print(f"[vworker] GPU 加载失败({type(e).__name__})，改纯 CPU 加载", flush=True)
            llm = Llama(n_gpu_layers=0, **load_kwargs)
            gpu_layers = 0
        else:
            raise
    _STATE["llm"] = llm
    _STATE["loaded"] = True
    _STATE["gguf"] = gguf
    _STATE["gpu_layers"] = gpu_layers
    print(f"[vworker] READY 模型已加载 gguf={Path(gguf).name}", flush=True)


def _image_to_uri(image_b64: str) -> str:
    if image_b64.startswith("data:"):
        return image_b64
    return "data:image/jpeg;base64," + (image_b64 or "")


def _infer(payload: dict) -> dict:
    llm = _STATE["llm"]
    uri = _image_to_uri(payload.get("image_b64") or "")
    prompt = payload.get("prompt") or "请用中文简要描述这张图片里的内容。"
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": uri}},
    ]}]
    out = llm.create_chat_completion(messages=messages,
                                     max_tokens=int(payload.get("max_tokens") or 1024))
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    return {"text": text}


def _chat(payload: dict):
    llm = _STATE["llm"]
    messages = payload.get("messages") or []
    if payload.get("stream"):
        return llm.create_chat_completion(messages=messages, stream=True)
    out = llm.create_chat_completion(messages=messages)
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    return {"text": text}


# ══ HTTP 壳（http.server 零依赖）═══════════════════════════════
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):   # 静默默认访问日志（关键节点手动 print）
        pass

    def _send_json(self, obj: dict, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _STATE["last_request"] = time.time()
        if self.path.rstrip("/") in ("/health", "/health/"):
            self._send_json({"ok": True, "loaded": _STATE["loaded"], "pid": os.getpid(),
                             "err": _STATE.get("err", ""), "gguf": _STATE.get("gguf", ""),
                             "notes": list(_STATE.get("notes") or []),
                             "gpu_layers": _STATE.get("gpu_layers")})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        _STATE["last_request"] = time.time()
        if self.path.rstrip("/") == "/shutdown":
            # ★ #1589 单载互斥：主进程让本 worker 让位（收养的孤儿 worker 没有进程句柄，走这里）
            self._send_json({"ok": True, "bye": True})
            print("[vworker] 收到 /shutdown，让位退出", flush=True)
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception as e:
            self._send_json({"error": f"bad request: {e}"}, 400)
            return
        if not _STATE["loaded"]:
            self._send_json({"error": f"worker 模型未就绪: {_STATE.get('err') or '加载中'}"}, 503)
            return
        try:
            with _INF_LOCK:                      # 推理串行（llama 非线程安全）
                if self.path.rstrip("/") == "/infer":
                    self._send_json(_infer(payload))
                elif self.path.rstrip("/") == "/chat":
                    if payload.get("stream"):
                        # 流式：Connection: close + 逐行 JSON，客户端读到 EOF
                        self.send_response(200)
                        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        for chunk in _chat(payload):
                            piece = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                            if piece:
                                self.wfile.write(json.dumps({"t": piece}, ensure_ascii=False).encode("utf-8") + b"\n")
                                self.wfile.flush()
                        self.wfile.write(b'{"done": true}\n')
                        self.wfile.flush()
                        self.close_connection = True
                    else:
                        self._send_json(_chat(payload))
                else:
                    self._send_json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass                                  # 客户端提前断开（超时/取消），正常
        except Exception as e:
            try:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass


def _idle_watchdog(idle_exit: float):
    while True:
        time.sleep(10)
        if time.time() - _STATE["last_request"] > idle_exit:
            print(f"[vworker] 空闲 {idle_exit:.0f}s，自动退出（主进程下次调用会重新拉起）", flush=True)
            os._exit(0)


def main():
    # ★ #1589 日志统一 UTF-8（主进程拉起时也设 PYTHONIOENCODING；这里双保险）
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--mmproj", required=True)
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--port-file", required=True)
    ap.add_argument("--idle-exit", type=float, default=900)
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    port = srv.server_address[1]
    pf = Path(args.port_file)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps({"port": port, "pid": os.getpid()}, ensure_ascii=False),
                  encoding="utf-8")
    print(f"[vworker] LISTEN port={port} pid={os.getpid()}", flush=True)

    threading.Thread(target=_idle_watchdog, args=(args.idle_exit,), daemon=True).start()

    def _load_bg():
        try:
            _load_model(args.gguf, args.mmproj, args.num_ctx)
        except Exception as e:
            _STATE["err"] = f"{type(e).__name__}: {e}"
            print(f"[vworker] LOAD_FAILED {_STATE['err']}", flush=True)

    threading.Thread(target=_load_bg, daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
