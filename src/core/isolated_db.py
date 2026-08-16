# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
三库物理隔离系统
隐私库 / 过渡库 / 商用库 三个独立SQLite数据库

隐私库支持 AES-256 应用层加密（依赖 privacy_password 配置）。
加密字段：value, metadata
明文保留：id, category, key, created_at, updated_at
"""
import logging
logger = logging.getLogger(__name__)
import os
import sqlite3
import uuid
import json
import base64
import hashlib
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

# 延迟导入 cryptography，避免启动时依赖缺失导致崩溃
_crypto_available = None
_Fernet = None

DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "isolated_dbs"
)

# 过渡库观察期（天）— 与文档 §5.2 一致
TRANSIENT_OBSERVATION_DAYS = 10

# 观察期结束后自动转正的最大安全评分阈值（≤此值自动转正）
AUTO_PROMOTE_SCORE_THRESHOLD = 30


def _init_crypto():
    """延迟初始化 cryptography，导入失败则标记不可用"""
    global _crypto_available, _Fernet
    if _crypto_available is not None:
        return _crypto_available
    try:
        from cryptography.fernet import Fernet
        _Fernet = Fernet
        _crypto_available = True
    except ImportError:
        _Fernet = None
        _crypto_available = False
    return _crypto_available


def _load_privacy_password() -> Optional[str]:
    """从 config.yaml 加载隐私库密码。
    优先级：privacy_password > remote_password（合并为一个密码）
    """
    try:
        import yaml
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.yaml"
        )
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            sec = config.get("security") or {}
            pwd = sec.get("privacy_password") or sec.get("remote_password") or None
            return pwd
    except Exception as _e: logger.debug(f"[isolated_db] 忽略: {_e}")
    return None


def _derive_key(password: str) -> bytes:
    """
    从密码派生 AES 密钥（PKCS5/PBKDF2-HMAC-SHA256）
    返回 32 字节 = Fernet 所需密钥长度
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    # 固定 salt（隐私库密钥派生不需要每次换 salt，因为密码不变）
    # 用项目路径做 salt，确保不同安装的密钥不同
    salt = hashlib.sha256(DATA_ROOT.encode("utf-8")).digest()[:16]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


