# -*- coding: utf-8 -*-
"""哈文全面体检 2026-09-18 晚"""
import json, sqlite3, urllib.request, urllib.error, subprocess, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
BASE = 'http://127.0.0.1:7000'
R = []
def chk(name, ok, info=''):
    R.append(('PASS' if ok else 'FAIL', name, info))

def get(url, timeout=15):
    with urllib.request.urlopen(BASE + url, timeout=timeout) as r:
        return r.status, r.read().decode('utf-8', errors='replace')

def req(method, url, data=None):
    r = urllib.request.Request(BASE + url, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read().decode())

# ═══ 1. 服务与核心接口 ═══
try:
    code, body = get('/api/panels')
    panels = json.loads(body)['panels']
    chk('F1 服务/面板列表', code == 200 and len(panels) == 17, f'{len(panels)}个面板')
except Exception as e:
    chk('F1 服务/面板列表', False, str(e)[:80]); panels = []

# 2. 全部 17 个面板页面 + 资源齐全
ok_n, bad = 0, []
for p in panels:
    try:
        code, html = get(f"/panel/{p['id']}/index.html")
        has = ('panel-assist.css' in html) and ('panel-assist.js' in html) and ('panel.js' in html)
        dead = 'panel-api.css' in html
        if code == 200 and has and not dead: ok_n += 1
        else: bad.append(p['id'] + '(css/js/死链异常)')
    except Exception as e:
        bad.append(p['id'] + ':' + str(e)[:40])
chk('F2 17面板页面完整(css+js+无死链)', ok_n == len(panels) and ok_n == 17, f'{ok_n}/17' + (' bad=' + ','.join(bad) if bad else ''))

# 3. 静态资源
for name, u in [('S1 panel-assist.js', '/static/panel-assist.js'), ('S2 panel-assist.css', '/static/panel-assist.css'),
                ('S3 panel-api.js', '/static/panel-api.js'), ('S4 鸭子图标', '/static/icons/duck_64.png')]:
    try:
        code, body = get(u)
        chk(name, code == 200 and len(body) > 100, f'len={len(body)}')
    except Exception as e:
        chk(name, False, str(e)[:60])
try:
    code, js = get('/static/panel-assist.js')
    chk('S5 助手自动展开代码在线', 'harven_assist_dismissed' in js, '')
except Exception as e:
    chk('S5 助手自动展开代码在线', False, str(e)[:60])

# 4. SPA 前端
try:
    code, html = get('/static/spa/index.html')
    import re
    m = re.search(r'assets/(index-[\w-]+\.js)', html)
    chk('F3 SPA index/引用bundle', code == 200 and bool(m), m.group(1) if m else 'no-bundle')
except Exception as e:
    chk('F3 SPA index/引用bundle', False, str(e)[:60])
try:
    code, body = get('/spa/')
    chk('F4 SPA 路由可达', code == 200, '')
except Exception as e:
    chk('F4 SPA 路由可达', False, str(e)[:60])

# 5. 面板数据 CRUD 冒烟（home_items 建一条删一条）
try:
    w = req('POST', '/api/panels/home_items/data', {'物品名称': '体检测试品', '数量': 1})
    rid = w.get('id') or w.get('record_id') or w.get('row_id')
    chk('F5 面板数据写入', bool(w.get('success', True)), f'rid={rid}')
    if rid:
        d = req('DELETE', f'/api/panels/home_items/data/{rid}')
        chk('F6 面板数据删除', bool(d.get('success', True)), '')
    else:
        # 兜底：查列表找刚插入的
        code, body = get('/api/panels/home_items/data')
        recs = json.loads(body).get('records', [])
        hit = [r for r in recs if r.get('物品名称') == '体检测试品']
        chk('F6 面板数据删除', False, f'没拿到id,残留{len(hit)}条待清')
except Exception as e:
    chk('F5/F6 面板数据CRUD', False, str(e)[:80])

# 6. kid 三域查询 + 时间轴
try:
    ch = req('GET', '/api/kid/children')
    kids = ch.get('children', [])
    chk('F7 孩子档案', ch.get('success') and len(kids) >= 1, f'{len(kids)}个孩子')
    if kids:
        cid = kids[0]['id']
        allok = True
        for dom in ['homework', 'exam', 'reading', 'mistakes', 'progress']:
            try:
                j = req('GET', f'/api/kid/{cid}/{dom}')
                if not j.get('success'): allok = False
            except Exception:
                allok = False
        chk('F8 学习五域查询(作业/考试/阅读/错题/进度)', allok, '')
        try:
            tl = req('GET', f'/api/kid/{cid}/timeline?category=learning')
            chk('F9 成长时间轴', tl.get('success'), f"{len(tl.get('records', []))}条")
        except Exception as e:
            chk('F9 成长时间轴', False, str(e)[:60])
except Exception as e:
    chk('F7 孩子档案', False, str(e)[:60])

# 7. 助手后端接口
for name, u in [('F10 待确认队列', '/api/assist/pending/count?panel_id=home_items'),
                ('F11 到期提醒', '/api/assist/alerts?panel_id=home_items'),
                ('F12 表单schema', '/api/assist/schema?panel_id=home_items&domain=')]:
    try:
        code, body = get(u)
        chk(name, code == 200, '')
    except Exception as e:
        chk(name, False, str(e)[:60])

# 8. 定时任务接口（路径未知则容忍404）
cron_ok, cron_info = None, ''
for u in ['/api/cron/jobs', '/api/cron', '/api/cron/status']:
    try:
        code, body = get(u)
        cron_ok, cron_info = code == 200, u
        break
    except urllib.error.HTTPError as e:
        cron_info = f'{u}->{e.code}'
    except Exception as e:
        cron_info = str(e)[:40]
chk('F13 定时任务接口', bool(cron_ok), cron_info)

# 9. 数据库完整性
try:
    conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
    cur = conn.cursor()
    qr = cur.execute('PRAGMA quick_check').fetchone()[0]
    ntab = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    orphans = cur.execute("SELECT COUNT(*) FROM kid_reading WHERE child_id NOT IN (SELECT id FROM children)").fetchone()[0]
    conn.close()
    chk('F14 数据库quick_check', qr == 'ok', qr)
    chk('F15 数据库规模', ntab > 20, f'{ntab}张表')
    chk('F16 无孤儿阅读记录', orphans == 0, f'{orphans}条')
except Exception as e:
    chk('F14-F16 数据库', False, str(e)[:60])

# 10. Python 核心语法
mods = ['src/core/panel_manager.py', 'src/core/kid_archive.py', 'src/core/security_guard.py', 'src/core/builtin_tools.py']
import py_compile
ok_py = []
for m in mods:
    try:
        py_compile.compile(r'H:\HarvenAI' + '\\' + m.replace('/', '\\'), doraise=True)
        ok_py.append(m.split('/')[-1])
    except Exception as e:
        chk(f'P-{m}', False, str(e)[:60])
chk('P1 Python核心编译x4', len(ok_py) == len(mods), ','.join(ok_py))

print(f'TOTAL={len(R)} PASS={sum(1 for s,_,_ in R if s=="PASS")}')
for s, n, i in R:
    print(s, n, i)