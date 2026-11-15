# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""拍照深识任务队列（v1.3 节点⑨·队列通道，2026-10-29）。

三通道定版（定版拍板）：
  - 秒通道：拍照 L0~L2 当场毫秒级入库，只记存放信息（现有链路）
  - 直答通道：批改/问图当场同步理解（现有链路）
  - 队列通道（本模块）：不当场识别的深识需求（保质期/药品/物品细认）
    → 入队 → 管家后台空闲时逐张走理解槽（用户选的模型，铁律 0）
    → 结果回填 + 管家议程提醒（「牛奶还剩3天，建议做奶油炖菜」）

设计要点：
  - 任务表在视觉库 eye_catalog.db（视觉子系统自治，数据真家唯一）
  - 无理解通道（用户没钉模型且外脑无 vision 位）→ 静默等待不报错，钉选后自动消化
  - 幂等：同 img_id+kind 只有一条 pending
  - 回填提醒走 butler_agenda._add_item（dedup_key 防重复轰炸）
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

PROMPTS: dict[str, str] = {
    "food": "这是家里冰箱/食材的照片。请识别里面的物品，逐项给出：名称、估算的保质期剩余天数（看不出就说看不出）、"
            "是否需要尽快食用、一句烹饪/食用建议。用 JSON 数组输出：[{\"name\":\"\",\"days_left\":0,\"urgent\":false,\"tip\":\"\"}]，"
            "只输出 JSON。",
    "medicine": "这是家里药品/药箱的照片。请识别每样药品，逐项给出：名称、有效期限（看得到就抄下来，看不到写未知）、"
                "用途一句话。用 JSON 数组输出：[{\"name\":\"\",\"expiry\":\"\",\"use\":\"\"}]，只输出 JSON。",
    "general": "这张照片里有哪些物品？逐项给出名称和一句话状态描述（新旧/位置/是否需要处理）。"
               "用 JSON 数组输出：[{\"name\":\"\",\"state\":\"\"}]，只输出 JSON。",
}

# 触发关键词（手机拍照 note/question 命中即自动入队）
DEEP_KEYWORDS: tuple[str, ...] = ("保质期", "过期", "有效期", "还能吃", "还能用", "什么时候吃",
                                  "药品", "药箱", "冰箱里", "食材", "剩多少", "库存")

_kind_lock = threading.Lock()
_loop_started = False


