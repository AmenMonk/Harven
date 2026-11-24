# -*- coding: utf-8 -*-
"""物流单（骨架核心 · B8 定版：chains_log 表，异步批量写，防拖慢主链）。

数据真家=lingzhi.db（铁律 10）。每环一行：耗时/成败/模型/通道/token。
C3 红线配套：写入走后台队列，主链只入队（微秒级），绝不阻塞照片链。
"""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time

DB_PATH = r"H:\HarvenAI\data\sqlite\lingzhi.db"

_DDL = """
CREATE TABLE IF NOT EXISTS chains_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    run_id TEXT NOT NULL,
    chain_id TEXT NOT NULL,
    chain_name TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    model_id TEXT DEFAULT '',
    channel TEXT DEFAULT '',
    elapsed_ms REAL DEFAULT 0,
    ok INTEGER DEFAULT 1,
    error TEXT DEFAULT '',
    token_cost REAL DEFAULT 0,
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chains_log_run ON chains_log(run_id);
CREATE INDEX IF NOT EXISTS idx_chains_log_chain ON chains_log(chain_id, ts);
"""


def ensure_table(conn: sqlite3.Connection | None = None):
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    conn.executescript(_DDL)
    conn.commit()
    if own:
        conn.close()


class Logistics:
    """异步物流单。主链只入队；后台线程批量落库。"""

    def __init__(self, db_path: str = DB_PATH, flush_interval: float = 2.0):
        self.db_path = db_path
        self.flush_interval = flush_interval
        ensure_table(sqlite3.connect(db_path))
        self._q: queue.Queue = queue.Queue(maxsize=10000)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, name="chain-logistics", daemon=True)
        self._t.start()

    def log_step(self, run_id: str, chain_id: str, chain_name: str, step_id: str,
                 step_name: str, model_id: str = "", channel: str = "",
                 elapsed_ms: float = 0.0, ok: bool = True, error: str = "",
                 token_cost: float = 0.0, meta: dict | None = None):
        """非阻塞：入队即返回（C3 红线配套）。"""
        row = (time.strftime("%Y-%m-%d %H:%M:%S"), run_id, chain_id, chain_name,
               step_id, step_name, model_id or "", channel or "",
               float(elapsed_ms or 0), 1 if ok else 0, (error or "")[:500],
               float(token_cost or 0), json.dumps(meta or {}, ensure_ascii=False))
        try:
            self._q.put_nowait(row)
        except queue.Full:      # 队列满宁可丢记账也不拖主链（如实计数）
            with self._lock_wrap():
                self._dropped = getattr(self, "_dropped", 0) + 1

    def _lock_wrap(self):
        class _N:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _N()

    def _loop(self):
        buf = []
        while not self._stop.is_set():
            try:
                buf.append(self._q.get(timeout=self.flush_interval))
                while len(buf) < 200:
                    buf.append(self._q.get_nowait())
            except queue.Empty:
                pass
            except Exception:
                pass
            if buf:
                self._flush(buf)
                buf = []

    def _flush(self, rows: list):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.executemany(
                "INSERT INTO chains_log(ts,run_id,chain_id,chain_name,step_id,step_name,"
                "model_id,channel,elapsed_ms,ok,error,token_cost,meta) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            conn.close()
        except Exception:
            pass

    def flush_now(self):
        """测试/关停用：把队列排干立即落库。"""
        rows = []
        while True:
            try:
                rows.append(self._q.get_nowait())
            except queue.Empty:
                break
        if rows:
            self._flush(rows)

    def query(self, chain_id: str | None = None, limit: int = 100) -> list:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if chain_id:
            rows = conn.execute(
                "SELECT * FROM chains_log WHERE chain_id=? ORDER BY id DESC LIMIT ?",
                (chain_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chains_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def stop(self):
        self._stop.set()
        self._t.join(timeout=3)
        self.flush_now()


# ══ 模块级单例 ═════════════════════════════════════════════════
_lg: Logistics | None = None
_lg_lock = threading.Lock()


def get_logistics() -> Logistics:
    global _lg
    with _lg_lock:
        if _lg is None:
            _lg = Logistics()
        return _lg
