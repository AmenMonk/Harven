# -*- coding: utf-8 -*-
import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
conn = sqlite3.connect(r'H:/HarvenAI/data/sqlite/lingzhi.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'panel%'")
ptables = [r[0] for r in cur.fetchall()]
print('PANEL_TABLES:', ptables)
# 找物品存放相关表
target = [t for t in ptables if 'home_items' in t or 'item' in t.lower()]
print('TARGET:', target)
for t in target:
    cur.execute('PRAGMA table_info(%s)' % t)
    cols = [c[1] for c in cur.fetchall()]
    cur.execute('SELECT COUNT(*) FROM %s' % t)
    n = cur.fetchone()[0]
    print('TABLE %s rows=%d cols=%s' % (t, n, cols))
    cur.execute('SELECT * FROM %s LIMIT 3' % t)
    for row in cur.fetchall():
        print('  SAMPLE:', row)
conn.close()
