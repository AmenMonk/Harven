# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
WebSocket 驱动 —— 实时双向通信设备协议
支持：无人驾驶遥测 / 机器人实时控制 / 自定义 WebSocket 设备

适用场景：
- 设备通过 WebSocket 提供实时遥测流
- 需要双向低延迟通信（如机器人控制）
- ROS2 Bridge（rosbridge_server）

配置参数：
    ws_url:     WebSocket 地址（必填），如 ws://192.168.1.100:9090
    token:      认证 Token（可选）
    protocol:   子协议（可选），如 "ros"
    ping_interval: 心跳间隔秒数（默认 20）
    reconnect:  是否自动重连（默认 true）
"""

import json
import time
import threading
import logging
from typing import List, Dict, Optional, Any, Callable

try:
    import websocket  # websocket-client
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

from src.core.device_drivers import (
    DeviceDriver, DeviceInfo, CommandResult,
    DeviceStatus, DeviceType, Capability,
    get_driver_registry,
)

logger = logging.getLogger(__name__)

DRIVER_ID = "websocket"
DRIVER_NAME = "WebSocket 驱动"
DRIVER_VERSION = "1.0.0"
DRIVER_PROTOCOLS = ["websocket", "ws", "wss", "ros2"]


class WebSocketDriver(DeviceDriver):
    """
    通用 WebSocket 驱动

    消息格式（JSON）：
      发送命令：{"type": "command", "device_id": "xxx", "command": "turn_on", "params": {}}
      接收状态：{"type": "state",   "device_id": "xxx", "state": {...}}
      接收事件：{"type": "event",   "device_id": "xxx", "event": "online/offline", "data": {}}

    ROS2 Bridge 兼容（rosbridge_server）：
      {"op": "publish", "topic": "/cmd_vel", "msg": {...}}
      {"op": "subscribe", "topic": "/odom"}
    """

    driver_id = DRIVER_ID
    driver_name = DRIVER_NAME
    protocol = "websocket"
    version = DRIVER_VERSION
    description = "通过 WebSocket 实时通信控制设备"
    supported_types = [DeviceType.VEHICLE, DeviceType.SENSOR, DeviceType.INDUSTRIAL, DeviceType.CUSTOM]
    supported_capabilities = [Capability.ON_OFF, Capability.MOTION, Capability.TEMPERATURE, Capability.HUMIDITY]

    def __init__(self):
        super().__init__()
        self._ws = None
        self._ws_thread: Optional[threading.Thread] = None
        self._reconnect = True
        self._reconnect_delay = 3
        self._state_callbacks: List[Callable] = []
        self._pending_commands: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def connect(self, config: Dict[str, Any]) -> bool:
        if not _WS_AVAILABLE:
            logger.error("[WsDriver] websocket-client 未安装，请执行: pip install websocket-client")
            return False

        ws_url = config.get("ws_url", "")
        if not ws_url:
            logger.error("[WsDriver] 缺少 ws_url 配置")
            return False

        self._config = config
        self._reconnect = config.get("reconnect", True)

        self._start_ws(ws_url)

        # 等待连接建立（最多 5 秒）
        for _ in range(50):
            if self._connected:
                return True
            time.sleep(0.1)

        logger.warning("[WsDriver] WebSocket 连接超时（5s），后台继续重试")
        return self._connected

    def disconnect(self) -> bool:
        self._reconnect = False
        if self._ws:
            try:
                self._ws.close()
            except Exception as _e: logger.debug(f"[websocket_driver] 忽略: {_e}")
        self._connected = False
        logger.info("[WsDriver] 已断开")
        return True

    def is_connected(self) -> bool:
        return self._connected

    def discover_devices(self, timeout: int = 10) -> List[DeviceInfo]:
        """WebSocket 驱动通过服务端推送发现设备，不支持主动扫描"""
        logger.info("[WsDriver] WebSocket 设备发现依赖服务端推送，请查看已接收的 state 消息")
        return []

    def send_command(
        self,
        device: DeviceInfo,
        command: str,
        params: Dict[str, Any],
    ) -> CommandResult:
        if not self._connected or not self._ws:
            return CommandResult(success=False, error="WebSocket 未连接")

        # 判断是否是 ROS2 Bridge 模式
        protocol = self._config.get("protocol", "")
        if protocol == "ros" or "ros" in self._config.get("ws_url", "").lower():
            # ROS2 Bridge 格式
            topic = params.pop("topic", f"/harven/{device.device_id}")
            msg = {"op": "publish", "topic": topic, "msg": {"command": command, **params}}
        else:
            # 通用格式
            msg = {
                "type": "command",
                "device_id": device.device_id,
                "command": command,
                "params": params,
            }

        try:
            self._ws.send(json.dumps(msg))
            return CommandResult(
                success=True,
                message=f"WebSocket 命令已发送: {command}",
                data=msg,
            )
        except Exception as e:
            logger.error(f"[WsDriver] 发送命令失败: {e}")
            return CommandResult(success=False, error=str(e))

    def get_device_state(self, device: DeviceInfo) -> Dict[str, Any]:
        return self._state_cache.get(device.device_id, {"status": "unknown"})

    def on_state_change(self, callback: Callable) -> None:
        self._state_callbacks.append(callback)

    # ── 内部方法 ──────────────────────────────────────────────

    def _start_ws(self, url: str):
        def on_open(ws):
            self._connected = True
            logger.info(f"[WsDriver] WebSocket 已连接: {url}")

            # 如果是 ROS2 Bridge，可以订阅 topic
            auto_subscribe = self._config.get("subscribe_topics", [])
            for topic in auto_subscribe:
                try:
                    ws.send(json.dumps({"op": "subscribe", "topic": topic}))
                except Exception as _e: logger.debug(f"[websocket_driver] 忽略: {_e}")

        def on_message(ws, message):
            try:
                data = json.loads(message)
                msg_type = data.get("type") or data.get("op", "")
                device_id = data.get("device_id", "")

                if msg_type in ("state", "publish"):
                    state = data.get("state") or data.get("msg", {})
                    if device_id:
                        old = self._state_cache.get(device_id)
                        self._state_cache[device_id] = state
                        if state != old:
                            for cb in self._state_callbacks:
                                try:
                                    cb(device_id, state)
                                except Exception as _e: logger.debug(f"[websocket_driver] 忽略: {_e}")
                elif msg_type == "event":
                    event = data.get("event", "")
                    logger.info(f"[WsDriver] 设备事件 {device_id}: {event}")
            except Exception as e:
                logger.warning(f"[WsDriver] 消息解析失败: {e}")

        def on_close(ws, code, msg):
            self._connected = False
            logger.warning(f"[WsDriver] WebSocket 断开 (code={code})")
            if self._reconnect:
                time.sleep(self._reconnect_delay)
                logger.info("[WsDriver] 正在重连...")
                self._start_ws(url)

        def on_error(ws, error):
            logger.error(f"[WsDriver] WebSocket 错误: {error}")

        token = self._config.get("token", "")
        headers = []
        if token:
            headers.append(f"Authorization: Bearer {token}")

        self._ws = websocket.WebSocketApp(
            url,
            header=headers or None,
            on_open=on_open,
            on_message=on_message,
            on_close=on_close,
            on_error=on_error,
        )

        self._ws_thread = threading.Thread(
            target=lambda: self._ws.run_forever(
                ping_interval=int(self._config.get("ping_interval", 20)),
                ping_timeout=10,
            ),
            daemon=True,
            name="WsDriver-Thread",
        )
        self._ws_thread.start()


# 注册驱动
try:
    get_driver_registry().register(WebSocketDriver)
    logger.info("[WsDriver] WebSocket 驱动已注册")
except Exception as _e:
    logger.warning(f"[WsDriver] 驱动注册跳过: {_e}")
