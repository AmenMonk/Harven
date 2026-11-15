# -*- coding: utf-8 -*-
"""#1244 方案1验证：Eye 快速档起名（真实照片，测效果+耗时）"""
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

photos = sorted(glob.glob(r'H:\HarvenEye\tests\_realphotos\*.jpg'))[:3]
assert photos, '找不到真实测试照片'

from src.core.photo_namer import name_photo
for i, p in enumerate(photos, 1):
    with open(p, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    t0 = time.time()
    r = name_photo(b64)
    dt = time.time() - t0
    print(f"\n[照片{i}] {os.path.basename(p)}")
    check(f"P{i} 起名成功", not r.get('err'), str(r.get('err'))[:70])
    check(f"P{i} 有名字", len(r.get('title', '')) >= 2,
          f"title=「{r.get('title')}」 cat={r.get('cat')} model={r.get('model')} 耗时{dt:.2f}s")
    check(f"P{i} 秒级（<5s）", dt < 5, f'{dt:.2f}s')

print('\n' + '═' * 50)
print(f'结果：{len(PASS)} PASS / {len(FAIL)} FAIL')
if FAIL:
    print('失败项：', FAIL); sys.exit(1)
