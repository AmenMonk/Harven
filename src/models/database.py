"""
数据库初始化和模型定义
SQLite 数据库：用户信息、对话、消息、设置、专项培养
"""
import os
import sqlite3
import uuid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# 数据库路径（必须在所有函数之前定义）
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "data", "sqlite", "lingzhi.db"
)


def get_connection(db_path):
    """获取数据库连接（线程安全，WAL模式，锁超时）"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _db_write_with_retry(conn, execute_func, max_retries=3):
    """★ 2026-08-09: 带重试的数据库写操作（修复 database is locked）
    
    Args:
        conn: 数据库连接
        execute_func: 执行写操作的函数（接收conn参数）
        max_retries: 最大重试次数
    
    Returns:
        execute_func的返回值
    
    Raises:
        最后一次重试仍失败的异常
    """
    import time as _time
    last_err = None
    for attempt in range(max_retries):
        try:
            result = execute_func(conn)
            conn.commit()
            return result
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                wait = 0.1 * (attempt + 1)  # 0.1s, 0.2s, 0.3s
                logger.warning(f"[DB] 写锁冲突，{wait:.1f}s后重试({attempt+1}/{max_retries}): {e}")
                _time.sleep(wait)
                continue
            raise  # 非锁冲突，直接抛出
    raise last_err  # 重试耗尽


def init_db(db_path):
    """初始化所有表"""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    # 用户信息表
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            name TEXT NOT NULL DEFAULT '主人',
            master_name TEXT DEFAULT '',
            language TEXT DEFAULT 'zh-CN',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS user_preferences (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS specializations (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            name TEXT NOT NULL,
            icon TEXT DEFAULT '🤖',
            description TEXT DEFAULT '',
            system_prompt TEXT DEFAULT '',
            knowledge_count INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT DEFAULT '新对话',
            specialization_id TEXT,
            model TEXT DEFAULT '',
            category TEXT DEFAULT '',
            is_archived INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (specialization_id) REFERENCES specializations(id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'thinking')),
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cognitive_locks (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            source TEXT DEFAULT 'master',
            locked_at TEXT DEFAULT (datetime('now', 'localtime')),
            priority INTEGER DEFAULT 100
        );

        CREATE TABLE IF NOT EXISTS lockdown_log (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            reason TEXT NOT NULL,
            conversation_id TEXT,
            warning_count INTEGER DEFAULT 0,
            lockdown_level INTEGER DEFAULT 0,
            need_shop_unlock INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_conv_special ON conversations(specialization_id);
        CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);

        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            device_type TEXT DEFAULT 'custom',
            protocol TEXT DEFAULT 'unknown',
            address TEXT DEFAULT '',
            driver_id TEXT DEFAULT '',
            manufacturer TEXT DEFAULT '',
            model TEXT DEFAULT '',
            capabilities TEXT DEFAULT '[]',
            room TEXT DEFAULT '',
            zone TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            status TEXT DEFAULT 'unknown',
            state TEXT DEFAULT '{}',
            permission TEXT DEFAULT 'owner',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS device_commands (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            device_id TEXT NOT NULL,
            command TEXT NOT NULL,
            params TEXT DEFAULT '{}',
            success INTEGER DEFAULT 0,
            message TEXT DEFAULT '',
            timestamp TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (device_id) REFERENCES devices(device_id)
        );

        CREATE TABLE IF NOT EXISTS device_permissions (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            device_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            permission_level TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(device_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_devices_driver ON devices(driver_id);
        CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
        CREATE INDEX IF NOT EXISTS idx_devcmd_device ON device_commands(device_id);
        CREATE INDEX IF NOT EXISTS idx_devperm_device ON device_permissions(device_id);

        -- ★ 2026-07-07: 任务列表表（跨对话保留，用户手动点击执行）
        CREATE TABLE IF NOT EXISTS task_list (
            id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',  -- pending/completed/failed
            source TEXT DEFAULT 'manual',   -- manual/auto_detected
            conversation_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            completed_at TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_task_status ON task_list(status);
        CREATE INDEX IF NOT EXISTS idx_task_created ON task_list(created_at DESC);

        -- ★ 2026-07-21: 邮件缓存表（email_fetch拉取后本地缓存，email_search搜索）
        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,              -- IMAP UID
            account TEXT NOT NULL,            -- 邮箱账号
            folder TEXT DEFAULT 'INBOX',      -- 文件夹
            from_addr TEXT DEFAULT '',        -- 发件人
            to_addr TEXT DEFAULT '',          -- 收件人
            subject TEXT DEFAULT '',          -- 主题
            body_text TEXT DEFAULT '',        -- 正文（纯文本）
            date TEXT DEFAULT '',             -- 邮件日期
            is_read INTEGER DEFAULT 0,        -- 是否已读
            is_important INTEGER DEFAULT 0,   -- 是否重要
            fetched_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_email_account ON emails(account);
        CREATE INDEX IF NOT EXISTS idx_email_date ON emails(date DESC);
        CREATE INDEX IF NOT EXISTS idx_email_subject ON emails(subject);
        """)

    # 兼容旧数据库：添加 category 列
    try:
        cursor.execute("ALTER TABLE conversations ADD COLUMN category TEXT DEFAULT ''")
    except Exception as _e: logger.debug(f"[database] 忽略: {_e}")  # 列已存在则忽略
    
    # category 索引必须在列存在之后创建
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_category ON conversations(category)")
    except Exception as _e: logger.debug(f"[database] 忽略: {_e}")
    
    conn.commit()
    conn.close()
    return True


