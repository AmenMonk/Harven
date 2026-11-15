"""视觉索引库存储 — SQLite + FTS5(关键词) + sqlite-vec(向量)。

⭐ 零原图入库铁律（第五章 catalog 模式）：
  库里只有 ①指纹(dhash) ②512维向量 ③标签+识别结果+引擎版本 ④缩略图+原图路径指针
  原图真家 = 指针指向；失指图保留 30 天（LOST_POINTER_TTL_DAYS）。

分级入库（第五章）:
  index     L1/L2 高置信图 —— 只写索引，不进训练池（零信息样本）
  candidate L3 疑难图     —— 候选标注池（训练资产）
  gold      用户纠错图     —— 金样本池（最高价值训练资产）
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import numpy as np

from . import config
from .dedup import hamming


class CatalogStore:
    def __init__(self, db_path: Path | str = config.DB_PATH):
        self.db_path = str(db_path)
        # ★ v1.3 融合: 新装机 data/vision/ 不存在 → 自建（旧独立进程时代目录总在，融合后必踩）
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._dhash_cache: list[tuple[int, np.ndarray]] | None = None  # L0 近重查缓存
        self.ensure_schema()

    # ── 连接与建表 ───────────────────────────────────────────────
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                import sqlite_vec
                conn.enable_load_extension(True)   # sqlite_vec.load 不自带 enable
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            except (ImportError, sqlite3.Error):
                pass    # 向量检索不可用时降级为纯 FTS5（13.6 无卡降级链）
            self._conn = conn
        return self._conn

    def ensure_schema(self) -> None:
        c = self.conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dhash       TEXT NOT NULL UNIQUE,        -- 16位hex指纹
            pointer     TEXT NOT NULL,               -- 原图路径指针（原图真家）
            thumb_path  TEXT,                        -- 缩略图路径
            level       TEXT NOT NULL DEFAULT 'index',   -- index|candidate|gold
            tags        TEXT DEFAULT '',             -- 逗号分隔中文标签
            description TEXT DEFAULT '',             -- L3 文字描述
            detections  TEXT DEFAULT '[]',           -- L1 检出 JSON
            engine_ver  TEXT DEFAULT '',             -- 引擎版本溯源
            vec_rowid   INTEGER,                     -- sqlite-vec 行号
            status      TEXT DEFAULT 'ok',           -- ok|lost_pointer
            created_at  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_level ON items(level);
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            tags, description, tokenize='unicode61'
        );
        """)
        # 向量表：维度从配置读（CLIP vit-base = 512）
        try:
            c.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0("
                f"embedding float[{config.VECTOR_DIM}])"
            )
            self._vec_ok = True
        except sqlite3.OperationalError:
            self._vec_ok = False    # sqlite-vec 扩展加载失败
        c.commit()

    # ── L0 近重检查（dhash 汉明）────────────────────────────────
    def _load_dhash_cache(self) -> list[tuple[int, np.ndarray]]:
        if self._dhash_cache is None:
            rows = self.conn.execute("SELECT id, dhash FROM items").fetchall()
            self._dhash_cache = [
                (rid, np.array([b == "1" for b in f"{int(h, 16):064b}"]))
                for rid, h in rows
            ]
        return self._dhash_cache

    def find_near_duplicate(self, h: np.ndarray, threshold: int = 10) -> tuple[int, float] | None:
        """返回 (item_id, 距离) 或 None。缓存失效在 add/delete 时维护。"""
        for rid, cached in self._load_dhash_cache():
            d = hamming(h, cached)
            if d <= threshold:
                return rid, d
        return None

    # ── 缩略图（零原图铁律下的唯一图像落盘）────────────────────
    @staticmethod
    def make_thumb(image_path: str) -> str:
        from PIL import Image
        config.THUMB_DIR.mkdir(parents=True, exist_ok=True)
        img = Image.open(image_path).convert("RGB")
        img.thumbnail((config.THUMB_SIZE, config.THUMB_SIZE), Image.LANCZOS)
        out = config.THUMB_DIR / f"{Path(image_path).stem}_{abs(hash(image_path)) % 99999}.jpg"
        img.save(out, quality=80)
        return str(out)

    # ── 入库（分级）────────────────────────────────────────────
    def add_item(self, dhash_hex: str, pointer: str, vector: np.ndarray | None,
                 level: str = "index", tags: str = "", description: str = "",
                 detections: list | None = None, engine_ver: str = "") -> int:
        c = self.conn
        vec_rowid = None
        if vector is not None and self._vec_ok:
            cur = c.execute(
                "INSERT INTO vec_items(embedding) VALUES (?)",
                (np.asarray(vector, dtype=np.float32).tobytes(),),
            )
            vec_rowid = cur.lastrowid
        thumb = self.make_thumb(pointer) if Path(pointer).exists() else None
        now = time.time()
        cur = c.execute(
            "INSERT INTO items(dhash, pointer, thumb_path, level, tags, description,"
            " detections, engine_ver, vec_rowid, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (dhash_hex, pointer, thumb, level, tags, description,
             json.dumps(detections or [], ensure_ascii=False), engine_ver, vec_rowid, now),
        )
        item_id = cur.lastrowid
        c.execute("INSERT INTO items_fts(rowid, tags, description) VALUES (?,?,?)",
                  (item_id, tags, description))
        # 维护 L0 缓存
        bits = np.array([b == "1" for b in f"{int(dhash_hex, 16):064b}"])
        if self._dhash_cache is not None:
            self._dhash_cache.append((item_id, bits))
        c.commit()
        return item_id

    def set_level(self, item_id: int, level: str) -> None:
        """用户纠错升级金样本（分级入库制度）。"""
        self.conn.execute("UPDATE items SET level=? WHERE id=?", (level, item_id))
        self.conn.commit()

    def set_tags(self, item_id: int, tags: str, description: str | None = None) -> None:
        c = self.conn
        c.execute("UPDATE items SET tags=? WHERE id=?", (tags, item_id))
        if description is not None:
            c.execute("UPDATE items SET description=? WHERE id=?", (description, item_id))
        c.execute("DELETE FROM items_fts WHERE rowid=?", (item_id,))
        c.execute("INSERT INTO items_fts(rowid, tags, description) VALUES (?,?,?)",
                  (item_id, tags, description if description is not None else ""))
        c.commit()

    # ── 检索双路：关键词(FTS5) + 向量(sqlite-vec) ────────────────
    def search_keyword(self, query: str, top_k: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT i.* FROM items_fts f JOIN items i ON i.id = f.rowid"
            " WHERE items_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, top_k),
        ).fetchall()
        return [self._row2dict(r) for r in rows]

    def search_vector(self, vector: np.ndarray, top_k: int = 20) -> list[dict]:
        if not self._vec_ok:
            return []
        rows = self.conn.execute(
            "SELECT v.rowid, v.distance FROM vec_items v"
            " WHERE v.embedding MATCH ? AND k = ? ORDER BY distance",
            (np.asarray(vector, dtype=np.float32).tobytes(), top_k),
        ).fetchall()
        out = []
        for rowid, dist in rows:
            r = self.conn.execute("SELECT * FROM items WHERE vec_rowid=?", (rowid,)).fetchone()
            if r:
                d = self._row2dict(r)
                d["similarity"] = round(1.0 - float(dist), 4)
                out.append(d)
        return out

    def get_by_id(self, item_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return self._row2dict(r) if r else None

    def list_items(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM items ORDER BY id LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [self._row2dict(r) for r in rows]

    # ── 失指检测（Lightroom 方案）────────────────────────────────
    def scan_lost_pointers(self) -> list[int]:
        """指针指向的原图不存在 → 标记 lost_pointer（保留30天后清理）。"""
        lost = []
        for r in self.conn.execute("SELECT id, pointer FROM items WHERE status='ok'").fetchall():
            if not Path(r[1]).exists():
                self.conn.execute("UPDATE items SET status='lost_pointer' WHERE id=?", (r[0],))
                lost.append(r[0])
        self.conn.commit()
        return lost

    def stats(self) -> dict:
        c = self.conn
        total = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        by_level = dict(c.execute(
            "SELECT level, COUNT(*) FROM items GROUP BY level").fetchall())
        lost = c.execute("SELECT COUNT(*) FROM items WHERE status='lost_pointer'").fetchone()[0]
        return {"total": total, "by_level": by_level, "lost_pointer": lost,
                "vector_search": self._vec_ok}

    # ── 内部 ────────────────────────────────────────────────────
    @staticmethod
    def _row2dict(r) -> dict:
        cols = ["id", "dhash", "pointer", "thumb_path", "level", "tags",
                "description", "detections", "engine_ver", "vec_rowid",
                "status", "created_at"]
        d = dict(zip(cols, r))
        d["detections"] = json.loads(d["detections"] or "[]")
        return d

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            self._dhash_cache = None
