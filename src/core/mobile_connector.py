# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
手机联动模块 — 远程连接 + 二维码 + 设备管理

Phase 5 核心功能：
1. 生成连接二维码（内嵌局域网 IP + Token）
2. 设备注册与授权
3. 远程对话代理（手机端通过 API 代理对话）
4. 设备状态监控
"""
import logging
logger = logging.getLogger(__name__)
import os
import sys
import json
import time
import uuid
import hashlib
import secrets
import socket
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MOBILE_DB = DATA_DIR / "sqlite" / "mobile_devices.db"


def _get_db() -> sqlite3.Connection:
    """获取手机设备数据库连接"""
    MOBILE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MOBILE_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_mobile_db():
    """初始化手机设备数据库"""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mobile_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            device_info TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            last_seen_at TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            permissions TEXT DEFAULT 'read,chat'
        );
        CREATE TABLE IF NOT EXISTS mobile_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            session_id TEXT UNIQUE NOT NULL,
            connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            disconnected_at TIMESTAMP,
            ip_address TEXT,
            user_agent TEXT,
            FOREIGN KEY (token) REFERENCES mobile_tokens(token)
        );
        CREATE TABLE IF NOT EXISTS mobile_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            message TEXT NOT NULL,
            title TEXT DEFAULT 'Harven AI',
            data TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP,
            FOREIGN KEY (token) REFERENCES mobile_tokens(token)
        );
        CREATE INDEX IF NOT EXISTS idx_mobile_tokens_active ON mobile_tokens(is_active);
        CREATE INDEX IF NOT EXISTS idx_mobile_sessions_token ON mobile_sessions(token);
        CREATE INDEX IF NOT EXISTS idx_mobile_notifications_token ON mobile_notifications(token, read_at);
    """)
    conn.commit()
    conn.close()


