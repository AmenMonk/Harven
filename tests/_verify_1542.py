# -*- coding: utf-8 -*-
"""#1542 验证：retag_deep 真跑一张队列照片 → 真标签 + 清标记 + auto_tried=3 + 归位。"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"H:\HarvenAI")
import os
os.chdir(r"H:\HarvenAI")
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

from src.core.img_store import retag_deep, get, _conn

# 捞一张队列里的图（不挑特殊的，取 id 最小）
c = _conn()
row = c.execute("SELECT id, path, album, tags FROM images_index WHERE auto_tried=2 "
                "AND deleted_at='' ORDER BY id ASC LIMIT 1").fetchone()
c.close()
if not row:
    print("队列为空，无可验证")
    sys.exit(0)
iid = row['id']
print(f"验证目标 id={iid} | album={row['album']} | tags={row['tags']}")

tags, model, err = retag_deep(iid)
print(f"结果: tags={tags} | model={model} | err={err}")

item = get(iid)
import sqlite3
conn = sqlite3.connect(r"H:\HarvenAI\data\sqlite\lingzhi.db")
db = conn.execute("SELECT tags, auto_tried, album FROM images_index WHERE id=?",
                  (iid,)).fetchone()
conn.close()
print(f"回读(直查库): tags={db[0]} | auto_tried={db[1]} | album={db[2]}")
ok = bool(tags) and not err and '⏳待深识' not in str(db[0]) and db[1] == 3
print("VERIFY:", "PASS" if ok else "FAIL")
