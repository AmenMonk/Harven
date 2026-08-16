# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
定时提醒插件 — 设置/取消定时提醒
"""
import re
import threading
from src.core.plugin_system import PluginBase, PluginResult, PluginContext


class TimerPlugin(PluginBase):
    name = "timer"
    version = "1.0.0"
    description = "定时提醒：设置倒计时或定时任务"
    author = "Harven AI"

    _TIMER_PATTERNS = [
        (r"(?:提醒我|定时|倒计时|闹钟)[:：]?\s*(.+)", 0.9),
        (r"(\d+)\s*(?:分钟|秒|小时)\s*(?:后|之后)\s*(?:提醒|通知)", 0.95),
        (r"(?:取消提醒|取消定时|取消闹钟)", 0.95),
    ]

    def __init__(self):
        super().__init__()
        self._timers = {}  # timer_id -> threading.Timer

    def should_handle(self, message: str) -> float:
        for pattern, score in self._TIMER_PATTERNS:
            if re.search(pattern, message):
                return score
        return 0.0

    def execute(self, context: PluginContext) -> PluginResult:
        msg = context.user_message

        # 取消定时
        if any(kw in msg for kw in ["取消提醒", "取消定时", "取消闹钟"]):
            cancelled = 0
            for tid, timer in list(self._timers.items()):
                timer.cancel()
                cancelled += 1
            self._timers.clear()
            return PluginResult(
                success=True,
                message=f"✅ 已取消 {cancelled} 个定时提醒",
            )

        # 解析时间
        match = re.search(r"(\d+)\s*(分钟|秒|小时)\s*(?:后|之后)\s*(?:提醒|通知)(.+)", msg)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            note = match.group(3).strip() if match.group(3) else "时间到了！"

            seconds = value
            if unit == "分钟":
                seconds = value * 60
            elif unit == "小时":
                seconds = value * 3600

            timer_id = f"timer_{context.conversation_id}_{len(self._timers)}"
            timer = threading.Timer(seconds, self._on_timer, args=[timer_id, note, context])
            timer.daemon = True
            timer.start()
            self._timers[timer_id] = timer

            return PluginResult(
                success=True,
                message=f"⏰ 已设置提醒：{value}{unit}后提醒「{note}」",
                data={"timer_id": timer_id, "seconds": seconds, "note": note},
            )

        return PluginResult(success=False, message="无法解析定时参数，请使用格式：10分钟后提醒 开会")

    def _on_timer(self, timer_id: str, note: str, context: PluginContext):
        """定时器触发时的回调（存储到知识库以便下次查询）"""
        context.save_to_knowledge(
            f"⏰ 提醒：{note}",
            source=f"timer:{timer_id}"
        )
        self._timers.pop(timer_id, None)


plugin_class = TimerPlugin
