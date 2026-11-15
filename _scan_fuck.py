# -*- coding: utf-8 -*-
"""安全排查：全库扫描 fuck 留言"""
import os
import sqlite3
import sys

sys.path.insert(0, r'H:\HarvenAI')
os.chdir(r'H:\HarvenAI')

hits = []
for root, dirs, files in os.walk('data'):
    dirs[:] = [d for d in dirs if d not in ('media', 'models', 'downloads', 'skills', 'node_modules', '__pycache__')]
    for fn in files:
        if not fn.endswith(('.db', '.sqlite')):
            continue
        p = os.path.join(root, fn)
        try:
            conn = sqlite3.connect(p)
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for tb in tables:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{tb}")').fetchall()]
                for c in cols:
                    try:
                        rows = conn.execute(
                            f'SELECT rowid, "{c}" FROM "{tb}" WHERE "{c}" LIKE \'%fuck%\' COLLATE NOCASE'
                        ).fetchall()
                        for r in rows[:5]:
                            hits.append((p, tb, c, r[0], str(r[1])[:120]))
                    except Exception:
                        pass
            conn.close()
        except Exception:
            pass
for h in hits:
    print(h)
print('完成, 命中', len(hits))
