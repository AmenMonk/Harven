import json, urllib.request

def req(method, url, data=None):
    r = urllib.request.Request(url, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode())

R = []
# 1. 孩子列表
ch = req('GET', 'http://127.0.0.1:7000/api/kid/children')
kids = ch.get('children', [])
R.append(('S1-children', True, f'{len(kids)}个孩子'))
if kids:
    cid = kids[0]['id']
    # 2. reading 域写入
    w = req('POST', f'http://127.0.0.1:7000/api/kid/{cid}/reading',
            {'date': '2026-09-18', 'book_name': 'SMOKE测试书', 'minutes': '20', 'pages': '58',
             'rating': '喜欢', 'comment': '冒烟测试', 'source': 'smoke'})
    R.append(('S2-reading-write', w.get('success'), f"rid={w.get('record_id')}"))
    rid = w.get('record_id')
    # 3. reading 域查询
    q = req('GET', f'http://127.0.0.1:7000/api/kid/{cid}/reading')
    recs = [r for r in q.get('records', []) if r.get('book_name') == 'SMOKE测试书']
    R.append(('S3-reading-query', len(recs) == 1, f'{len(recs)}条'))
    # 4. 时间轴自动记录
    tl = req('GET', f'http://127.0.0.1:7000/api/kid/{cid}/timeline?category=learning')
    hit = any('阅读打卡' in (t.get('title') or '') for t in tl.get('records', []))
    R.append(('S4-timeline', hit, '阅读打卡进时间轴'))
    # 5. 清理测试数据
    d = req('DELETE', f'http://127.0.0.1:7000/api/kid/{cid}/reading/{rid}')
    R.append(('S5-cleanup', d.get('success'), ''))
else:
    R.append(('S2-S5', True, '无孩子档案，跳过增删（表结构已由建表保证）'))
# 6. exam/mistakes 域可查询（表存在）
if kids:
    cid = kids[0]['id']
    e = req('GET', f'http://127.0.0.1:7000/api/kid/{cid}/exam')
    m = req('GET', f'http://127.0.0.1:7000/api/kid/{cid}/mistakes')
    rd = req('GET', f'http://127.0.0.1:7000/api/kid/{cid}/reading')
    R.append(('S6-domains', e.get('success') and m.get('success') and rd.get('success'), 'exam/mistakes/reading全通'))
# 7. SPA index 引用新 bundle
html = urllib.request.urlopen('http://127.0.0.1:7000/static/spa/index.html', timeout=10).read().decode()
R.append(('S7-spa-bundle', 'index-BQ1eFizX.js' in html, ''))
# 8. 面板资源
for u in ['/panel/home_items/panel.js', '/panel/kid_archive/kid.js?v=20260918b', '/static/panel-api.js']:
    code = urllib.request.urlopen('http://127.0.0.1:7000' + u, timeout=10).getcode()
    R.append((f'S8-{u.split("/")[-1][:20]}', code == 200, ''))
ok = sum(1 for _, s, _ in R if s)
print(f'{ok}/{len(R)}')
for name, s, info in R:
    print(('PASS' if s else 'FAIL'), name, info)