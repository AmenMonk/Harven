# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import sqlite3
conn = sqlite3.connect(r"H:\HarvenAI\data\sqlite\lingzhi.db")
print("total:", conn.execute("SELECT COUNT(*) FROM images_index").fetchone()[0])
print("deleted:", conn.execute("SELECT COUNT(*) FROM images_index WHERE deleted_at IS NOT NULL").fetchone()[0])
print("deleted values:", conn.execute(
    "SELECT deleted_at, COUNT(*) FROM images_index GROUP BY deleted_at ORDER BY 2 DESC LIMIT 5").fetchall())
print("latest rows:")
q = ("SELECT id, substr(path,-30), album, substr(coalesce(tags,''),1,30), created_at, deleted_at "
     "FROM images_index ORDER BY id DESC LIMIT 8")
for r in conn.execute(q).fetchall():
    print(r)
conn.close()
