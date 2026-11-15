# -*- coding: utf-8 -*-
"""
sync_bus.py — 数据版本总线（#1220-A）
=====================================
解决「电脑端写库了，手机端/SPA 不知道」的跨端感知问题。

设计：
- 进程内内存计数器（线程安全），任何写类操作 bump 一次
- /api/sync/version GET 返回当前版本号 + 时间戳
- 前端（SPA / 手机页）定时轮询，版本号变化 → 刷新面板数据
- 不持久化：重启后从 1 重新计数，前端以「数值变化」为信号而非单调递增，
  重启后首轮轮询必触发一次刷新（等效全量同步，无害）

用法：
    from src.core.sync_bus import bump, get_version
    bump('panel')        # 面板数据变了
    bump('reminder')     # 提醒数据变了
    get_version()        # {'version': 42, 'updated_at': '...', 'reasons': ['panel', ...]}
"""
import threading
from datetime import datetime

_lock = threading.Lock()
_version = 0
_reasons = []          # 最近触发原因（最多保留 10 条，供调试）
_last_bump_at = None


def bump(reason: str = 'unknown') -> int:
    """版本号 +1。reason 仅用于诊断展示。"""
    global _version, _last_bump_at
    with _lock:
        _version += 1
        _last_bump_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if reason not in _reasons:
            _reasons.append(reason)
        if len(_reasons) > 10:
            _reasons.pop(0)
        return _version


def get_version() -> dict:
    """当前版本快照。前端以 version 数值变化为刷新信号。"""
    with _lock:
        return {
            'version': _version,
            'updated_at': _last_bump_at,
            'reasons': list(_reasons),
        }
