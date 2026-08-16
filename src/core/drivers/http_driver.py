# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
HTTP REST 驱动 —— 通用 HTTP/REST 设备协议
支持：普通 IoT 设备 / 医疗设备 / 特斯拉 API / 任意 REST API 设备

适用场景：
- 设备提供 HTTP REST 接口
- 发送 GET/POST/PUT/DELETE 控制设备
- 轮询 GET 获取设备状态

配置参数：
    base_url:   设备接口根地址（必填），如 http://192.168.1.100/api
    token:      认证 Token（可选），放在 Authorization: Bearer 头
    headers:    额外请求头（可选），dict
    timeout:    请求超时秒数（默认 10）
    poll_interval: 状态轮询间隔秒数（默认 30，0=不轮询）
"""

import json
import time
import threading
import logging
from typing import List, Dict, Optional, Any

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

from src.core.device_drivers import (
    DeviceDriver, DeviceInfo, CommandResult,
    DeviceStatus, DeviceType, Capability,
    get_driver_registry,
)

logger = logging.getLogger(__name__)

DRIVER_ID = "http"
DRIVER_NAME = "HTTP REST 驱动"
DRIVER_VERSION = "1.0.0"
DRIVER_PROTOCOLS = ["http", "https", "rest"]


class HttpDriver(DeviceDriver):
    """
    通用 HTTP REST 驱动

    命令映射规则（command → HTTP 请求）：
      turn_on / turn_off / toggle   → POST {base_url}/command   body: {"command": cmd, **params}
      get_state                     → GET  {base_url}/state
      自定义命令                     → POST {base_url}/command   或 使用 endpoint/method 覆盖

    也支持在 params 里传：
      {"_endpoint": "/custom/path", "_method": "GET"}  覆盖默认路由
    """

    driver_id = DRIVER_ID
    driver_name = DRIVER_NAME
    protocol = "http"
    version = DRIVER_VERSION
    description = "通过 HTTP REST API 控制设备"
    supported_types = [DeviceType.SWITCH, DeviceType.SENSOR, DeviceType.LIGHT, DeviceType.CLIMATE, DeviceType.APPLIANCE]
    supported_capabilities = [Capability.ON_OFF, Capability.BRIGHTNESS, Capability.TEMPERATURE, Capability.HUMIDITY]

    def __init__(self):
        super().__init__()
        self._session = None
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._state_callbacks = []

    def connect(self, config: Dict[str, Any]) -> bool:
        if not _REQUESTS_AVAILABLE:
            logger.error("[HttpDriver] requests 库未安装，请执行: pip install requests")
            return False

        base_url = config.get("base_url", "").rstrip("/")
        if not base_url:
            logger.error("[HttpDriver] 缺少 base_url 配置")
            return False

        self._config = config
        self._session = requests.Session()

        # 设置认证头
        token = config.get("token", "")
        if token:
            self._session.headers.update({"Authorization": f"Bearer {token}"})

        # 设置额外 headers
        extra_headers = config.get("headers", {})
        if extra_headers:
            self._session.headers.update(extra_headers)

        self._connected = True
        logger.info(f"[HttpDriver] 已连接到 {base_url}")

        # 启动轮询（如果配置了 poll_interval > 0）
        poll_interval = int(config.get("poll_interval", 0))
        if poll_interval > 0:
            self._start_polling(poll_interval)

        return True

    def disconnect(self) -> bool:
        self._poll_stop.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3)
        if self._session:
            self._session.close()
            self._session = None
        self._connected = False
        logger.info("[HttpDriver] 已断开连接")
        return True

    def is_connected(self) -> bool:
        return self._connected

    def discover_devices(self, timeout: int = 10) -> List[DeviceInfo]:
        """
        HTTP 驱动默认不支持自动发现
        用户可在 config 里提供 discovery_url 来获取设备列表
        """
        discovery_url = self._config.get("discovery_url", "")
        if not discovery_url:
            logger.info("[HttpDriver] 未配置 discovery_url，跳过自动发现")
            return []

        try:
            resp = self._session.get(discovery_url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            devices = []
            for item in data.get("devices", []):
                dev = DeviceInfo(
                    device_id=item.get("id", ""),
                    name=item.get("name", "HTTP设备"),
                    device_type=item.get("type", "custom"),
                    protocol="http",
                    address=item.get("address", discovery_url),
                    driver_id=DRIVER_ID,
                    capabilities=item.get("capabilities", []),
                    metadata=item,
                )
                devices.append(dev)
            logger.info(f"[HttpDriver] 发现 {len(devices)} 个设备")
            return devices
        except Exception as e:
            logger.error(f"[HttpDriver] 设备发现失败: {e}")
            return []

    def send_command(
        self,
        device: DeviceInfo,
        command: str,
        params: Dict[str, Any],
    ) -> CommandResult:
        if not self._connected or not self._session:
            return CommandResult(success=False, error="驱动未连接")

        base_url = self._config.get("base_url", "").rstrip("/")
        timeout = int(self._config.get("timeout", 10))

        # 允许 params 里覆盖 endpoint 和 method
        endpoint = params.pop("_endpoint", "/command")
        method = params.pop("_method", "POST").upper()

        url = f"{base_url}{endpoint}"

        try:
            payload = {"command": command, "device_id": device.device_id, **params}

            if method == "GET":
                resp = self._session.get(url, params=payload, timeout=timeout)
            elif method == "PUT":
                resp = self._session.put(url, json=payload, timeout=timeout)
            elif method == "DELETE":
                resp = self._session.delete(url, json=payload, timeout=timeout)
            else:
                resp = self._session.post(url, json=payload, timeout=timeout)

            resp.raise_for_status()
            result_data = {}
            try:
                result_data = resp.json()
            except Exception:
                result_data = {"raw": resp.text}

            return CommandResult(
                success=True,
                data=result_data,
                message=f"HTTP {method} {url} → {resp.status_code}",
            )
        except Exception as e:
            logger.error(f"[HttpDriver] 命令失败 {command}: {e}")
            return CommandResult(success=False, error=str(e))

    def get_device_state(self, device: DeviceInfo) -> Dict[str, Any]:
        if not self._connected or not self._session:
            return {"status": "offline", "error": "驱动未连接"}

        # 优先从缓存获取（减少请求）
        cached = self._state_cache.get(device.device_id)
        if cached:
            return cached

        base_url = self._config.get("base_url", "").rstrip("/")
        state_path = self._config.get("state_path", f"/devices/{device.device_id}/state")
        timeout = int(self._config.get("timeout", 10))

        try:
            resp = self._session.get(f"{base_url}{state_path}", timeout=timeout)
            resp.raise_for_status()
            state = resp.json()
            self._state_cache[device.device_id] = state
            return state
        except Exception as e:
            logger.warning(f"[HttpDriver] 获取状态失败 {device.device_id}: {e}")
            return {"status": "unknown", "error": str(e)}

    def on_state_change(self, callback) -> None:
        self._state_callbacks.append(callback)

    # ── 内部方法 ─────────────────────────────────────────────

    def _start_polling(self, interval: int):
        """后台线程定期轮询设备状态"""
        self._poll_stop.clear()

        def _poll():
            while not self._poll_stop.is_set():
                # 刷新所有已缓存设备状态
                for device_id in list(self._state_cache.keys()):
                    # 简单轮询：直接请求状态路径
                    base_url = self._config.get("base_url", "").rstrip("/")
                    state_path = f"/devices/{device_id}/state"
                    timeout = int(self._config.get("timeout", 10))
                    try:
                        if self._session:
                            resp = self._session.get(f"{base_url}{state_path}", timeout=timeout)
                            if resp.ok:
                                new_state = resp.json()
                                old_state = self._state_cache.get(device_id, {})
                                if new_state != old_state:
                                    self._state_cache[device_id] = new_state
                                    for cb in self._state_callbacks:
                                        try:
                                            cb(device_id, new_state)
                                        except Exception as _e: logger.debug(f"[http_driver] 忽略: {_e}")
                    except Exception as _e: logger.debug(f"[http_driver] 忽略: {_e}")
                self._poll_stop.wait(interval)

        self._poll_thread = threading.Thread(target=_poll, daemon=True, name="HttpDriver-Poll")
        self._poll_thread.start()
        logger.info(f"[HttpDriver] 状态轮询已启动（间隔 {interval}s）")


# 注册驱动
try:
    get_driver_registry().register(HttpDriver)
    logger.info("[HttpDriver] HTTP 驱动已注册")
except Exception as _e:
    logger.warning(f"[HttpDriver] 驱动注册跳过: {_e}")
