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
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config
from .base import BaseEngine, EngineResult

_log = logging.getLogger("vlm")


class VlmDegraded(Exception):
    """VLM 不可用/超时——调用方应降级使用 L1+L2 结果。"""


def _worker_log_path() -> Path:
    """视觉 worker 日志真实落点（data/models/logs/vision_worker.log）。"""
    return Path(config.MODELS_DIR).parent / "logs" / "vision_worker.log"


def _worker_log_hint() -> str:
    """报错话术里的日志位置（相对哈文根，人能找到的路径）。"""
    try:
        root = Path(config.PROJECT_ROOT)
        p = _worker_log_path()
        rel = p.relative_to(root)
        return f"（详情见 {rel.as_posix()}）" if p.exists() else f"（日志将写入 {rel.as_posix()}）"
    except Exception:
        return f"（详情见 {_worker_log_path()}）"


# ══ 通道1: Ollama REST 适配（过渡兼容通道）══════════════════════════

class OllamaVlmEngine(BaseEngine):
    name = "qwen3-vl"
    slot = "understand"

    def __init__(self, model_override: str | None = None):
        # ★ #1589（2026-11-27）：组合链按链里选的模型跑（同一份 GGUF 已注册 Ollama 时走本通道），
        #   不再被全局理解槽（用户钉选/自导线）偷换。None=跟全局理解槽（原行为）。
        self._model_override = model_override or None
        self._sig = model_override or ""

    @property
    def base_url(self) -> str:
        return config.ENGINES["understand"]["base_url"].rstrip("/")

    @property
    def model(self) -> str:
        return self._model_override or config.ENGINES["understand"]["model"]

    def runtime(self) -> str:
        return "ollama"

    def retirable_gb(self) -> float:
        return 0.0   # Ollama 自己管驻留，不算「可让位的进程内 worker」

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

    def _need_gb(self) -> float:
        """本模型装载显存估算（GB）：/api/show 权重体积 + 1.2GB 上下文/计算缓冲；查不到退 3GB 门槛。"""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/show", data=json.dumps({"model": self.model}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                size = (json.load(r) or {}).get("size") or 0
            if size:
                return max(3.0, size / 1024 ** 3 + 1.2)
        except Exception:
            pass
        return 3.0

    def _vram_guard(self):
        """★ 修④（2026-11-12）：Ollama 通道显存预检——llama_cpp 通道有 #1431 三梯队，
        本通道一直没有，实锤场景：桌面应用把 12GB 显存吃剩不足（11.9/12），Ollama 收到
        请求后想装载模型→等显存→无限挂→手机永远转圈。规则：
        · 目标模型已在驻留（/api/ps 有货）→ 直接放行（能跑）
        · ★ #1589 单载互斥：模型未驻留且空闲不够 → 先让进程内视觉 worker 让位
          （等在途推理收尾再退，同卡只留一份视觉大模型），让位后再量
        · 仍不够（按 /api/show 权重体积估算）→ 不发请求，直接 VlmDegraded 带指引，
          宁可如实报错也不无限等。无 torch 测不了显存 → 放行（笔记本/无卡机不受影响）。"""
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/ps", timeout=3) as r:
                loaded = [m.get("name", "") for m in (json.load(r) or {}).get("models", [])]
            if any(m.startswith(self.model) or m.split(":")[0] == self.model for m in loaded):
                return   # 模型已驻留，能跑
        except Exception:
            return       # Ollama 没响应？后面请求自然会报错，不在这里误伤
        free = _cuda_free_gb()
        if free is None:
            return
        need = self._need_gb()
        if free < need:
            retired = retire_inprocess_workers(
                f"Ollama 要装载 {self.model}（空闲 {free:.1f}GB / 需 ≈{need:.1f}GB，单载互斥让位）")
            if retired:
                free = _cuda_free_gb()
        if free < need:
            raise VlmDegraded(
                f"本地模型装不进显存（空闲仅 {free:.1f}GB，{self.model} 需 ≈{need:.1f}GB）："
                "QQ/Edge/Office 等吃显存的应用把卡占满了。关掉几个再试，"
                "或在设置里把模型切成外脑（智谱等）")

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

    def __init__(self, gguf_override: str | None = None, mmproj_override: str | None = None):
        # ★ #1584（2026-11-27）：推理本体已挪到独立 worker 子进程（src/vision/vision_worker.py），
        #   本类只做客户端。背景：进程内 llama_cpp 跑 qwen3-vl 期间与 Ollama 驻留模型争 12GB
        #   显存 → ggml-cuda 断言 abort → 哈文整进程秒死（无 traceback）。隔离后崩了只崩 worker。
        # ★ #1589：组合链按链里选的模型跑——override 指定 GGUF/mmproj，不读全局理解槽；
        #   都为空=跟全局理解槽（原行为）。
        self._gguf_override = gguf_override or ""
        self._mmproj_override = mmproj_override or ""
        self._sig = self._gguf_override
        self._proc = None          # worker 子进程句柄
        self._port = 0             # worker HTTP 端口（写进 port-file 后得知）
        self._worker_gguf = ""     # 当前 worker 装的模型（换模重建用）
        self._inflight = 0         # 在途推理数（单载互斥让位时等它归零）
        self._inflight_lock = threading.Lock()
        self._adopted_port = 0     # 收养的孤儿 worker 端口（还在加载中时先记着）
        self.notes: list[str] = []   # ★ #1589 worker 加载期自愈动作（卸 Ollama/降层/纯CPU），不静默

    def _cfg(self) -> dict:
        """本引擎生效的理解槽配置（全局配置 + 本实例 override 合并）。"""
        u = dict(config.ENGINES["understand"])
        if self._gguf_override:
            u["gguf"] = self._gguf_override
        if self._mmproj_override:
            u["mmproj"] = self._mmproj_override
        return u

    def runtime(self) -> str:
        return "llama_cpp"

    @property
    def model_label(self) -> str:
        return Path(self._cfg().get("gguf", "")).stem or self.name

    def retirable_gb(self) -> float:
        """活着的 worker 可让位显存估算（GB）= GGUF+mmproj 文件体积（偏保守的上界）。"""
        if not self._alive():
            return 0.0
        tot = 0.0
        for p in (self._cfg().get("gguf"), self._cfg().get("mmproj")):
            try:
                tot += Path(p).stat().st_size
            except Exception:
                pass
        return tot / 1024 ** 3

    def _alive(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        return self._port != 0 or self._adopted_port != 0   # 收养的孤儿 worker（主进程重启场景）

    def _check_ready(self) -> None:
        """就绪检查：缺 wheel 或缺模型文件 → 带指引报错。"""
        cfg = self._cfg()
        try:
            import llama_cpp  # noqa: F401
        except ImportError as e:
            raise VlmDegraded(
                "视觉理解(llama_cpp通道)未就绪：哈文缺 llama-cpp-python 组件，"
                "请在哈文设置→视觉引擎 里安装，或先用外脑视觉模型") from e
        gguf, mmproj = cfg.get("gguf", ""), cfg.get("mmproj", "")
        if not gguf or not Path(gguf).exists():
            raise VlmDegraded(
                "视觉理解(llama_cpp通道)未就绪：未下载本地视觉模型，"
                "请到 设置→视觉引擎 下载安装（或设置 HARVENEYE_VLM_PROVIDER=ollama 走本机 Ollama）")
        if not mmproj or not Path(mmproj).exists():
            raise VlmDegraded(
                "视觉理解(llama_cpp通道)未就绪：缺视觉投影文件(mmproj)，"
                "请到 设置→视觉引擎 重新下载")

    # ── ★ #1584 worker 客户端 ────────────────────────────────
    _WORKER_IDLE_EXIT = 900      # worker 空闲 15 分钟自动退出，下次调用重新拉起
    _LOAD_WAIT_S = 600           # 等模型加载就绪上限（8B 首载 ~70s，留足余量）

    def _worker_portfile(self):
        # ★ #1589：端口文件按模型分开（组合链与全局槽可能是两份不同模型，互不覆盖）
        import hashlib
        tag = hashlib.sha1(self._cfg().get("gguf", "").encode()).hexdigest()[:8]
        return Path(config.MODELS_DIR).parent / f"vision_worker_port_{tag}.json"

    def _try_adopt(self) -> bool:
        """★ #1589 收养同模型孤儿 worker：主进程重启/旧引擎已释放时，同 GGUF 的 worker
        可能还活着（空闲 15 分钟才自退）——再拉一个就是同模型双载（#1581 同款隐患）。
        /health 可达且 gguf 相同 → 复用其端口，不再 spawn。"""
        pf = self._worker_portfile()
        try:
            st = json.loads(pf.read_text(encoding="utf-8")) or {}
            port = int(st.get("port") or 0)
            if not port:
                return False
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                h = json.load(r) or {}
            if h.get("gguf") and h.get("gguf") == self._cfg().get("gguf") and not h.get("err"):
                if h.get("loaded"):
                    self._port = port
                else:
                    self._adopted_port = port      # 还在加载，等它
                self._worker_gguf = self._cfg().get("gguf")
                print(f"[vlm] #1589 收养同模型视觉 worker port={port}（不再双载）", flush=True)
                return True
        except Exception:
            pass
        return False

    def _spawn_worker(self):
        """拉起独立 worker 子进程（幂等：同模型已活则复用；能收养孤儿则收养）。"""
        import hashlib
        import subprocess
        cfg = self._cfg()
        gguf = cfg["gguf"]
        if self._proc is not None and self._proc.poll() is None and self._worker_gguf == gguf:
            return                      # 已活且同一模型，复用
        if not self._alive() and self._try_adopt():
            return
        # ★ #1589 单载互斥：同卡只留一份进程内视觉大模型——拉新 worker 前让其他 worker 让位
        retire_inprocess_workers(f"视觉 worker 要加载 {Path(gguf).name}（单载互斥让位）", keep_gguf=gguf)
        pf = self._worker_portfile()
        try:
            pf.unlink()                 # 旧端口文件清掉（换模型/旧 worker 已死场景）
        except Exception:
            pass
        script = Path(__file__).resolve().parents[1] / "vision_worker.py"
        root = Path(config.MODELS_DIR).parent.parent
        log_p = _worker_log_path()
        log_p.parent.mkdir(parents=True, exist_ok=True)
        # ★ #1589 日志统一 UTF-8：旧 GBK 日志改名封存（混编码日志没法读）
        try:
            if log_p.is_file() and log_p.stat().st_size > 0:
                try:
                    log_p.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    log_p.rename(log_p.with_suffix(".log.gbk.bak"))
        except Exception:
            pass
        gguf_tag = hashlib.sha1(gguf.encode()).hexdigest()[:8]
        logf = open(log_p, "ab")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            [sys.executable, "-u", str(script),
             "--gguf", gguf, "--mmproj", cfg["mmproj"],
             "--num-ctx", str(cfg.get("num_ctx", config.CHAT_NUM_CTX)),
             "--port-file", str(pf), "--idle-exit", str(self._WORKER_IDLE_EXIT)],
            stdout=logf, stderr=subprocess.STDOUT, cwd=str(root),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **kwargs)
        try:
            logf.close()               # 父进程句柄随手关（子进程已继承）
        except Exception:
            pass
        self._worker_gguf = gguf
        self._port = 0
        _register_live(self)
        print(f"[vlm] #1584 视觉 worker 已拉起 pid={self._proc.pid} 模型={Path(gguf).name} tag={gguf_tag}", flush=True)

    def _wait_worker_ready(self, wait_s: float | None = None) -> int:
        """等 worker 监听+模型加载就绪，返回端口。超时/失败 → VlmDegraded 人话。"""
        pf = self._worker_portfile()
        wait = wait_s or self._LOAD_WAIT_S
        deadline = time.time() + wait
        port = self._adopted_port or 0
        while time.time() < deadline and port == 0:
            if self._proc is not None and self._proc.poll() is not None:
                raise VlmDegraded(f"视觉 worker 进程启动即退出{_worker_log_hint()}")
            try:
                port = int((json.loads(pf.read_text(encoding="utf-8")) or {}).get("port") or 0)
            except Exception:
                port = 0
            if port:
                break
            time.sleep(0.5)
        if not port:
            raise VlmDegraded(f"视觉 worker {wait:.0f}s 内未就绪（首次加载本地视觉模型较慢，请稍后重试）")
        self._adopted_port = 0
        # 等模型真加载完（/health loaded=true；加载失败把 err 带出）
        notes_seen: set[str] = set()
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                    st = json.load(r)
                for n in (st.get("notes") or []):
                    if n not in notes_seen:
                        notes_seen.add(n)
                        self._absorb_note(n)
                if st.get("loaded"):
                    self._port = port
                    return port
                if st.get("err"):
                    raise VlmDegraded(f"视觉模型加载失败：{str(st['err'])[:160]}{_worker_log_hint()}")
            except VlmDegraded:
                raise
            except Exception:
                pass
            if self._proc is not None and self._proc.poll() is not None:
                raise VlmDegraded(f"视觉 worker 进程中途退出{_worker_log_hint()}")
            time.sleep(1.0)
        raise VlmDegraded("视觉模型加载超时，请稍后重试或到设置里改用外脑视觉模型")

    def _absorb_note(self, note) -> None:
        """★ #1589 worker 自愈动作进主进程：日志 WARNING + 引擎可见（不静默）。"""
        try:
            text = note if isinstance(note, str) else json.dumps(note, ensure_ascii=False)
        except Exception:
            text = str(note)
        if text not in self.notes:
            self.notes.append(text)
        print(f"[vlm] #1589 worker 自愈动作: {text}", flush=True)
        _log.warning(f"视觉 worker 自愈动作（{self.model_label}）: {text}")

    def _note_text(self) -> str:
        return "；".join(self.notes) if self.notes else ""

    def _post(self, path: str, payload: dict, timeout: float):
        """POST 到 worker；连接类失败 → 重拉一次再试一次（worker 可能闲置自退了）。"""
        import requests as _rq
        with self._inflight_lock:
            self._inflight += 1
        try:
            try:
                return _rq.post(f"http://127.0.0.1:{self._port}{path}", json=payload, timeout=timeout)
            except Exception:
                self._proc = None          # worker 已死，重建
                self._port = 0
            self._spawn_worker()
            self._wait_worker_ready()
            return _rq.post(f"http://127.0.0.1:{self._port}{path}", json=payload, timeout=timeout)
        finally:
            with self._inflight_lock:
                self._inflight -= 1

    def load(self) -> None:
        self._check_ready()
        self._spawn_worker()
        self._wait_worker_ready()
        self.loaded = True

    def infer(self, image_path: str, prompt: str = "", timeout_s: float | None = None,
              **kwargs) -> EngineResult:
        """★ #1584：经 worker 子进程推理（接口/返回不变）。"""
        t0 = self._now_ms()
        self._check_ready()
        if not self.loaded or self._port == 0:
            self.load()
        timeout = (timeout_s or 120) + 30
        uri = self._image_to_uri(image_path)
        r = self._post("/infer", {"image_b64": uri,
                                  "prompt": prompt or "请用中文简要描述这张图片里的内容。"},
                       timeout)
        if r.status_code != 200:
            try:
                detail = (r.json() or {}).get("error", "")
            except Exception:
                detail = r.text[:160]
            raise VlmDegraded(f"视觉模型处理失败：{detail or r.status_code}")
        text = (r.json() or {}).get("text", "").strip()
        if not text:
            raise VlmDegraded("VLM 返回空内容")
        return EngineResult(
            source_layer="L3", confidence=0.9,
            elapsed_ms=self._now_ms() - t0,
            data={"description": text, "model": self.model_label,
                  **({"note": self._note_text()} if self.notes else {})},
        )

    def chat(self, messages: list[dict], stream: bool = False,
             timeout_s: float | None = None):
        """通用对话（★ #1584 起经 worker 子进程；stream=True 返回逐 token 生成器）。"""
        self._check_ready()
        if not self.loaded or self._port == 0:
            self.load()
        if stream:
            return self._stream_chat(messages)
        timeout = (timeout_s or 120) + 30
        r = self._post("/chat", {"messages": messages, "stream": False}, timeout)
        if r.status_code != 200:
            try:
                detail = (r.json() or {}).get("error", "")
            except Exception:
                detail = r.text[:160]
            raise VlmDegraded(f"对话降级：{detail or r.status_code}")
        text = (r.json() or {}).get("text", "").strip()
        if not text:
            raise VlmDegraded("对话返回空内容")
        return text

    def _stream_chat(self, messages: list[dict]):
        """逐 token 生成器（#1446 接口不变）：worker NDJSON 逐行透传。"""
        with self._inflight_lock:
            self._inflight += 1
        try:
            yield from self._stream_chat_inner(messages)
        finally:
            with self._inflight_lock:
                self._inflight -= 1

    def _stream_chat_inner(self, messages: list[dict]):
        import json as _json
        import requests as _rq
        try:
            r = _rq.post(f"http://127.0.0.1:{self._port}/chat",
                         json={"messages": messages, "stream": True},
                         timeout=300, stream=True)
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    obj = _json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
                if obj.get("done"):
                    break
                piece = obj.get("t") or ""
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
        """★ #1584：worker 进程整体终结（模型随进程消失，显存即刻归还）。
        ★ #1589：收养的孤儿 worker（无进程句柄）走 /shutdown 端点请它自退。"""
        port = self._port or self._adopted_port
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            elif port:
                import requests as _rq
                _rq.post(f"http://127.0.0.1:{port}/shutdown", json={}, timeout=3)
        except Exception:
            pass
        self._proc = None
        self._port = 0
        self._adopted_port = 0
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

    def runtime(self) -> str:
        return "api"

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

