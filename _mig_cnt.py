import sqlite3, json
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
cur = conn.cursor()
out = {}
for t in ['panel_daily_todo_1', 'panel_family_todo', 'panel_user_0918162906', 'kid_reading']:
    try:
        n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{t}")').fetchall()]
        out[t] = {'count': n, 'cols': cols}
        if n:
            rows = cur.execute(f'SELECT * FROM "{t}" LIMIT 5').fetchall()
            out[t]['sample'] = rows
    except Exception as e:
        out[t] = {'err': str(e)}
try:
    kids = cur.execute('SELECT id, name FROM children').fetchall()
except Exception:
    try:
        kids = cur.execute('SELECT id, name FROM kid_children').fetchall()
    except Exception as e:
        kids = str(e)
out['children'] = kids
conn.close()
open(r'H:\HarvenAI\_mig_cnt.json', 'w', encoding='utf-8').write(json.dumps(out, ensure_ascii=False, indent=1, default=str))
print('WROTE')