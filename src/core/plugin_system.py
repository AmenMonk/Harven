# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
插件沙盒框架
为灵识提供可扩展的功能插件系统
插件在独立命名空间中运行，无法直接访问核心数据
"""
import os
import importlib
import json
import traceback
import inspect
from datetime import datetime
from src.core.event_bus import get_event_bus, Events
import logging
logger = logging.getLogger(__name__)


class PluginResult:
    """插件执行结果"""

    def __init__(self, success=True, data=None, message="", error=None):
        self.success = success
        self.data = data or {}
        self.message = message
        self.error = error

    def to_dict(self):
        return {
            "success": self.success,
            "data": self.data,
            "message": self.message,
            "error": self.error,
        }


class PluginContext:
    """
    插件执行上下文（沙盒接口）
    插件只能通过此接口访问灵识功能，不能直接访问核心模块
    """

    def __init__(self, conversation_id, user_message, config=None):
        self.conversation_id = conversation_id
        self.user_message = user_message
        self._config = config or {}
        self._data = {}  # 插件临时数据

    def get_config(self, key, default=None):
        """获取配置"""
        return self._config.get(key, default)

    def set_temp_data(self, key, value):
        """设置临时数据（仅在本次调用中有效）"""
        self._data[key] = value

    def get_temp_data(self, key, default=None):
        """获取临时数据"""
        return self._data.get(key, default)

    def get_user_info(self):
        """获取主人信息（只读）"""
        try:
            from src.core.memory import get_memory_manager
            return get_memory_manager().get_master_info()
        except Exception:
            return {}

    def search_knowledge(self, query, top_k=3):
        """搜索知识库（只读）"""
        try:
            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            return kb.search(query, top_k=top_k)
        except Exception:
            return []

    def save_to_knowledge(self, text, source="plugin"):
        """向知识库添加知识"""
        try:
            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            return kb.add_document(text, source=source)
        except Exception:
            return None


class PluginBase:
    """插件基类"""

    # 插件元数据
    name = "unnamed_plugin"
    version = "1.0.0"
    description = ""
    author = ""

    def __init__(self):
        self.enabled = True

    def should_handle(self, message):
        """
        判断是否应该处理这条消息
        Returns: float 0.0-1.0，表示匹配度
        """
        return 0.0

    def execute(self, context):
        """
        执行插件逻辑
        Args:
            context: PluginContext
        Returns:
            PluginResult
        """
        return PluginResult(success=False, message="Plugin not implemented")

    def get_hooks(self):
        """
        返回插件注册的 Hooks 列表
        Returns:
            list of (event_type, handler, priority)
        """
        return []

    def on_enable(self):
        """插件启用时调用：自动注册 Hooks"""
        hooks = self.get_hooks()
        for event_type, handler, priority in hooks:
            get_event_bus().on(event_type, handler, priority=priority, name=f"{self.name}.{handler.__name__}")

    def on_disable(self):
        """插件禁用时调用：自动注销 Hooks"""
        hooks = self.get_hooks()
        for event_type, handler, priority in hooks:
            get_event_bus().off(event_type, handler=handler)
        pass


class PluginManager:
    """插件管理器"""

    def __init__(self):
        self._plugins = {}  # name -> PluginBase
        self._plugins_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "plugins"
        )

    def register(self, plugin):
        """注册插件"""
        if not isinstance(plugin, PluginBase):
            return False
        self._plugins[plugin.name] = plugin
        return True

    def unregister(self, name):
        """注销插件"""
        if name in self._plugins:
            self._plugins[name].on_disable()
            del self._plugins[name]

    def load_plugins(self):
        """从plugins目录加载插件"""
        if not os.path.exists(self._plugins_dir):
            os.makedirs(self._plugins_dir, exist_ok=True)
            return

        for filename in os.listdir(self._plugins_dir):
            if filename.endswith("_plugin.py"):
                try:
                    module_name = filename[:-3]
                    spec = importlib.util.spec_from_file_location(
                        f"plugins.{module_name}",
                        os.path.join(self._plugins_dir, filename)
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # 查找插件类
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (isinstance(attr, type) and issubclass(attr, PluginBase)
                                and attr is not PluginBase):
                            plugin = attr()
                            self.register(plugin)
                            logger.info(f"[PluginManager] Loaded plugin: {plugin.name} v{plugin.version}")
                except Exception as e:
                    logger.error(f"[PluginManager] Failed to load plugin {filename}: {e}")
                    traceback.print_exc()

    def get_matching_plugins(self, message):
        """获取匹配消息的插件（按匹配度排序）"""
        matches = []
        for name, plugin in self._plugins.items():
            if plugin.enabled:
                score = plugin.should_handle(message)
                if score > 0:
                    matches.append((score, plugin))
        matches.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in matches]

    def execute_plugin(self, plugin, context):
        """安全执行插件（沙盒）"""
        try:
            result = plugin.execute(context)
            if not isinstance(result, PluginResult):
                result = PluginResult(success=True, message=str(result))
            return result
        except Exception as e:
            return PluginResult(success=False, error=str(e), message=f"Plugin error: {e}")

    def list_plugins(self):
        """列出所有已注册插件"""
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "enabled": p.enabled,
            }
            for p in self._plugins.values()
        ]


# 全局单例
_plugin_manager = None


def get_plugin_manager():
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager()
    return _plugin_manager


# 尝试加载标准库中的 importlib.util
try:
    import importlib.util
except ImportError:
    importlib.util = None  # type: ignore