def _ollama_model_for_same_gguf(base_url: str, gguf: str | None = None) -> str:
    """★ #1584 单载裁决：这份 GGUF 是否已注册进 Ollama（读同目录 .ollama_registered.json
    缓存 + /api/tags 在列校验）。在列返回 Ollama 模型名（同一份模型，非代选），否则空。
    ★ #1589：gguf 可显式指定（组合链选的模型），缺省=全局理解槽。"""
    gguf = gguf or config.ENGINES["understand"].get("gguf", "")
    if not gguf:
        return ""
    try:
        cache = Path(gguf).parent / ".ollama_registered.json"
        omodel = ((json.loads(cache.read_text(encoding="utf-8")) or {}).get("model") or "") \
            if cache.is_file() else ""
        if not omodel:
            return ""
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=2) as r:
            names = [m.get("name", "") for m in (json.load(r) or {}).get("models", [])]
        for n in names:
            if n == omodel or n.split(":")[0] == omodel:
                return omodel
    except Exception:
        pass
    return ""


def _ollama_base() -> str:
    return config.ENGINES["understand"].get("base_url", "http://127.0.0.1:11434")


def _build_understand_engine_uncached() -> BaseEngine:
    """理解槽工厂本体 — provider 决定通道；未知 provider 如实报错不兜底代选。

    ★ #1584 单载裁决：provider=llama_cpp 但同一份 GGUF 已注册进 Ollama 时，
    强制切 ollama 通道（同一模型非代选，铁律 0 合规）——杜绝「Ollama 驻留一份
    + 进程内 llama_cpp 再载一份」双载爆 12GB 显存（11-27 ggml abort 实锤）。"""
    provider = config.ENGINES["understand"].get("provider", "ollama")
    if provider == "ollama":
        return OllamaVlmEngine()
    if provider == "llama_cpp":
        omodel = _ollama_model_for_same_gguf(_ollama_base())
        if omodel:
            config.ENGINES["understand"]["provider"] = "ollama"
            config.ENGINES["understand"]["model"] = omodel
            print(f"[vlm] #1584 单载裁决: 同一模型已驻 Ollama（{omodel}），"
                  "理解槽切 ollama 通道，不进程内双载", flush=True)
            return OllamaVlmEngine()
        return LlamaCppVlmEngine()
    if provider == "api":
        return ApiVlmEngine()
    raise VlmDegraded(f"未知理解通道 provider={provider!r}（可选 ollama/llama_cpp/api）")


