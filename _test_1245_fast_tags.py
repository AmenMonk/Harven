# -*- coding: utf-8 -*-
"""#1245 验证 v2：标签级联（快速档秒试→VLM兜底）/起名已砍/浮条停转"""
import base64
import glob
import os
import sys
import time

sys.path.insert(0, r'H:\HarvenAI')
os.chdir(r'H:\HarvenAI')

PASS, FAIL = [], []
def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))

from src.utils.config import get_config
print(f"[准备] current_model = {(get_config() or {}).get('current_model', '')}")

photos = sorted(glob.glob(r'H:\HarvenEye\tests\_realphotos\*.jpg'))[:2]
assert photos, '找不到测试照片'

# T1 快速档：能识别的图秒级出标准名录标签
print("\n[T1] 快速档秒级标签")
from src.core.photo_namer import _eye_fast_tags
for i, p in enumerate(photos, 1):
    with open(p, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    t0 = time.time()
    tags, err = _eye_fast_tags(b64)
    dt = time.time() - t0
    # 级联语义：出标签=秒级且全中文；识别不出=如实报错（都算正确行为）
    if not err:
        check(f"T1-{i} 秒级出标签", dt < 3 and all(not t.isascii() for t in tags), f'{tags} {dt:.2f}s')
    else:
        check(f"T1-{i} 如实不硬编（级联落VLM）", '识别不出' in err, f'{dt:.2f}s {err[:40]}')

# T2 起名已下线
print("\n[T2] 起名已下线")
from src.core.photo_namer import name_photo
with open(photos[0], 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()
r = name_photo(b64)
check("T2a title 恒空（起名已砍）", r.get('title') == '', str(r.get('title')))

# T3 retag 级联（富贵竹类疑难图：快速档搞不定→VLM兜底出标签）
print("\n[T3] retag 级联（用图库疑难图）")
from src.core import img_store
cur = img_store.search(limit=1)
iid = cur[0]['id']
old = img_store.get(iid)
old_tags = old.get('tags')
t0 = time.time()
tags, model, err = img_store.retag(iid)
dt = time.time() - t0
check("T3a 级联出标签", not err and tags, f'{model} {tags} {err[:40]}')
check("T3b 耗时可接受(<40s)", dt < 40, f'{dt:.2f}s')
img_store.set_tags(iid, old_tags or [])
print(f"  [还原] id={iid} tags → {old_tags}")

# T4 auto_name 闭环：打标签 → status done=True（浮条停）
print("\n[T4] auto_name 闭环 + 浮条停转")
from src.core.photo_namer import auto_name, status
import sqlite3 as _sq
_c = _sq.connect(r'data/sqlite/lingzhi.db')
_c.row_factory = _sq.Row
_row = _c.execute("SELECT id, path FROM images_index WHERE source='mobile' ORDER BY id DESC LIMIT 1").fetchone()
_c.close()
assert _row, '图库无 mobile 图'
src_abs = img_store.get_file(_row['id'])
assert src_abs and os.path.exists(src_abs), f'库仓图不存在: {src_abs}'
reg = img_store.register(src_file=src_abs, source='test_1245', album='未分类', auto_tag=False)
tid = reg['id']
auto_name(tid)
ok_stop = False
st = {}
for _ in range(60):
    time.sleep(0.5)
    st = status(tid)
    if st.get('done'):
        ok_stop = True
        break
check("T4a 浮条 30s 内停转（done=True）", ok_stop, str(st))
check("T4b 有标签或如实失败标记", bool(st.get('title')) or bool(st.get('err')), str(st))
try:
    fp = img_store.get_file(tid)
    if fp and os.path.exists(fp):
        os.remove(fp)
    import sqlite3
    c = sqlite3.connect(r'data/sqlite/lingzhi.db')
    c.execute('DELETE FROM images_index WHERE id=?', (tid,))
    c.commit(); c.close()
    print(f"  [清理] 临时测试图 id={tid} 已删")
except Exception as e:
    print(f"  [清理跳过] {e}")

print('\n' + '═' * 50)
print(f'结果：{len(PASS)} PASS / {len(FAIL)} FAIL')
if FAIL:
    print('失败项：', FAIL); sys.exit(1)
