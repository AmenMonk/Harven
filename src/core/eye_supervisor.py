"""★ #1257（2026-10-28 定版拍板「一步到位」）HarvenEye 托管管家 —— 合并形态 B 方案。

═══ 设计铁律 ═══
- 两套代码库物理隔离不变：本模块对 Eye **只发 HTTP + 子进程拉起**，禁止 import 其引擎代码
  （AGPL 红线，eye_client 同款纪律）
- 桌面单一图标体验：哈文启动 → 自动拉起 Eye → 健康检查就绪；哈文退出 → 通知 Eye 关闭
- 绝不阻塞哈文主流程：Eye 拉不起来/超时/崩溃，哈文照常启动（eye_client 优雅降级兜底）

生命周期：
  start()        启动时调用：已在跑→直接用；没跑→子进程拉起→轮询 /status（最多 30s）
  stop()         退出时调用：POST /shutdown（Eye 侧延迟自退），失败则 terminate
  ensure_alive() 心跳：引擎进程死了自动重启一次（只一次，防重启风暴）
"""
from __future__ import annotations

import atexit
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = "http://127.0.0.1:7001"

# ★ #1260（A1）：Eye 子进程日志落盘（原先 DEVNULL——503 诊断时无据可查，实测实锤）
LOG_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "eye_sup.log"


def _log_fh():
    """Eye 子进程 stdout/stderr 落盘句柄。超 5MB 简单截断防膨胀；
    打不开文件降级 DEVNULL，绝不影响拉起主流程。"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
            LOG_FILE.write_bytes(b"")
        fh = open(LOG_FILE, "ab")
        fh.write(f"\n==== [EyeSup] {time.strftime('%Y-%m-%d %H:%M:%S')} 拉起 Eye ===\n".encode("utf-8"))
        return fh
    except Exception:
        return subprocess.DEVNULL


def _find_eye() -> tuple:
    """★ B2（#1260）：Eye 安装位置探测，不再写死 H 盘——新用户装到别的盘也能被托管拉起。
    优先级：环境变量 HARVENEYE_HOME > 哈文同盘同级标准路径 > 常见盘符标准路径。
    返回 (eye_home, eye_python)，找不到返回 (None, None)。"""
    cands = []
    env = os.environ.get("HARVENEYE_HOME", "").strip()
    if env:
        cands.append(Path(env))
    try:
        ai_root = Path(__file__).resolve().parent.parent.parent   # 哈文根（如 H:\HarvenAI）
        cands.append(ai_root.parent / "HarvenEye")                # 同盘同级（合并默认布局）
    except Exception:
        pass
    for drv in "CDEFGH":
        cands.append(Path(f"{drv}:" + os.sep + "HarvenEye"))
    for c in cands:
        py = c / ".venv" / "Scripts" / "python.exe"
        try:
            if py.exists():
                return c, py
        except Exception:
            continue
    return None, None

_proc: subprocess.Popen | None = None
_restarted = False            # 崩溃只自动重启一次
_lock = threading.Lock()
_stopped = False


def _alive(timeout: float = 2.0) -> bool:
    """Eye 是否活着（/status 探活）。"""
    try:
        import requests
        s = requests.Session()
        s.trust_env = False                     # 本机回环不走代理（eye_client 同款）
        r = s.get(BASE_URL + "/status", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def start(wait_s: float = 30.0) -> dict:
    """启动托管入口（哈文启动钩子调用，后台线程执行不阻塞）。"""
    threading.Thread(target=_start_bg, args=(wait_s,), daemon=True).start()
    return {"spawned": "bg"}


def _start_bg(wait_s: float) -> None:
    global _proc
    with _lock:
        if _alive():
            logger.info("[EyeSup] HarvenEye 已在运行，直接复用")
            return
        # ★ B2：探测安装位置（环境变量 > 同盘同级 > 常见盘符），找不到给明确引导不崩哈文
        eye_home, eye_python = _find_eye()
        if eye_home is None:
            logger.warning("[EyeSup] 未找到视觉引擎安装（已探测 HARVENEYE_HOME / 同盘同级 / C~H 盘标准路径）。"
                           "视觉深度功能降级；如需使用请安装视觉引擎或设置环境变量 HARVENEYE_HOME 指向其目录")
            return
        fh = _log_fh()
        try:
            # ★ #1257 实测（2026-10-28）：沙箱/Job 环境下子进程会随父进程被清——
            # 优先尝试 CREATE_BREAKAWAY_FROM_JOB 脱离父 Job；不允许 breakaway 时
            # 降级普通创建（生产桌面环境本就不随父死，两种都安全）
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
                _proc = subprocess.Popen(
                    [str(eye_python), "-m", "eye.api"],
                    cwd=str(eye_home), creationflags=flags,
                    stdout=fh, stderr=fh,
                )
            except Exception:
                _proc = subprocess.Popen(
                    [str(eye_python), "-m", "eye.api"],
                    cwd=str(eye_home),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    stdout=fh, stderr=fh,
                )
        except Exception as e:
            logger.warning(f"[EyeSup] Eye 拉起失败（不阻塞哈文）: {e}")
            return
        finally:
            try:
                if fh not in (subprocess.DEVNULL, None):
                    fh.close()   # 子进程已继承句柄，父进程副本即关（日志仍持续写入）
            except Exception:
                pass
        t0 = time.time()
        while time.time() - t0 < wait_s:
            if _alive():
                logger.info(f"[EyeSup] HarvenEye 就绪（等待 {time.time()-t0:.1f}s）")
                return
            if _proc.poll() is not None:
                logger.warning(f"[EyeSup] Eye 进程启动即退出（code={_proc.returncode}），视觉功能降级")
                return
            time.sleep(0.8)
        logger.warning(f"[EyeSup] Eye {wait_s:.0f}s 未就绪（可能仍在加载模型），不再等待")


def ensure_alive() -> None:
    """心跳（可在 /api/status 等低频路径顺带调用）：进程死了重启一次。"""
    global _restarted, _proc
    if _restarted or _stopped or _alive():
        return
    with _lock:
        if _restarted or _alive():
            return
        _restarted = True
    # ★ #1270（2026-10-29 死锁实锤）：_start_bg 内部也要抢 _lock（threading.Lock 不可重入），
    #   在锁内调它 = 自己等自己 → 心跳线程永久冻结、Eye 永远拉不起来（16:04 实测 60s 无复活）。
    #   必须先出锁再拉起——这可能正是 #1257~#1268 期间 ensure_alive 一直没人敢接线的暗雷。
    logger.warning("[EyeSup] HarvenEye 进程失联，自动重启一次（之后只降级不再重启）")
    _start_bg(wait_s=15.0)


def stop(timeout: float = 5.0) -> None:
    """哈文退出时调用：通知 Eye 优雅关闭，失败则硬停。"""
    global _stopped
    _stopped = True
    try:
        import requests
        s = requests.Session()
        s.trust_env = False
        s.post(BASE_URL + "/shutdown", timeout=2.0)
    except Exception:
        pass
    if _proc is not None:
        try:
            _proc.wait(timeout=timeout)
        except Exception:
            try:
                _proc.terminate()
            except Exception:
                pass
    logger.info("[EyeSup] HarvenEye 托管关闭完成")


def status_hint() -> str:
    """给托盘/设置页的一句话状态。"""
    return "✅ 视觉引擎已就绪" if _alive() else "⚠️ 视觉引擎未启动，视觉功能暂不可用（其余功能不受影响）"
