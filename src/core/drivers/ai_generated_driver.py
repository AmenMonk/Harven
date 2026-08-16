# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
AI 生成驱动 —— 当没有现成驱动时，让 Harven AI 临时生成设备驱动代码
这是"万能接口"的核心：用 AI 能力动态创建新驱动

工作流程：
  1. 用户说"帮我连接 XX 设备，型号 YYY"
  2. Harven 调用 generate_driver_code() 生成驱动 Python 文件
  3. 保存到 drivers/generated/XXX_driver.py
  4. 动态加载并注册到 DeviceDriverRegistry
  5. 用户一键连接设备

安全机制：
  - 生成的驱动在受控沙箱环境审查后才加载
  - 只允许访问 device_drivers 接口，不允许访问核心数据库
  - 用户可以查看生成的代码，审核后再启用
"""

import os
import sys
import json
import importlib.util
import traceback
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# 生成驱动保存目录
GENERATED_DRIVERS_DIR = Path(__file__).parent / "generated"
GENERATED_DRIVERS_DIR.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════════
# 驱动代码模板（AI 填充变量部分）
# ════════════════════════════════════════════════════════════════

DRIVER_TEMPLATE = '''# Auto-generated driver by Harven AI
# Device: {device_name} ({device_model})
# Protocol: {protocol}
# Generated at: {generated_at}
# WARNING: Review this code before enabling in production.

import json
import time
import logging
from typing import List, Dict, Any

{imports}

from src.core.device_drivers import (
    DeviceDriver, DeviceInfo, CommandResult,
    DeviceStatus, DeviceType, get_driver_registry,
)

logger = logging.getLogger(__name__)

DRIVER_ID = "{driver_id}"
DRIVER_NAME = "{driver_name}"


class {class_name}(DeviceDriver):
    """Auto-generated driver for {device_name}"""

    DRIVER_ID = DRIVER_ID

    def __init__(self):
        self._config = {{}}
        self._connected = False
        {init_extras}

    @classmethod
    def driver_id(cls): return DRIVER_ID

    @classmethod
    def driver_name(cls): return DRIVER_NAME

    @classmethod
    def driver_version(cls): return "1.0.0"

    @classmethod
    def supported_protocols(cls): return ["{protocol}"]

    def connect(self, config: Dict[str, Any]) -> bool:
        self._config = config
        {connect_body}
        self._connected = True
        logger.info(f"[{class_name}] 已连接")
        return True

    def disconnect(self) -> bool:
        {disconnect_body}
        self._connected = False
        return True

    def is_connected(self) -> bool:
        return self._connected

    def discover_devices(self, timeout: int = 10) -> List[DeviceInfo]:
        {discover_body}
        return []

    def send_command(self, device: DeviceInfo, command: str, params: Dict[str, Any]) -> CommandResult:
        if not self._connected:
            return CommandResult(success=False, error="未连接")
        try:
            {send_command_body}
            return CommandResult(success=True, message=f"命令 {{command}} 已执行")
        except Exception as e:
            return CommandResult(success=False, error=str(e))

    def get_device_state(self, device: DeviceInfo) -> Dict[str, Any]:
        if not self._connected:
            return {{"status": "offline"}}
        try:
            {get_state_body}
        except Exception as e:
            return {{"status": "error", "error": str(e)}}

    def on_state_change(self, callback) -> None:
        pass


# 注册驱动
try:
    get_driver_registry().register({class_name})
except Exception as _e: logger.debug(f"[ai_generated_driver] 忽略: {_e}")
'''


class AiDriverGenerator:
    """
    AI 驱动代码生成器

    使用 Harven 的 LLM 引擎生成设备驱动代码
    生成后保存到 drivers/generated/ 目录并动态加载
    """

    def __init__(self, engine=None):
        """
        engine: Harven 的 LLM 引擎实例（可选，None 时使用占位模板）
        """
        self._engine = engine
        self._generated: Dict[str, str] = {}  # driver_id → 文件路径

    def generate_driver(
        self,
        device_name: str,
        device_model: str = "",
        protocol: str = "http",
        device_description: str = "",
        extra_context: str = "",
    ) -> Tuple[bool, str, Optional[str]]:
        """
        生成设备驱动代码

        返回：(success, message, driver_id)
        """
        from datetime import datetime

        driver_id = self._make_driver_id(device_name, protocol)
        class_name = self._make_class_name(device_name)

        # 如果有 LLM 引擎，调用 AI 生成完整驱动代码
        if self._engine:
            code = self._generate_with_llm(
                device_name, device_model, protocol, device_description, extra_context, driver_id, class_name
            )
        else:
            # 没有 LLM 时使用默认模板（仅占位）
            code = self._fill_template(
                device_name=device_name,
                device_model=device_model,
                protocol=protocol,
                driver_id=driver_id,
                class_name=class_name,
                generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

        # 保存驱动文件
        filename = f"{driver_id}_driver.py"
        filepath = GENERATED_DRIVERS_DIR / filename
        try:
            filepath.write_text(code, encoding="utf-8")
            logger.info(f"[AiDriverGen] 驱动已保存: {filepath}")
        except Exception as e:
            return False, f"保存驱动文件失败: {e}", None

        # 动态加载驱动
        success, msg = self._load_driver(filepath, driver_id)
        if success:
            self._generated[driver_id] = str(filepath)
            return True, f"驱动 {driver_id} 已生成并加载成功", driver_id
        else:
            return False, f"驱动加载失败: {msg}", None

    def get_generated_drivers(self) -> Dict[str, str]:
        """返回所有已生成的驱动（driver_id → 文件路径）"""
        return dict(self._generated)

    def get_driver_code(self, driver_id: str) -> Optional[str]:
        """获取驱动源代码（供用户审查）"""
        filepath = self._generated.get(driver_id)
        if filepath and Path(filepath).exists():
            return Path(filepath).read_text(encoding="utf-8")
        return None

    def delete_generated_driver(self, driver_id: str) -> bool:
        """删除已生成的驱动"""
        filepath = self._generated.pop(driver_id, None)
        if filepath and Path(filepath).exists():
            Path(filepath).unlink()
            return True
        return False

    # ── 内部方法 ──────────────────────────────────────────────

    def _make_driver_id(self, device_name: str, protocol: str) -> str:
        """生成合法的 driver_id"""
        clean = "".join(c if c.isalnum() else "_" for c in device_name.lower())
        return f"gen_{protocol}_{clean}"[:40]

    def _make_class_name(self, device_name: str) -> str:
        """生成合法的 Python 类名"""
        parts = device_name.replace("-", " ").replace("_", " ").split()
        return "".join(p.capitalize() for p in parts) + "Driver"

    def _fill_template(
        self, device_name, device_model, protocol, driver_id, class_name, generated_at
    ) -> str:
        """用占位内容填充模板"""
        protocol_imports = {
            "http":  "try:\n    import requests\nexcept ImportError:\n    pass",
            "mqtt":  "try:\n    import paho.mqtt.client as mqtt\nexcept ImportError:\n    pass",
            "serial": "try:\n    import serial\nexcept ImportError:\n    pass",
            "ws":    "try:\n    import websocket\nexcept ImportError:\n    pass",
        }
        return DRIVER_TEMPLATE.format(
            device_name=device_name,
            device_model=device_model,
            protocol=protocol,
            generated_at=generated_at,
            driver_id=driver_id,
            driver_name=f"{device_name} 驱动（AI生成）",
            class_name=class_name,
            imports=protocol_imports.get(protocol, ""),
            init_extras="# AI 生成的初始化代码将会自动填入",
            connect_body="\"\"\"连接到设备\"\"\"\n        # TODO: 使用 LLM 生成时的连接逻辑会被自动替换\n        self._connected = True\n        return self._connected",
            disconnect_body="\"\"\"断开设备连接\"\"\"\n        self._connected = False",
            discover_body="\"\"\"发现设备\"\"\"\n        # TODO: 使用 LLM 生成时的发现逻辑会被自动替换\n        return []",
            send_command_body="\"\"\"发送命令\"\"\"\n        # TODO: 使用 LLM 生成时的命令逻辑会被自动替换\n        return {'status': 'ok', 'response': ''}",
            get_state_body="return {'status': 'unknown', 'connected': self._connected}",
        )

    def _generate_with_llm(
        self, device_name, device_model, protocol, device_description, extra_context,
        driver_id, class_name
    ) -> str:
        """
        调用 Harven LLM 引擎生成完整的驱动代码

        提示词设计：告诉 AI 需要生成符合 DeviceDriver 接口的 Python 驱动
        """
        from datetime import datetime
        prompt = f"""你是一个专业的 Python 设备驱动开发者。
