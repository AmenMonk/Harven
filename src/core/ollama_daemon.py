# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Ollama 进程守护 — 自动管理 Ollama 服务的生命周期

功能：
- 启动时自动检测 Ollama 是否运行
- 未运行时自动启动 Ollama serve
- 运行中自动检测健康状态
- 异常退出时自动重启（最多 3 次）
- 关闭 Harven AI 时优雅停止 Ollama
"""

import os
import sys
import time
import signal
import subprocess
import threading
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_RESTART_ATTEMPTS = 3
HEALTH_CHECK_INTERVAL = 30  # 秒
OLLAMA_DEFAULT_PORT = 11434


class OllamaDaemon:
    """Ollama 进程守护"""

    def __init__(self, ollama_path: str = None, port: int = None):
        self._ollama_path = ollama_path or self._find_ollama()
        self._port = port or OLLAMA_DEFAULT_PORT
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._restart_count = 0
        self._health_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @staticmethod
    def _find_ollama() -> Optional[str]:
        """查找 Ollama 可执行文件路径"""
        # 1. 检查 PATH
        for cmd in ["ollama", "ollama.exe"]:
            try:
                result = subprocess.run(
                    ["where", cmd] if sys.platform == "win32" else ["which", cmd],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split("\n")[0]
            except Exception as _e: logger.debug(f"[ollama_daemon] 忽略: {_e}")

        # 2. 检查常见路径
        common_paths = [
            os.path.expanduser("~/ollama/ollama"),
            os.path.expanduser("~/ollama/ollama.exe"),
            "C:\\Program Files\\Ollama\\ollama.exe",
            "C:\\Users\\" + os.environ.get("USERNAME", "") + "\\AppData\\Local\\Programs\\Ollama\\ollama.exe",
            "/usr/local/bin/ollama",
            "/usr/bin/ollama",
        ]
        for p in common_paths:
            if os.path.exists(p):
                return p

        return None

    def is_ollama_running(self) -> bool:
        """检测 Ollama 是否正在运行"""
        import requests
        try:
            resp = requests.get(f"http://127.0.0.1:{self._port}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def start(self) -> bool:
        """启动 Ollama 服务（如果未运行）"""
        with self._lock:
            if self.is_ollama_running():
                logger.info("[OllamaDaemon] Ollama 已在运行中")
                self._running = True
                return True

            if not self._ollama_path:
                logger.warning("[OllamaDaemon] 未找到 Ollama，跳过守护")
                return False

            logger.info(f"[OllamaDaemon] 启动 Ollama: {self._ollama_path}")
            try:
                # 构建环境变量（继承当前进程环境，追加 Ollama 相关变量）
                env = os.environ.copy()
                # 确保 PATH 包含 Ollama 所在目录（Windows 沙盒可能未继承）
                ollama_dir = os.path.dirname(self._ollama_path)
                if ollama_dir and ollama_dir not in env.get("PATH", ""):
                    env["PATH"] = ollama_dir + os.pathsep + env.get("PATH", "")

                if sys.platform == "win32":
                    self._process = subprocess.Popen(
                        [self._ollama_path, "serve"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        env=env,
                    )
                else:
                    self._process = subprocess.Popen(
                        [self._ollama_path, "serve"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                    )

                # 等待 Ollama 启动
                for _ in range(15):
                    time.sleep(1)
                    if self.is_ollama_running():
                        self._running = True
                        logger.info("[OllamaDaemon] Ollama 启动成功")
                        self._start_health_check()
                        return True

                logger.warning("[OllamaDaemon] Ollama 启动超时")
                return False
            except Exception as e:
                logger.error(f"[OllamaDaemon] 启动失败: {e}")
                return False

    def stop(self):
        """优雅停止 Ollama"""
        with self._lock:
            self._running = False
            if self._health_thread and self._health_thread.is_alive():
                self._health_thread.join(timeout=5)

            if self._process and self._process.poll() is None:
                logger.info("[OllamaDaemon] 正在停止 Ollama...")
                try:
                    if sys.platform == "win32":
                        self._process.terminate()
                    else:
                        self._process.send_signal(signal.SIGTERM)
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                except Exception as e:
                    logger.warning(f"[OllamaDaemon] 停止异常: {e}")

    def _start_health_check(self):
        """启动后台健康检查线程"""
        if self._health_thread and self._health_thread.is_alive():
            return

        def _check_loop():
            while self._running:
                time.sleep(HEALTH_CHECK_INTERVAL)
                if not self._running:
                    break
                if not self.is_ollama_running():
                    logger.warning("[OllamaDaemon] Ollama 进程异常退出，尝试重启...")
                    if self._restart_count < MAX_RESTART_ATTEMPTS:
                        self._restart_count += 1
                        if self.start():
                            logger.info(f"[OllamaDaemon] 重启成功 (第 {self._restart_count} 次)")
                        else:
                            logger.error(f"[OllamaDaemon] 重启失败 (第 {self._restart_count} 次)")
                    else:
                        logger.error(f"[OllamaDaemon] 已达最大重启次数 ({MAX_RESTART_ATTEMPTS})，停止守护")
                        self._running = False

        self._health_thread = threading.Thread(target=_check_loop, daemon=True)
        self._health_thread.start()

    def get_status(self) -> dict:
        """获取守护状态"""
        return {
            "ollama_running": self.is_ollama_running(),
            "daemon_active": self._running,
            "ollama_path": self._ollama_path,
            "restart_count": self._restart_count,
            "port": self._port,
        }


# 单例
_daemon: Optional[OllamaDaemon] = None


def get_ollama_daemon() -> OllamaDaemon:
    global _daemon
    if _daemon is None:
        _daemon = OllamaDaemon()
    return _daemon


def start_ollama_daemon():
    """启动时调用"""
    daemon = get_ollama_daemon()
    success = daemon.start()
    if success:
        logger.info(f"[OllamaDaemon] Ollama 守护已启动")
    else:
        logger.info(f"[OllamaDaemon] Ollama 守护未启动（Ollama 不可用）")
    return success


def stop_ollama_daemon():
    """关闭时调用"""
    global _daemon
    if _daemon:
        _daemon.stop()
        _daemon = None