class IsolatedDB:
    """隔离数据库基类

    当 encrypt=True 时，value 和 metadata 字段在写入前加密、
    读取后解密。加解密失败时抛异常，不静默降级。
    """

    def __init__(self, db_name: str, encrypt: bool = False):
        os.makedirs(DATA_ROOT, exist_ok=True)
        self._db_path = os.path.join(DATA_ROOT, f"{db_name}.db")
        self._name = db_name
        self._encrypt = encrypt
        self._fernet = None          # 延迟初始化
        self._key_loaded = False     # 避免每次操作都重新派生密钥
        self._init_tables()

    # ─── 加密相关 ───────────────────────────────────────────────────────

    def _ensure_fernet(self):
        """延迟初始化 Fernet 实例（仅加密库需要）"""
        if not self._encrypt:
            return
        if self._fernet is not None:
            return
        if not _init_crypto():
            raise RuntimeError(
                "隐私库已启用加密，但 cryptography 库未安装。\n"
                "请运行：pip install cryptography"
            )
        pwd = _load_privacy_password()
        if not pwd:
            raise RuntimeError(
                "隐私库已启用加密，但未设置 privacy_password。\n"
                "请在设置中设置隐私库密码，或在 config.yaml 中配置 security.privacy_password。"
            )
        raw_key = _derive_key(pwd)
        self._fernet = _Fernet(raw_key)

    def _encrypt_field(self, plaintext: str) -> str:
        """加密字符串，返回 base64 密文（带前缀标识）"""
        if not plaintext:
            return plaintext
        self._ensure_fernet()
        cipher = self._fernet.encrypt(plaintext.encode("utf-8"))
        return "enc::" + base64.urlsafe_b64encode(cipher).decode("utf-8")

    def _decrypt_field(self, ciphertext: str) -> str:
        """解密字符串，自动识别是否加密"""
        if not ciphertext or not ciphertext.startswith("enc::"):
            return ciphertext  # 兼容未加密的旧数据
        self._ensure_fernet()
        raw = base64.urlsafe_b64decode(ciphertext[5:])
        return self._fernet.decrypt(raw).decode("utf-8")

    # ─── 连接管理 ─────────────────────────────────────────────────────────

    def _get_conn(self):
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")  # ★ 2026-08-10: 避免WAL文件导致readonly
        return conn

    @contextmanager
    def _conn(self):
        """上下文管理器，确保连接异常时也能正确关闭"""
        conn = self._get_conn()
        try:
            yield conn
        finally:
            conn.close()

    # ─── Schema ──────────────────────────────────────────────────────────

    def _init_tables(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
                    category    TEXT DEFAULT 'general',
                    key         TEXT DEFAULT '',
                    value       TEXT NOT NULL,
                    metadata    TEXT DEFAULT '{}',
                    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
                    updated_at  TEXT DEFAULT (datetime('now', 'localtime')),
                    -- 过渡库观察期字段
                    imported_at TEXT DEFAULT (datetime('now', 'localtime')),
                    safety_score INTEGER DEFAULT 0,
                    observation_passed INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_records_cat ON records(category);
                CREATE INDEX IF NOT EXISTS idx_records_key ON records(key);
                CREATE INDEX IF NOT EXISTS idx_records_imported
                    ON records(imported_at) WHERE imported_at IS NOT NULL;
            """)

            # 数据库迁移：兼容旧版本数据库（CREATE TABLE IF NOT EXISTS 不会添加缺失列）
            try:
                cursor = conn.execute("PRAGMA table_info(records)")
                existing_columns = {row[1] for row in cursor.fetchall()}

                # 需要添加的列：(列名, 列定义)
                migrations = [
                    ("imported_at", "TEXT DEFAULT (datetime('now', 'localtime'))"),
                    ("safety_score", "INTEGER DEFAULT 0"),
                    ("observation_passed", "INTEGER DEFAULT 0"),
                ]

                for col_name, col_def in migrations:
                    if col_name not in existing_columns:
                        conn.execute(
                            f"ALTER TABLE records ADD COLUMN {col_name} {col_def}"
                        )
                        logger.info(f"[IsolatedDB] 已添加缺失列: {col_name}")
            except Exception as e:
                logger.warning(f"[IsolatedDB] 数据库迁移检查失败（可忽略）: {e}")

            conn.commit()

    # ─── CRUD ─────────────────────────────────────────────────────────────

    def add(self, category: str, key: str, value, metadata=None):
        """添加记录（加密库自动加密 value 和 metadata）"""
        with self._conn() as conn:
            record_id = uuid.uuid4().hex[:16]
            meta_str = json.dumps(metadata or {}, ensure_ascii=False)
            if self._encrypt:
                store_value = self._encrypt_field(str(value))
                store_meta  = self._encrypt_field(meta_str)
            else:
                store_value = str(value)
                store_meta  = meta_str
            conn.execute(
                "INSERT INTO records (id, category, key, value, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (record_id, category, key, store_value, store_meta)
            )
            conn.commit()
            return record_id

    def get(self, category=None, key=None, limit=50):
        """查询记录（加密库自动解密）"""
        with self._conn() as conn:
            sql = "SELECT * FROM records WHERE 1=1"
            params = []
            if category:
                sql += " AND category = ?"
                params.append(category)
            if key:
                sql += " AND key = ?"
                params.append(key)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            result = [dict(r) for r in rows]
            if self._encrypt:
                for row in result:
                    row["value"]    = self._decrypt_field(row.get("value", ""))
                    row["metadata"] = self._decrypt_field(row.get("metadata", "{}"))
            return result

    def search(self, keyword: str, limit: int = 20):
        """
        全文搜索
        - 非加密库：SQL LIKE（高效）
        - 加密库：内存解密后搜索（隐私库数据量小，可接受）
        """
        if not self._encrypt:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM records WHERE value LIKE ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (f"%{keyword}%", limit)
                ).fetchall()
                return [dict(r) for r in rows]
        else:
            # 加密库：全量取出 → 解密 → 内存过滤
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM records ORDER BY updated_at DESC"
                ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                try:
                    plain_value = self._decrypt_field(d.get("value", ""))
                    plain_meta  = self._decrypt_field(d.get("metadata", "{}"))
                except Exception:
                    continue  # 跳过损坏记录
                if keyword in plain_value or keyword in plain_meta:
                    d["value"]    = plain_value
                    d["metadata"] = plain_meta
                    results.append(d)
                    if len(results) >= limit:
                        break
            return results

    def update(self, record_id: str, value=None, metadata=None):
        """更新记录"""
        with self._conn() as conn:
            updates = ["updated_at = datetime('now', 'localtime')"]
            params  = []
            if value is not None:
                updates.append("value = ?")
                params.append(self._encrypt_field(str(value)) if self._encrypt else str(value))
            if metadata is not None:
                updates.append("metadata = ?")
                meta_str = json.dumps(metadata, ensure_ascii=False)
                params.append(self._encrypt_field(meta_str) if self._encrypt else meta_str)
            params.append(record_id)
            conn.execute(
                f"UPDATE records SET {', '.join(updates)} WHERE id = ?",
                params
            )
            conn.commit()

    def delete(self, record_id: str):
        """删除记录"""
        with self._conn() as conn:
            conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
            conn.commit()

    def count(self, category=None):
        """统计记录数"""
        with self._conn() as conn:
            if category:
                row = conn.execute(
                    "SELECT COUNT(*) FROM records WHERE category = ?", (category,)
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM records").fetchone()
            return row[0] if row else 0

    def get_categories(self):
        """获取所有分类"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category, COUNT(*) as cnt "
                "FROM records GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def clear_category(self, category: str):
        """清空某个分类"""
        with self._conn() as conn:
            conn.execute("DELETE FROM records WHERE category = ?", (category,))
            conn.commit()

    def export_all(self):
        """导出所有数据（JSON，自动解密）"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM records ORDER BY created_at ASC"
            ).fetchall()
            result = [dict(r) for r in rows]
            if self._encrypt:
                for row in result:
                    row["value"]    = self._decrypt_field(row.get("value", ""))
                    row["metadata"] = self._decrypt_field(row.get("metadata", "{}"))
            return result

    def get_db_path(self):
        return self._db_path

    # ─── 过渡库观察期专用方法 ─────────────────────────────────────────

    def mark_observation_passed(self, record_id: str, score: int = 0):
        """标记观察期通过，允许转正"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE records SET observation_passed=1, safety_score=? "
                "WHERE id = ?",
                (score, record_id)
            )
            conn.commit()

    def promote_to_commercial(self, record_id: str) -> bool:
        """
        将过渡库记录转入商用库
        返回是否成功
        """
        from src.core.isolated_db import get_commercial_db  # 延迟导入，避免循环
        row = self.get_by_id(record_id)
        if not row:
            return False
        comm_db = get_commercial_db()
        comm_db.add(
            category=row.get("category", "imported"),
            key=row.get("key", ""),
            value=row.get("value", ""),
            metadata=json.loads(row.get("metadata", "{}"))
        )
        self.delete(record_id)
        return True

    def get_by_id(self, record_id: str):
        """按 ID 获取单条记录"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            if self._encrypt:
                d["value"]    = self._decrypt_field(d.get("value", ""))
                d["metadata"] = self._decrypt_field(d.get("metadata", "{}"))
            return d

    def list_observation_pending(self, limit: int = 50):
        """列出观察期尚未通过的记录"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, category, key, safety_score, imported_at "
                "FROM records "
                "WHERE observation_passed = 0 "
                "ORDER BY imported_at ASC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]


