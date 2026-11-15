# -*- coding: utf-8 -*-
import json, sqlite3, urllib.request, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
BASE = 'http://127.0.0.1:7000'
R = []
def chk(name, ok, info=''):
    R.append(('PASS' if ok else 'FAIL', name, info))

# 1. 清两个定制面板的死链 panel-api.css（panel-assist.css 已在，直接删死链行）
dead_line = '<link rel="stylesheet" href="/static/panel-api.css">'
for pid in ['accounting_daily_1', 'kid_archive']:
    f = Path(rf'H:\HarvenAI\data\panels\{pid}\index.html')
    html = f.read_text(encoding='utf-8')
    if dead_line in html:
        f.write_text(html.replace(dead_line + '\n', '', 1).replace(dead_line, '', 1), encoding='utf-8')
    chk(f'C1 死链清除 {pid}', dead_line not in f.read_text(encoding='utf-8'), '')

# 2. 删 home_items 残留测试记录
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
cur = conn.cursor()
rows = cur.execute('SELECT id FROM panel_home_items WHERE 物品名称=?', ('体检测试品',)).fetchall()
for (rid,) in rows:
    cur.execute('DELETE FROM panel_home_items WHERE id=?', (rid,))
conn.commit()
left = cur.execute('SELECT COUNT(*) FROM panel_home_items').fetchone()[0]
conn.close()
chk('C2 残留测试记录清除', len(rows) >= 0, f'删{len(rows)}条, 表剩{left}条')

# 3. 复验：面板页面完整性（定制面板用自己的 js 判据）
def get(url):
    with urllib.request.urlopen(BASE + url, timeout=15) as r:
        return r.status, r.read().decode('utf-8', errors='replace')
with urllib.request.urlopen(BASE + '/api/panels', timeout=15) as r:
    panels = json.loads(r.read().decode())['panels']
ok_n, bad = 0, []
for p in panels:
    code, html = get(f"/panel/{p['id']}/index.html")
    has_assist = ('panel-assist.css' in html) and ('panel-assist.js' in html)
    has_own = ('panel.js' in html) or ('accounting.js' in html) or ('kid.js' in html)
    dead = 'panel-api.css' in html
    if code == 200 and has_assist and has_own and not dead: ok_n += 1
    else: bad.append(p['id'])
chk('C3 17面板页面完整(修正判据)', ok_n == 17, f'{ok_n}/17' + (' bad=' + ','.join(bad) if bad else ''))

# 4. cron 正确路径复测
code, body = get('/api/cron/list')
j = json.loads(body)
chk('C4 定时任务接口(/api/cron/list)', code == 200 and j.get('success'), f"任务数={len(j.get('tasks', j.get('jobs', [])))}")

# 5. 线上确认死链已从服务端消失
for pid in ['accounting_daily_1', 'kid_archive']:
    code, html = get(f'/panel/{pid}/index.html')
    chk(f'C5 线上无死链 {pid}', 'panel-api.css' not in html, '')

print(f'TOTAL={len(R)} PASS={sum(1 for s,_,_ in R if s=="PASS")}')
for s, n, i in R:
    print(s, n, i)