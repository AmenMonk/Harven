# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
设备驱动包 — 子目录驱动实现

本包包含所有具体驱动实现：
  - mqtt_driver.py     : MQTT/HomeAssistant 驱动
  - http_driver.py      : HTTP REST 驱动
  - websocket_driver.py : WebSocket 驱动
  - ai_generated_driver.py : AI 自动生成驱动
  - mock_driver.py      : 模拟驱动（测试用）

驱动通过 register_driver_class() 注册到 device_drivers 的全局注册表。
首次导入本包时会自动导入所有驱动模块，完成注册。
"""

import os
import logging

logger = logging.getLogger(__name__)

# 自动导入所有驱动模块，触发 register_driver_class()
_driver_modules = [
    "mqtt_driver",
    "http_driver",
    "websocket_driver",
    "ai_generated_driver",
    "mock_driver",
]

for _mod in _driver_modules:
    _fp = os.path.join(os.path.dirname(__file__), f"{_mod}.py")
    if os.path.exists(_fp):
        try:
            import importlib
            importlib.import_module(f"src.core.drivers.{_mod}")
            logger.debug(f"[drivers] 已加载驱动模块: {_mod}")
        except Exception:
            logger.warning(f"[drivers] 加载驱动模块失败: {_mod}", exc_info=True)

logger.info("[drivers] 驱动包初始化完成")