def _conn() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    with _conn() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS vision_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            img_id TEXT DEFAULT '',
            image_path TEXT NOT NULL,
            kind TEXT DEFAULT 'general',
            status TEXT DEFAULT 'pending',
            result TEXT DEFAULT '',
            error TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            done_at TEXT DEFAULT '')''')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vt_status ON vision_tasks(status)")


def enqueue(image_path: str, kind: str = "general", note: str = "", img_id: str = "") -> int:
    """入队（幂等：同 img_id+kind 已有 pending/done 则不重复）。
    image_path 可空——只给 img_id，消化时从图库解析。返回任务 id（0=已存在）。"""
    ensure_schema()
    kind = kind if kind in PROMPTS else "general"
    if not image_path and not img_id:
        return 0
    with _conn() as conn:
        dup_key = str(img_id) if img_id else str(image_path)
        if dup_key:
            row = conn.execute("SELECT id FROM vision_tasks WHERE (img_id=? OR (?='' AND image_path=?)) AND kind=? AND status IN('pending','running','done')",
                               (dup_key, str(img_id), str(image_path), kind)).fetchone()
            if row:
                return 0
        cur = conn.execute(
            "INSERT INTO vision_tasks(img_id,image_path,kind,note,created_at) VALUES(?,?,?,?,?)",
            (str(img_id or ''), str(image_path), kind, str(note or '')[:200],
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        _kick()
        return cur.lastrowid or 0


def list_tasks(limit: int = 50, status: str = '') -> list[dict]:
    ensure_schema()
    q = "SELECT * FROM vision_tasks"
    args: list = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as conn:
        rows = conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("result"):
            try:
                d["result"] = json.loads(d["result"])
            except Exception:
                pass
        out.append(d)
    return out


def maybe_enqueue_from_note(image_path: str, note: str, img_id: str = "") -> int:
    """拍照入库时的自动触发：note 命中深识关键词 → 入队（不挡主链路）。"""
    try:
        t = str(note or '')
        if not any(k in t for k in DEEP_KEYWORDS):
            return 0
        kind = "medicine" if any(k in t for k in ("药", "药品", "药箱")) else \
               "food" if any(k in t for k in ("吃", "食材", "冰箱", "保质期", "过期", "库存")) else "general"
        return enqueue(image_path, kind=kind, note=t, img_id=img_id)
    except Exception as e:
        logger.debug(f"[VisionTasks] 自动入队跳过: {e}")
        return 0


# ── 后台消化 ────────────────────────────────────────────────────

def _resolve_path(row: sqlite3.Row) -> str:
    """任务图路径：优先 image_path，空则从图库按 img_id 解析（vault 本体）。"""
    p = str(row["image_path"] or "")
    if p and Path(p).is_file():
        return p
    if row["img_id"]:
        try:
            from src.core import img_store
            with img_store._conn() as c:
                r = c.execute("SELECT path FROM images_index WHERE id=?", (row["img_id"],)).fetchone()
            if r and r["path"]:
                fp = Path(str(r["path"]))
                if not fp.is_absolute():
                    # ★ #1308 修复（冒烟实锤）：不能靠 Path.cwd() 侥幸拼对——
                    #   一律用图库项目根 BASE_DIR 拼接（cwd 漂移就找不到图）
                    fp = img_store.BASE_DIR / fp
                if fp.is_file():
                    return str(fp)
        except Exception as e:
            logger.debug(f"[VisionTasks] 图库路径解析失败: {e}")
    return ""


def _digest_one(row: sqlite3.Row) -> bool:
    """消化单任务：走理解槽（用户选的模型）。返回是否成功。"""
    from src.core import vision_link
    ch = vision_link.resolve_understand_channel()
    if not ch:
        return False   # 无通道：静默等待（不占错误名额）
    image_path = _resolve_path(row)
    if not image_path:
        with _conn() as conn:
            conn.execute("UPDATE vision_tasks SET status='fail', error=?, done_at=? WHERE id=?",
                         ("图片文件找不到", datetime.now().strftime('%Y-%m-%d %H:%M:%S'), row["id"]))
            conn.commit()
        return True
    prompt = PROMPTS.get(row["kind"], PROMPTS["general"])
    try:
        text = vision_link.understand_image(image_path, prompt=prompt)
        result = str(text)
        _finish(row["id"], result)
        _remind(row, result)
        _fridge_draft(row, result)   # ★ #1308 C：food 深识 → 冰箱存货草稿（待确认）
        return True
    except Exception as e:
        with _conn() as conn:
            conn.execute("UPDATE vision_tasks SET status='fail', error=?, done_at=? WHERE id=?",
                         (str(e)[:300], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), row["id"]))
            conn.commit()
        return True   # 有通道但失败：算消化过（不堵队列），失败详情落库


def _finish(task_id: int, result: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE vision_tasks SET status='done', result=?, done_at=? WHERE id=?",
                     (str(result)[:8000], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), task_id))
        conn.commit()


def _remind(row: sqlite3.Row, result: str) -> None:
    """结果摘要进管家议程（dedup_key=vision:{task_id} 每任务终身一条）。"""
    try:
        from src.core.butler_agenda import _add_item
        summary = _summarize(row["kind"], result)
        if summary:
            _add_item(f"vision:{row['id']}", "vision_tasks", "info", "normal",
                      summary["title"], detail=summary["detail"])
    except Exception as e:
        logger.debug(f"[VisionTasks] 议程提醒失败: {e}")


def _fridge_draft(row, result: str) -> None:
    """★ #1308（2026-10-29 定版拍板）C：food 深识结果 → 冰箱存货记录草稿。
    实锤断层：照片挂了面板但台账零记录（开发者「模块什么都没存」）。
    只对属于 fridge_stock_1 面板的图生效；草稿进收纳盒「📥 待确认」，
    用户点头才写台账（AI 只提议不落库；qwen3-vl 幻觉实锤，看图结果必须过人眼）。"""
    try:
        if row["kind"] != "food" or not row["img_id"]:
            return
        from src.core import img_store
        it = img_store.get(int(row["img_id"]))
        if not it or str(it.get('panel_id') or '') != 'fridge_stock_1':
            return
        s = result[result.find("["):result.rfind("]") + 1]
        items = json.loads(s)
        if not isinstance(items, list):
            return
        recs = []
        today = datetime.now()
        for _it in items[:6]:
            name = str(_it.get("name", "")).strip()[:20]
            if not name or name in ("未知", "无", "看不清"):
                continue
            rec = {"名称": name, "状态": "在存"}
            dl = _it.get("days_left")
            if isinstance(dl, (int, float)) and 0 <= dl <= 3650:
                rec["到期日"] = (today + timedelta(days=int(dl))).strftime("%Y-%m-%d")
            tip = str(_it.get("tip", "")).strip()
            if tip:
                rec["备注"] = tip[:80]
            recs.append(rec)
        if not recs:
            return
        from src.core import panel_assist
        panel_assist.add_pending("fridge_stock_1", "fridge_stock_1", recs,
                                 source_text="🥶 冰箱深识草稿（拍照自动识别，待确认）")
        logger.info(f"[VisionTasks] 冰箱存货草稿已入待确认（{len(recs)} 条）img={row['img_id']}")
    except Exception as e:
        logger.debug(f"[VisionTasks] 冰箱草稿生成跳过: {e}")


def _summarize(kind: str, result: str) -> dict | None:
    """把模型 JSON 结果压成一条人话提醒。解析失败退化为原文截断。"""
    try:
        s = result[result.find("["):result.rfind("]") + 1]
        items = json.loads(s)
        if not isinstance(items, list) or not items:
            return None
        if kind == "food":
            urgent = [it for it in items if it.get("urgent") or (isinstance(it.get("days_left"), int) and it["days_left"] <= 3)]
            head = f"🥶 冰箱盘点：{'、'.join(str(it.get('name','')) for it in items[:4])}"
            if urgent:
                head += f" —— {urgent[0].get('name','')} 需尽快吃（{'、'.join(str(it.get('tip','')) for it in urgent[:1])}）"
            return {"title": head[:120], "detail": result[:500]}
        if kind == "medicine":
            return {"title": "💊 药箱盘点：" + "、".join(str(it.get("name", "")) for it in items[:4])[:100],
                    "detail": result[:500]}
        return {"title": "📦 物品识别：" + "、".join(str(it.get("name", "")) for it in items[:5])[:100],
                "detail": result[:500]}
    except Exception:
        return {"title": "📷 深识完成（结果待查看）", "detail": result[:500]}


def _kick() -> None:
    """有新任务时轻推循环（循环未启动则顺带启动）。"""
    ensure_loop()


def ensure_loop() -> None:
    """后台消化线程（幂等启动）：30s 一轮，每轮最多消化 3 条（错峰不吃满资源）。"""
    global _loop_started
    with _kind_lock:
        if _loop_started:
            return
        _loop_started = True

    def _loop():
        while True:
            try:
                # ★ #1320 对话优先闸门：用户对话中，深识消化本轮让路（30s 后再试，拍照草稿延迟可接受）
                from src.core import dialog_gate as _dg
                if _dg.is_busy():
                    time.sleep(30)
                    continue
                from src.core import vision_link
                if vision_link.resolve_understand_channel():
                    with _conn() as conn:
                        rows = conn.execute(
                            "SELECT * FROM vision_tasks WHERE status='pending' ORDER BY id LIMIT 3").fetchall()
                    for row in rows:
                        with _conn() as conn:
                            conn.execute("UPDATE vision_tasks SET status='running' WHERE id=?", (row["id"],))
                            conn.commit()
                        if not _digest_one(row):
                            with _conn() as conn:
                                conn.execute("UPDATE vision_tasks SET status='pending' WHERE id=?", (row["id"],))
                                conn.commit()
            except Exception as e:
                logger.debug(f"[VisionTasks] 循环异常: {e}")
            time.sleep(30)

    threading.Thread(target=_loop, name="vision-tasks", daemon=True).start()
    logger.info("[VisionTasks] 深识队列循环已启动")
