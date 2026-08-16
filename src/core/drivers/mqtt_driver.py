# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
MQTT 驱动 —— 第一个接入的设备协议
完全兼容 HomeAssistant MQTT Discovery 规范

协议说明：
- HomeAssistant 通过 MQTT Discovery 自动创建设备实体
- Topic 格式：homeassistant/{component}/{object_id}/config
- 设备状态 Topic：{device_id}/state
- 设备命令 Topic：{device_id}/set

依赖：
- paho-mqtt：MQTT 客户端（需安装：pip install paho-mqtt）
"""

import json
import logging
import time
import threading
from typing import List, Dict, Optional, Any
from dataclasses import asdict

from src.core.device_drivers import (
    DeviceDriver, DeviceInfo, CommandResult,
    DeviceStatus, DeviceType, Capability,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════
# HomeAssistant MQTT Discovery 组件类型映射
# ═══════════════════════════════════════════════════

HA_COMPONENT_MAP = {
    DeviceType.LIGHT: "light",
    DeviceType.SWITCH: "switch",
    DeviceType.SENSOR: "sensor",
    DeviceType.CLIMATE: "climate",
    DeviceType.MEDIA: "media_player",
    DeviceType.LOCK: "lock",
    DeviceType.CAMERA: "camera",
    DeviceType.COVER: "cover",
    DeviceType.VACUUM: "vacuum",
    DeviceType.APPLIANCE: "switch",   # 家电默认映射为 switch
    DeviceType.VEHICLE: "device_automation",
    DeviceType.INDUSTRIAL: "sensor",
    DeviceType.MEDICAL: "sensor",
    DeviceType.CUSTOM: "sensor",
}

# 标准命令 → MQTT payload 映射
COMMAND_PAYLOAD_MAP = {
    "turn_on": "ON",
    "turn_off": "OFF",
    "toggle": "TOGGLE",
    "set_brightness": lambda params: json.dumps({"brightness": params.get("brightness", 255)}),
    "set_color": lambda params: json.dumps({"color": params.get("color", "white")}),
    "set_temperature": lambda params: json.dumps({"temperature": params.get("temperature", 20)}),
    "set_volume": lambda params: json.dumps({"volume": params.get("volume", 50)}),
    "play": "play",
    "pause": "pause",
    "open_cover": "OPEN",
    "close_cover": "CLOSE",
    "set_cover_position": lambda params: json.dumps({"position": params.get("position", 50)}),
}


class MqttDriver(DeviceDriver):
    """
    MQTT 驱动（HomeAssistant 兼容）

    配置参数（configure 传入）：
    - host: MQTT Broker 地址（默认 localhost）
    - port: MQTT Broker 端口（默认 1883）
    - username: 用户名（可选）
    - password: 密码（可选）
    - discovery_prefix: HA Discovery 前缀（默认 homeassistant）
    - state_prefix: 设备状态前缀（默认 home）
    """

    driver_id = "mqtt"
    driver_name = "MQTT 驱动（HomeAssistant 兼容）"
    protocol = "mqtt"
    version = "1.0.0"
    description = "通过 MQTT 协议接入 HomeAssistant smart home 设备"
    supported_types = [
        DeviceType.LIGHT, DeviceType.SWITCH, DeviceType.SENSOR,
        DeviceType.CLIMATE, DeviceType.MEDIA, DeviceType.LOCK,
        DeviceType.COVER, DeviceType.VACUUM,
    ]
    supported_capabilities = [
        Capability.ON_OFF, Capability.BRIGHTNESS, Capability.COLOR,
        Capability.TEMPERATURE, Capability.OPEN_CLOSE,
        Capability.LOCK_UNLOCK, Capability.PLAY_PAUSE,
        Capability.VOLUME,
    ]

    def __init__(self):
        super().__init__()
        self._host: str = "localhost"
        self._port: int = 1883
        self._username: str = ""
        self._password: str = ""
        self._discovery_prefix: str = "homeassistant"
        self._state_prefix: str = "home"
        self._client = None
        self._discovered_devices: List[DeviceInfo] = []
        self._subscribed_topics: set = set()

    def configure(self, config: Dict[str, Any]) -> bool:
        """配置 MQTT 连接参数"""
        self._host = config.get("host", "localhost")
        self._port = int(config.get("port", 1883))
        self._username = config.get("username", "")
        self._password = config.get("password", "")
        self._discovery_prefix = config.get("discovery_prefix", "homeassistant")
        self._state_prefix = config.get("state_prefix", "home")
        self._config = config
        return True

    def connect(self) -> bool:
        """连接到 MQTT Broker"""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.debug("[MqttDriver] 缺少 paho-mqtt 库，请运行：pip install paho-mqtt")
            return False

        try:
            client_id = f"harven_ai_{int(time.time())}"
            self._client = mqtt.Client(client_id=client_id, clean_session=True)

            if self._username:
                self._client.username_pw_set(self._username, self._password)

            # 设置回调
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect

            self._client.connect(self._host, self._port, keepalive=60)
            self._client.loop_start()
            self._connected = True
            logger.info(f"[MqttDriver] 已连接到 MQTT Broker：{self._host}:{self._port}")
            return True
        except Exception as e:
            logger.error(f"[MqttDriver] 连接失败：{e}")
            return False

    def disconnect(self) -> bool:
        """断开 MQTT 连接"""
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception as e:
                logger.error(f"[MqttDriver] 断开连接失败：{e}")
        self._connected = False
        logger.debug("[MqttDriver] 已断开连接")
        return True

    def discover(self, timeout: int = 10) -> List[DeviceInfo]:
        """
        发现设备（通过 HomeAssistant MQTT Discovery）

        订阅 HA Discovery topic，等待设备上线
        同时也订阅 {state_prefix}/+/state 来发现手动配置的设备
        """
        if not self._connected or not self._client:
            logger.info("[MqttDriver] 未连接到 MQTT Broker，无法发现设备")
            return []

        self._discovered_devices = []

        # 订阅 HA Discovery topics
        discovery_topic = f"{self._discovery_prefix}/+/+/config"
        self._subscribe(discovery_topic)

        # 订阅设备状态 topics（通配符）
        state_topic = f"{self._state_prefix}/+/state"
        self._subscribe(state_topic)

        # 等待设备上线
        logger.debug(f"[MqttDriver] 正在扫描设备（{timeout} 秒）...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.2)

        logger.debug(f"[MqttDriver] 发现 {len(self._discovered_devices)} 个设备")
        return self._discovered_devices

    def send_command(self, device_id: str, command: str,
                     params: Dict[str, Any] = None) -> CommandResult:
        """
        向设备发送命令

        Args:
            device_id: 设备 ID（对应 MQTT topic 中的 device_id）
            command: 命令名
            params: 命令参数

        Returns:
            CommandResult
        """
        if not self._connected or not self._client:
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message="MQTT 未连接",
            )

        # 构造命令 topic
        command_topic = f"{self._state_prefix}/{device_id}/set"

        # 构造 payload
        payload = self._build_payload(command, params or {})

        try:
            result = self._client.publish(command_topic, payload=payload, qos=1)
            if result.rc == 0:
                # 等待确认
                result.wait_for_publish(timeout=5)
                return CommandResult(
                    success=True,
                    command=command,
                    device_id=device_id,
                    message=f"命令已发送：{command}",
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                )
            else:
                return CommandResult(
                    success=False,
                    command=command,
                    device_id=device_id,
                    message=f"发送失败，返回码：{result.rc}",
                )
        except Exception as e:
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"发送异常：{e}",
            )

    def get_state(self, device_id: str) -> Dict[str, Any]:
        """获取设备状态（从缓存）"""
        return self._state_cache.get(device_id, {})

    # ── 内部方法 ──

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT 连接回调"""
        if rc == 0:
            logger.debug(f"[MqttDriver] MQTT 连接成功")
        else:
            logger.error(f"[MqttDriver] MQTT 连接失败，返回码：{rc}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT 断开连接回调"""
        self._connected = False
        logger.debug(f"[MqttDriver] MQTT 已断开（返回码：{rc}）")
        # 自动重连
        if rc != 0:
            try:
                self._client.reconnect()
                self._connected = True
                logger.debug("[MqttDriver] MQTT 自动重连成功")
            except Exception as e:
                logger.error(f"[MqttDriver] 自动重连失败：{e}")

    def _on_message(self, client, userdata, msg):
        """MQTT 消息回调"""
        topic = msg.topic
        try:
            payload = msg.payload.decode("utf-8")
        except Exception:
            return

        # print(f"[MqttDriver] 收到消息：{topic} → {payload[:100]}")

        # 判断消息类型
        if "config" in topic:
            # HomeAssistant Discovery 消息
            self._handle_discovery_message(topic, payload)
        elif "state" in topic:
            # 设备状态消息
            self._handle_state_message(topic, payload)

    def _handle_discovery_message(self, topic: str, payload: str):
        """处理 HomeAssistant Discovery 配置消息"""
        try:
            config = json.loads(payload)
            parts = topic.split("/")
            if len(parts) >= 3:
                component = parts[1]
                object_id = parts[2]

                device_id = config.get("uniq_id", object_id)
                name = config.get("name", object_id)
                device_type = self._ha_component_to_type(component)

                # 提取能力
                capabilities = []
                if "brightness" in config:
                    capabilities.append(Capability.BRIGHTNESS)
                if "color_temp" in config or "rgb" in config or "hs" in config:
                    capabilities.append(Capability.COLOR)
                if "min_temp" in config or "max_temp" in config:
                    capabilities.append(Capability.TEMPERATURE)
                if capabilities == []:
                    capabilities.append(Capability.ON_OFF)

                dev = DeviceInfo(
                    device_id=device_id,
                    name=name,
                    device_type=device_type,
                    protocol="mqtt",
                    address=f"{self._state_prefix}/{device_id}",
                    driver_id="mqtt",
                    capabilities=capabilities,
                    status=DeviceStatus.ONLINE.value,
                    metadata={"ha_component": component, "ha_config": config},
                )
                # 避免重复
                if not any(d.device_id == device_id for d in self._discovered_devices):
                    self._discovered_devices.append(dev)
                    logger.debug(f"[MqttDriver] 发现设备：{name} ({device_id})")
        except Exception as e:
            logger.error(f"[MqttDriver] 处理 Discovery 消息失败：{e}")

    def _handle_state_message(self, topic: str, payload: str):
        """处理设备状态消息"""
        parts = topic.split("/")
        if len(parts) >= 3:
            device_id = parts[1]
            try:
                state = json.loads(payload)
            except Exception:
                state = {"state": payload}

            old_state = self._state_cache.get(device_id, {})
            self._state_cache[device_id] = state

            # 触发回调
            self._notify_state_change(device_id, old_state, state)

            # 如果是新设备（不在 discovered 列表中），自动注册
            if not any(d.device_id == device_id for d in self._discovered_devices):
                dev = DeviceInfo(
                    device_id=device_id,
                    name=device_id,
                    device_type=DeviceType.APPLIANCE.value,
                    protocol="mqtt",
                    address=f"{self._state_prefix}/{device_id}",
                    driver_id="mqtt",
                    capabilities=[Capability.ON_OFF],
                    status=DeviceStatus.ONLINE.value,
                    state=state,
                )
                self._discovered_devices.append(dev)
                logger.debug(f"[MqttDriver] 发现新设备（通过状态上报）：{device_id}")

    def _subscribe(self, topic: str):
        """订阅 Topic"""
        if topic in self._subscribed_topics:
            return
        try:
            result = self._client.subscribe(topic, qos=1)
            if result[0] == 0:
                self._subscribed_topics.add(topic)
                logger.debug(f"[MqttDriver] 已订阅：{topic}")
        except Exception as e:
            logger.error(f"[MqttDriver] 订阅失败 {topic}：{e}")

    def _build_payload(self, command: str, params: Dict) -> str:
        """构造命令 payload"""
        if command in COMMAND_PAYLOAD_MAP:
            entry = COMMAND_PAYLOAD_MAP[command]
            if callable(entry):
                return entry(params)
            else:
                return entry
        # 默认：JSON 格式
        return json.dumps({"command": command, **params}, ensure_ascii=False)

    def _ha_component_to_type(self, component: str) -> str:
        """HA 组件类型 → DeviceType"""
        reverse_map = {v: k for k, v in HA_COMPONENT_MAP.items()}
        return reverse_map.get(component, DeviceType.CUSTOM.value)