def get_local_ip() -> str:
    """获取局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_local_port() -> int:
    """获取当前服务端口"""
    return int(os.environ.get("HARVEN_PORT", "7000"))  # ★ 2026-07-29: 5000→7000


def _generate_token() -> str:
    """生成设备连接 Token"""
    return secrets.token_urlsafe(24)


def create_mobile_token(name: str = "", lifetime_hours: float = 72) -> dict:
    """
    创建移动设备连接 Token。

    Args:
        name: 设备名称（如"我的手机"）
        lifetime_hours: Token 有效期（小时）

    Returns:
        {"token": str, "expires_at": str, "qr_url": str}
    """
    init_mobile_db()
    conn = _get_db()
    token = _generate_token()
    expires_at = (datetime.now() + timedelta(hours=lifetime_hours)).isoformat()

    conn.execute(
        "INSERT INTO mobile_tokens (token, name, expires_at) VALUES (?, ?, ?)",
        (token, name or "未命名设备", expires_at)
    )
    conn.commit()

    local_ip = get_local_ip()
    port = get_local_port()
    # ★ 2026-08-09: 扫码直接进完整版（不再跳转到简化移动版）
    qr_url = f"http://{local_ip}:{port}/"

    conn.close()
    return {
        "token": token,
        "name": name or "未命名设备",
        "expires_at": expires_at,
        "qr_url": qr_url,
        "local_ip": local_ip,
        "port": port,
    }


def generate_qr_base64(url: str) -> str:
    """生成二维码 base64 PNG（前端用<img>直接渲染）"""
    try:
        import qrcode
        import io, base64
        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.debug(f"[mobile_connector] QR base64生成失败: {e}")
        return ""


def validate_token(token: str) -> Optional[dict]:
    """
    验证 Token 是否有效。

    Returns:
        有效时返回设备信息 dict，无效时返回 None
    """
    init_mobile_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM mobile_tokens WHERE token = ? AND is_active = 1",
        (token,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    device = dict(row)
    # 检查过期
    if device["expires_at"]:
        try:
            expires = datetime.fromisoformat(device["expires_at"])
            if datetime.now() > expires:
                conn.execute(
                    "UPDATE mobile_tokens SET is_active = 0 WHERE token = ?", (token,)
                )
                conn.commit()
                conn.close()
                return None
        except (ValueError, TypeError):
            pass

    # 更新最后活跃时间
    conn.execute(
        "UPDATE mobile_tokens SET last_seen_at = ? WHERE token = ?",
        (datetime.now().isoformat(), token)
    )
    conn.commit()

    # 记录会话
    session_id = uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO mobile_sessions (token, session_id, ip_address) VALUES (?, ?, ?)",
        (token, session_id, "mobile")
    )
    conn.commit()
    conn.close()

    device["session_id"] = session_id
    return device


def revoke_token(token: str) -> bool:
    """吊销 Token"""
    init_mobile_db()
    conn = _get_db()
    conn.execute("UPDATE mobile_tokens SET is_active = 0 WHERE token = ?", (token,))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def list_devices() -> List[dict]:
    """列出所有已授权设备"""
    init_mobile_db()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM mobile_tokens WHERE is_active = 1 ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_device_status() -> dict:
    """获取移动端连接状态"""
    devices = list_devices()
    online_count = sum(
        1 for d in devices
        if d.get("last_seen_at") and
        (datetime.now() - datetime.fromisoformat(d["last_seen_at"])).seconds < 300
    )
    return {
        "total_devices": len(devices),
        "online_devices": online_count,
        "devices": devices,
    }


def proxy_mobile_chat(token: str, message: str, conv_id: str = None,
                      specialization_id: int = None) -> dict:
    """
    移动端代理对话 — 手机发送消息，AI 回复。

    Args:
        token: 设备连接 Token
        message: 用户消息
        conv_id: 对话 ID（可选，自动创建）
        specialization_id: 专项培养 ID

    Returns:
        {"reply": str, "conv_id": str}
    """
    # 验证 Token
    device = validate_token(token)
    if not device:
        return {"error": "无效或过期的连接 Token", "code": "invalid_token"}

    # 权限检查
    permissions = device.get("permissions", "read,chat").split(",")
    if "chat" not in permissions:
        return {"error": "该设备无对话权限", "code": "no_permission"}

    try:
        from src.models.database import create_conversation, save_message
        from src.core.engine import ConversationEngine
        from src.core.memory import get_memory_manager as get_mm

        # 创建或使用已有对话
        if not conv_id:
            conv_id = create_conversation(f"[手机] {device.get('name', '移动设备')}")

        # 保存用户消息
        save_message(conv_id, "user", message)

        # 获取专项提示词
        spec_prompt = ""
        if specialization_id:
            from src.models.database import get_specializations
            for s in get_specializations():
                if s["id"] == specialization_id and s.get("system_prompt"):
                    spec_prompt = s["system_prompt"]
                    break

        # 调用引擎
        mm = get_mm()
        engine = ConversationEngine(mm, conv_id, specialization_prompt=spec_prompt)
        reply_parts = []
        for msg_type, content in engine.send(message, stream=False):
            if msg_type == "text":
                reply_parts.append(content)

        reply = "".join(reply_parts)
        save_message(conv_id, "assistant", reply)

        return {
            "reply": reply,
            "conv_id": conv_id,
            "device_name": device.get("name", ""),
        }
    except Exception as e:
        return {"error": str(e), "code": "engine_error"}


def generate_qr_svg(url: str) -> str:
    """
    生成二维码 SVG（纯 Python，无外部依赖）。

    使用简单的矩阵二维码生成逻辑。
    """
    import math

    # 简化版：生成包含 URL 的纯文本 SVG 占位 + 实际数据 URL
    # 生产环境建议使用 qrcode 库：pip install qrcode[pil]

    # 尝试使用 qrcode 库
    try:
        import qrcode
        import qrcode.image.svg
        import io
        factory = qrcode.image.svg.SvgImage
        img = qrcode.make(url, image_factory=factory)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except ImportError as _e:
        logger.debug(f"[mobile_connector] 忽略: {_e}")

    # 回退：生成简单的数据 URI 占位 SVG
    encoded_url = url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">
  <rect width="200" height="200" fill="white"/>
  <rect x="10" y="10" width="180" height="180" rx="8" fill="#f0f4ff" stroke="#4a7bf7" stroke-width="2"/>
  <text x="100" y="60" text-anchor="middle" font-family="Microsoft YaHei, sans-serif" font-size="14" fill="#333">
    扫描二维码连接
  </text>
  <text x="100" y="80" text-anchor="middle" font-family="Microsoft YaHei, sans-serif" font-size="11" fill="#666">
    Harven AI
  </text>
  <text x="100" y="115" text-anchor="middle" font-family="monospace" font-size="8" fill="#999">
    {encoded_url[:40]}
  </text>
  <text x="100" y="128" text-anchor="middle" font-family="monospace" font-size="8" fill="#999">
    {encoded_url[40:80] if len(encoded_url) > 40 else ""}
  </text>
  <rect x="40" y="140" width="120" height="36" rx="18" fill="#4a7bf7"/>
  <text x="100" y="163" text-anchor="middle" font-family="Microsoft YaHei, sans-serif" font-size="13" fill="white" font-weight="bold">
    点击连接
  </text>
  <text x="100" y="195" text-anchor="middle" font-family="monospace" font-size="7" fill="#ccc">
    Install qrcode for real QR
  </text>
</svg>'''
    return svg


# 模块初始化
init_mobile_db()


# ===== 通知推送功能 =====

def push_notification(token: str, message: str, title: str = "Harven AI", data: dict = None) -> bool:
    """
    向移动设备推送通知。
    
    Args:
        token: 设备 Token
        message: 通知内容
        title: 通知标题
        data: 额外数据（JSON）
    
    Returns:
        是否成功
    """
    init_mobile_db()
    conn = _get_db()
    
    # 验证 Token 是否有效
    device = conn.execute(
        "SELECT id FROM mobile_tokens WHERE token = ? AND is_active = 1", (token,)
    ).fetchone()
    if not device:
        conn.close()
        return False
    
    conn.execute(
        "INSERT INTO mobile_notifications (token, message, title, data) VALUES (?, ?, ?, ?)",
        (token, message, title, json.dumps(data or {}))
    )
    conn.commit()
    conn.close()
    return True


def get_notifications(token: str) -> list:
    """
    获取设备的未读通知。
    
    Args:
        token: 设备 Token
    
    Returns:
        未读通知列表
    """
    init_mobile_db()
    conn = _get_db()
    
    rows = conn.execute(
        "SELECT * FROM mobile_notifications WHERE token = ? AND read_at IS NULL ORDER BY created_at ASC",
        (token,)
    ).fetchall()
    
    notifications = []
    for row in rows:
        n = dict(row)
        try:
            n['data'] = json.loads(n['data'])
        except:
            n['data'] = {}
        notifications.append(n)
    
    conn.close()
    return notifications


def mark_notifications_read(token: str) -> int:
    """
    标记设备的所有通知为已读。
    
    Args:
        token: 设备 Token
    
    Returns:
        标记的数量
    """
    init_mobile_db()
    conn = _get_db()
    
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE mobile_notifications SET read_at = ? WHERE token = ? AND read_at IS NULL",
        (now, token)
    )
    affected = conn.total_changes
    conn.commit()
    conn.close()
    
    return affected