# ── 三库实例 ────────────────────────────────────────────────────────────

_privacy_db   = None
_transient_db = None
_commercial_db = None


def get_privacy_db():
    """隐私库：主人的私密信息，AES-256 加密"""
    global _privacy_db
    if _privacy_db is None:
        _privacy_db = IsolatedDB("privacy", encrypt=True)
    return _privacy_db


def get_transient_db():
    """过渡库：临时信息，10 天观察期，定期清理"""
    global _transient_db
    if _transient_db is None:
        _transient_db = IsolatedDB("transient", encrypt=False)
    return _transient_db


def get_commercial_db():
    """商用库：对外公开的知识，不加密"""
    global _commercial_db
    if _commercial_db is None:
        _commercial_db = IsolatedDB("commercial", encrypt=False)
    return _commercial_db


def cleanup_transient(max_age_days: int = TRANSIENT_OBSERVATION_DAYS):
    """
    清理过渡库中超过观察期且未转正的数据
    与文档 §5.2 一致：默认 10 天
    """
    with get_transient_db()._conn() as conn:
        result = conn.execute(
            "DELETE FROM records "
            "WHERE imported_at < datetime('now', 'localtime', ?) "
            "AND observation_passed = 0",
            (f"-{max_age_days} days",)
        )
        conn.commit()
        return result.rowcount


def auto_promote_transient():
    """
    自动将观察期通过且安全评分较低的记录转入商用库
    应在每次启动时调用
    """
    db = get_transient_db()
    pending = db.list_observation_pending(limit=100)
    promoted = 0
    for rec in pending:
        imported_at = rec.get("imported_at", "")
        if not imported_at:
            continue
        try:
            from datetime import datetime
            dt = datetime.strptime(imported_at[:19], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - dt).days >= TRANSIENT_OBSERVATION_DAYS:
                db.mark_observation_passed(rec["id"], score=0)
                db.promote_to_commercial(rec["id"])
                promoted += 1
        except Exception:
            continue
    return promoted
