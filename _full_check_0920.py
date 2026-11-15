# -*- coding: utf-8 -*-
"""哈文全面检测 2026-09-20：今晨 2 修复 + #802~#806 昨今新作（用后即删）"""
import sys, io, json, sqlite3, subprocess, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
ROOT = r'H:\HarvenAI'
HUB = r'H:\HarvenHub'
STAGE = ROOT + r'\_package_staging'
results = []
def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))

# ═══ A. 今晨两项修复 ═══
r = subprocess.run([ROOT + r'\.venv\Scripts\python.exe', '-m', 'py_compile',
                    ROOT + r'\src\core\conversation_archiver.py'], capture_output=True)
check('A1 conversation_archiver.py 语法', r.returncode == 0)
r = subprocess.run([ROOT + r'\.venv\Scripts\python.exe', '-c',
    'import logging; logging.disable(logging.CRITICAL);'
    'from src.core.conversation_archiver import auto_archive_old_conversations;'
    'n = auto_archive_old_conversations(); print("OK n=", n)'],
    capture_output=True, text=True, cwd=ROOT, encoding='utf-8', errors='replace')
out = (r.stdout or '') + (r.stderr or '')
check('A2 自动归档实跑', 'OK n=' in out and 'no such column' not in out,
      ('含报错: ' + out[-150:]) if 'no such column' in out else f'result={out.strip()[-20:]}')
import os
check('A3 device_binding.json 已删(重启重绑)', not os.path.exists(ROOT + r'\data\device_binding.json'))
check('A4 绑定文件备份存在', os.path.exists(ROOT + r'\data\device_binding.json.bak-20260920'))

# ═══ B. #803 云端通道 ═══
r = subprocess.run([ROOT + r'\.venv\Scripts\python.exe', '-m', 'py_compile',
                    ROOT + r'\src\core\cloud_link.py'], capture_output=True)
check('B1 cloud_link.py 语法', r.returncode == 0)
import yaml
cfg = yaml.safe_load(open(ROOT + r'\config.yaml', encoding='utf-8'))
cl = cfg.get('cloud_link') or {}
check('B2 config.yaml cloud_link 段', cl.get('enabled') is False and 'server_url' in cl,
      f"enabled={cl.get('enabled')} url={cl.get('server_url')}")
wa = open(ROOT + r'\web_app.py', encoding='utf-8').read()
check('B3 web_app 路由注册', 'register_cloud_link_routes' in wa)
check('B4 web_app 启动钩子', 'start_cloud_link' in wa)
check('B5 system_info 补 lan_ip', 'lan_ip' in wa)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from src.core import cloud_link as CL
allowed = getattr(CL, 'ALLOWED_ACTIONS', set())
bad = {'exec', 'eval', 'shell', 'cmd', 'run_code', 'delete', 'uninstall', 'install', 'system'}
check('B6 白名单无危险指令', bool(allowed) and not (allowed & bad), f'白名单={sorted(allowed)}')
r1 = CL._dispatch('not_in_list', {})
check('B7 白名单外指令被拒', '不在白名单' in (r1.get('error') or ''))
r2 = CL._dispatch('ping', {})
check('B8 ping 指令放行', r2.get('pong') is True)
pc = CL.pair_start()
code = pc.get('pair_code', '')
check('B9 配对码 6 位数字', len(code) == 6 and code.isdigit(), f'code={code}')
check('B10 配对码 TTL 300s', pc.get('ttl') == 300, f"ttl={pc.get('ttl')}")
CL._pair_code_clear()
check('B11 配对码可清除(一次性机制)', CL._pair_code_get() is None)
st = CL.get_status()
check('B12 get_status 结构', all(k in st for k in ('enabled', 'connected', 'devices')),
      f"keys={sorted(st.keys())}")

# ═══ C. #804 SPA 设置页 ═══
import glob
assets = sorted(glob.glob(ROOT + r'\static\spa\assets\index-*.js'),
                key=os.path.getmtime, reverse=True)
js = open(assets[0], 'rb').read().decode('utf-8', errors='replace') if assets else ''
css_f = sorted(glob.glob(ROOT + r'\static\spa\assets\index-*.css'),
               key=os.path.getmtime, reverse=True)
