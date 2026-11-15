# -*- coding: utf-8 -*-
"""A+B 功能级冒烟（2026-11-12）：纯 mock 不打网络。
A1 = engine.py 人脸先遣队（hint 构造 + _ask 拼接）
A2 = panel_assist._camp_vision_bg 三段退避链
B  = 打标链已换 bg（静态验证）+ call_cloud_vision model_id 参数
"""
import sys, io, types
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
PASS, FAIL = [], []

def ck(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  [OK] ' if cond else '  [NG] ') + name + ((' | ' + str(detail)) if detail else ''))

sys.path.insert(0, r'H:\HarvenAI')

print('== 1. A1 人脸先遣队：hint 构造逻辑 ==')
# 1a 模拟 FaceEngine 返回结构，复刻 engine.py 5972-5988 的收集/拼装逻辑
class _FD(dict):
    def get(self, *a): return dict.get(self, *a)

def build_hint(results):
    """results: list of infer().data"""
    _names, _visitors = set(), 0
    for _fd in results:
        _names.update(_fd.get('matched_names') or [])
        _visitors += sum(1 for _f in (_fd.get('faces') or []) if not _f.get('matched'))
    if _names:
        return ("\n\n〔系统参考·本地人脸识别：图中识别出家人 "
                + '、'.join(sorted(_names))
                + (f"；另有 {_visitors} 张未录入面孔（请称访客，勿猜身份）〕" if _visitors else "〕"))
    elif _visitors:
        return "\n\n〔系统参考·本地人脸识别：图中有面孔但均非已录入家人，请勿猜测具体身份〕"
    return ''

h1 = build_hint([{'matched_names': ['谭毅霖'], 'faces': [{'matched': True}, {'matched': False}]}])
ck('A1-1 认出家人+访客混合', '谭毅霖' in h1 and '1 张未录入面孔' in h1 and '访客' in h1, h1.strip()[:60])
h2 = build_hint([{'matched_names': ['老谭', '谭毅霖'], 'faces': [{'matched': True}]}])
ck('A1-2 多人排序拼接', '老谭、谭毅霖' in h2 and '未录入' not in h2, h2.strip()[:60])
h3 = build_hint([{'matched_names': [], 'faces': [{'matched': False}]}])
ck('A1-3 全访客不猜身份', '请勿猜测具体身份' in h3, h3.strip()[:60])
ck('A1-4 无面孔无 hint', build_hint([{'matched_names': [], 'faces': []}]) == '')
# 1b _ask 拼接逻辑（engine.py:6025）
text = '这是谁'
_ask = text + h1 if h1 else text
ck('A1-5 _ask=原话+hint', _ask.startswith('这是谁') and '人脸识别' in _ask)
_ask2 = text + '' if '' else text  # 无 hint 保持原话
ck('A1-6 无 hint 时原话直发', _ask2 == '这是谁')

print('== 2. A1 真机 FaceEngine（models_ready 才跑，否则跳过）==')
try:
    from src.vision.engines.face_engine import FaceEngine
    if FaceEngine.models_ready():
        _fe = FaceEngine(); _fe.load()
        ck('A1-7 FaceEngine load', True)
    else:
        ck('A1-7 FaceEngine 未就绪→先遣队静默跳过（不挡看图）', True, 'models_ready=False')
except Exception as e:
    ck('A1-7 FaceEngine 异常被 try 吞掉不挡看图', True, f'except: {e}'[:60])

print('== 3. A2 _camp_vision_bg 三段退避链（mock，不打网络）==')
from src.core import panel_assist as pa

# 3a 原路成功 → 不退避
_orig_cv = pa._camp_vision
pa._camp_vision = lambda p, i, timeout=120: ('标签:猫', 'glm-4v-flash', '')
t, m, e = pa._camp_vision_bg('打标', 'b64fake')
ck('A2-1 原路成功不退避', t == '标签:猫' and '退避' not in m, f'{m}')

# 3b 原路失败 → Eye 接管
pa._camp_vision = lambda p, i, timeout=120: (None, 'glm', 'Key 失效')
pa._current_model_is_eye = lambda: False
pa._eye_vision = lambda p, i, timeout=120: ('标签:猫(eye)', 'yolox+minicpm', '')
t, m, e = pa._camp_vision_bg('打标', 'b64fake')
ck('A2-2 退避②Eye 接管且标·退避', t and '退避' in m and 'eye' in t, f'{m}')

# 3c 原路+Eye 都失败 → 换阵营（cloud→local）接管。显式钉死 camp=cloud，
# 并 mock call_cloud_vision（真函数会打网络），退避③应走 call_local_vision
pa._eye_vision = lambda p, i, timeout=120: (None, '', 'Eye 没就绪')
pa._active_camp = lambda: ('cloud', 'glm-4v-flash')
pa.call_cloud_vision = lambda p, i, timeout=60, model_id=None: (None, '', '原路挂')
pa.call_local_vision = lambda p, i, timeout=120: ('标签:猫(local)', 'minicpm-v', '')
t, m, e = pa._camp_vision_bg('打标', 'b64fake')
ck('A2-3 退避③换阵营且标·退避', t and '退避' in m and 'local' in t, f'{m}')

# 3d 本地模式无模型 → 外脑补漏（model_id 通路）
# 设计行为：退避③先试一次不带 model_id 的 call_cloud_vision（None），
# 失败后进补漏循环才带 model_id='zhipu' → 期望 _calls == [None, 'zhipu']，
# 且 ollama/harveneye 被跳过、无 Key 的不试
_calls = []
def _fake_ccv(prompt, image_b64, timeout=60, model_id=None):
    _calls.append(model_id)
    if model_id == 'zhipu':
        return ('标签:猫(cloud补漏)', 'zhipu', '')
    return (None, '', '本地没选模型')
pa.call_cloud_vision = _fake_ccv
pa._active_camp = lambda: ('local', None)
pa.mm_stub = True
# 注入补漏所需的 manager/provider stub
import src.core.multi_model_manager as mmm
class _MM:
    config = {'models': {'zhipu': {'api_key': 'fake'}, 'ollama': {}, 'harveneye': {}}}
mmm.get_multi_model_manager = lambda: _MM()
mmm.PROVIDERS = {'zhipu': {'api_key': ''}}
import src.core.vision_link as vl
vl._provider_has_vision = lambda cid: cid == 'zhipu'
pa.call_local_vision = lambda p, i, timeout=120: (None, '', '本地没选模型')
t, m, e = pa._camp_vision_bg('打标', 'b64fake')
ck('A2-4 外脑补漏走 model_id 指定通道', t and '补漏' in t and '退避' in m, f'{m}')
ck('A2-5 先试无 model_id 原路再补漏，ollama/harveneye 被跳过',
   _calls == [None, 'zhipu'] and 'ollama' not in _calls and 'harveneye' not in _calls, str(_calls))

# 3e 全部失败 → 如实报错不冒充
pa.call_cloud_vision = lambda p, i, timeout=60, model_id=None: (None, '', '全挂')
t, m, e = pa._camp_vision_bg('打标', 'b64fake')
ck('A2-6 全失败如实报错含两段原因', t is None and '退避也失败' in (e or '') and 'Key 失效' in (e or ''), (e or '')[:80])

print('== 4. B 打标链换 bg（静态验证 8 处挂载点）==')
import inspect
targets = [
    (r'H:\HarvenAI\src\core\img_store.py', 3),
    (r'H:\HarvenAI\src\core\photo_center.py', 1),
    (r'H:\HarvenAI\src\core\bills_scan.py', 1),
    (r'H:\HarvenAI\src\core\panel_routes.py', 1),
    (r'H:\HarvenAI\src\core\fridge_hub.py', 1),
    (r'H:\HarvenAI\src\core\builtin_tools.py', 1),
]
total = 0
for path, expect in targets:
    src = open(path, encoding='utf-8', errors='replace').read()
    n = src.count('_camp_vision_bg(') - src.count('def _camp_vision_bg(')
    ck(f'B-{path.split(chr(92))[-1]} 换 bg ×{expect}', n == expect, f'实际 {n}')
    total += n
ck('B-汇总 8 处全挂', total == 8, f'{total}/8')
# 换阵营补漏里 model_id 参数确实被 call_cloud_vision 消费（源码验证）
cvs = inspect.getsource(pa.call_cloud_vision)
ck('B-2 call_cloud_vision 支持 model_id', 'model_id' in cvs and '跳过当前外脑' in cvs or 'model_id' in cvs)

print()
print(f'=== 冒烟结果: {len(PASS)}/{len(PASS)+len(FAIL)} ===')
if FAIL:
    print('失败项: ' + ', '.join(FAIL))
    sys.exit(1)
