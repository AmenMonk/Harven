# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
模拟设备驱动（Mock Driver）
用于无硬件环境下端到端验证 IoT 流程。

支持同时管理多个模拟设备（通过 device_id 区分）：
  - mock_light  : 智能灯（开关 + 亮度 + 色温）
  - mock_sensor  : 温度传感器（只读，自动上报）
  - mock_switch  : 智能开关（开关 + 功耗）

不需要真实 MQTT Broker，所有状态在内存中模拟。

用法（由 DeviceManager 调用）：
  driver = MockDriver()
  driver.configure({"device_id": "mock_light"})
  driver.connect()
  result = driver.send_command("mock_light", "power_on")
"""

import time
import threading
import random
import logging
from typing import Dict, Any, Optional, List

from src.core.device_drivers import (
    DeviceDriver, DeviceInfo, CommandResult,
    DeviceStatus, Capability,
    get_driver_registry,
)

logger = logging.getLogger(__name__)

# ── 模拟设备默认状态 ─────────────────────────────────────────────
_DEFAULT_STATES = {
    "mock_light":   {"power": False, "brightness": 80, "color_temp": 3700},
    "mock_sensor":  {"temperature": 24.5, "humidity": 60.0, "battery": 85},
    "mock_switch": {"power": False, "power_consumption": 0.0},
}

# 每个设备支持的能力
_DEVICE_CAPABILITIES = {
    "mock_light":  [Capability.ON_OFF, Capability.BRIGHTNESS, Capability.COLOR],
    "mock_sensor": [Capability.HUMIDITY, Capability.TEMPERATURE],
    "mock_switch": [Capability.ON_OFF, Capability.VOLUME],
}

# 每个设备支持的国际 commands（DeviceManager.send_command() 传入的标准命令）
_DEVICE_COMMANDS = {
    "mock_light": ["turn_on", "turn_off", "set_brightness", "set_color_temp"],
    "mock_sensor": ["get_readings"],
    "mock_switch": ["turn_on", "turn_off", "get_power"],
}


# ── ──────────────────────────────────────────────────────────────────
# MockDriver 类
# ── ──────────────────────────────────────────────────────────────────

class MockDriver(DeviceDriver):
    """
    模拟驱动 —— 不连接任何外部协议，纯内存模拟。
    支持同时管理多个模拟设备（每个 device_id 有独立状态）。
    """

    # 覆盖基类类属性（不要用 DRIVER_ 前缀！）
    driver_id   = "mock"
    driver_name = "模拟设备驱动（测试用）"
    version    = "1.0.0"
    protocol   = "mock"
    description = "纯内存模拟驱动，用于无硬件环境端到端验证"

    # ── 生命周期 ─────────────────────────────────────────────────────

    def __init__(self):
        """无参实例化（DriverRegistry.register() 会无参调用）"""
        super().__init__()
        self._connected_devices: Dict[str, bool] = {}   # device_id → connected
        self._states: Dict[str, Dict] = {}             # device_id → state dict
        self._running_flags: Dict[str, bool] = {}      # device_id → sensor thread running
        self._sensor_threads: Dict[str, threading.Thread] = {}
        # 从类属性复制实例属性（供 DeviceManager 访问）
        self.driver_name = self.__class__.driver_name

    def configure(self, config: Dict[str, Any]) -> bool:
        """保存配置（DeviceManager.connect_driver() 会先调用此方法）"""
        self._config = config or {}
        return True

    # ── 连接 / 断开 ──────────────────────────────────────────────

    def connect(self) -> bool:
        """
        模拟连接（无参，匹配基类签名）。
        从 self._config 中读取 device_id 连接特定设备；
        如果未指定 device_id，则自动连接所有 Mock 设备。
        """
        device_id = (self._config or {}).get("device_id", "").strip()
        
        # 如果指定了 device_id，只连接该设备
        if device_id:
            return self._connect_one(device_id)
        
        # 未指定 device_id → 连接所有默认 Mock 设备
        connected_any = False
        for dev_id in _DEFAULT_STATES:
            if self._connect_one(dev_id):
                connected_any = True
        return connected_any

    def _connect_one(self, device_id: str) -> bool:
        if not device_id or device_id not in _DEFAULT_STATES:
            logger.warning(f"[MockDriver] _connect_one() 无效 device_id={device_id}")
            return False

        # 初始化该设备的状态（如果尚未初始化）
        if device_id not in self._states:
            self._states[device_id] = dict(_DEFAULT_STATES[device_id])
            self._connected_devices[device_id] = True
            self._running_flags[device_id] = True

            # 传感器启动自动上报线程
            if device_id == "mock_sensor":
                t = threading.Thread(
                    target=self._sensor_loop, args=(device_id,), daemon=True
                )
                self._sensor_threads[device_id] = t
                t.start()

            logger.info(f"[MockDriver] {device_id} 已连接（模拟）")
        else:
            # 已初始化，标记为已连接
            self._connected_devices[device_id] = True
            self._running_flags[device_id] = True
            if device_id == "mock_sensor" and device_id not in self._sensor_threads:
                t = threading.Thread(
                    target=self._sensor_loop, args=(device_id,), daemon=True
                )
                self._sensor_threads[device_id] = t
                t.start()

        return True

    def disconnect(self) -> bool:
        """断开所有已连接的模拟设备"""
        for device_id in list(self._connected_devices.keys()):
            self._connected_devices[device_id] = False
            self._running_flags[device_id] = False
        # 等待传感器线程结束
        for device_id, t in list(self._sensor_threads.items()):
            if t.is_alive():
                t.join(timeout=2)
        self._sensor_threads.clear()
        logger.info("[MockDriver] 所有模拟设备已断开")
        return True

    def disconnect_device(self, device_id: str) -> bool:
        """断开指定设备（扩展方法，DeviceManager 可能调用）"""
        if device_id in self._connected_devices:
            self._connected_devices[device_id] = False
            self._running_flags[device_id] = False
            if device_id in self._sensor_threads:
                t = self._sensor_threads[device_id]
                if t.is_alive():
                    t.join(timeout=2)
                del self._sensor_threads[device_id]
            logger.info(f"[MockDriver] {device_id} 已断开（模拟）")
            return True
        return False

    def is_connected(self, device_id: str = None) -> bool:
        """检查指定设备是否已连接（若不指定 device_id，检查是否有任何设备已连接）"""
        if device_id:
            return self._connected_devices.get(device_id, False)
        return any(self._connected_devices.values())

    # ── 设备发现 ─────────────────────────────────────────────────

    def discover(self, timeout: int = 10) -> List[DeviceInfo]:
        """返回 3 个预定义模拟设备的 DeviceInfo"""
        return [
            DeviceInfo(
                device_id="mock_light",
                name="模拟智能灯",
                device_type="light",
                protocol="mock",
                address="mock://local/light",
                driver_id="mock",
                manufacturer="HarvenAI",
                model="MockLight v1",
                capabilities=["power_on", "power_off", "set_brightness", "set_color_temp"],
                room="测试房间",
                zone="客厅",
                metadata={"mock": True, "icon": "💡"},
                status=DeviceStatus.OFFLINE.value,
            ),
            DeviceInfo(
                device_id="mock_sensor",
                name="模拟温度传感器",
                device_type="sensor",
                protocol="mock",
                address="mock://local/sensor",
                driver_id="mock",
                manufacturer="HarvenAI",
                model="MockSensor v1",
                capabilities=["get_readings"],
                room="测试房间",
                zone="卧室",
                metadata={"mock": True, "icon": "🌡️"},
                status=DeviceStatus.OFFLINE.value,
            ),
            DeviceInfo(
                device_id="mock_switch",
                name="模拟智能开关",
                device_type="switch",
                protocol="mock",
                address="mock://local/switch",
                driver_id="mock",
                manufacturer="HarvenAI",
                model="MockSwitch v1",
                capabilities=["power_on", "power_off", "get_power"],
                room="测试房间",
                zone="书房",
                metadata={"mock": True, "icon": "🔌"},
                status=DeviceStatus.OFFLINE.value,
            ),
        ]

    def discover_devices(self, timeout: int = 10) -> List[DeviceInfo]:
        """兼容旧接口（返回 discover() 的结果）"""
        return self.discover(timeout=timeout)

    # ── 命令执行 ────────────────────────────────────────────────

    def send_command(self, device_id: str, command: str, params: Dict = None) -> CommandResult:
        """
        发送命令（匹配基类接口）
        
        Args:
            device_id: 设备ID
            command: 命令名（标准命令，如 turn_on/turn_off/set_brightness）
            params: 命令参数
        """
        if not self.is_connected(device_id):
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"设备未连接（模拟）: {device_id}"
            )

        if device_id not in self._states:
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"设备状态未初始化: {device_id}"
            )

        params = params or {}
        try:
            # 根据 device_id 和 command 路由到对应的处理方法
            if device_id == "mock_light":
                result = self._cmd_light(device_id, command, params)
            elif device_id == "mock_sensor":
                result = self._cmd_sensor(device_id, command, params)
            elif device_id == "mock_switch":
                result = self._cmd_switch(device_id, command, params)
            else:
                return CommandResult(
                    success=False,
                    command=command,
                    device_id=device_id,
                    message=f"未知模拟设备: {device_id}"
                )

            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            return CommandResult(
                success=True,
                command=command,
                device_id=device_id,
                message=f"命令 {command} 执行成功（模拟）state={result}",
                new_state=result,
                timestamp=ts,
            )
        except ValueError as e:
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"不支持的命令: {command} ({e})"
            )
        except Exception as e:
            logger.exception(f"[MockDriver] 命令执行异常: {e}")
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"命令执行异常: {e}"
            )

    def get_state(self, device_id: str) -> Dict:
        """
        获取设备状态（匹配基类接口）
        
        Args:
            device_id: 设备ID
        """
        if device_id not in self._states:
            return {}
        return dict(self._states[device_id])

    def get_capabilities(self, device_id: str = None) -> List[Capability]:
        """返回指定设备支持的能力枚举列表（扩展方法）"""
        if device_id and device_id in _DEVICE_CAPABILITIES:
            return _DEVICE_CAPABILITIES[device_id]
        # 若无指定设备，返回所有能力
        all_caps = set()
        for caps in _DEVICE_CAPABILITIES.values():
            all_caps.update(caps)
        return list(all_caps)

    # ── 设备特定命令处理 ──────────────────────────────────────────

    def _cmd_light(self, device_id: str, cmd: str, params: Dict) -> Dict:
        """处理灯光命令"""
        state = self._states[device_id]
        if cmd == "turn_on" or cmd == "power_on":
            state["power"] = True
        elif cmd == "turn_off" or cmd == "power_off":
            state["power"] = False
        elif cmd == "set_brightness":
            val = max(1, min(100, int(params.get("brightness") or params.get("value", 80))))
            state["brightness"] = val
        elif cmd == "set_color_temp":
            val = max(2000, min(6500, int(params.get("color_temp") or params.get("value", 3700))))
            state["color_temp"] = val
        else:
            raise ValueError(f"不支持的命令: {cmd}")
        return dict(state)

    def _cmd_sensor(self, device_id: str, cmd: str, params: Dict) -> Dict:
        """处理传感器命令"""
        state = self._states[device_id]
        if cmd == "get_readings":
            # 模拟数据微微变化
            state["temperature"] = round(state["temperature"] + 0.1, 1)
            state["humidity"] = round(state["humidity"] - 0.2, 1)
        else:
            raise ValueError(f"不支持的命令: {cmd}")
        return dict(state)

    def _cmd_switch(self, device_id: str, cmd: str, params: Dict) -> Dict:
        """处理开关命令"""
        state = self._states[device_id]
        if cmd == "turn_on" or cmd == "power_on":
            state["power"] = True
            state["power_consumption"] = 120.0
        elif cmd == "turn_off" or cmd == "power_off":
            state["power"] = False
            state["power_consumption"] = 0.0
        elif cmd == "get_power":
            pass  # 仅返回当前状态
        else:
            raise ValueError(f"不支持的命令: {cmd}")
        return dict(state)

    # ── 传感器自动上报 ─────────────────────────────────────────

    def _sensor_loop(self, device_id: str = "mock_sensor"):
        """模拟传感器每 10 秒自动上报数据"""
        while self._running_flags.get(device_id, False):
            time.sleep(10)
            if not self._running_flags.get(device_id, False):
                break
            state = self._states.get(device_id)
            if state:
                state["temperature"] = round(
                    state["temperature"] + random.uniform(-0.5, 0.5), 1
                )
                state["humidity"] = round(
                    state["humidity"] + random.uniform(-1.0, 1.0), 1
                )
                state["battery"] = max(0, state["battery"] - 1)
                logger.debug(f"[MockSensor] 自动上报: {state}")


# ── ──────────────────────────────────────────────────────────────────
# 注册到 DriverRegistry
# ── ──────────────────────────────────────────────────────────────────

def _register():
    reg = get_driver_registry()
    reg.register(MockDriver)
    logger.info("[MockDriver] 已注册到 DriverRegistry")

_register()

del _register


# ── ──────────────────────────────────────────────────────────────────
# 辅助函数（供 API 调用）
# ── ──────────────────────────────────────────────────────────────────

def get_mock_device_infos() -> List[DeviceInfo]:
    """返回 3 个模拟设备的 DeviceInfo（供 DeviceManager 注册用）"""
    driver = MockDriver()
    return driver.discover_devices()
