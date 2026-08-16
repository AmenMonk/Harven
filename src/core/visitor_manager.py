# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
访客管理模块 — 访问码校验 + 访客统计

功能：
1. 访问码保护：设置后外部访客必须输入正确码才能进入
2. 访客统计：记录每次访问（IP/UA/时间/消息数），你随时查看
3. 本机/局域网自动免码（不影响日常使用）
"""

import logging
logger = logging.getLogger(__name__)
import sqlite3
import hashlib
import secrets
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

PROJECT_ROOT = Path(__file__).parent.parent.parent
VISITOR_DB = PROJECT_ROOT / "data" / "sqlite" / "visitors.db"

_lock = threading.Lock()


# ===================== DB 初始化 =====================

def _get_db() -> sqlite3.Connection:
    VISITOR_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(VISITOR_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_visitor_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS visitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE NOT NULL,
            ip_address TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            first_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            msg_count   INTEGER DEFAULT 0,
            is_blocked  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS visitor_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,   -- 'visit' / 'chat' / 'blocked'
            detail     TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS access_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_visitors_ip   ON visitors(ip_address);
        CREATE INDEX IF NOT EXISTS idx_visitors_seen ON visitors(last_seen);
        CREATE INDEX IF NOT EXISTS idx_events_sid    ON visitor_events(session_id);
    """)
    conn.commit()
    conn.close()


# ===================== 访问码管理 =====================

def set_access_code(code: str):
    """设置访问码（空字符串 = 关闭保护）"""
    init_visitor_db()
    hashed = _hash_code(code) if code.strip() else ""
    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO access_config(key, value) VALUES('access_code_hash', ?)",
        (hashed,)
    )
    conn.commit()
    conn.close()


def get_access_code_hash() -> str:
    """获取当前访问码 hash，空字符串表示未设置"""
    init_visitor_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT value FROM access_config WHERE key='access_code_hash'"
    ).fetchone()
    conn.close()
    return row["value"] if row else ""


def verify_access_code(code: str) -> bool:
    """校验访问码是否正确"""
    stored = get_access_code_hash()
    if not stored:
        return True   # 未设置访问码，直接放行
    return _hash_code(code) == stored


def is_access_protected() -> bool:
    """是否已启用访问码保护"""
    return bool(get_access_code_hash())


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


# ===================== 会话令牌 =====================

def generate_session_token() -> str:
    """生成访客会话令牌（存入 cookie）"""
    return secrets.token_urlsafe(16)


def register_session(session_id: str, ip: str, ua: str):
    """注册一次新访问"""
    init_visitor_db()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO visitors(session_id, ip_address, user_agent)
               VALUES(?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   last_seen=CURRENT_TIMESTAMP,
                   ip_address=excluded.ip_address""",
            (session_id, ip[:64], ua[:256])
        )
        conn.execute(
            "INSERT INTO visitor_events(session_id, event_type) VALUES(?, 'visit')",
            (session_id,)
        )
        conn.commit()
    except Exception as _e: logger.debug(f"[visitor_manager] 忽略: {_e}")
    finally:
        conn.close()


def record_chat(session_id: str):
    """记录一次对话"""
    init_visitor_db()
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE visitors SET msg_count=msg_count+1, last_seen=CURRENT_TIMESTAMP WHERE session_id=?",
            (session_id,)
        )
        conn.execute(
            "INSERT INTO visitor_events(session_id, event_type) VALUES(?, 'chat')",
            (session_id,)
        )
        conn.commit()
    except Exception as _e: logger.debug(f"[visitor_manager] 忽略: {_e}")
    finally:
        conn.close()


def is_valid_session(session_id: str) -> bool:
    """检查 session 是否已授权"""
    if not session_id:
        return False
    init_visitor_db()
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM visitors WHERE session_id=? AND is_blocked=0",
        (session_id,)
    ).fetchone()
    conn.close()
    return row is not None


# ===================== 统计查询 =====================

def get_stats() -> Dict:
    """汇总统计数据"""
    init_visitor_db()
    conn = _get_db()

    total = conn.execute("SELECT COUNT(*) as c FROM visitors").fetchone()["c"]
    today = conn.execute(
        "SELECT COUNT(*) as c FROM visitors WHERE date(first_seen)=date('now')"
    ).fetchone()["c"]
    online = conn.execute(
        """SELECT COUNT(*) as c FROM visitors
           WHERE last_seen >= datetime('now', '-10 minutes') AND is_blocked=0"""
    ).fetchone()["c"]
    total_msgs = conn.execute(
        "SELECT COALESCE(SUM(msg_count),0) as c FROM visitors"
    ).fetchone()["c"]
    today_msgs = conn.execute(
        """SELECT COALESCE(SUM(msg_count),0) as c FROM visitors
           WHERE date(first_seen)=date('now')"""
    ).fetchone()["c"]

    conn.close()
    return {
        "total_visitors": total,
        "today_visitors": today,
        "online_now": online,
        "total_messages": total_msgs,
        "today_messages": today_msgs,
        "protected": is_access_protected(),
    }


def get_visitor_list(limit: int = 50) -> List[Dict]:
    """获取最近访客列表"""
    init_visitor_db()
    conn = _get_db()
    rows = conn.execute(
        """SELECT session_id, ip_address, user_agent, first_seen, last_seen, msg_count, is_blocked
           FROM visitors ORDER BY last_seen DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def block_visitor(session_id: str):
    """封禁访客"""
    init_visitor_db()
    conn = _get_db()
    conn.execute("UPDATE visitors SET is_blocked=1 WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()


def unblock_visitor(session_id: str):
    """解封访客"""
    init_visitor_db()
    conn = _get_db()
    conn.execute("UPDATE visitors SET is_blocked=0 WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()


# 模块初始化
init_visitor_db()
