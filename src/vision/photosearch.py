# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""能力引擎②: 一句话搜照片 — Chinese-CLIP 嵌入索引（零新增模型）。

原理：图库每张图过已有 ClipEngine 出 512 维向量存 sqlite（photo_semantic 表），
搜索时「老谭在海边」→ 同空间文本向量 → 全量余弦点积 → top-k。
索引后台线程增量建（跳过已索引），重启不丢；删除照片时由调用方顺手清理。
首次使用需加载 CLIP（753MB，约 3~8 秒），之后单张嵌入 ~100ms 级。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_DB = Path(__file__).resolve().parents[2] / 'data' / 'sqlite' / 'lingzhi.db'

_state_lock = threading.Lock()
_state = {"running": False, "indexed": 0, "total": 0, "error": "", "finished_at": 0.0}
_stop = threading.Event()

_eng = None


def _clip():
    global _eng
    if _eng is None:
        from .engines.clip_engine import ClipEngine
        _eng = ClipEngine()
        _eng.load()
    return _eng


def _conn():
    c = sqlite3.connect(str(_DB), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def ensure_table():
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS photo_semantic (
            img_id INTEGER PRIMARY KEY,
            vec BLOB NOT NULL,
            dim INTEGER NOT NULL,
            updated_at TEXT)''')


def indexed_count() -> int:
    ensure_table()
    with _conn() as c:
        return c.execute('SELECT COUNT(*) FROM photo_semantic').fetchone()[0]


def total_photos() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM images_index WHERE deleted_at=''").fetchone()[0]


def status() -> dict:
    with _state_lock:
        s = dict(_state)
    s["indexed"] = indexed_count()
    s["total"] = total_photos()
    from . import config
    s["clip_ready"] = (Path(config.MODELS_DIR) / 'chinese-clip' / 'pytorch_model.bin').exists()
    return s


def index_all(force: bool = False) -> dict:
    """启动后台增量索引线程（幂等：已在跑则直接返回）。"""
    with _state_lock:
        if _state["running"]:
            return {"started": False, "reason": "已在跑"}
        _state.update({"running": True, "indexed": 0, "error": ""})
    _stop.clear()
    t = threading.Thread(target=_index_worker, args=(force,), daemon=True)
    t.start()
    return {"started": True}


def index_one(img_id: int, image_path: str) -> bool:
    """单张入索引（新照片注册后可顺手调用，失败静默不碰主链）。"""
    try:
        vec = _clip().infer(image_path).data["vector"]
        ensure_table()
        with _conn() as c:
            c.execute('INSERT OR REPLACE INTO photo_semantic(img_id, vec, dim, updated_at) VALUES(?,?,?,?)',
                      (img_id, np.asarray(vec, dtype=np.float32).tobytes(), len(vec),
                       datetime.now().isoformat(timespec='seconds')))
        return True
    except Exception as e:
        logger.debug(f"[PhotoSearch] 单张索引失败 #{img_id}: {e}")
        return False


def remove_one(img_id: int):
    try:
        with _conn() as c:
            c.execute('DELETE FROM photo_semantic WHERE img_id=?', (img_id,))
    except Exception:
        pass


def _index_worker(force: bool):
    from src.core import img_store
    try:
        ensure_table()
        clip = _clip()
        with _conn() as c:
            done = {r['img_id'] for r in c.execute('SELECT img_id FROM photo_semantic')}
        imgs = img_store.search('', limit=100000)
        todo = [im for im in imgs if im['id'] not in done] if not force else imgs
        with _state_lock:
            _state["total"] = len(todo)
        n = 0
        t0 = time.time()
        for im in todo:
            if _stop.is_set():
                break
            p = im.get('path', '')
            if not p or not Path(p).exists():
                continue
            try:
                vec = clip.infer(p).data["vector"]
                with _conn() as c:
                    c.execute('INSERT OR REPLACE INTO photo_semantic(img_id, vec, dim, updated_at) VALUES(?,?,?,?)',
                              (im['id'], np.asarray(vec, dtype=np.float32).tobytes(), len(vec),
                               datetime.now().isoformat(timespec='seconds')))
                n += 1
                with _state_lock:
                    _state["indexed"] = n
            except Exception as e:
                logger.debug(f"[PhotoSearch] 跳过 #{im['id']}: {e}")
        logger.info(f"[PhotoSearch] 索引完成 {n}/{len(todo)} 张，耗时 {time.time()-t0:.0f}s")
    except Exception as e:
        with _state_lock:
            _state["error"] = str(e)
        logger.warning(f"[PhotoSearch] 索引异常: {e}")
    finally:
        with _state_lock:
            _state["running"] = False
            _state["finished_at"] = time.time()


def search(query: str, limit: int = 40, min_score: float = 0.18) -> list[dict]:
    """一句话搜照片。返回 [{id, score, ...图信息}]，按相似度降序。"""
    if not query.strip():
        return []
    ensure_table()
    qvec = _clip().text_to_vec(query.strip())
    with _conn() as c:
        rows = c.execute('''SELECT s.img_id, s.vec FROM photo_semantic s
                            JOIN images_index i ON i.id = s.img_id
                            WHERE i.deleted_at='' ''').fetchall()
    if not rows:
        return []
    ids = np.fromiter((r['img_id'] for r in rows), dtype=np.int64, count=len(rows))
    mat = np.stack([np.frombuffer(r['vec'], dtype=np.float32) for r in rows])
    scores = mat @ qvec
    order = np.argsort(-scores)[:limit * 2]
    out = []
    from src.core import img_store
    for i in order:
        if scores[i] < min_score:
            break
        im = img_store.get(int(ids[i]))
        if not im or im.get('deleted_at'):
            continue
        im['score'] = round(float(scores[i]), 3)
        out.append(im)
        if len(out) >= limit:
            break
    return out
