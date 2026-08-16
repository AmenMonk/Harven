# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
事件总线（Hooks 系统核心）
支持事件的注册、触发、异步执行
"""
import inspect
from typing import Callable, Any, Dict, List
import logging
logger = logging.getLogger(__name__)


# 核心事件类型定义
class Events:
    """系统事件常量"""

    # 对话生命周期
    CONVERSATION_START = "conversation.start"
    CONVERSATION_END = "conversation.end"

    # 消息事件
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"

    # 知识库事件
    KB_UPLOADED = "kb.uploaded"
    KB_SEARCHED = "kb.searched"

    # 任务事件
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_COMPLETED = "task.completed"

    # 系统事件
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"

    # 用户事件
    USER_PROFILE_UPDATED = "user.profile_updated"
    MEMORY_EXTRACTED = "memory.extracted"


class EventBus:
    """事件总线：支持同步/异步钩子注册与触发"""

    def __init__(self):
        # { event_type: [ (priority, handler, name) ] }
        self._handlers: Dict[str, List[tuple]] = {}
        self._sync_mode = True  # 默认同步执行

    def on(self, event_type: str, handler: Callable = None, *, priority: int = 50, name: str = None):
        """
        注册事件处理器（装饰器或普通调用）

        Args:
            event_type: 事件类型（使用 Events 常量）
            handler: 处理函数
            priority: 优先级，数字越小越先执行（0-100）
            name: 处理器名称（用于去重/调试）
        """
        if handler is None:
            # 用作装饰器
            def decorator(fn):
                self._register(event_type, fn, priority, name or fn.__name__)
                return fn
            return decorator

        self._register(event_type, handler, priority, name or handler.__name__)
        return handler

    def _register(self, event_type: str, handler: Callable, priority: int, name: str):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        # 去重：同名同事件只注册一次
        for i, (p, h, n) in enumerate(self._handlers[event_type]):
            if n == name:
                self._handlers[event_type][i] = (priority, handler, name)
                return
        self._handlers[event_type].append((priority, handler, name))
        self._handlers[event_type].sort(key=lambda x: x[0])  # 按优先级排序

    def off(self, event_type: str, handler: Callable = None, name: str = None):
        """取消注册"""
        if event_type not in self._handlers:
            return
        if handler is None and name is None:
            self._handlers[event_type] = []
            return
        self._handlers[event_type] = [
            (p, h, n) for (p, h, n) in self._handlers[event_type]
            if (handler is not None and h != handler) or (name is not None and n != name)
        ]

    def emit(self, event_type: str, *args, **kwargs) -> List[Any]:
        """
        触发事件（同步）
        Returns: 所有处理器的返回值列表
        """
        if event_type not in self._handlers:
            return []

        results = []
        for priority, handler, name in self._handlers[event_type]:
            try:
                if inspect.iscoroutinefunction(handler):
                    import asyncio
                    result = asyncio.get_event_loop().run_until_complete(
                        handler(*args, **kwargs)
                    )
                else:
                    result = handler(*args, **kwargs)
                results.append(result)
            except Exception as e:
                import traceback
                logger.error(f"[EventBus] 处理器 {name} 执行失败: {e}")
                traceback.print_exc()
        return results

    def emit_async(self, event_type: str, *args, **kwargs):
        """触发事件（异步，不阻塞）"""
        import threading
        def _run():
            self.emit(event_type, *args, **kwargs)
        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def list_handlers(self, event_type: str = None):
        """列出所有已注册的处理器"""
        if event_type:
            return [
                {"name": n, "priority": p, "event": event_type}
                for (p, _, n) in self._handlers.get(event_type, [])
            ]
        result = []
        for evt, handlers in self._handlers.items():
            for (p, _, n) in handlers:
                result.append({"name": n, "priority": p, "event": evt})
        return result


# 全局事件总线实例
_event_bus = None

def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
