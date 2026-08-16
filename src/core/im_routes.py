# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
IM 连接 API 路由

端点:
- GET  /api/im/platforms        平台列表
- GET  /api/im/channels         渠道列表
- POST /api/im/channels         添加渠道
- PUT  /api/im/channels/<id>    更新渠道
- DELETE /api/im/channels/<id>  删除渠道
- POST /api/im/channels/<id>/send     发送消息
- POST /api/im/channels/<id>/test     测试连接
- POST /api/im/webhook/<ch_id>        接收 Webhook 回调
- POST /api/im/broadcast              广播消息
- GET  /api/im/health                 全渠道健康检查
"""

import json
import logging
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request

from src.core.im_connector import (
    ChannelConfig,
    broadcast_to_enabled,
    check_all_health,
    get_all_platforms,
    get_channel_manager,
    send_message,
)

logger = logging.getLogger(__name__)

bp = Blueprint("im", __name__, url_prefix="/api/im")


def _validate_channel_body(data: dict) -> tuple:
    """验证渠道请求体"""
    name = (data.get("name") or "").strip()
    platform = (data.get("platform") or "").strip()
    # 标准化平台名：qqbot → qq
    if platform == "qqbot":
        platform = "qq"
    if not name:
        return None, "渠道名称不能为空"
    if not platform:
        return None, "平台类型不能为空"
    if platform not in ("wecom", "dingtalk", "feishu", "qq"):
        return None, f"不支持的平台: {platform}"
    data["platform"] = platform  # 回写标准化后的值
    return data, ""


# ═══════════════════════════════════════════════════════════════════════
# 平台信息
# ═══════════════════════════════════════════════════════════════════════

@bp.route("/platforms", methods=["GET"])
def list_platforms():
    """获取所有支持的 IM 平台"""
    return jsonify({"success": True, "platforms": get_all_platforms()})


# ═══════════════════════════════════════════════════════════════════════
# 渠道管理 CRUD
# ═══════════════════════════════════════════════════════════════════════

@bp.route("/channels", methods=["GET"])
def list_channels():
    """获取所有渠道"""
    mgr = get_channel_manager()
    channels = [ch.to_dict() for ch in mgr.list_all()]
    return jsonify({"success": True, "channels": channels})


@bp.route("/channels", methods=["POST"])
def create_channel():
    """添加渠道"""
    data = request.get_json(force=True, silent=True) or {}
    validated, err = _validate_channel_body(data)
    if err:
        return jsonify({"success": False, "error": err}), 400

    # 支持两种数据格式：
    # A) 扁平格式：{name, platform, webhook_url, secret, token, app_id, ...}
    # B) 嵌套格式：{name, platform, config: {corpid, agentid, secret, ...}}
    nested_config = data.get("config", {})
    if isinstance(nested_config, dict) and nested_config:
        ch_id = data.get("id") or f"ch_{uuid.uuid4().hex[:8]}"
        mgr = get_channel_manager()
        cfg = ChannelConfig(
            id=ch_id,
            name=validated["name"],
            platform=validated["platform"],
            webhook_url=nested_config.get("webhook_url", nested_config.get("token", "")),
            secret=nested_config.get("secret", nested_config.get("appsecret", nested_config.get("app_secret", ""))),
            token=nested_config.get("token", nested_config.get("verification_token", "")),
            app_id=nested_config.get("app_id", nested_config.get("appid", nested_config.get("corpid", nested_config.get("appkey", "")))),
            enabled=data.get("enabled", True),
            created_at=datetime.now().isoformat(),
        )
    else:
        ch_id = data.get("id") or f"ch_{uuid.uuid4().hex[:8]}"
        mgr = get_channel_manager()
        cfg = ChannelConfig(
            id=ch_id,
            name=validated["name"],
            platform=validated["platform"],
            webhook_url=data.get("webhook_url", ""),
            secret=data.get("secret", ""),
            token=data.get("token", ""),
            app_id=data.get("app_id", ""),
            enabled=data.get("enabled", True),
            created_at=datetime.now().isoformat(),
        )

    if mgr.add(cfg):
        logger.info(f"[IM] 创建渠道: {ch_id} ({cfg.platform})")
        return jsonify({"success": True, "channel": cfg.to_dict()})
    return jsonify({"success": False, "error": "渠道 ID 已存在"}), 409


@bp.route("/channels/<ch_id>", methods=["PUT"])
def update_channel(ch_id: str):
    """更新渠道"""
    data = request.get_json(force=True, silent=True) or {}
    mgr = get_channel_manager()
    ch = mgr.get(ch_id)
    if not ch:
        return jsonify({"success": False, "error": "渠道不存在"}), 404

    updatable = ["name", "webhook_url", "secret", "token", "app_id", "enabled", "platform"]
    updates = {k: v for k, v in data.items() if k in updatable and v is not None}

    if mgr.update(ch_id, **updates):
        ch = mgr.get(ch_id)
        return jsonify({"success": True, "channel": ch.to_dict() if ch else {}})
    return jsonify({"success": False, "error": "更新失败"}), 500


@bp.route("/channels/<ch_id>", methods=["DELETE"])
def delete_channel(ch_id: str):
    """删除渠道"""
    mgr = get_channel_manager()
    if mgr.remove(ch_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "渠道不存在"}), 404


@bp.route("/channels/<ch_id>/toggle", methods=["POST"])
def toggle_channel(ch_id: str):
    """启用/禁用渠道"""
    data = request.get_json(force=True, silent=True) or {}
    enabled = data.get("enabled", False)
    mgr = get_channel_manager()
    if mgr.update(ch_id, enabled=enabled):
        ch = mgr.get(ch_id)
        return jsonify({"success": True, "enabled": enabled, "channel": ch.to_dict() if ch else {}})
    return jsonify({"success": False, "error": "渠道不存在"}), 404


@bp.route("/test", methods=["POST"])
def standalone_test():
    """独立测试连接（不保存渠道）"""
    data = request.get_json(force=True, silent=True) or {}
    platform = (data.get("platform") or "").strip()
    if platform == "qqbot":
        platform = "qq"
    config = data.get("config", {})
    if not platform:
        return jsonify({"success": False, "error": "平台类型不能为空"}), 400

    try:
        from src.core.im_connector import AdapterRegistry, _registry as reg
        adapter = reg.get(platform)
        if not adapter:
            # 尝试动态加载适配器
            from src.core.im_connector import _init_adapters
            _init_adapters()
            adapter = reg.get(platform)
        if not adapter:
            return jsonify({"success": False, "error": f"未找到平台适配器: {platform}"}), 500

        # 构造临时 webhook_url 用于测试
        webhook_url = config.get("webhook_url", config.get("token", ""))
        ok, msg = adapter.health_check(webhook_url)
        return jsonify({"success": ok, "message": msg or ("连接正常" if ok else "连接失败"), "platform": platform})
    except Exception as e:
        logger.error(f"[IM] 独立测试失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# 消息操作
# ═══════════════════════════════════════════════════════════════════════

@bp.route("/channels/<ch_id>/send", methods=["POST"])
def send_to_channel(ch_id: str):
    """向指定渠道发送消息"""
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"success": False, "error": "消息内容不能为空"}), 400

    msg_type = data.get("msg_type", "text")
    title = data.get("title", "")
    ok, msg = send_message(ch_id, content, msg_type, title)

    return jsonify({
        "success": ok,
        "message": msg,
        "channel_id": ch_id,
    }), (200 if ok else 500)


@bp.route("/channels/<ch_id>/test", methods=["POST"])
def test_channel(ch_id: str):
    """测试渠道连接"""
    mgr = get_channel_manager()
    ch = mgr.get(ch_id)
    if not ch:
        return jsonify({"success": False, "error": "渠道不存在"}), 404

    from src.core.im_connector import AdapterRegistry
    registry = AdapterRegistry()
    # 使用全局 registry
    from src.core.im_connector import _registry as reg
    adapter = reg.get(ch.platform)
    if not adapter:
        return jsonify({"success": False, "error": f"平台适配器未注册: {ch.platform}"}), 500

    ok, msg = adapter.health_check(ch.webhook_url)
    return jsonify({
        "success": ok,
        "message": msg,
        "channel_id": ch_id,
        "platform": ch.platform,
    })


@bp.route("/broadcast", methods=["POST"])
def broadcast():
    """向所有启用的渠道广播消息"""
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"success": False, "error": "消息内容不能为空"}), 400

    exclude = data.get("exclude", [])
    results = broadcast_to_enabled(content, exclude)

    success_count = sum(1 for ok, _ in results.values() if ok)
    return jsonify({
        "success": True,
        "total": len(results),
        "succeeded": success_count,
        "failed": len(results) - success_count,
        "results": {
            ch_id: {"ok": ok, "msg": msg}
            for ch_id, (ok, msg) in results.items()
        },
    })


# ═══════════════════════════════════════════════════════════════════════
# Webhook 回调接收
# ═══════════════════════════════════════════════════════════════════════

@bp.route("/webhook/<ch_id>", methods=["POST"])
def webhook_callback(ch_id: str):
    """接收 IM 平台的 Webhook 回调"""
    mgr = get_channel_manager()
    ch = mgr.get(ch_id)
    if not ch:
        return jsonify({"code": 404, "msg": "unknown channel"}), 404
    if not ch.enabled:
        return jsonify({"code": 403, "msg": "channel disabled"}), 403

    from src.core.im_connector import _registry as reg
    adapter = reg.get(ch.platform)
    if not adapter:
        return jsonify({"code": 500, "msg": "adapter not found"}), 500

    try:
        # 签名验证
        signature = request.headers.get("X-Signature", "")
        if ch.secret:
            payload = request.get_json(force=True, silent=True) or {}
            if not adapter.verify_signature(payload, signature, ch.secret):
                logger.warning(f"[IM] 签名验证失败: {ch_id}")
                return jsonify({"code": 401, "msg": "signature verification failed"}), 401

        payload = request.get_json(force=True, silent=True) or {}
        msg = adapter.parse_callback(payload, dict(request.headers))

        if msg:
            msg.channel_id = ch_id
            reply = mgr.handle_incoming(msg)
            if reply:
                # 异步回复
                import threading
                def _reply():
                    send_message(ch_id, reply)
                threading.Thread(target=_reply, daemon=True).start()

            logger.info(f"[IM] 收到消息: {ch.platform}/{ch.name} → {msg.content[:50]}")
            return jsonify({"code": 0, "msg": "ok"})

        return jsonify({"code": 0, "msg": "ignored"})

    except Exception as e:
        logger.error(f"[IM] Webhook 处理异常: {e}")
        return jsonify({"code": 500, "msg": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════════════════════════

@bp.route("/health", methods=["GET"])
def health():
    """全渠道健康检查"""
    results = check_all_health()
    healthy = all(r["healthy"] for r in results.values())
    return jsonify({
        "success": True,
        "all_healthy": healthy,
        "channels": results,
    })
