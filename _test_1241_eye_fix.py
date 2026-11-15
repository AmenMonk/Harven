# -*- coding: utf-8 -*-
"""#1241 Eye 三处漏改点全修测试：手机看图/起名器/外脑路由 不再撞 KeyError 'base_url'
前置：Eye 在线(7001) + Ollama qwen3-vl:8b + 哈文配置 current_model 临时切 harveneye"""
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

# 测试图：随机纹理红块图（防 dhash 近重复）
from PIL import Image
import random
random.seed(time.time())
img = Image.new('RGB', (320, 240), (200, 30, 30))
for _ in range(2000):
    x, y = random.randrange(320), random.randrange(240)
    img.putpixel((x, y), (random.randrange(256),) * 3)
buf = io.BytesIO()
img.save(buf, format='JPEG')
B64 = base64.b64encode(buf.getvalue()).decode()
DATAURL = 'data:image/jpeg;base64,' + B64

# 临时图落盘（给 Eye 同机读图用）
TMP = os.path.join(os.environ['TEMP'], f'hv_1241_{int(time.time())}.jpg')
img.save(TMP, format='JPEG')

from src.core.multi_model_manager import get_multi_model_manager
mm = get_multi_model_manager()
_saved = mm.current_model
print(f"[准备] current_model 临时 → harveneye（原值 {_saved}，测完还原）")
mm.current_model = 'harveneye'

try:
    # ── T1 mm.generate_response 入口拦截（原 KeyError 'base_url' 实锤处）──
    print("\n[T1] mm 外脑路由入口拦截")
    try:
        evts = list(mm.generate_response([{'role': 'user', 'content': 'hi'}], stream=False))
        err = next((e.get('content', '') for e in evts if isinstance(e, dict) and e.get('type') == 'error'), '')
        check("T1a 不再撞 KeyError 'base_url'", 'base_url' not in err, err[:60])
        check("T1b 友好指路含 🏠本地", '本地' in err, err[:80])
    except KeyError as e:
        check("T1a 不再撞 KeyError 'base_url'", False, f'仍 KeyError: {e}')
    except Exception as e:
        # Eye 门禁关时可能走别的报错——只要不是 KeyError 'base_url' 就算过
        check("T1a 不再撞 KeyError 'base_url'", 'base_url' not in str(e), str(e)[:80])

    # ── T2 手机看图链（_photo_answer）：harveneye → Eye 带图真答 ──
    print("\n[T2] 手机发图 → Eye 带图对话")
    from src.core.cloud_link import _photo_answer
    t0 = time.time()
    ans = _photo_answer(DATAURL, '这张图主色调是什么颜色？')
    dt = time.time() - t0
    check("T2a 无 KeyError 'base_url'", 'base_url' not in ans, f'耗时{dt:.1f}s')
    check("T2b Eye 真答了（含红色）", ('红' in ans) and ('看图失败' not in ans), ans[:60])

    # ── T3 起名器通道：harveneye → 如实跳过 ──
    print("\n[T3] 起名器/面板助手外脑通道")
    from src.core.panel_assist import call_cloud_vision
    ok, name, err = call_cloud_vision('给这张图起个名字', B64)
    check("T3a 不再报「没配好」", '没配好' not in err, err[:60])
    check("T3b 明确说明是本地引擎", '本地混合模型' in err, err[:60])

    # ── T4 panel_assist 外脑路由拦截 ──
    print("\n[T4] panel_assist 外脑路由")
    from src.core.panel_assist import _llm_call
    out, model, err = _llm_call(system='你是助手', user='你好', gate='unused_gate_xyz', prefer='cloud')
    check("T4a 不撞 KeyError 'base_url'", 'base_url' not in str(err) and 'base_url' not in str(out), str(err or out)[:60])
    check("T4b 友好指路含 🏠本地", '本地' in str(err), str(err)[:80])
finally:
    mm.current_model = _saved
    print(f"\n[还原] current_model → {_saved}")
    try:
        os.remove(TMP)
    except OSError:
        pass

print('\n' + '═' * 50)
print(f'结果：{len(PASS)} PASS / {len(FAIL)} FAIL')
if FAIL:
    print('失败项：', FAIL)
    sys.exit(1)