def create_conversation(title="新对话", specialization_id=None):
    """创建新对话，返回对话ID"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "sqlite", "lingzhi.db"
    )
    conn = get_connection(db_path)
    conv_id = uuid.uuid4().hex[:16]
    # ★ 2026-08-09: 带重试的写操作
    def _do_create(conn):
        conn.execute(
            "INSERT INTO conversations (id, title, specialization_id) VALUES (?, ?, ?)",
            (conv_id, title, specialization_id)
        )
    _db_write_with_retry(conn, _do_create)
    conn.close()
    return conv_id


def save_message(conversation_id, role, content, metadata=None):
    """保存一条消息（主库 + 自动路由到三库隔离）"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "sqlite", "lingzhi.db"
    )
    conn = get_connection(db_path)
    try:
        # ★ 2026-08-09: 带重试的写操作（修复 database is locked）
        def _do_save(conn):
            # ★ 防御：如果 conversation 不存在则自动创建
            existing = conn.execute(
                "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO conversations (id, title) VALUES (?, ?)",
                    (conversation_id, "对话恢复")
                )
                logger.warning(f"[DB] 自动恢复缺失的 conversation: {conversation_id}")

            msg_id = uuid.uuid4().hex[:16]
            import json
            meta_str = json.dumps(metadata or {}, ensure_ascii=False)
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
                (msg_id, conversation_id, role, content, meta_str)
            )
            # 更新对话的 updated_at
            conn.execute(
                "UPDATE conversations SET updated_at = datetime('now', 'localtime') WHERE id = ?",
                (conversation_id,)
            )
            return msg_id

        msg_id = _db_write_with_retry(conn, _do_save)
    finally:
        conn.close()

    # 自动路由到三库隔离（异步后台线程，不影响当前响应速度）
    try:
        from src.core.db_router import route_to_isolated_db
        import threading
        threading.Thread(
            target=route_to_isolated_db,
            args=(content, f"conversation:{conversation_id}", f"{role}:{msg_id}"),
            kwargs={"metadata": {"role": role, "conversation_id": conversation_id}},
            daemon=True
        ).start()
    except Exception as e:
        logger.debug(f"[database] 忽略: {e}")  # 静默失败，不影响主存储

    return msg_id


def get_messages(conversation_id, limit=50):
    """获取对话的消息历史（最近N条）"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "sqlite", "lingzhi.db"
    )
    conn = get_connection(db_path)
    # 先取最近的 N 条（DESC），再反转回 ASC 顺序
    rows = conn.execute(
        "SELECT role, content, metadata, created_at FROM messages "
        "WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
        (conversation_id, limit)
    ).fetchall()
    conn.close()
    result = [dict(r) for r in reversed(rows)]
    return result


def get_conversation(conv_id):
    """获取单个对话"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "sqlite", "lingzhi.db"
    )
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT id, title, specialization_id, model, category, created_at, updated_at "
        "FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_conversations(specialization_id=None, archived=False):
    """获取对话列表"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "sqlite", "lingzhi.db"
    )
    conn = get_connection(db_path)
    if specialization_id:
        rows = conn.execute(
            "SELECT c.id, c.title, s.name as spec_name, s.icon as spec_icon, "
            "c.category, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count "
            "FROM conversations c LEFT JOIN specializations s ON c.specialization_id = s.id "
            "WHERE c.specialization_id = ? AND c.is_archived = ? "
            "ORDER BY c.updated_at DESC",
            (specialization_id, 1 if archived else 0)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.id, c.title, s.name as spec_name, s.icon as spec_icon, "
            "c.category, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count "
            "FROM conversations c LEFT JOIN specializations s ON c.specialization_id = s.id "
            "WHERE c.is_archived = ? "
            "ORDER BY c.updated_at DESC",
            (1 if archived else 0,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_specializations():
    """获取所有专项培养列表"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "sqlite", "lingzhi.db"
    )
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT id, name, icon, description, knowledge_count, message_count, updated_at "
        "FROM specializations ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_specialization(name, icon="🤖", description=""):
    """创建新的专项培养"""
    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "sqlite", "lingzhi.db"
    )
    conn = get_connection(db_path)
    spec_id = uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO specializations (id, name, icon, description) VALUES (?, ?, ?, ?)",
        (spec_id, name, icon, description)
    )
    conn.commit()
    conn.close()
    return spec_id


def delete_conversation(conv_id):
    """删除对话及其所有消息"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    conn.commit()
    conn.close()


def delete_conversations_batch(conv_ids):
    """批量删除对话"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    for cid in conv_ids:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    conn.commit()
    conn.close()


