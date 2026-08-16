# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
硬件安全模块 — 设备绑定 + 防克隆 + 硬件指纹

Phase 6+ 核心功能：
1. 设备指纹生成（CPU + 主板 + MAC + 磁盘序列号）
2. 设备绑定与解绑
3. 克隆检测（硬件指纹变更告警）
4. 安全等级评估

设计原则：
- 纯 Python 实现（可后续用 C 编译为 .pyd 作为安全核心）
- 零网络依赖，纯本地校验
- 指纹哈希不可逆
"""
import logging
logger = logging.getLogger(__name__)
import os
import sys
import json
import uuid
import hashlib
import platform
import subprocess
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SECURITY_DB = DATA_DIR / "sqlite" / "device_security.db"


def _get_db() -> sqlite3.Connection:
    SECURITY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SECURITY_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_security_db():
    """初始化安全数据库"""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bound_devices (
            id TEXT PRIMARY KEY,
            fingerprint_hash TEXT UNIQUE NOT NULL,
            name TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            cpu_info TEXT DEFAULT '',
            bind_code TEXT DEFAULT '',
            is_current INTEGER DEFAULT 0,
            bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_verified_at TIMESTAMP,
            clone_warning INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS security_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            device_id TEXT,
            description TEXT,
            severity TEXT DEFAULT 'info',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_bound_fingerprint ON bound_devices(fingerprint_hash);
        CREATE INDEX IF NOT EXISTS idx_security_events_device ON security_events(device_id);
    """)
    conn.commit()
    conn.close()


def _get_cpu_serial() -> str:
    """获取 CPU 序列号"""
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["wmic", "cpu", "get", "ProcessorId"],
                capture_output=True, text=True, timeout=10
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and "ProcessorId" not in l]
            return lines[0] if lines else ""
        elif system == "Linux":
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "Serial" in line:
                        return line.split(":")[1].strip()
            return ""
        elif system == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip()
    except Exception as _e: logger.debug(f"[hw_security] 忽略: {_e}")
    return ""


