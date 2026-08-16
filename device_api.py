"""
设备 API —— REST 路由
供前端调用设备管理功能

Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""

import json
import traceback
from flask import jsonify, request
from src.core.device_drivers import (
    DeviceInfo, DeviceStatus, DeviceType,
    PermissionLevel, get_driver_registry
)
from src.core.device_manager import DeviceManager, get_device_manager, DeviceEvents
import logging
logger = logging.getLogger(__name__)

# ══════════════════════════════════════
# 注册所有可用驱动（模块加载时执行）
# ══════════════════════════════════════
try:
    from src.core.drivers.mqtt_driver import MqttDriver
    get_driver_registry().register(MqttDriver)
    logger.info("[DeviceAPI] MQTT 驱动注册成功")
except Exception as e:
    logger.error(f"[DeviceAPI] MQTT 驱动注册失败: {e}")

try:
    from src.core.drivers.http_driver import HttpDriver
    get_driver_registry().register(HttpDriver)
    logger.info("[DeviceAPI] HTTP 驱动注册成功")
except Exception as e:
    logger.warning(f"[DeviceAPI] HTTP 驱动注册失败: {e}")

try:
    from src.core.drivers.websocket_driver import WebSocketDriver
    get_driver_registry().register(WebSocketDriver)
    logger.info("[DeviceAPI] WebSocket 驱动注册成功")
except Exception as e:
    logger.warning(f"[DeviceAPI] WebSocket 驱动注册失败: {e}")

try:
    from src.core.drivers.mock_driver import MockDriver
    get_driver_registry().register(MockDriver)
    logger.info("[DeviceAPI] Mock 驱动注册成功（测试用）")
except Exception as e:
    logger.warning(f"[DeviceAPI] Mock 驱动注册失败: {e}")


def register_device_api_routes(app):
    """注册设备 API 路由到 Flask app"""

    # ══════════════════════════════════════
    # 设备查询
    # ══════════════════════════════════════

    def list_devices():
        """列出所有已注册设备（支持 type/status 过滤）"""
        try:
            dm = get_device_manager()
            device_type = request.args.get('type', '').strip() or None
            status = request.args.get('status', '').strip() or None
            devices = dm.list_devices(device_type=device_type, status=status)
            return jsonify({
                "success": True,
                "devices": [d.to_dict() for d in devices],
                "total": len(devices),
            })
        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    def list_devices_by_type(device_type):
        """按类型列出设备"""
        try:
            dm = get_device_manager()
            devices = dm.list_devices(device_type=device_type)
            return jsonify({
                "success": True,
                "devices": [d.to_dict() for d in devices],
                "type": device_type,
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def get_device(device_id):
        """获取单个设备详情"""
        try:
            dm = get_device_manager()
            dev = dm.get_device(device_id)
            if not dev:
                return jsonify({"success": False, "error": f"设备不存在: {device_id}"}), 404
            state = dm.get_device_state(device_id)
            return jsonify({
                "success": True,
                "device": dev.to_dict(),
                "state": state,
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def list_rooms():
        """列出所有房间"""
        try:
            dm = get_device_manager()
            devices = dm.list_devices()
            rooms = sorted(set(d.room for d in devices if d.room))
            return jsonify({"success": True, "rooms": rooms})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ══════════════════════════════════════
    # 设备注册
    # ══════════════════════════════════════

    def register_device():
        """注册一个设备"""
        try:
            data = request.json or {}
            dm = get_device_manager()

            dev = DeviceInfo(
                device_id=data.get("device_id", ""),
                name=data.get("name", "未命名设备"),
                device_type=data.get("device_type", "custom"),
                protocol=data.get("protocol", "unknown"),
                address=data.get("address", ""),
                driver_id=data.get("driver_id", ""),
                manufacturer=data.get("manufacturer", ""),
                model=data.get("model", ""),
                capabilities=data.get("capabilities", []),
                room=data.get("room", ""),
                zone=data.get("zone", ""),
                metadata=data.get("metadata", {}),
                permission=data.get("permission", "owner"),
            )

            success = dm.register_device(dev)
            if success:
                return jsonify({"success": True, "device": dev.to_dict()})
            else:
                return jsonify({"success": False, "error": "注册失败"}), 500
        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    def unregister_device(device_id):
        """注销设备"""
        try:
            dm = get_device_manager()
            success = dm.unregister_device(device_id)
            if success:
                return jsonify({"success": True, "message": f"设备已注销: {device_id}"})
            else:
                return jsonify({"success": False, "error": f"设备不存在: {device_id}"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ══════════════════════════════════════
    # 设备发现
    # ══════════════════════════════════════

    def discover_devices():
        """发现设备（默认使用 mqtt 驱动），并自动注册发现的设备"""
        try:
            data = request.json or {}
            driver_id = data.get("driver_id", "mqtt")
            timeout = data.get("timeout", 10)

            dm = get_device_manager()
            devices = dm.discover_devices(driver_id, timeout=timeout)
            # 自动注册发现的设备
            registered = []
            for dev in devices:
                try:
                    if dm.register_device(dev):
                        registered.append(dev.to_dict())
                except Exception as _e: logger.debug(f"[device_api] 忽略: {_e}")
            return jsonify({
                "success": True,
                "devices": registered,
                "count": len(registered),
                "message": f"发现 {len(devices)} 个设备，已注册 {len(registered)} 个",
            })
        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    # ══════════════════════════════════════
    # 设备控制
    # ══════════════════════════════════════

    def send_command(device_id):
        """向设备发送命令"""
        try:
            data = request.json or {}
            command = data.get("command", "")
            params = data.get("params", {})
            user_id = data.get("user_id", "master")

            if not command:
                return jsonify({"success": False, "error": "command 不能为空"}), 400

            dm = get_device_manager()
            result = dm.send_command(device_id, command, params, user_id=user_id)
            return jsonify(result.to_dict())
        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    def get_device_state_api(device_id):
        """获取设备状态"""
        try:
            user_id = request.args.get("user_id", "master")
            dm = get_device_manager()
            state = dm.get_device_state(device_id, user_id=user_id)
            return jsonify({
                "success": True,
                "device_id": device_id,
                "state": state,
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def refresh_states():
        """刷新所有设备状态"""
        try:
            dm = get_device_manager()
            states = dm.refresh_all_states()
            return jsonify({
                "success": True,
                "states": states,
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ══════════════════════════════════════
    # 驱动管理
    # ══════════════════════════════════════

    def _sanitize_for_json(obj):
        """递归清理对象，确保 JSON 可序列化"""
        import enum
        if isinstance(obj, dict):
            return {k: _sanitize_for_json(v) for k, v in obj.items()
                    if not callable(v) and not isinstance(v, type(lambda: None))}
        if isinstance(obj, (list, tuple)):
            return [_sanitize_for_json(v) for v in obj]
        if isinstance(obj, enum.Enum):
            return obj.value
        if callable(obj):
            return None
        return obj

    def list_drivers():
        """列出所有已注册的驱动（含加载/连接状态和配置）"""
        try:
            registry = get_driver_registry()
            drivers = registry.list_drivers()
            dm = get_device_manager()
            loaded = dm.get_loaded_drivers()
            loaded_map = {d.get("driver_id", ""): d for d in loaded}
            for d in drivers:
                did = d.get("driver_id", "")
                ld = loaded_map.get(did, {})
                d["id"] = did
                d["loaded"] = did in loaded_map
                d["connected"] = ld.get("connected", False) if d["loaded"] else False
                d["config"] = ld.get("config", {}) if d["loaded"] else {}
                if not d.get("name"):
                    d["name"] = d.get("driver_name", did)
                if not d.get("type"):
                    d["type"] = did
            # 清理不可序列化的值
            drivers = _sanitize_for_json(drivers)
            return jsonify({"success": True, "drivers": drivers})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    def load_driver(driver_id):
        """加载驱动"""
        try:
            dm = get_device_manager()
            success = dm.load_driver(driver_id)
            if success:
                return jsonify({"success": True, "message": f"驱动已加载: {driver_id}"})
            else:
                return jsonify({"success": False, "error": f"驱动加载失败: {driver_id}"}), 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def connect_driver(driver_id):
        """配置并连接驱动（config 可嵌套在 config 字段或直接传平铺键）"""
        try:
            data = request.json or {}
            dm = get_device_manager()
            # 支持两种格式：{config: {...}} 或直接传 host/port 等键
            config = data.get("config", {})
            if not config:
                # 从平铺字段提取（去除 driver_id/timeout 等元字段）
                config = {k: v for k, v in data.items()
                          if k not in ('driver_id', 'timeout', 'user_id') and v is not None}
            success = dm.connect_driver(driver_id, config) if config else dm.connect_driver(driver_id)
            if success:
                return jsonify({"success": True, "message": f"驱动已连接: {driver_id}"})
            else:
                return jsonify({"success": False, "error": f"驱动连接失败: {driver_id}"}), 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def disconnect_driver(driver_id):
        """断开驱动连接"""
        try:
            dm = get_device_manager()
            success = dm.disconnect_driver(driver_id)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ══════════════════════════════════════
    # 命令日志
    # ══════════════════════════════════════

    def get_command_log(device_id=None):
        """获取命令日志"""
        try:
            limit = int(request.args.get("limit", 50))
            dm = get_device_manager()
            logs = dm.get_command_log(device_id=device_id, limit=limit)
            return jsonify({
                "success": True,
                "commands": logs,
                "total": len(logs),
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def get_device_log():
        """获取设备操作日志（兼容数据保险箱 /api/devices/log）"""
        try:
            limit = int(request.args.get("limit", 50))
            dm = get_device_manager()
            logs = dm.get_command_log(device_id=None, limit=limit)
            return jsonify({
                "success": True,
                "logs": logs,
                "total": len(logs),
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ══════════════════════════════════════
    # 权限管理
    # ══════════════════════════════════════

    def grant_permission(device_id):
        """授予设备权限"""
        try:
            data = request.json or {}
            user_id = data.get("user_id", "")
            level = data.get("level", "view")

            if not user_id:
                return jsonify({"success": False, "error": "user_id 不能为空"}), 400

            dm = get_device_manager()
            success = dm.grant_permission(device_id, user_id, level)
            if success:
                return jsonify({"success": True, "message": f"已授予 {user_id} 对 {device_id} 的 {level} 权限"})
            else:
                return jsonify({"success": False, "error": "授权失败"}), 500
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def get_summary():
        """获取设备管理器摘要"""
        try:
            dm = get_device_manager()
            summary = dm.get_summary()
            return jsonify({"success": True, **summary})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ══════════════════════════════════════
    # 注册路由
    # ══════════════════════════════════════

    # 设备 CRUD
    app.add_url_rule("/api/devices", view_func=list_devices, methods=["GET"])
    app.add_url_rule("/api/devices", view_func=register_device, methods=["POST"])
    app.add_url_rule("/api/devices/<device_id>", view_func=get_device, methods=["GET"])
    app.add_url_rule("/api/devices/<device_id>", view_func=unregister_device, methods=["DELETE"])

    # 按类型/房间
    app.add_url_rule("/api/devices/type/<device_type>", view_func=list_devices_by_type, methods=["GET"])
    app.add_url_rule("/api/devices/rooms", view_func=list_rooms, methods=["GET"])

    # 发现
    app.add_url_rule("/api/devices/discover", view_func=discover_devices, methods=["POST"])

    # 控制
    app.add_url_rule("/api/devices/<device_id>/command", view_func=send_command, methods=["POST"])
    app.add_url_rule("/api/devices/<device_id>/state", view_func=get_device_state_api, methods=["GET"])
    app.add_url_rule("/api/devices/refresh", view_func=refresh_states, methods=["POST"])

    # 驱动管理
    app.add_url_rule("/api/devices/drivers", view_func=list_drivers, methods=["GET"])
    app.add_url_rule("/api/devices/drivers/<driver_id>/load", view_func=load_driver, methods=["POST"])
    app.add_url_rule("/api/devices/drivers/<driver_id>/connect", view_func=connect_driver, methods=["POST"])
    app.add_url_rule("/api/devices/drivers/<driver_id>/disconnect", view_func=disconnect_driver, methods=["POST"])

    # 命令日志
    app.add_url_rule("/api/devices/commands", view_func=get_command_log, methods=["GET"])
    app.add_url_rule("/api/devices/log", view_func=get_device_log, methods=["GET"])

    # 权限
    app.add_url_rule("/api/devices/<device_id>/permission", view_func=grant_permission, methods=["POST"])

    # 摘要
    app.add_url_rule("/api/devices/summary", view_func=get_summary, methods=["GET"])

    # ══════════════════════════════════════
    # AI 生成驱动 API
    # ══════════════════════════════════════

    def generate_ai_driver():
        """使用 AI 生成设备驱动代码"""
        try:
            from src.core.drivers.ai_generated_driver import get_ai_driver_generator
            data = request.json or {}
            device_name = data.get("device_name", "")
            if not device_name:
                return jsonify({"success": False, "error": "device_name 不能为空"}), 400

            gen = get_ai_driver_generator()
            success, message, driver_id = gen.generate_driver(
                device_name=device_name,
                device_model=data.get("device_model", ""),
                protocol=data.get("protocol", "http"),
                device_description=data.get("description", ""),
                extra_context=data.get("extra_context", ""),
            )
            return jsonify({
                "success": success,
                "message": message,
                "driver_id": driver_id,
            })
        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    def list_ai_drivers():
        """列出所有 AI 生成的驱动"""
        try:
            from src.core.drivers.ai_generated_driver import list_generated_drivers, get_ai_driver_generator
            drivers = list_generated_drivers()
            gen = get_ai_driver_generator()
            result = []
            for driver_id, filepath in drivers.items():
                result.append({
                    "driver_id": driver_id,
                    "filepath": filepath,
                    "code_preview": (gen.get_driver_code(driver_id) or "")[:500],
                })
            return jsonify({"success": True, "drivers": result})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def get_ai_driver_code(driver_id):
        """获取 AI 生成驱动的完整源代码（供用户审查）"""
        try:
            from src.core.drivers.ai_generated_driver import get_ai_driver_generator
            gen = get_ai_driver_generator()
            code = gen.get_driver_code(driver_id)
            if code is None:
                return jsonify({"success": False, "error": "驱动不存在"}), 404
            return jsonify({"success": True, "driver_id": driver_id, "code": code})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def delete_ai_driver(driver_id):
        """删除 AI 生成的驱动"""
        try:
            from src.core.drivers.ai_generated_driver import get_ai_driver_generator
            gen = get_ai_driver_generator()
            success = gen.delete_generated_driver(driver_id)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    app.add_url_rule("/api/devices/ai-driver/generate", view_func=generate_ai_driver, methods=["POST"])
    app.add_url_rule("/api/devices/ai-driver/list", view_func=list_ai_drivers, methods=["GET"])
    app.add_url_rule("/api/devices/ai-driver/<driver_id>/code", view_func=get_ai_driver_code, methods=["GET"])
    app.add_url_rule("/api/devices/ai-driver/<driver_id>", view_func=delete_ai_driver, methods=["DELETE"])

    # ── 模拟设备（测试模式） ─────────────────────────────────────────────
    def load_mock_devices():
        """加载 3 个模拟设备（无需真实硬件）"""
        try:
            from src.core.drivers.mock_driver import get_mock_device_infos
            from src.core.device_manager import get_device_manager
            mgr = get_device_manager()
            # 先加载 mock 驱动
            mgr.load_driver("mock")
            devices = get_mock_device_infos()
            loaded = []
            for info in devices:
                mgr.register_device(info)
                loaded.append({"device_id": info.device_id, "name": info.name, "type": info.device_type})
            return jsonify({"success": True, "loaded": loaded, "message": f"已加载 {len(loaded)} 个模拟设备"})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(e)}), 500

    def unload_mock_devices():
        """卸载所有模拟设备"""
        try:
            from src.core.device_manager import get_device_manager
            mgr = get_device_manager()
            mock_ids = ["mock_light", "mock_sensor", "mock_switch"]
            removed = []
            for dev_id in mock_ids:
                if mgr.get_device(dev_id):
                    mgr.unregister_device(dev_id)
                    removed.append(dev_id)
            return jsonify({"success": True, "removed": removed, "message": f"已卸载 {len(removed)} 个模拟设备"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def test_mock_command():
        """向模拟设备发送测试命令（自动检测设备类型）"""
        try:
            data = request.json or {}
            device_id = data.get("device_id", "")
            command   = data.get("command", "")
            params    = data.get("params", {})
            if not device_id or not command:
                return jsonify({"success": False, "error": "device_id 和 command 必填"}), 400

            from src.core.device_manager import get_device_manager
            mgr = get_device_manager()
            result = mgr.send_command(device_id, command, params)
            import time
            return jsonify({
                "success": result.success,
                "command": result.command,
                "device_id": result.device_id,
                "message": result.message,
                "new_state": result.new_state,
                "timestamp": result.timestamp,
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    app.add_url_rule("/api/devices/mock/load",      view_func=load_mock_devices,    methods=["POST"])
    app.add_url_rule("/api/devices/mock/unload",    view_func=unload_mock_devices,  methods=["POST"])
    app.add_url_rule("/api/devices/mock/test",      view_func=test_mock_command,    methods=["POST"])

    logger.info("[DeviceAPI] 设备 API 路由注册成功（含模拟设备测试端点）")
