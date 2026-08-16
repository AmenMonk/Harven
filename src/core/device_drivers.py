# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
设备驱动抽象层
所有设备协议（MQTT、HTTP、Zigbee等）的统一接口

设计原则：
- 每种协议实现一个 Driver 子类
- Driver 只负责通信，不管业务逻辑
- DeviceManager 负责：注册、调度、权限、日志
- 通过 EventBus 触发事件（设备上线/离线/状态变化/命令执行）
"""

import json
import time
import traceback
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Callable
import logging
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════

class DeviceStatus(str, Enum):
    """设备状态"""
    ONLINE = "online"
    OFFLINE = "offline"
    UNREACHABLE = "unreachable"
    ERROR = "error"
    UNKNOWN = "unknown"


class DeviceType(str, Enum):
    """设备类型大类"""
    LIGHT = "light"           # 灯光
    SWITCH = "switch"         # 开关
    SENSOR = "sensor"         # 传感器
    CLIMATE = "climate"       # 温控（空调/暖气）
    MEDIA = "media"           # 媒体（音箱/电视）
    LOCK = "lock"             # 门锁
    CAMERA = "camera"         # 摄像头
    COVER = "cover"           # 窗帘/卷帘
    VACUUM = "vacuum"         # 扫地机器人
    APPLIANCE = "appliance"   # 家电
    VEHICLE = "vehicle"       # 车辆
    INDUSTRIAL = "industrial" # 工业设备
    MEDICAL = "medical"       # 医疗设备
    CUSTOM = "custom"         # 自定义


class PermissionLevel(str, Enum):
    """设备权限分级"""
    VIEW = "view"             # 只读：查看设备状态
    CONTROL = "control"       # 控制：发送基础命令（开/关/调节）
    CONFIGURE = "configure"   # 配置：修改设备设置
    ADMIN = "admin"           # 管理：添加/删除/权限分配
    OWNER = "owner"           # 所有者：完全控制 + 授权


class Capability(str, Enum):
    """设备能力（声明设备支持哪些操作）"""
    ON_OFF = "on_off"         # 开关
    BRIGHTNESS = "brightness" # 亮度调节
    COLOR = "color"           # 颜色调节
    TEMPERATURE = "temperature" # 温度设置
    HUMIDITY = "humidity"     # 湿度
    MOTION = "motion"         # 运动检测
    OPEN_CLOSE = "open_close" # 开合（门窗/窗帘）
    LOCK_UNLOCK = "lock_unlock" # 锁定/解锁
    PLAY_PAUSE = "play_pause" # 播放/暂停
    VOLUME = "volume"         # 音量
    TIMER = "timer"           # 定时
    SCENE = "scene"           # 场景模式
    CUSTOM = "custom"         # 自定义能力


@dataclass
class DeviceInfo:
    """设备信息（注册时使用）"""
    device_id: str = ""                        # 设备唯一标识
    name: str = ""                               # 设备名称
    device_type: str = "custom"                # 设备类型（DeviceType 枚举值）
    protocol: str = "unknown"                  # 通信协议（mqtt/http/zigbee 等）
    address: str = ""                          # 设备地址/Topic/URL
    driver_id: str = ""                      # 驱动标识
    manufacturer: str = ""                  # 厂商
    model: str = ""                         # 型号
    capabilities: List[str] = None         # 能力列表
    room: str = ""                          # 所在房间/区域
    zone: str = ""                          # 所属区域
    metadata: Dict[str, Any] = None       # 扩展元数据
    status: str = "unknown"                 # 当前状态
    state: Dict[str, Any] = None           # 当前状态值
    permission: str = "owner"               # 默认权限
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if self.capabilities is None:
            self.capabilities = []
        if self.metadata is None:
            self.metadata = {}
        if self.state is None:
            self.state = {}

    def to_dict(self):
        return {
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type,
            "protocol": self.protocol,
            "address": self.address,
            "driver_id": self.driver_id,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "capabilities": self.capabilities,
            "room": self.room,
            "zone": self.zone,
            "metadata": self.metadata,
            "status": self.status,
            "state": self.state,
            "permission": self.permission,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class CommandResult:
    """命令执行结果"""
    success: bool = False
    command: str = ""
    device_id: str = ""
    message: str = ""
    new_state: Dict[str, Any] = None
    timestamp: str = ""

    def __post_init__(self):
        if self.new_state is None:
            self.new_state = {}
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self):
        return {
            "success": self.success,
            "command": self.command,
            "device_id": self.device_id,
            "message": self.message,
            "new_state": self.new_state,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════
# 驱动基类
# ═══════════════════════════════════════════════════

class DeviceDriver:
    """
    设备驱动基类

    所有协议驱动必须继承此类并实现以下方法：
    - connect(): 连接到协议网关/中间件
    - disconnect(): 断开连接
    - discover(): 发现可用设备
    - send_command(): 发送控制命令
    - get_state(): 获取设备状态
    """

    # 驱动元数据（子类必须覆盖）
    driver_id: str = "base"
    driver_name: str = "基础驱动"
    protocol: str = "unknown"
    version: str = "1.0.0"
    description: str = ""
    supported_types: List[str] = None       # 支持的设备类型
    supported_capabilities: List[str] = None

    def __init__(self):
        self._connected = False
        self._config: Dict[str, Any] = {}
        self._state_cache: Dict[str, Dict] = {}  # device_id -> state
        self._on_state_change: Optional[Callable] = None  # 状态变化回调
        if self.supported_types is None:
            self.supported_types = []
        if self.supported_capabilities is None:
            self.supported_capabilities = []

    def configure(self, config: Dict[str, Any]) -> bool:
        """
        配置驱动（连接前调用）

        Args:
            config: 驱动配置，例如 MQTT 的 host/port/username/password

        Returns:
            bool: 配置是否成功
        """
        self._config = config
        return True

    def connect(self) -> bool:
        """
        连接到协议网关/中间件

        Returns:
            bool: 是否连接成功
        """
        raise NotImplementedError("子类必须实现 connect()")

    def disconnect(self) -> bool:
        """
        断开连接

        Returns:
            bool: 是否断开成功
        """
        raise NotImplementedError("子类必须实现 disconnect()")

    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected

    def discover(self, timeout: int = 10) -> List[DeviceInfo]:
        """
        发现可用设备

        Args:
            timeout: 发现超时（秒）

        Returns:
            发现到的设备列表
        """
        return []

    def send_command(self, device_id: str, command: str,
                     params: Dict[str, Any] = None) -> CommandResult:
        """
        向设备发送控制命令

        Args:
            device_id: 设备ID
            command: 命令名（turn_on/turn_off/set_brightness/set_temperature 等）
            params: 命令参数

        Returns:
            CommandResult: 执行结果
        """
        raise NotImplementedError("子类必须实现 send_command()")

    def get_state(self, device_id: str) -> Dict[str, Any]:
        """
        获取设备当前状态

        Args:
            device_id: 设备ID

        Returns:
            状态字典
        """
        return self._state_cache.get(device_id, {})

    def register_state_callback(self, callback: Callable):
        """
        注册状态变化回调（设备状态变更时自动触发）

        Args:
            callback: 回调函数 callback(device_id, old_state, new_state)
        """
        self._on_state_change = callback

    def _notify_state_change(self, device_id: str, old_state: Dict, new_state: Dict):
        """内部方法：触发状态变化通知"""
        if self._on_state_change and old_state != new_state:
            try:
                self._on_state_change(device_id, old_state, new_state)
            except Exception as e:
                logger.error(f"[{self.driver_id}] 状态回调执行失败: {e}")

    def get_metadata(self) -> Dict[str, Any]:
        """获取驱动元数据"""
        return {
            "driver_id": self.driver_id,
            "driver_name": self.driver_name,
            "protocol": self.protocol,
            "version": self.version,
            "description": self.description,
            "connected": self._connected,
            "supported_types": self.supported_types,
            "supported_capabilities": self.supported_capabilities,
        }


# ═══════════════════════════════════════════════════
# 驱动注册表
# ═══════════════════════════════════════════════════

class DriverRegistry:
    """驱动注册表：管理所有可用驱动的注册和实例化"""

    def __init__(self):
        self._drivers: Dict[str, type] = {}  # driver_id -> Driver class

    def register(self, driver_class: type):
        """注册驱动类"""
        if not issubclass(driver_class, DeviceDriver):
            raise TypeError(f"{driver_class} 必须继承 DeviceDriver")
        instance = driver_class()

        # 统一获取 driver_id（兼容类属性 和 @classmethod 两种写法）
        _driver_id = None
        raw_id = getattr(instance, "driver_id", None)
        if callable(raw_id) and not isinstance(raw_id, str):
            _driver_id = raw_id()   # 是方法 → 调用它
        else:
            _driver_id = raw_id      # 是字符串 → 直接用

        if not isinstance(_driver_id, str) or not _driver_id:
            raise ValueError(f"[DriverRegistry] {driver_class.__name__}.driver_id 无效: {_driver_id}")

        # 统一获取 driver_name
        _driver_name = None
        raw_name = getattr(instance, "driver_name", None)
        if callable(raw_name) and not isinstance(raw_name, str):
            _driver_name = raw_name()
        else:
            _driver_name = raw_name or driver_class.__name__

        # 统一获取 version
        _version = None
        raw_ver = getattr(instance, "version", None)
        if callable(raw_ver) and not isinstance(raw_ver, str):
            _version = raw_ver()
        else:
            _version = raw_ver or "1.0.0"

        self._drivers[_driver_id] = driver_class
        logger.debug(f"[DriverRegistry] 注册驱动: {_driver_name} ({_driver_id}) v{_version}")


    def get_driver_class(self, driver_id: str) -> Optional[type]:
        """获取驱动类"""
        return self._drivers.get(driver_id)

    def create_driver(self, driver_id: str) -> Optional[DeviceDriver]:
        """创建驱动实例"""
        cls = self._drivers.get(driver_id)
        if cls:
            return cls()
        return None

    def list_drivers(self) -> List[Dict[str, Any]]:
        """列出所有已注册驱动"""
        result = []
        for driver_id, cls in self._drivers.items():
            try:
                instance = cls()
                result.append(instance.get_metadata())
            except Exception as e:
                result.append({
                    "driver_id": driver_id,
                    "error": str(e)
                })
        return result


# ═══════════════════════════════════════════════════
# 命令标准化
# ═══════════════════════════════════════════════════

# 标准命令映射（统一不同协议的命令名）
STANDARD_COMMANDS = {
    # 通用
    "turn_on": ["on", "open", "enable", "start", "unlock"],
    "turn_off": ["off", "close", "disable", "stop", "lock"],
    "toggle": ["toggle", "flip"],

    # 灯光
    "set_brightness": ["brightness", "dim", "bright"],
    "set_color": ["color", "rgb", "hue"],

    # 温控
    "set_temperature": ["temperature", "temp", "set_temp"],
    "set_mode": ["mode", "preset", "scene_mode"],

    # 媒体
    "play": ["play", "resume"],
    "pause": ["pause"],
    "set_volume": ["volume", "vol"],
    "next": ["next", "skip"],
    "prev": ["prev", "previous", "previous_track"],

    # 窗帘
    "open_cover": ["open_cover", "open"],
    "close_cover": ["close_cover", "close"],
    "set_cover_position": ["cover_position", "set_position"],
}

# 能力到命令的映射
CAPABILITY_COMMANDS = {
    Capability.ON_OFF: ["turn_on", "turn_off", "toggle"],
    Capability.BRIGHTNESS: ["set_brightness", "turn_on", "turn_off"],
    Capability.COLOR: ["set_color"],
    Capability.TEMPERATURE: ["set_temperature", "set_mode"],
    Capability.OPEN_CLOSE: ["open_cover", "close_cover", "set_cover_position"],
    Capability.LOCK_UNLOCK: ["turn_on", "turn_off"],  # lock=off, unlock=on
    Capability.PLAY_PAUSE: ["play", "pause"],
    Capability.VOLUME: ["set_volume"],
}


# ═══════════════════════════════════════════════════
# 全局注册表实例
# ═══════════════════════════════════════════════════

_registry: Optional[DriverRegistry] = None
_registry_lock = None  # 延迟导入 threading


def get_driver_registry() -> DriverRegistry:
    """获取全局驱动注册表（单例）"""
    global _registry, _registry_lock
    if _registry is None:
        import threading
        _registry_lock = threading.Lock()
        with _registry_lock:
            if _registry is None:
                _registry = DriverRegistry()
    return _registry
