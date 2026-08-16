# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
设备管理器 —— 核心中枢
负责：设备注册/发现/调度、权限检查、命令日志、EventBus 集成

架构：
DeviceManager（单例）
  ├── _drivers: driver_id → DeviceDriver 实例
  ├── _devices: device_id → DeviceInfo
  ├── _permissions: device_id_or_"*" → {user → permission_level}
  └── _command_log: 命令执行历史（内存 + 数据库持久化）
"""

import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any

from src.core.device_drivers import (
    DeviceDriver, DeviceInfo, CommandResult,
    DeviceStatus, PermissionLevel, Capability,
    get_driver_registry
)
from src.core.event_bus import get_event_bus, Events
import logging
logger = logging.getLogger(__name__)


# ════════════════════════════════════════
# EventBus 事件类型扩展
# ════════════════════════════════════════

class DeviceEvents:
    """设备相关事件类型（注册到 EventBus）"""
    DEVICE_DISCOVERED = "device.discovered"     # 发现新设备
    DEVICE_REGISTERED = "device.registered"   # 设备注册成功
    DEVICE_REMOVED = "device.removed"       # 设备移除
    DEVICE_ONLINE = "device.online"         # 设备上线
    DEVICE_OFFLINE = "device.offline"       # 设备离线
    DEVICE_STATE_CHANGED = "device.state_changed"  # 设备状态变化
    COMMAND_SENT = "device.command_sent"    # 命令发送
    COMMAND_FAILED = "device.command_failed" # 命令执行失败
    PERMISSION_DENIED = "device.permission_denied"  # 权限不足


# ════════════════════════════════════════
# 权限管理器
# ════════════════════════════════════════

class PermissionManager:
    """
    设备权限管理

    权限层级（由低到高）：
    VIEW < CONTROL < CONFIGURE < ADMIN < OWNER

    检查逻辑：
    1. 查设备特定权限
    2. 查全局默认权限（device_id = "*"）
    3. 默认拒绝
    """

    # 权限层级数值（越高权限越大）
    _LEVELS = {
        "view": 10,
        "control": 20,
        "configure": 30,
        "admin": 40,
        "owner": 50,
    }

    def __init__(self, db_path: str = None):
        self._permissions: Dict[str, Dict[str, str]] = {
            # device_id → {user_id: permission_level}
            # "*" 表示全局默认
        }
        self._db_path = db_path
        if self._db_path:
            self._load_permissions()

    def check_permission(self, device_id: str, user_id: str,
                           required_level: str) -> bool:
        """
        检查用户是否有某设备的某权限

        Args:
            device_id: 设备ID（"*" 表示任意设备）
            user_id: 用户标识（"master" 表示主人）
            required_level: 所需最低权限等级

        Returns:
            bool
        """
        req_level = self._LEVELS.get(required_level, 0)

        # 1. 查设备特定权限
        device_perms = self._permissions.get(device_id, {})
        user_level = device_perms.get(user_id)
        if user_level:
            if self._LEVELS.get(user_level, 0) >= req_level:
                return True

        # 2. 查全局默认（"master" 的全局权限）
        global_perms = self._permissions.get("*", {})
        global_level = global_perms.get(user_id)
        if global_level:
            if self._LEVELS.get(global_level, 0) >= req_level:
                return True

        # 3. 主人（master）默认拥有所有设备的最高权限
        if user_id == "master":
            return True

        return False

    def grant_permission(self, device_id: str, user_id: str,
                          level: str) -> bool:
        """授予用户设备权限"""
        if level not in self._LEVELS:
            return False
        if device_id not in self._permissions:
            self._permissions[device_id] = {}
        self._permissions[device_id][user_id] = level
        self._save_permissions()
        return True

    def revoke_permission(self, device_id: str, user_id: str) -> bool:
        """撤销用户设备权限"""
        if device_id in self._permissions:
            if user_id in self._permissions[device_id]:
                del self._permissions[device_id][user_id]
                self._save_permissions()
                return True
        return False

    def get_user_permissions(self, user_id: str) -> Dict[str, str]:
        """获取用户所有设备权限"""
        result = {}
        for dev_id, perms in self._permissions.items():
            if user_id in perms:
                result[dev_id] = perms[user_id]
        return result

    def _load_permissions(self):
        """从数据库加载权限配置"""
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                "SELECT device_id, user_id, permission_level FROM device_permissions"
            ).fetchall()
            for row in rows:
                dev_id, uid, level = row
                if dev_id not in self._permissions:
                    self._permissions[dev_id] = {}
                self._permissions[dev_id][uid] = level
            conn.close()
        except Exception as _e: logger.debug(f"[device_manager] 忽略: {_e}")

    def _save_permissions(self):
        """保存权限配置到数据库"""
        import sqlite3
        if not self._db_path:
            return
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("DELETE FROM device_permissions")
            for dev_id, perms in self._permissions.items():
                for uid, level in perms.items():
                    conn.execute(
                        "INSERT INTO device_permissions (device_id, user_id, permission_level) VALUES (?, ?, ?)",
                        (dev_id, uid, level)
                    )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[PermissionManager] 保存权限失败: {e}")


# ════════════════════════════════════════
# 设备管理器（核心）
# ════════════════════════════════════════

class DeviceManager:
    """
    设备管理器单例

    职责：
    - 管理所有已加载的驱动实例
    - 注册/移除设备
    - 权限检查
    - 命令调度（选择驱动 → 执行命令 → 记录日志）
    - 集成 EventBus
    """

    def __init__(self, db_path: str = None):
        self._db_path = db_path
        self._drivers: Dict[str, DeviceDriver] = {}   # driver_id → instance
        self._devices: Dict[str, DeviceInfo] = {}     # device_id → DeviceInfo
        self._permission_mgr = PermissionManager(db_path)
        self._event_bus = get_event_bus()
        self._command_log: List[Dict] = []          # 内存日志（最近100条）
        self._log_max_len = 100

        # 加载已注册的设备
        if db_path:
            self._load_devices()

        # 注册 EventBus 事件类型（通知 Harven AI 其他模块）
        # EventBus 已自启动，无需额外注册

    # ── 驱动管理 ──

    def load_driver(self, driver_id: str) -> bool:
        """
        加载一个驱动（创建实例）

        Args:
            driver_id: 驱动标识（如 "mqtt"）

        Returns:
            bool: 是否加载成功
        """
        registry = get_driver_registry()
        driver_class = registry.get_driver_class(driver_id)
        if not driver_class:
            logger.debug(f"[DeviceManager] 驱动未注册: {driver_id}")
            return False
        try:
            driver = driver_class()
            self._drivers[driver_id] = driver
            logger.info(f"[DeviceManager] 驱动加载成功: {driver.driver_name}")
            return True
        except Exception as e:
            logger.error(f"[DeviceManager] 驱动加载失败 {driver_id}: {e}")
            traceback.print_exc()
            return False

    def unload_driver(self, driver_id: str) -> bool:
        """卸载驱动"""
        if driver_id in self._drivers:
            try:
                self._drivers[driver_id].disconnect()
            except Exception as _e: logger.debug(f"[device_manager] 忽略: {_e}")
            del self._drivers[driver_id]
            logger.debug(f"[DeviceManager] 驱动已卸载: {driver_id}")
            return True
        return False

    def get_loaded_drivers(self) -> List[Dict[str, Any]]:
        """获取已加载驱动列表"""
        return [d.get_metadata() for d in self._drivers.values()]

    def connect_driver(self, driver_id: str, config: Dict[str, Any] = None) -> bool:
        """
        配置并连接驱动

        Args:
            driver_id: 驱动标识
            config: 驱动配置（如 MQTT 的 host/port 等）
        """
        if driver_id not in self._drivers:
            if not self.load_driver(driver_id):
                return False
        driver = self._drivers[driver_id]
        if config:
            driver.configure(config)
        try:
            result = driver.connect()
            if result:
                logger.debug(f"[DeviceManager] 驱动已连接: {driver_id}")
                # 注册状态变化回调
                driver.register_state_callback(self._on_driver_state_change)
            return result
        except Exception as e:
            logger.error(f"[DeviceManager] 驱动连接失败 {driver_id}: {e}")
            return False

    def disconnect_driver(self, driver_id: str) -> bool:
        """断开驱动连接"""
        if driver_id in self._drivers:
            try:
                return self._drivers[driver_id].disconnect()
            except Exception as e:
                logger.error(f"[DeviceManager] 断开驱动失败 {driver_id}: {e}")
            return False
        return True

    # ── 设备注册/发现 ──

    def discover_devices(self, driver_id: str, timeout: int = 10) -> List[DeviceInfo]:
        """
        通过指定驱动发现可用设备

        Args:
            driver_id: 驱动标识
            timeout: 超时秒数

        Returns:
            List[DeviceInfo]: 发现到的设备列表
        """
        if driver_id not in self._drivers:
            if not self.connect_driver(driver_id):
                return []
        driver = self._drivers[driver_id]
        try:
            devices = driver.discover(timeout=timeout)
            # 触发 EventBus 事件
            for dev in devices:
                dev.driver_id = driver_id
                self._event_bus.emit(
                    DeviceEvents.DEVICE_DISCOVERED,
                    dev.device_id, dev.to_dict()
                )
            return devices
        except Exception as e:
            logger.error(f"[DeviceManager] 设备发现失败 {driver_id}: {e}")
            traceback.print_exc()
            return []

    def register_device(self, device_info: DeviceInfo) -> bool:
        """
        注册一个设备（手动或自动发现后注册）

        Args:
            device_info: 设备信息

        Returns:
            bool: 是否注册成功
        """
        if not device_info.device_id:
            import uuid
            device_info.device_id = uuid.uuid4().hex[:12]
        if not device_info.created_at:
            device_info.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not device_info.updated_at:
            device_info.updated_at = device_info.created_at

        self._devices[device_info.device_id] = device_info

        # 持久化到数据库
        self._save_device(device_info)

        # 授予主人（master）对该设备的 owner 权限
        self._permission_mgr.grant_permission(
            device_info.device_id, "master", "owner"
        )

        # 触发 EventBus 事件
        self._event_bus.emit(
            DeviceEvents.DEVICE_REGISTERED,
            device_info.device_id, device_info.to_dict()
        )
        self._event_bus.emit("device.list_changed")

        logger.debug(f"[DeviceManager] 设备注册成功: {device_info.name} ({device_info.device_id})")
        return True

    def unregister_device(self, device_id: str) -> bool:
        """注销设备"""
        if device_id in self._devices:
            dev = self._devices[device_id]
            del self._devices[device_id]
            self._remove_device_from_db(device_id)
            self._permission_mgr.revoke_permission(device_id, "master")
            self._event_bus.emit(DeviceEvents.DEVICE_REMOVED, device_id, dev.to_dict())
            self._event_bus.emit("device.list_changed")
            logger.debug(f"[DeviceManager] 设备已注销: {device_id}")
            return True
        return False

    def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """获取单个设备信息"""
        return self._devices.get(device_id)

    def list_devices(self, device_type: str = None, status: str = None) -> List[DeviceInfo]:
        """
        列出已注册设备

        Args:
            device_type: 按类型过滤（可选）
            status: 按状态过滤（可选）

        Returns:
            List[DeviceInfo]
        """
        result = list(self._devices.values())
        if device_type:
            result = [d for d in result if d.device_type == device_type]
        if status:
            result = [d for d in result if d.status == status]
        return result

    def get_devices_by_room(self, room: str) -> List[DeviceInfo]:
        """按房间获取设备"""
        return [d for d in self._devices.values() if d.room == room]

    # ── 命令执行（核心） ──

    def send_command(self, device_id: str, command: str,
                       params: Dict[str, Any] = None,
                       user_id: str = "master") -> CommandResult:
        """
        向设备发送命令（带权限检查）

        Args:
            device_id: 设备ID
            command: 命令名
            params: 命令参数
            user_id: 执行命令的用户（默认 master）

        Returns:
            CommandResult: 执行结果
        """
        # 1. 查找设备
        device = self._devices.get(device_id)
        if not device:
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"设备不存在: {device_id}"
            )

        # 2. 权限检查（最低需要 control 权限）
        if not self._permission_mgr.check_permission(
            device_id, user_id, "control"
        ):
            self._event_bus.emit(
                DeviceEvents.PERMISSION_DENIED,
                device_id, user_id, command
            )
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message="权限不足：需要 control 及以上权限"
            )

        # 3. 查找驱动
        driver_id = device.driver_id
        if driver_id not in self._drivers:
            return CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"驱动未加载: {driver_id}"
            )

        # 4. 获取旧状态
        old_state = self._drivers[driver_id].get_state(device_id)

        # 5. 执行命令
        try:
            result = self._drivers[driver_id].send_command(
                device_id, command, params or {}
            )
            # 6. 记录日志
            self._log_command(device_id, command, params, result)

            # 7. 触发 EventBus 事件
            if result.success:
                new_state = self._drivers[driver_id].get_state(device_id)
                self._event_bus.emit(
                    DeviceEvents.COMMAND_SENT,
                    device_id, command, result.to_dict()
                )
                # 状态变化事件（如果状态有变化）
                if old_state != new_state:
                    self._event_bus.emit(
                        DeviceEvents.DEVICE_STATE_CHANGED,
                        device_id, old_state, new_state
                    )
                    # 更新设备状态
                    device.state = new_state
                    device.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._save_device(device)
            else:
                self._event_bus.emit(
                    DeviceEvents.COMMAND_FAILED,
                    device_id, command, result.message
                )

            return result

        except Exception as e:
            traceback.print_exc()
            result = CommandResult(
                success=False,
                command=command,
                device_id=device_id,
                message=f"命令执行异常: {e}"
            )
            self._log_command(device_id, command, params, result)
            self._event_bus.emit(
                DeviceEvents.COMMAND_FAILED,
                device_id, command, str(e)
            )
            return result

    # ── 状态查询 ──

    def get_device_state(self, device_id: str, user_id: str = "master") -> Dict[str, Any]:
        """
        获取设备状态（带权限检查）

        Args:
            device_id: 设备ID
            user_id: 查询者

        Returns:
            状态字典；权限不足返回空字典
        """
        if not self._permission_mgr.check_permission(
            device_id, user_id, "view"
        ):
            return {}

        device = self._devices.get(device_id)
        if not device:
            return {}

        driver_id = device.driver_id
        if driver_id in self._drivers:
            return self._drivers[driver_id].get_state(device_id)
        return device.state

    def refresh_all_states(self) -> Dict[str, Dict]:
        """刷新所有设备状态"""
        results = {}
        for dev_id, device in self._devices.items():
            driver_id = device.driver_id
            if driver_id in self._drivers:
                try:
                    state = self._drivers[driver_id].get_state(dev_id)
                    results[dev_id] = state
                    device.state = state
                except Exception as e:
                    results[dev_id] = {"error": str(e)}
        return results

    # ── 命令日志 ──

    def _log_command(self, device_id: str, command: str,
                       params: Dict, result: CommandResult):
        """记录命令执行日志"""
        log_entry = {
            "device_id": device_id,
            "command": command,
            "params": params or {},
            "success": result.success,
            "message": result.message,
            "timestamp": result.timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._command_log.append(log_entry)
        # 限制内存日志长度
        if len(self._command_log) > self._log_max_len:
            self._command_log = self._command_log[-self._log_max_len:]

        # 持久化到数据库
        self._save_command_log(log_entry)

    def get_command_log(self, device_id: str = None, limit: int = 50) -> List[Dict]:
        """获取命令日志"""
        if device_id:
            return [e for e in self._command_log if e["device_id"] == device_id][-limit:]
        return self._command_log[-limit:]

    # ── 状态变化回调（来自驱动） ──

    def _on_driver_state_change(self, device_id: str, old_state: Dict, new_state: Dict):
        """驱动上报的状态变化"""
        device = self._devices.get(device_id)
        if device:
            device.state = new_state
            device.status = DeviceStatus.ONLINE.value
            device.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_device(device)

        self._event_bus.emit(
            DeviceEvents.DEVICE_STATE_CHANGED,
            device_id, old_state, new_state
        )

    # ── 数据库持久化 ──

    def _save_device(self, device: DeviceInfo):
        """保存设备到数据库"""
        if not self._db_path:
            return
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path)
            info = device.to_dict()
            conn.execute("""
                INSERT OR REPLACE INTO devices
                (device_id, name, device_type, protocol, address,
                 driver_id, manufacturer, model, capabilities,
                 room, zone, metadata, status, state, permission,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                info["device_id"],
                info["name"],
                info["device_type"],
                info["protocol"],
                info["address"],
                info["driver_id"],
                info["manufacturer"],
                info["model"],
                json.dumps(info["capabilities"], ensure_ascii=False),
                info["room"],
                info["zone"],
                json.dumps(info["metadata"], ensure_ascii=False),
                info["status"],
                json.dumps(info["state"], ensure_ascii=False),
                info["permission"],
                info["created_at"],
                info["updated_at"],
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[DeviceManager] 保存设备失败: {e}")

    def _remove_device_from_db(self, device_id: str):
        """从数据库删除设备"""
        if not self._db_path:
            return
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[DeviceManager] 删除设备失败: {e}")

    def _load_devices(self):
        """从数据库加载已注册的设备"""
        if not self._db_path:
            return
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM devices").fetchall()
            for row in rows:
                dev = DeviceInfo(
                    device_id=row["device_id"],
                    name=row["name"],
                    device_type=row["device_type"],
                    protocol=row["protocol"],
                    address=row["address"],
                    driver_id=row["driver_id"],
                    manufacturer=row["manufacturer"] or "",
                    model=row["model"] or "",
                    capabilities=json.loads(row["capabilities"]) if row["capabilities"] else [],
                    room=row["room"] or "",
                    zone=row["zone"] or "",
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    status=row["status"] or "unknown",
                    state=json.loads(row["state"]) if row["state"] else {},
                    permission=row["permission"] or "owner",
                    created_at=row["created_at"] or "",
                    updated_at=row["updated_at"] or "",
                )
                self._devices[dev.device_id] = dev
            conn.close()
            logger.info(f"[DeviceManager] 从数据库加载了 {len(self._devices)} 个设备")
        except Exception as e:
            logger.error(f"[DeviceManager] 加载设备失败: {e}")
            traceback.print_exc()

    def _save_command_log(self, log_entry: Dict):
        """保存命令日志到数据库"""
        if not self._db_path:
            return
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                INSERT INTO device_commands
                (device_id, command, params, success, message, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                log_entry["device_id"],
                log_entry["command"],
                json.dumps(log_entry["params"], ensure_ascii=False),
                log_entry["success"],
                log_entry["message"],
                log_entry["timestamp"],
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[DeviceManager] 保存命令日志失败: {e}")

    # ── 权限管理（代理到 PermissionManager） ──

    def check_permission(self, device_id: str, user_id: str, level: str) -> bool:
        return self._permission_mgr.check_permission(device_id, user_id, level)

    def grant_permission(self, device_id: str, user_id: str, level: str) -> bool:
        return self._permission_mgr.grant_permission(device_id, user_id, level)

    def get_user_devices(self, user_id: str) -> List[DeviceInfo]:
        """获取用户有权限访问的设备列表"""
        result = []
        for dev_id, dev in self._devices.items():
            if self._permission_mgr.check_permission(dev_id, user_id, "view"):
                result.append(dev)
        return result

    # ── 摘要 ──

    def get_summary(self) -> Dict[str, Any]:
        """获取设备管理器摘要（供 AI 使用）"""
        online = [d for d in self._devices.values() if d.status == DeviceStatus.ONLINE.value]
        return {
            "total_devices": len(self._devices),
            "online_devices": len(online),
            "loaded_drivers": list(self._drivers.keys()),
            "recent_commands": len(self._command_log),
        }


# ════════════════════════════════════════
# 全局单例
# ════════════════════════════════════════

_device_manager: Optional[DeviceManager] = None
_dm_lock = None  # 延迟导入 threading


def get_device_manager(db_path: str = None) -> DeviceManager:
    """获取全局 DeviceManager 单例"""
    global _device_manager, _dm_lock
    if _device_manager is None:
        import threading
        _dm_lock = threading.Lock()
        with _dm_lock:
            if _device_manager is None:
                _device_manager = DeviceManager(db_path=db_path)
    return _device_manager
