# -*- coding: utf-8 -*-
"""#1243 验证：_camp_vision 接 Eye（起名全链）+ 新提示词"""
import base64
import io
import os
import sys
import time

sys.path.insert(0, r'H:\HarvenAI')
os.chdir(r'H:\HarvenAI')

PASS, FAIL = [], []
def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))

# 随机纹理图
from PIL import Image
import random
random.seed(time.time())
img = Image.new('RGB', (360, 270), (40, 90, 200))
for _ in range(2500):
    img.putpixel((random.randrange(360), random.randrange(270)), (random.randrange(256),) * 3)
buf = io.BytesIO(); img.save(buf, format='JPEG')
B64 = base64.b64encode(buf.getvalue()).decode()

from src.utils.config import get_config
cur = (get_config() or {}).get('current_model', '')
print(f"[准备] current_model = {cur}")

# T1 _camp_vision 走 Eye 真答
print("\n[T1] _camp_vision → Eye")
from src.core.panel_assist import _camp_vision
text, model, err = _camp_vision('用一句话描述这张图的主色调', B64, timeout=90)
check("T1a 无报错", not err, err[:60])
check("T1b 模型标注 HarvenEye", 'HarvenEye' in model, model)
check("T1c 真答了（含蓝色）", '蓝' in text, text[:60])

# T2 起名全链
print("\n[T2] 起名全链 name_photo")
from src.core.photo_namer import name_photo
r = name_photo(B64, hint_cat='物品存放')
check("T2a 起名成功无 err", not r.get('err'), str(r.get('err'))[:60])
check("T2b 出了名字", len(r.get('title', '')) >= 2, f"title={r.get('title')} cat={r.get('cat')}")

print('\n' + '═' * 50)
print(f'结果：{len(PASS)} PASS / {len(FAIL)} FAIL')
if FAIL:
    print('失败项：', FAIL); sys.exit(1)