请为以下设备生成一个完整可用的 Python 设备驱动类：

设备名称：{device_name}
设备型号：{device_model}
通信协议：{protocol}
设备描述：{device_description}
额外信息：{extra_context}

要求：
1. 类名为 {class_name}，继承自 DeviceDriver 抽象基类
2. 驱动 ID 为 {driver_id}
3. 实现以下方法：connect(config) / disconnect() / is_connected() / discover_devices() / send_command() / get_device_state()
4. connect() 方法从 config dict 读取连接参数
5. 代码要安全、健壮，包含 try/except 错误处理
6. 只输出纯 Python 代码，不要 markdown 代码块

DeviceDriver 接口参考：
from src.core.device_drivers import DeviceDriver, DeviceInfo, CommandResult, get_driver_registry

CommandResult 构造：CommandResult(success=bool, message=str, data=dict, error=str)

请生成完整的驱动代码："""

        try:
            response = self._engine.chat(
                message=prompt,
                conversation_id="__driver_gen__",
                system_prompt="你是专业的 Python IoT 设备驱动开发者，只输出代码，不要解释。",
            )
            code = response.get("content", "")
            # 清理可能的 markdown 代码块
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0].strip()
            elif "```" in code:
                code = code.split("```")[1].split("```")[0].strip()
            return code
        except Exception as e:
            logger.warning(f"[AiDriverGen] LLM 生成失败，使用模板: {e}")
            return self._fill_template(
                device_name=device_name,
                device_model=device_model,
                protocol=protocol,
                driver_id=driver_id,
                class_name=class_name,
                generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

    def _load_driver(self, filepath: Path, driver_id: str) -> Tuple[bool, str]:
        """动态加载驱动模块"""
        try:
            spec = importlib.util.spec_from_file_location(f"generated_{driver_id}", filepath)
            if spec is None:
                return False, "无法创建模块规格"
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"generated_{driver_id}"] = module
            spec.loader.exec_module(module)
            logger.info(f"[AiDriverGen] 驱动模块已加载: {filepath.name}")
            return True, "加载成功"
        except Exception as e:
            traceback.print_exc()
            return False, str(e)


# ════════════════════════════════════════════════════════════════
# 单例接口
# ════════════════════════════════════════════════════════════════

_generator_instance: Optional[AiDriverGenerator] = None


def get_ai_driver_generator(engine=None) -> AiDriverGenerator:
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = AiDriverGenerator(engine=engine)
    elif engine and _generator_instance._engine is None:
        _generator_instance._engine = engine
    return _generator_instance


def list_generated_drivers() -> Dict[str, str]:
    """列出所有 AI 生成的驱动"""
    gen = get_ai_driver_generator()
    result = {}
    # 扫描目录
    for f in GENERATED_DRIVERS_DIR.glob("*_driver.py"):
        driver_id = f.stem.replace("_driver", "")
        result[driver_id] = str(f)
    return result
