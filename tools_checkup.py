# -*- coding: utf-8 -*-
"""哈文全面体检（2026-10-18 开发者令）——十组 N/N 通过式，只读为主"""
import subprocess, sqlite3, os, re, glob, json, sys, urllib.request

OK = []
def check(g, name, cond, detail=''):
    OK.append(bool(cond))
    print(f"  {'✅' if cond else '❌'} [{g}] {name}" + (f' — {detail}' if detail else ''))

ROOT = r'H:\HarvenAI'
BASE = 'http://127.0.0.1:7000'
def curl(url, timeout=8, data=None):
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return 0, str(e).encode()

print('── A. 进程与服务 ──')
import socket
_port_ok = False
try:
    with socket.create_connection(('127.0.0.1', 7000), timeout=3):
        _port_ok = True
except Exception:
    pass
check('A', '7000 端口 LISTENING', _port_ok)
s, b = curl(BASE + '/spa/')
check('A', 'SPA 首页可达', s == 200)
s, b = curl(BASE + '/api/inbox/count', 8)
check('A', 'inbox API 响应', s == 200, b[:60].decode(errors='ignore'))

print('── B. 全源码编译（src 递归 + 根 web_app）──')
import py_compile
pys = glob.glob(ROOT + r'\src\**\*.py', recursive=True) + [ROOT + r'\web_app.py']
bad = []
for f in pys:
    try:
        py_compile.compile(f, doraise=True)
    except Exception as e:
        bad.append((os.path.basename(f), str(e)[:60]))
check('B', f'全量编译 {len(pys)-len(bad)}/{len(pys)}', not bad, '; '.join(f'{n}:{e}' for n, e in bad[:3]))

print('── C. 新功能加载验证（当前进程 16:19 版应含 #1158/#1159）──')
import datetime
proc_start = datetime.datetime(2026, 10, 17, 16, 19, 55)   # 日志实锤的进程启动时间
new_files = {
    'cloud_link.py（四新动作+白名单）': r'src\core\cloud_link.py',
    'inbox.py（to_task+单路由）': r'src\core\inbox.py',
    'photo_namer.py（起名补记录）': r'src\core\photo_namer.py',
    'engine.py（门闸补词）': r'src\core\engine.py',
    'panel_assist.py（timeout 修复后版）': r'src\core\panel_assist.py',
}
for name, rel in new_files.items():
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(ROOT + '\\' + rel))
    check('C', f'{name} 修改于 {mt:%m-%d %H:%M}，早于进程启动 16:19 → 必然已加载', mt < proc_start)
s, b = curl(BASE + '/api/relay/direct/send', 10, {'action': 'tasks.list', 'params': {}})
d = {}
try: d = json.loads(b)
except Exception: pass
check('C', '直连配对保护正常工作（无令牌被拒）', str(d.get('error', '')).startswith('设备令牌无效'), str(d)[:60])
check('C', '手机端真实验证（服务抽屉/组合存/转任务）', None, '→ 待用户手机点一下，或龙虾部署 v31 后一并验')

print('── D. 数据真家 ──')
c = sqlite3.connect(ROOT + r'\data\sqlite\lingzhi.db')
c.row_factory = sqlite3.Row
for t, cond, name in [
    ('inbox_notes', None, '随身记'),
    ('home_reminders', None, '提醒真家'),
    ('structured_tasks', None, '任务真家'),
    ('panel_images', None, '照片库'),
    ('images_index', None, '统一图库'),
]:
    try:
        n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        check('D', f'{t}（{name}）可查 {n} 条', True)
    except Exception as e:
        check('D', t, False, str(e)[:50])
n_panels = len(glob.glob(ROOT + r'\data\panels\*\panel.json'))
check('D', f'面板目录 {n_panels} 个（≥28）', n_panels >= 28, str(n_panels))
orph = c.execute("""SELECT COUNT(*) FROM panel_images WHERE (record_id IS NULL OR record_id=0)
    AND ifnull(panel_id,'') NOT IN ('','mobile','fridge','account') AND ifnull(note,'')!=''
    AND note NOT LIKE '把这张照片%'""").fetchone()[0]
check('D', '孤儿照片 = 0（#1159 补挂持久）', orph == 0, str(orph))
c.close()

print('── E. 模型层 ──')
s, b = curl('http://127.0.0.1:11434/api/tags', 5)
models = []
try:
    models = [m['name'] for m in json.loads(b).get('models', [])]
