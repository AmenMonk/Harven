# -*- coding: utf-8 -*-
"""#1542 存量捞回：49 张 🏷标签失败 → ⏳待深识 + auto_tried=2 入补标队列。"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import sqlite3

conn = sqlite3.connect(r"H:\HarvenAI\data\sqlite\lingzhi.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT id, tags, auto_tried FROM images_index "
    "WHERE tags LIKE '%标签失败%' AND deleted_at=''").fetchall()
print(f"待捞回: {len(rows)} 张")
n = 0
for r in rows:
    tags = [t.strip() for t in (r['tags'] or '').split(',') if t.strip()]
    new = ['⏳待深识' if t == '🏷标签失败' else t for t in tags]
    if '⏳待深识' not in new:
        new.append('⏳待深识')
    conn.execute("UPDATE images_index SET tags=?, auto_tried=2 WHERE id=?",
                 (','.join(new)[:200], r['id']))
    n += 1
conn.commit()
cnt = conn.execute(
    "SELECT COUNT(*) FROM images_index WHERE auto_tried=2 AND deleted_at=''").fetchone()[0]
print(f"已捞回 {n} 张；队列总数 auto_tried=2: {cnt}")
conn.close()