# ══ ★ #1589 进程内视觉 worker 活跃登记表（单载互斥）════════════════════
# 同卡同时只留一份进程内视觉大模型：谁要加载（新 worker / Ollama 新模型），
# 先让其他 worker 让位（等在途推理收尾再退，不砍正在跑的请求）。
_LIVE_CPP: list["LlamaCppVlmEngine"] = []
_LIVE_LOCK = threading.Lock()


def _register_live(eng: "LlamaCppVlmEngine") -> None:
    with _LIVE_LOCK:
        _LIVE_CPP[:] = [e for e in _LIVE_CPP if e is not eng and e._alive()]
        _LIVE_CPP.append(eng)


def _live_cpp_engines() -> list["LlamaCppVlmEngine"]:
    with _LIVE_LOCK:
        _LIVE_CPP[:] = [e for e in _LIVE_CPP if e._alive()]
        return list(_LIVE_CPP)


def retirable_gb() -> float:
    """活着的进程内视觉 worker 可让位显存估算合计（GB）——preflight 真实空闲口径用。"""
    return sum(e.retirable_gb() for e in _live_cpp_engines())


def retire_inprocess_workers(reason: str, keep_gguf: str | None = None,
                             wait_s: float = 120.0) -> list[str]:
    """★ #1589 单载互斥：让其他进程内视觉 worker 让位。

    · keep_gguf：要保留的那份（即将/正在加载的同款 worker 不动）
    · 先等在途推理收尾（最多 wait_s 秒），再终结 worker，显存即刻归还
    · 返回让位的模型文件名列表；全程进日志（不静默）"""
    victims = [e for e in _live_cpp_engines() if e._cfg().get("gguf") != keep_gguf]
    retired: list[str] = []
    for e in victims:
        deadline = time.time() + wait_s
        while time.time() < deadline:
            with e._inflight_lock:
                n = e._inflight
            if n <= 0:
                break
            time.sleep(0.5)
        name = Path(e._cfg().get("gguf", "")).name
        e.unload()
        retired.append(name)
        msg = f"单载互斥：视觉 worker「{name}」已让位退出（{reason}）"
        print(f"[vlm] #1589 {msg}", flush=True)
        _log.warning(msg)
    return retired


