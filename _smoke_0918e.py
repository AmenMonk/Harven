# -*- coding: utf-8 -*-
"""三件套冒烟：宠物/梦境/照片中心"""
import json, sqlite3, urllib.request, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
BASE = 'http://127.0.0.1:7000'
R = []
def chk(name, ok, info=''):
    R.append(('PASS' if ok else 'FAIL', name, info))
def req(method, url, data=None):
    r = urllib.request.Request(BASE + url, method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read().decode())

# P1 宠物建档
w = req('POST', '/api/pet/pets', {'name': 'SMOKE测试宠', 'species': '狗', 'breed': '柴犬', 'weight': '10'})
pid = w.get('pet_id')
chk('P1 建档', bool(pid), 'pet_id=%s' % pid)
# P2 喂养打卡
w2 = req('POST', '/api/pet/%s/feeding' % pid, {'date': '2026-09-18', 'type': '主粮', 'amount': '80克', 'detail': '冒烟'})
chk('P2 喂养写入', w2.get('success'), 'rid=%s' % w2.get('record_id'))
# P3 体外驱虫自动排期
w3 = req('POST', '/api/pet/%s/shots' % pid, {'date': '2026-09-18', 'type': '体外驱虫', 'detail': 'SMOKE药'})
q3 = req('GET', '/api/pet/%s/shots' % pid)
rec = next((r for r in q3['records'] if r['detail'] == 'SMOKE药'), {})
chk('P3 驱虫自动排下次', rec.get('next_date') == '2026-10-18', 'next=%s' % rec.get('next_date'))
# P4 提醒接口
al = req('GET', '/api/pet/alerts?days=3')
chk('P4 到期提醒接口', al.get('success'), '%s条' % len(al.get('records', [])))
# P5 查询三域
f1 = req('GET', '/api/pet/%s/feeding' % pid)
f2 = req('GET', '/api/pet/%s/care' % pid)
chk('P5 三域查询', f1.get('success') and f2.get('success'), '')
# P6 照片中心扫到宠物照片
tiny = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
req('POST', '/api/pet/%s/shots' % pid, {'date': '2026-09-18', 'type': '疫苗', 'detail': 'SMOKE照片针', 'photo': tiny})
ph = req('GET', '/api/photos/list')
hit = [p for p in ph.get('photos', []) if 'SMOKE照片针' in (p.get('title') or '')]
chk('P6 照片中心索引到宠物照片', len(hit) == 1, '共%s张' % ph.get('total'))
# P7 梦境面板页面+资源
html = urllib.request.urlopen(BASE + '/panel/dream_journal/index.html', timeout=10).read().decode('utf-8')
chk('P7 梦境面板页面', 'field_梦的内容' in html and 'panel-assist.css' in html, '')
# P8 面板列表含新模块
lst = req('GET', '/api/panels')
ids = [p['id'] for p in lst['panels']]
chk('P8 面板列表', 'dream_journal' in ids and 'pet_care_1' in ids, '%s个' % len(ids))
# P9 宠物定制页未被生成器覆盖
html2 = urllib.request.urlopen(BASE + '/panel/pet_care_1/index.html', timeout=10).read().decode('utf-8')
chk('P9 宠物定制页(pet.js引用)', 'pet.js' in html2 and 'petTabs' in html2, '')
# P10 SPA bundle
spah = urllib.request.urlopen(BASE + '/static/spa/index.html', timeout=10).read().decode('utf-8')
chk('P10 SPA 新bundle', 'index-D9ZMU__I.js' in spah, '')
# 清理
req('DELETE', '/api/pet/pets/%s' % pid)
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
n = conn.execute("SELECT COUNT(*) FROM pets WHERE name LIKE 'SMOKE%'").fetchone()[0]
n2 = conn.execute("SELECT COUNT(*) FROM pet_logs WHERE detail LIKE '%SMOKE%' OR detail LIKE '%冒烟%'").fetchone()[0]
conn.close()
chk('P11 清理', n == 0 and n2 == 0, 'pets残留%s logs残留%s' % (n, n2))

print('TOTAL=%s PASS=%s' % (len(R), sum(1 for s, _, _ in R if s == 'PASS')))
for s, n, i in R:
    print(s, n, i)