def _get_motherboard_serial() -> str:
    """获取主板序列号"""
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["wmic", "baseboard", "get", "SerialNumber"],
                capture_output=True, text=True, timeout=10
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and "SerialNumber" not in l]
            return lines[0] if lines else ""
        elif system == "Linux":
            result = subprocess.run(
                ["dmidecode", "-s", "baseboard-serial-number"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip()
    except Exception as _e: logger.debug(f"[hw_security] 忽略: {_e}")
    return ""


def _get_mac_addresses() -> str:
    """获取 MAC 地址列表"""
    import uuid as _uuid
    try:
        node = _uuid.getnode()
        mac = ':'.join(f"{(node >> (i * 8)) & 0xff:02x}" for i in range(5, -1, -1))
        return mac
    except Exception:
        return ""


def _get_disk_serial() -> str:
    """获取系统盘序列号"""
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["wmic", "diskdrive", "where", "Index=0", "get", "SerialNumber"],
                capture_output=True, text=True, timeout=10
            )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip() and "SerialNumber" not in l]
            return lines[0] if lines else ""
        elif system == "Linux":
            result = subprocess.run(
                ["lsblk", "-o", "SERIAL", "-n", "-d", "/dev/sda"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip()
    except Exception as _e: logger.debug(f"[hw_security] 忽略: {_e}")
    return ""


def generate_device_fingerprint() -> str:
    """
    生成设备指纹哈希。

    采集维度：
    - CPU 序列号
    - 主板序列号
    - MAC 地址
    - 磁盘序列号
    - 操作系统 + 主机名

    使用 SHA-256 不可逆哈希。
    """
    components = [
        _get_cpu_serial(),
        _get_motherboard_serial(),
        _get_mac_addresses(),
        _get_disk_serial(),
        platform.system() + platform.node(),
    ]

    raw = "|".join(components)
    if not raw.strip("|"):
        # 回退：使用 platform 基础信息
        raw = f"{platform.system()}|{platform.node()}|{platform.processor()}|{platform.machine()}"

    return hashlib.sha256(raw.encode()).hexdigest()


def get_device_security_status() -> dict:
    """
    获取当前设备的完整安全状态。

    Returns:
        {
            "fingerprint_hash": str,
            "is_bound": bool,
            "bound_devices": list,
            "clone_warnings": list,
            "security_level": "high"|"medium"|"low"|"unbound"
        }
    """
    init_security_db()
    conn = _get_db()

    current_fp = generate_device_fingerprint()

    # 检查是否已绑定
    bound = conn.execute(
        "SELECT * FROM bound_devices WHERE fingerprint_hash = ? AND is_current = 1",
        (current_fp,)
    ).fetchone()

    # 所有绑定设备
    all_bound = conn.execute(
        "SELECT id, name, platform, fingerprint_hash, bound_at, clone_warning FROM bound_devices ORDER BY bound_at DESC"
    ).fetchall()

    # 克隆警告
    warnings = conn.execute(
        "SELECT * FROM bound_devices WHERE clone_warning = 1"
    ).fetchall()

    conn.close()

    # 安全等级
    if bound:
        security_level = "high"
    elif all_bound:
        security_level = "medium"  # 有绑定但不是当前设备
    else:
        security_level = "unbound"

    return {
        "fingerprint_hash": current_fp[:16] + "...",
        "is_bound": bool(bound),
        "current_device": dict(bound) if bound else None,
        "bound_devices": [dict(d) for d in all_bound],
        "clone_warnings": [dict(w) for w in warnings],
        "security_level": security_level,
    }


def bind_current_device(bind_code: str = "") -> dict:
    """
    【已禁用】设备绑定功能已移除。
    方案A：不绑定，只记录硬件指纹用于安全日志和诊断。
    保留函数签名以避免导入错误，但不再执行任何绑定操作。
    """
    return {
        "success": False,
        "error": "设备绑定功能已禁用。Harven 采用方案A：不绑定硬件，只记录变更用于诊断。",
    }


def unbind_device(device_id: str) -> dict:
    """【已禁用】设备解绑功能已移除。"""
    return {
        "success": False,
        "error": "设备绑定功能已禁用，无需解绑。",
    }


def verify_device_integrity() -> Tuple[bool, str]:
    """
    设备硬件变更检测 — 启动时调用（方案A）。

    不再绑定/锁死设备，只记录硬件变更用于安全日志。
    无论硬件是否变更，均返回 (True, message)，不阻止程序运行。

    返回 (True, message)
    """
    init_security_db()
    conn = _get_db()

    current_fp = generate_device_fingerprint()
    current_node = platform.node()

    # 查询历史指纹记录（用于对比，不用于锁定）
    prev_records = conn.execute(
        "SELECT * FROM bound_devices ORDER BY bound_at DESC LIMIT 5"
    ).fetchall()

    # 检查是否有指纹变更
    fingerprint_changed = False
    for rec in prev_records:
        if rec["fingerprint_hash"] != current_fp:
            fingerprint_changed = True
            break

    if fingerprint_changed:
        # 记录硬件变更事件（只记录，不锁死）
        event_id = uuid.uuid4().hex[:16]
        conn.execute(
            "INSERT INTO security_events (id, event_type, device_id, description, severity) VALUES (?, 'hardware_change', ?, ?, 'info')",
            (event_id, rec["id"] if prev_records else "",
             f"硬件变更检测: {current_node} 指纹已变更（可能更换了硬件或重装系统）。当前指纹: {current_fp[:16]}...")
        )
        conn.commit()
        conn.close()
        return True, f"⚠️ 检测到硬件变更（指纹不匹配），已记录到安全日志。Harven 继续正常运行。"

    # 无变更或首次运行：记录当前指纹到 bound_devices（仅用于日志）
    existing = conn.execute(
        "SELECT * FROM bound_devices WHERE fingerprint_hash = ?", (current_fp,)
    ).fetchone()

    if not existing:
        device_id = uuid.uuid4().hex[:16]
        conn.execute(
            """INSERT INTO bound_devices
               (id, fingerprint_hash, name, platform, cpu_info, is_current, last_verified_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (device_id, current_fp, current_node, platform.system(),
             _get_cpu_serial()[:50], datetime.now().isoformat())
        )
        conn.commit()

    conn.close()
    return True, "设备硬件状态正常"


def get_device_fingerprint() -> dict:
    """
    获取当前设备指纹的详细信息（供管理后台展示用）。

    Returns:
        {
            "hash": str,           # 指纹哈希（取前32位展示）
            "cpu": str,            # CPU 信息
            "motherboard": str,    # 主板信息
            "mac": str,            # MAC 地址
            "bound": bool,         # 是否已绑定记录
        }
    """
    init_security_db()
    current_fp = generate_device_fingerprint()
    conn = _get_db()
    existing = conn.execute(
        "SELECT * FROM bound_devices WHERE fingerprint_hash = ?", (current_fp,)
    ).fetchone()
    conn.close()

    return {
        "hash": current_fp,
        "cpu": _get_cpu_serial(),
        "motherboard": _get_motherboard_serial(),
        "mac": _get_mac_addresses(),
        "bound": existing is not None,
    }


def get_security_events(limit: int = 50) -> list:
    """
    获取安全事件记录。

    Returns:
        List of {timestamp, level, message}
    """
    init_security_db()
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT created_at, event_type, details FROM security_events ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        events = []
        for row in rows:
            events.append({
                "timestamp": row["created_at"],
                "level": "WARN" if "变更" in (row["details"] or "") else "INFO",
                "message": row["details"] or row["event_type"] or "安全事件",
            })
        return events
    except Exception:
        return []
    finally:
        conn.close()


# 模块初始化
init_security_db()