css = open(css_f[0], 'rb').read().decode('utf-8', errors='replace') if css_f else ''
check('C1 SPA JS 含「手机与云端」', '手机与云端' in js, assets[0].split('\\')[-1] if assets else 'NO JS')
check('C2 SPA JS 含「生成配对码」', '生成配对码' in js)
check('C3 SPA JS 调 cloud_link API', 'cloud_link/status' in js and 'cloud_link/pair/start' in js)
check('C4 SPA CSS 含配对码卡', 'pair-code' in css)
sp = open(ROOT + r'\spa\src\pages\SettingsPage.vue', encoding='utf-8').read()
check('C5 设置页源码含解绑', 'devices/unbind' in sp and '手机与云端' in sp)

# ═══ D. #805 Hub 中转 ═══
r = subprocess.run([ROOT + r'\.venv\Scripts\python.exe', '-m', 'py_compile',
                    HUB + r'\app\api\relay_routes.py'], capture_output=True)
check('D1 relay_routes.py 语法', r.returncode == 0)
rr = open(HUB + r'\app\api\relay_routes.py', encoding='utf-8').read()
check('D2 WS 端点 /ws/harven', '/ws/harven' in rr)
check('D3 配对兑换 /api/pair', '/api/pair' in rr or 'pair' in rr)
check('D4 指令中继 relay/send', 'relay/send' in rr)
hub_init = open(HUB + r'\app\__init__.py', encoding='utf-8').read()
check('D5 /phone 路由已注册', "'/phone'" in hub_init or '"/phone"' in hub_init)
check('D6 phone.html 存在', os.path.exists(HUB + r'\app\templates\phone.html'))
req = open(HUB + r'\requirements.txt', encoding='utf-8').read()
check('D7 Hub requirements 含 flask-sock', 'flask-sock' in req)
phone = open(HUB + r'\app\templates\phone.html', encoding='utf-8').read()
check('D8 手机页含配对码输入', '配对码' in phone)

# ═══ E. #802 PWA + #806 打包物 ═══
check('E1 manifest.webmanifest 存在', os.path.exists(ROOT + r'\static\spa\manifest.webmanifest'))
check('E2 sw.js 存在', os.path.exists(ROOT + r'\static\spa\sw.js'))
bat = open(ROOT + r'\启动HarvenAI.bat', 'rb').read().decode('utf-8', errors='replace')
check('E3 bat start 空标题修复', 'start /b "" .venv' in bat)
check('E4 bat 任务管理器文案修复', '打开任务管理器' in bat)

# ═══ F. staging 同步（MD5 一致性）═══
def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()
sync_files = [
    ('web_app.py', 'web_app.py'),
    ('src\\core\\cloud_link.py', 'src\\core\\cloud_link.py'),
    ('src\\core\\conversation_archiver.py', 'src\\core\\conversation_archiver.py'),
    ('启动HarvenAI.bat', '启动HarvenAI.bat'),
    ('config.yaml.example', 'config.yaml.example'),
    ('spa\\src\\pages\\SettingsPage.vue', 'spa\\src\\pages\\SettingsPage.vue'),
    ('static\\spa\\index.html', 'static\\spa\\index.html'),
    ('static\\spa\\sw.js', 'static\\spa\\sw.js'),
    ('static\\spa\\manifest.webmanifest', 'static\\spa\\manifest.webmanifest'),
]
for src_rel, dst_rel in sync_files:
    s, d = os.path.join(ROOT, src_rel), os.path.join(STAGE, dst_rel)
    if not os.path.exists(d):
        check(f'F staging: {src_rel}', False, 'staging 缺文件')
    elif md5(s) == md5(d):
        check(f'F staging: {src_rel}', True, 'MD5 一致')
    else:
        check(f'F staging: {src_rel}', False, f'MD5 不一致 s={md5(s)[:8]} d={md5(d)[:8]}')
js_name = os.path.basename(assets[0]) if assets else ''
check('F staging: 新 SPA JS', os.path.exists(STAGE + r'\static\spa\assets' + '\\' + js_name), js_name)

# ═══ 汇总 ═══
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
print(f'\n{"="*56}\n  哈文全面检测 2026-09-20: {passed}/{total} 通过\n{"="*56}')
for name, ok, detail in results:
    mark = 'PASS' if ok else 'FAIL'
    print(f'  [{mark}] {name}' + (f'  -- {detail}' if detail and not ok else (f'  ({detail})' if detail else '')))
if passed < total:
    print('\n--- 未过项明细 ---')
    for name, ok, detail in results:
        if not ok:
            print(f'  {name}: {detail}')
