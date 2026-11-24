# -*- coding: utf-8 -*-
"""#1535 冒烟：组合包 share/import（mock 网络层，验证本地逻辑全链）。跑完即删。"""
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, r"H:\HarvenAI")
os.chdir(r"H:\HarvenAI")

from src.core.chain_engine.registry import get_registry
from src.core.chain_engine.routes import bp as chains_bp

# ── mock 网络层 ────────────────────────────────────────────
_UPLOADED = {}

import src.core.harvenhub_client as hub_client

def _fake_upload(file_path, item_type, title, description='', author='', category='other', license=''):
    with open(file_path, 'rb') as f:
        _UPLOADED['bytes'] = f.read()
    _UPLOADED.update(type=item_type, title=title, license=license, desc=description)
    return {'success': True, 'item_id': 'fake123', 'error': ''}

def _fake_download(item_id, save_dir=None):
    p = os.path.join(r"H:\HarvenAI\data\chains\tmp", f"dl_{item_id}.zip")
    with open(p, 'wb') as f:
        f.write(_UPLOADED['bytes'])
    return {'success': True, 'file_path': p, 'error': ''}

hub_client.hub_upload = _fake_upload
hub_client.hub_download = _fake_download

# routes.py 内是函数内 import，也要 patch 引用点
import src.core.chain_engine.routes as cr
cr.hub_upload = _fake_upload
cr.hub_download = _fake_download

# ── Flask test client（铁律12：手动注册蓝图）────────────────
from flask import Flask
app = Flask(__name__)
app.register_blueprint(chains_bp)
c = app.test_client()

PASS = 0
def check(name, cond, extra=''):
    global PASS
    mark = 'PASS' if cond else 'FAIL'
    print(f"[{mark}] {name} {extra}")
    if cond:
        PASS += 1

reg = get_registry()
chains = reg.list_chains()
check('组合清单非空', len(chains) >= 3, f'n={len(chains)}')

# 1. share：导出+上传（mock 收包）
r = c.post('/api/chains/video_brief/share', json={'license': 'MIT'})
d = r.get_json()
check('share 上传成功', r.status_code == 200 and d.get('ok'), str(d)[:120])
check('share 类型=chain', _UPLOADED.get('type') == 'chain')
check('share 许可证=MIT', _UPLOADED.get('license') == 'MIT')

# 2. zip 内容合法
with zipfile.ZipFile(io.BytesIO(_UPLOADED['bytes'])) as z:
    names = z.namelist()
    raw = json.loads(z.read('chain.json').decode('utf-8'))
check('zip 含 chain.json+README', 'chain.json' in names and 'README.txt' in names, str(names))
check('chain.json 有 steps', len(raw.get('steps', [])) == 4, f"steps={len(raw.get('steps', []))}")
check('导出已摘除 enabled', 'enabled' not in raw)

# 3. import：下载+解包+注册（id 冲突走 -hub 后缀）
r = c.post('/api/chains/import', json={'item_id': 'fake123'})
d = r.get_json()
check('import 成功', r.status_code == 200 and d.get('ok'), str(d)[:160])
new_id = d.get('recipe_id', '')
check('id 冲突自动 -hub 后缀', new_id == 'video_brief-hub', new_id)
check('导入默认停用', reg.get_chain(new_id).enabled is False)
check('导入 origin=forked', reg.get_chain(new_id).origin == 'forked')
check('三道门体检附带', 'check' in d, str(d.get('check'))[:100])

# 4. 再导入一次 → id 再消歧
r = c.post('/api/chains/import', json={'item_id': 'fake123'})
d2 = r.get_json()
print('二次导入响应:', r.status_code, d2)
check('二次导入成功', r.status_code == 200 and d2.get('ok'), d2.get('recipe_id', ''))
new_id2 = d2.get('recipe_id', '')
check('二次导入 id 唯一', new_id2 != new_id, f'{new_id} vs {new_id2}')

# 5. 清理导入的两条测试组合
for rid in (new_id, new_id2):
    rr = c.delete(f'/api/chains/{rid}')
    check(f'清理 {rid}', rr.status_code == 200)

print(f"\n=== #1535 冒烟 {PASS} 项通过 ===")