def update_conversation_category(conv_id, category):
    """更新对话分类"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE conversations SET category = ? WHERE id = ?",
        (category, conv_id)
    )
    conn.commit()
    conn.close()


def rename_conversation(conv_id, new_title):
    """重命名对话"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE conversations SET title = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
        (new_title, conv_id)
    )
    conn.commit()
    conn.close()


def archive_conversation(conv_id, archived=True):
    """归档/取消归档对话"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE conversations SET is_archived = ? WHERE id = ?",
        (1 if archived else 0, conv_id)
    )
    conn.commit()
    conn.close()


def search_conversations(keyword):
    """搜索对话（按标题或消息内容）"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT c.id, c.title, c.updated_at, "
        "(SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count "
        "FROM conversations c "
        "LEFT JOIN messages m ON c.id = m.conversation_id "
        "WHERE c.is_archived = 0 AND (c.title LIKE ? OR m.content LIKE ?) "
        "ORDER BY c.updated_at DESC",
        (f"%{keyword}%", f"%{keyword}%")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_messages(keyword, limit=50, date_from=None, date_to=None):
    """
    全局搜索消息内容（跨对话跨天检索）

    Args:
        keyword: 搜索关键词
        limit: 最大返回数
        date_from: 起始日期 (YYYY-MM-DD)
        date_to: 结束日期 (YYYY-MM-DD)

    Returns:
        list: [{"conversation_id": str, "role": str, "content": str, "created_at": str, "conv_title": str}]
    """
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)

    sql = """
        SELECT m.conversation_id, m.role, m.content, m.created_at, c.title as conv_title
        FROM messages m
        LEFT JOIN conversations c ON m.conversation_id = c.id
        WHERE m.content LIKE ?
    """
    params = [f"%{keyword}%"]

    if date_from:
        sql += " AND m.created_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND m.created_at <= ?"
        params.append(date_to + " 23:59:59")

    sql += " ORDER BY m.created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversations_by_date(date_str):
    """获取指定日期的所有对话"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT DISTINCT c.id, c.title, c.updated_at FROM conversations c "
        "INNER JOIN messages m ON c.id = m.conversation_id "
        "WHERE date(m.created_at) = ? "
        "ORDER BY c.updated_at DESC",
        (date_str,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_message_stats():
    """获取消息统计信息"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    try:
        total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        total_convs = conn.execute("SELECT COUNT(*) FROM conversations WHERE is_archived = 0").fetchone()[0]
        today_msgs = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(created_at) = date('now', 'localtime')"
        ).fetchone()[0]
        # 最近7天
        week_msgs = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= datetime('now', 'localtime', '-7 days')"
        ).fetchone()[0]
        return {
            "total_messages": total_msgs,
            "total_conversations": total_convs,
            "today_messages": today_msgs,
            "week_messages": week_msgs,
        }
    finally:
        conn.close()


def update_specialization(spec_id, name=None, icon=None, description=None, system_prompt=None):
    """更新专项培养信息"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if icon is not None:
        updates.append("icon = ?")
        params.append(icon)
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if system_prompt is not None:
        updates.append("system_prompt = ?")
        params.append(system_prompt)
    if updates:
        updates.append("updated_at = datetime('now', 'localtime')")
        params.append(spec_id)
        conn.execute(f"UPDATE specializations SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    conn.close()


def delete_specialization(spec_id):
    """删除专项培养"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    conn.execute("UPDATE conversations SET specialization_id = NULL WHERE specialization_id = ?", (spec_id,))
    conn.execute("DELETE FROM specializations WHERE id = ?", (spec_id,))
    conn.commit()
    conn.close()


def get_user_profile():
    """获取用户信息"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM user_profile LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_profile(name=None, master_name=None, language=None):
    """更新用户信息"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    # 确保有一条记录
    conn.execute("INSERT OR IGNORE INTO user_profile (id) VALUES ('default')")
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if master_name is not None:
        updates.append("master_name = ?")
        params.append(master_name)
    if language is not None:
        updates.append("language = ?")
        params.append(language)
    if updates:
        updates.append("updated_at = datetime('now', 'localtime')")
        conn.execute(f"UPDATE user_profile SET {', '.join(updates)} WHERE id = 'default'", params)
        conn.commit()
    conn.close()


def get_preference(key, default=None):
    """获取用户偏好"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_preference(key, value):
    """设置用户偏好"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO user_preferences (key, value, updated_at) VALUES (?, ?, datetime('now', 'localtime')) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, str(value))
    )
    conn.commit()
    conn.close()


def get_conversation_message_count(conv_id):
    """获取对话消息数量"""
    db_path = os.path.normpath(DEFAULT_DB_PATH)
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?", (conv_id,)
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


# ── 快捷函数 ──


def init_database(db_path=None):
    """初始化数据库（快捷入口）"""
    if db_path is None:
        db_path = os.path.normpath(DEFAULT_DB_PATH)
    return init_db(db_path)
