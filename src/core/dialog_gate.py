# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""对话优先闸门（★ #1320，2026-10-30 定版拍板「开发」三件套之一）

病根（2026-10-30 桃子事故实锤）：哈文启动 2 秒后图库打标线程（每 2 分钟一批 10 张、
批内 3 并发）+ 深识队列循环 + 存量重跑同时开跑，与用户对话挤兑同一条 Ollama 串行
队列（单模型请求排队），用户提问排在后台任务队尾，首响拖到分钟级。

机制：前台入口（engine.send / cloud_link._photo_answer）打时间戳标记「对话活跃」；
后台 GPU 任务循环（imgstore-tag-loop / vision-tasks / 存量重跑）每轮开头查闸门，
忙则整轮跳过。窗口 5 分钟自动过期，无需手动释放（流式生成器无法可靠 finally）。

纯内存零持久化；模型中立不涉任何模型名。
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_until = 0.0        # 对话活跃截止时间戳（单调刷新）
WINDOW_S = 300.0           # 活跃窗口：一次标记保 5 分钟（覆盖长回答流式）


def mark_active(window: float = WINDOW_S):
    """前台入口调用：标记对话活跃 window 秒（可重复刷新）。"""
    global _active_until
    with _lock:
        _active_until = time.time() + float(window)


def is_busy() -> bool:
    """后台循环调用：True=对话活跃中，本轮 GPU 任务应让路。"""
    with _lock:
        return time.time() < _active_until