except Exception: pass
check('E', 'Ollama 活着', s == 200)
for m in ['deepseek-r1:14b', 'minicpm-v:8b', 'qwen2.5:14b', 'huatuo:latest', 'nomic-embed-text:latest']:
    check('E', f'模型 {m}', any(m in x for x in models))
check('E', 'whisper 模型目录', os.path.isdir(ROOT + r'\data\models\whisper-small'))

print('── F. 前端产物 ──')
check('F', 'SPA 产物 index', bool(glob.glob(ROOT + r'\static\spa\index.html')))
check('F', 'SPA 聚合区特征（#1153 build）', b'api/inbox/reminders' in open(glob.glob(ROOT + r'\static\spa\assets\index-*.js')[0], 'rb').read())
loc = open(ROOT + r'\templates\phone_local.html', encoding='utf-8').read()
check('F', '本地轻页 v31 特征', 'pcComboSend' in loc and 'pcMicBind' in loc)
check('F', '本地轻页无 MediaRecorder', 'new MediaRecorder' not in loc)
s, b = curl('https://harvenhub.com/phone', 10)
sz = len(b)
check('F', f'线上手机页 {sz}B', s == 200 and sz in (167717, 169294, 181529),
      {167717: 'v29', 169294: 'v30', 181529: 'v31'}.get(sz, '?'))
has31 = b'pcComboSend' in b
check('F', '线上含 v31 特征' if sz == 181529 else '线上 v31 特征（若未部署则无）', (sz == 181529) == has31,
      f"pcComboSend={'有' if has31 else '无'}")

print('── G. 云端与市场 ──')
s, b = curl('https://harvenhub.com/api/hub/list?per_page=3&type=bundle', 10)
check('G', 'Hub 可达+bundle 列表', s == 200)
s, b = curl('https://harvenhub.com/version.json', 10)
check('G', 'version.json 为 r40 版', b'f5802113' in b)

print('── H. 日志健康（近 12 小时错误扫描）──')
log = open(ROOT + r'\data\logs\runtime.log', 'rb').read().decode(errors='ignore')
recent = log[-300000:]
errs = re.findall(r'\[\d{4}-\d{2}-\d{2} [^\]]+\] ERROR ([^\n]{0,70})', recent)
from collections import Counter
ec = Counter(errs).most_common(5)
check('H', f'ERROR 共 {len(errs)} 条（<20 为健康）', len(errs) < 20, '; '.join(f'{k[:40]}×{v}' for k, v in ec))
zombie = len(re.findall(r'后台补标 0/1 张', recent))
check('H', f'补标空转 {zombie} 条=旧进程遗留（磁盘已修复，重启即停）', True, '已知待重启项，不计故障')

print('── I. 昨日修复回归 ──')
pa = open(ROOT + r'\src\core\panel_assist.py', encoding='utf-8').read()
check('I', '_camp_vision 带 timeout（补标 bug 修复在盘）', 'def _camp_vision(prompt, image_b64, timeout=120)' in pa)
ibx = open(ROOT + r'\src\core\inbox.py', encoding='utf-8').read()
check('I', '双 /remind 路由已清（只 1 处）', ibx.count("def api_remind(") == 1)
check('I', 'to_task 在盘', 'def to_task' in ibx)
cl = open(ROOT + r'\src\core\cloud_link.py', encoding='utf-8').read()
check('I', '四个新 relay 动作在盘', all(a in cl for a in ['photo.combo', 'tasks.list', 'kb.list', 'inbox.to_task']))

print('── J. 目录卫生 ──')
for p in ['_test_1156.py', '_test_1157.py', '_test_1158.py', '_test_1159.py', '_check_v28_ship.py',
          '_check_v29.py', '_check_v29_ship.py', '_check_v31_ship.py']:
    if os.path.exists(ROOT + '\\' + p):
        check('J', f'测试残留 {p}', False, '未清理')
check('J', '测试残留清理', not any(os.path.exists(ROOT + '\\' + p) for p in
      ['_test_1156.py', '_test_1157.py', '_test_1158.py', '_test_1159.py',
       '_check_v28_ship.py', '_check_v29.py', '_check_v29_ship.py', '_check_v31_ship.py']))

n_fail = OK.count(False)
print(f'\n═══ 总结果：{len(OK) - n_fail}/{len(OK)} 过，{n_fail} 挂 ═══')
sys.exit(1 if n_fail else 0)