def build_understand_engine_for(gguf: str, mmproj: str) -> BaseEngine:
    """★ #1589：按指定 GGUF/mmproj 建视觉引擎（组合链选谁用谁，不看全局理解槽）。

    同款裁决与全局工厂一致（#1584）：这份 GGUF 已注册进 Ollama → 走 Ollama 通道
    （同一份模型，非代选）；否则进程内 worker 通道（独立子进程，独立显存账）。"""
    omodel = _ollama_model_for_same_gguf(_ollama_base(), gguf)
    if omodel:
        return OllamaVlmEngine(model_override=omodel)
    return LlamaCppVlmEngine(gguf_override=gguf, mmproj_override=mmproj)


# 全局理解槽引擎单例缓存：Pipeline 每次重建都调工厂，不缓存就会出现
# 「旧 Pipeline 的 worker 还活着 + 新 Pipeline 再拉一个同款 worker」同模型双载（#1589）。
_GLOBAL: dict = {"sig": None, "eng": None}
_G_LOCK = threading.Lock()


def _slot_signature() -> tuple:
    u = config.ENGINES["understand"]
    return (u.get("provider"), u.get("model"), u.get("gguf"), u.get("mmproj"), u.get("api_model"))


def build_understand_engine() -> BaseEngine:
    """理解槽工厂（全局单例缓存）— 同一配置只建一份引擎，换配置才换实例。

    换配置且旧引擎是进程内 worker：旧 worker 经 retire 让位（等在途推理收尾），
    同款 GGUF 的新引擎可直接收养复用（不双载）。"""
    eng = _build_understand_engine_uncached()
    sig = _slot_signature() + (type(eng).__name__, getattr(eng, "_sig", ""))
    with _G_LOCK:
        old = _GLOBAL.get("eng")
        old_sig = _GLOBAL.get("sig")
        if old is not None and old_sig == sig and old is not eng:
            if isinstance(eng, LlamaCppVlmEngine):
                eng.unload()      # 多余的同款新实例：让位（旧实例继续用）
            return old
        _GLOBAL["sig"], _GLOBAL["eng"] = sig, eng
    if old is not None and old is not eng and isinstance(old, LlamaCppVlmEngine):
        retire_inprocess_workers("理解槽换了模型", keep_gguf=eng._cfg().get("gguf")
                                 if isinstance(eng, LlamaCppVlmEngine) else None)
    return eng
