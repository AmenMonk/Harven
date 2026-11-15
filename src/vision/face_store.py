# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""★ #1470（2026-11-12）人脸识别数据层 — 家人成员 + 人脸向量（隐私红线物质化）。

铁律（设计稿 v1 第六章，写进代码不靠自觉）:
  - 独立 DB（data/vision/face.db）：与图片索引/相册完全隔离——
    「彻底清除」= 清两张表；生物特征天然不进任何图片备份/Hub 上传白名单
  - 向量只存本地，比对全链路进程内，零网络调用
  - 删成员 = 级联删全部向量 + 面板流水留痕（何时删的）
  - model_tier 隔离：lite(SFace 128维) / pro(高精档 512维) 不互通，切档旧向量拒用
  - 默认关闭：enabled 存 settings 表，首次为 False，设置页显式开启
"""
from __future__ import annotations

import json
import sqlite3
import struct
import threading
from pathlib import Path

from . import config

DB_PATH = Path(config.DATA_DIR) / "face.db"

_init_lock = threading.Lock()
_ensured = False


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure() -> None:
    """建表（import 即就绪模式，health_profile 同款）。幂等。"""
    global _ensured
    if _ensured:
        return
    with _init_lock:
        if _ensured:
            return
        conn = _connect()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS family_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                relation TEXT,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS face_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL REFERENCES family_members(id) ON DELETE CASCADE,
                embedding BLOB NOT NULL,          -- float32 数组序列化
                model_tier TEXT DEFAULT 'lite',   -- lite(SFace) / pro(高精)
                source_photo_id INTEGER,          -- 来源照片（images_index.id），溯源可删
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_face_emb_person ON face_embeddings(person_id);
            CREATE TABLE IF NOT EXISTS face_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,             -- register/delete/wipe/identify
                detail TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            """)
            conn.commit()
        finally:
            conn.close()
        _ensured = True


# ══ 设置（enabled / threshold / tier）════════════════════════════

def get_setting(key: str, default: str = "") -> str:
    ensure()
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    ensure()
    conn = _connect()
    try:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def is_enabled() -> bool:
    return get_setting("enabled", "0") == "1"


def get_threshold() -> float:
    try:
        return float(get_setting("threshold", "0.45"))
    except ValueError:
        return 0.45


def get_tier() -> str:
    t = get_setting("tier", "lite")
    return t if t in ("lite", "pro") else "lite"


# ══ 向量序列化（float32 blob）════════════════════════════════════

def vec_to_blob(vec) -> bytes:
    import numpy as np
    a = np.asarray(vec, dtype=np.float32).ravel()
    return a.tobytes()


def blob_to_vec(blob: bytes):
    import numpy as np
    n = len(blob) // 4
    return np.frombuffer(blob, dtype=np.float32, count=n).copy()


# ══ 成员 CRUD ═══════════════════════════════════════════════════

def add_member(name: str, relation: str = "", note: str = "") -> int:
    ensure()
    conn = _connect()
    try:
        cur = conn.execute("INSERT INTO family_members(name,relation,note) VALUES(?,?,?)",
                           (name.strip(), relation.strip(), note.strip()))
        conn.commit()
        _audit(conn, "register", f"成员「{name}」建档")
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_members() -> list[dict]:
    ensure()
    conn = _connect()
    try:
        rows = conn.execute("""
            SELECT m.id, m.name, m.relation, m.note, m.created_at,
                   COUNT(e.id) AS emb_count,
                   MAX(e.created_at) AS last_enrolled
            FROM family_members m
            LEFT JOIN face_embeddings e ON e.person_id = m.id
            GROUP BY m.id ORDER BY m.id
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_member(person_id: int) -> dict | None:
    ensure()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM family_members WHERE id=?", (person_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_member(person_id: int) -> dict:
    """删成员=级联删全部向量（FK ON DELETE CASCADE）+ 留痕。返回删除的向量数。"""
    ensure()
    conn = _connect()
    try:
        m = conn.execute("SELECT name FROM family_members WHERE id=?", (person_id,)).fetchone()
        if not m:
            return {"ok": False, "error": "成员不存在"}
        n = conn.execute("SELECT COUNT(*) AS c FROM face_embeddings WHERE person_id=?",
                         (person_id,)).fetchone()["c"]
        conn.execute("DELETE FROM face_embeddings WHERE person_id=?", (person_id,))
        conn.execute("DELETE FROM family_members WHERE id=?", (person_id,))
        _audit(conn, "delete", f"删除成员「{m['name']}」(id={person_id})，清除向量 {n} 条")
        conn.commit()
        return {"ok": True, "removed_embeddings": n}
    finally:
        conn.close()


# ══ 向量存取 ═════════════════════════════════════════════════════

def add_embeddings(person_id: int, vectors: list, tier: str,
                   source_photo_ids: list[int | None] | None = None) -> int:
    ensure()
    conn = _connect()
    try:
        src = list(source_photo_ids or [None] * len(vectors))
        rows = [(person_id, vec_to_blob(v), tier,
                 src[i] if i < len(src) else None) for i, v in enumerate(vectors)]
        conn.executemany("INSERT INTO face_embeddings(person_id,embedding,model_tier,source_photo_id) "
                         "VALUES(?,?,?,?)", rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def count_embeddings(person_id: int | None = None, tier: str | None = None) -> int:
    ensure()
    conn = _connect()
    try:
        q, args = "SELECT COUNT(*) AS c FROM face_embeddings", []
        if person_id is not None:
            q += " WHERE person_id=?"
            args.append(person_id)
        if tier:
            q += (" AND" if "WHERE" in q else " WHERE") + " model_tier=?"
            args.append(tier)
        return conn.execute(q, args).fetchone()["c"]
    finally:
        conn.close()


def load_embeddings(tier: str) -> list[dict]:
    """取某档全部向量（比对在内存做，家庭规模 <100 条毫秒级）。"""
    ensure()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT e.person_id, m.name, e.embedding FROM face_embeddings e "
            "JOIN family_members m ON m.id=e.person_id WHERE e.model_tier=?",
            (tier,)).fetchall()
        return [{"person_id": r["person_id"], "name": r["name"],
                 "vec": blob_to_vec(r["embedding"])} for r in rows]
    finally:
        conn.close()


# ══ 一键清除（隐私核按钮）════════════════════════════════════════

def wipe_all(keep_members: bool = False) -> dict:
    """彻底清除所有人脸数据。默认连成员档案一起清（生物特征零残留）。"""
    ensure()
    conn = _connect()
    try:
        n_emb = conn.execute("SELECT COUNT(*) AS c FROM face_embeddings").fetchone()["c"]
        n_mem = conn.execute("SELECT COUNT(*) AS c FROM family_members").fetchone()["c"]
        conn.execute("DELETE FROM face_embeddings")
        if not keep_members:
            conn.execute("DELETE FROM family_members")
        _audit(conn, "wipe", f"一键清除：向量 {n_emb} 条" +
               ("" if keep_members else f"，成员档案 {n_mem} 条"))
        conn.commit()
        return {"ok": True, "wiped_embeddings": n_emb, "wiped_members": 0 if keep_members else n_mem}
    finally:
        conn.close()


def _audit(conn, action: str, detail: str) -> None:
    """审计留痕（生物特征操作可追溯）。注意：调用方负责 commit（除独立调用）。"""
    try:
        conn.execute("INSERT INTO face_audit(action,detail) VALUES(?,?)", (action, detail))
        conn.commit()
    except Exception:
        pass


def audit_log(limit: int = 30) -> list[dict]:
    ensure()
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM face_audit ORDER BY id DESC LIMIT ?",
                            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
