import sqlite3

db = r'H:\HarvenAI\data\sqlite\lingzhi.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

for t in ['devices', 'device_permissions']:
    print('=====', t, '=====')
    cur.execute(f"SELECT * FROM {t}")
    cols = [d[0] for d in cur.description]
    print('cols:', cols)
    rows = cur.fetchall()
    print('count:', len(rows))
    for row in rows:
        print(row)
