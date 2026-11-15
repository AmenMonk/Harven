import io, sys, sqlite3, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
conn.row_factory = sqlite3.Row
try:
    rows = conn.execute("SELECT * FROM skill_accounting ORDER BY id DESC LIMIT 30").fetchall()
    print('skill_accounting rows:', len(rows))
    for r in rows:
        print(dict(r))
except Exception as e:
    print('ERR:', e)
conn.close()
