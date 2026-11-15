# -*- coding: utf-8 -*-
"""哈文全面检测 2026-11-12（四批：运行态/一致性/数据配置/新模块）"""
import json, sqlite3, urllib.request, subprocess, sys, io, os, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.chdir(r'H:\HarvenAI')
sys.path.insert(0, r'H:\HarvenAI')
BASE = 'http://127.0.0.1:7000'
R = []
def chk(name, ok, info=''):
    R.append((ok, name, info))

# 302 不跟随（自定义 handler，跟 302 会把 302 响应吞掉变 200）
class _NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
_op = urllib.request.build_opener(_NR)

def get(url, timeout=15):
    with urllib.request.urlopen(BASE + url, timeout=timeout) as r:
        return r.status, r.read().decode('utf-8', errors='replace')

def post(url, data=None, timeout=30):
    r = urllib.request.Request(BASE + url, method='POST',
        data=json.dumps(data or {}).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

# ═══ 批① 运行态 ═══
try:
    out = subprocess.run(['netstat', '-ano'], capture_output=True,
                         text=True, encoding='gbk', errors='replace').stdout
    chk('1.1 7000 LISTENING', ':7000' in out and 'LISTENING' in out)
except Exception as e:
    chk('1.1 7000 LISTENING', False, str(e)[:60])
v = subprocess.run([r'H:\HarvenAI\.venv\Scripts\python.exe', '--version'], capture_output=True, text=True).stdout.strip()
chk('1.2 venv Python', '3.13' in v, v)
try:
    import flask
    chk('1.3 Flask', flask.__version__.startswith('3'), flask.__version__)
except Exception as e:
    chk('1.3 Flask', False, str(e)[:60])
du = shutil_free = None
try:
    import shutil
    du = shutil.disk_usage('H:\\')
    chk('1.4 H 盘余量', du.free > 50*1024**3, f'{du.free//1024**3}G free')
except Exception as e:
    chk('1.4 H 盘余量', False, str(e)[:60])
for name, url, expect in [('1.5 GET / 302', '/', 302), ('1.6 /login 200', '/login', 200),
                          ('1.7 鸭子图标 200', '/static/icon-duck.png', 200),
                          ('1.8 /phone 200', '/phone', 200)]:
    try:
        req0 = urllib.request.Request(BASE + url, method='HEAD')
        try:
            with _op.open(req0, timeout=10) as r0:
                code = r0.status
        except urllib.error.HTTPError as e0:
            code = e0.code
        # HEAD 可能不被支持，退化 GET
        if code not in (200, 302):
            c, _b = get(url)
            code = c
        chk(name, code == expect, f'got {code}')
    except Exception as e:
        chk(name, False, str(e)[:60])
try:
    c, shell = get('/static/spa/index.html')
    m = re.search(r'assets/(index-[\w-]+\.js)', shell)
    chk('1.9 SPA 壳+JS 引用', c == 200 and bool(m), m.group(1) if m else 'no-js')
    SPA_JS = m.group(1) if m else ''
except Exception as e:
    chk('1.9 SPA 壳+JS 引用', False, str(e)[:60]); SPA_JS = ''

# ═══ 批② 一致性 ═══
import compileall, tempfile
ok_c = compileall.compile_dir(r'H:\HarvenAI\src', quiet=2, force=False, maxlevels=10)
chk('2.1 src 全量编译', bool(ok_c))
S = r'H:\HarvenAI\_package_staging'
FILES = ['src/core/builtin_tools.py', 'src/core/engine.py', 'src/core/butler_agenda.py',
         'src/core/panel_assist.py', 'src/core/panel_assist_routes.py', 'web_app.py',
         'src/vision/engines/face_engine.py', 'src/vision/engines/ocr_engine.py',
         'src/vision/engines/qr_engine.py', 'src/vision/engines/lpr_engine.py',
         'src/vision/engines/alive_engine.py', 'src/vision/photosearch.py',
         'src/vision/installer.py', 'requirements.txt', 'Dockerfile',
         'spa/src/pages/VisionPage.vue', 'spa/src/pages/PhotosPage.vue', 'spa/src/pages/ButlerPage.vue',
         'static/spa/index.html', 'static/spa/assets/' + SPA_JS]
import hashlib
def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
match, miss = 0, []
for f in FILES:
    a, b = os.path.join(r'H:\HarvenAI', f), os.path.join(S, f)
    if not os.path.exists(a):
        miss.append(f + '(live缺)'); continue
    if not os.path.exists(b):
        miss.append(f + '(staging缺)'); continue
    if md5(a) == md5(b):
        match += 1
    else:
        miss.append(f + '(MD5不同)')
chk(f'2.2 staging MD5 对账（{match}/{len(FILES)}）', not miss, ' '.join(miss)[:120])
try:
    c, js = get('/static/spa/assets/' + SPA_JS)
    marks = sum(js.count(k) for k in ('录家人', '语义', '视觉能力', '人脸识别', '待确认收纳盒'))
    chk('2.3 SPA 新特性标记 5 组', c == 200 and marks >= 5, f'hits={marks}')
except Exception as e:
    chk('2.3 SPA 新特性标记 5 组', False, str(e)[:60])

# ═══ 批③ 数据/配置 ═══
try:
    c = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
    chk('3.1 lingzhi.db integrity', c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
    ntab = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    chk('3.2 表数量', ntab >= 100, f'{ntab} 表')
    c.close()
except Exception as e:
    chk('3.1/3.2 lingzhi.db', False, str(e)[:60])
try:
    import yaml
    cfg = yaml.safe_load(open(r'H:\HarvenAI\config.yaml', encoding='utf-8'))
    chk('3.3 config.yaml 可解析', isinstance(cfg, dict) and len(cfg) >= 20, f'{len(cfg)} 键')
except Exception as e:
    chk('3.3 config.yaml', False, str(e)[:60])
try:
    mm = json.load(open(r'H:\HarvenAI\data\multi_models.json', encoding='utf-8'))
    cur = mm.get('current_model', '')
    chk('3.4 multi_models.json', 'models' in mm and bool(cur), f'current={cur}')
except Exception as e:
    chk('3.4 multi_models.json', False, str(e)[:60])
try:
    fdb = sqlite3.connect(r'H:\HarvenAI\data\vision\face.db')
    fdb.row_factory = sqlite3.Row
    emb = fdb.execute('SELECT COUNT(*) FROM face_embeddings').fetchone()[0]
    mem = fdb.execute('SELECT COUNT(*) FROM family_members').fetchone()[0]
    chk('3.5 face.db 人脸库', mem >= 1 and emb >= 10, f'{mem} 成员/{emb} 向量')
    fdb.close()
except Exception as e:
    chk('3.5 face.db', False, str(e)[:60])
try:
    c = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
    sem = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='photo_semantic'").fetchone()[0]
    nsem = c.execute('SELECT COUNT(*) FROM photo_semantic').fetchone()[0] if sem else 0
    chk('3.6 语义索引表', sem and nsem >= 100, f'{nsem} 条')
    c.close()
except Exception as e:
    chk('3.6 语义索引表', False, str(e)[:60])
mods = [('yolox', 'data/models/vision/yolox/yolox_s.onnx'), ('CLIP', 'data/models/vision/chinese-clip/pytorch_model.bin'),
        ('YuNet', 'data/models/vision/face/face_detection_yunet_2023mar.onnx'),
        ('SFace', 'data/models/vision/face/face_recognition_sface_2021dec.onnx'),
        ('活体V2', 'data/models/vision/alive/MiniFASNetV2.onnx'),
        ('活体V1SE', 'data/models/vision/alive/MiniFASNetV1SE.onnx')]
nm = sum(1 for _, p in mods if os.path.exists(os.path.join(r'H:\HarvenAI', p)))
chk('3.7 六组模型文件在位', nm == 6, f'{nm}/6')

# ═══ 批④ 新模块（live HTTP + 进程内） ═══
try:
    d = json.loads(get('/api/face/status')[1])
    chk('4.1 人脸总开关 enabled', d.get('enabled') is True, f"members={d.get('members')} emb={d.get('embeddings')}")
except Exception as e:
    chk('4.1 人脸总开关', False, str(e)[:60])
try:
    d = json.loads(get('/api/face/members')[1])
    names = [m['name'] for m in d.get('members', [])]
    chk('4.2 成员清单含老谭', '老谭' in names, str(names))
except Exception as e:
    chk('4.2 成员清单', False, str(e)[:60])
try:
    d = post('/api/face/identify', {'photo_id': 203})
    faces = d.get('faces', [])
    chk('4.3 认人 #203→老谭', d.get('success') and any(f['identity'] == '老谭' for f in faces),
        f"sim={faces[0]['similarity'] if faces else '-'}")
except Exception as e:
    chk('4.3 认人', False, str(e)[:60])
try:
    d = json.loads(get('/api/vision/capabilities')[1])
    chk('4.4 能力五件套状态', d.get('ocr') and d.get('lpr') and d.get('alive'), json.dumps({k: d.get(k) for k in ('ocr', 'lpr', 'alive')}, ensure_ascii=False)[:80])
except Exception as e:
    chk('4.4 能力状态', False, str(e)[:60])
try:
    d = post('/api/vision/capability/qr', {'photo_id': 203})
    chk('4.5 扫码端点通', d.get('success') is True, str(d.get('result', {}).get('count', d.get('error', '')))[:40])
except Exception as e:
    chk('4.5 扫码端点', False, str(e)[:60])
try:
    d = json.loads(get('/api/photos/semsearch?q=' + urllib.request.quote('冰箱') + '&limit=5')[1])
    chk('4.6 语义搜图', d.get('success') and len(d.get('items', [])) > 0, f"{len(d.get('items', []))} 条")
except Exception as e:
    chk('4.6 语义搜图', False, str(e)[:60])
try:
    from src.core.builtin_tools import call_tool, TOOL_REGISTRY, get_tool_definitions
    r = call_tool('manage_album', action='list')
    albums = {a['name']: a['count'] for a in r.get('albums', [])}
    chk('4.7 manage_album list', r['success'] and albums.get('老谭') == 23, f"老谭={albums.get('老谭')}")
    r2 = call_tool('manage_album', action='move_faces', person='_不存在_')
    chk('4.8 move_faces 诚实报错', r2['success'] is False and '录入人脸' in r2.get('error', ''))
    defs = [d0['function']['name'] for d0 in get_tool_definitions() if isinstance(d0, dict) and 'function' in d0]
    chk('4.9 manage_album 进 FC 清单', 'manage_album' in defs, f'{len(defs)} 工具')
except Exception as e:
    chk('4.7~4.9 manage_album', False, str(e)[:60])
try:
    from src.core import panel_assist as pa
    tp = '_fullchk_pending'
    pa.clear_pending(tp)
    recs = [{'项目': '检测面', '金额': '1'}]
    pa.add_pending(tp, 'accounting', recs, 'x')
    dup = pa.add_pending(tp, 'accounting', recs, 'x')
    chk('4.10 S1 重复去重', dup is False)
    pa.clear_pending(tp)
    chk('4.11 S1 一键清空', pa.pending_count(tp) == 0)
except Exception as e:
    chk('4.10/4.11 S1', False, str(e)[:60])
try:
    c = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
    n_old = c.execute("SELECT COUNT(*) FROM butler_agenda WHERE kind='briefing' AND status='open' AND date < date('now','localtime')").fetchone()[0]
    c.close()
    chk('4.12 简报无昨日霸屏', n_old == 0, f'旧 open 简报={n_old}')
except Exception as e:
    chk('4.12 简报', False, str(e)[:60])
try:
    from src.core.butler_agenda import build_briefing
    br = build_briefing('morning')
    chk('4.13 简报生成不嵌套', isinstance(br, dict) and bool(br.get('text')))
except Exception as e:
    chk('4.13 简报生成', False, str(e)[:60])
try:
    src_web = open(r'H:\HarvenAI\web_app.py', encoding='utf-8').read()
    chk('4.14 一键更新端点在码', "/api/version/update" in src_web and "/api/version/restart" in src_web)
    chk('4.15 账单扫端点在码', 'bills_scan' in src_web or '/api/bills' in src_web)
    chk('4.16 质检端点在码', '/api/face/check_photos' in src_web)
except Exception as e:
    chk('4.14~4.16 端点在码', False, str(e)[:60])
try:
    from src.core import eye_client
    rdy = eye_client.models_ready()
    chk('4.17 HarvenEye 门禁', rdy.get('ready') is True, str(rdy.get('model', rdy.get('error', '')))[:40])
except Exception as e:
    chk('4.17 HarvenEye 门禁', False, str(e)[:60])
try:
    d = json.loads(get('/api/butler/state')[1])
    chk('4.18 管家接口通', 'groups' in d or 'total' in d, f"total={d.get('total', '-')}")
except Exception as e:
    chk('4.18 管家接口', False, str(e)[:60])

# ═══ 汇总 ═══
p = sum(1 for ok, _, _ in R if ok)
print()
for ok, name, info in R:
    if not ok:
        print(f'[FAIL] {name} {info}')
print(f'═══ 哈文全面检测 {p}/{len(R)} ═══')
