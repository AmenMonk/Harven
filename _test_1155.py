# -*- coding: utf-8 -*-
"""#1155 视觉路由用户自由化 自检（临时，跑完删）"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'H:\HarvenAI')

PASS, FAIL = [], []
def ck(name, cond, detail=''):
    (PASS if cond else FAIL).append((name, detail))
    print(('  PASS ' if cond else '✗ FAIL ') + name + ((' | ' + str(detail)[:150]) if detail and not cond else ''))

from src.core.panel_assist import (_ASSIST_DEFAULTS, save_assist_settings, get_assist_settings,
                                   _vision_routing_pref, _resolve_vision_model,
                                   call_cloud_vision, call_local_vision, _camp_vision, _active_camp)

print('== A. 设置链路 ==')
ck('A1 白名单含 vision_routing', 'vision_routing' in _ASSIST_DEFAULTS)
save_assist_settings({'vision_routing': 'cloud'})
ck('A2 保存cloud读回cloud', _vision_routing_pref() == 'cloud', get_assist_settings().get('vision_routing'))
save_assist_settings({'vision_routing': 'local'})
ck('A3 保存local读回local', _vision_routing_pref() == 'local')
save_assist_settings({'vision_routing': 'bogus'})
ck('A4 非法值回退auto', _vision_routing_pref() == 'auto')
save_assist_settings({'vision_routing': 'auto'})
ck('A5 恢复auto', _vision_routing_pref() == 'auto')

print('== B. 本地视觉探测 ==')
m = _resolve_vision_model()
print('   探测结果:', repr(m))
ck('B1 探测不炸（返回str）', isinstance(m, str))
if m:
    low = m.lower()
    ck('B2 探测到的是视觉系', any(k in low for k in ['minicpm', 'llava', 'vl', 'moondream', 'vision', 'bakllava']), m)

print('== C. 外脑看图如实报 ==')
camp, cur = _active_camp()
print('   当前阵营:', camp, cur)
txt, model, err = call_cloud_vision('测试', 'A' * 200)
ck('C1 返回三元组', txt is None and isinstance(err, str) and err)
print('   外脑报错原文:', err[:100])
ck('C2 本地模式下外脑通道如实报', ('本地模式' in err) or ('外脑' in err), err)

print('== D. 路由跟随 ==')
import unittest.mock as _mock
with _mock.patch('src.core.panel_assist._vision_routing_pref', return_value='local'):
    with _mock.patch('src.core.panel_assist.call_local_vision', return_value=('ok', 'm', '')) as _clv:
        r = _camp_vision('p', 'A' * 200)
        ck('D1 local固定走本地', r[0] == 'ok' and _clv.called)
with _mock.patch('src.core.panel_assist._vision_routing_pref', return_value='cloud'):
    with _mock.patch('src.core.panel_assist.call_cloud_vision', return_value=('ok', 'm', '')) as _ccv:
        r = _camp_vision('p', 'A' * 200)
        ck('D2 cloud固定走外脑', r[0] == 'ok' and _ccv.called)
with _mock.patch('src.core.panel_assist._vision_routing_pref', return_value='auto'):
    if camp == 'local':
        with _mock.patch('src.core.panel_assist.call_local_vision', return_value=('ok', 'm', '')) as _clv2:
            _camp_vision('p', 'A' * 200)
            ck('D3 auto+本地阵营→本地', _clv2.called)
    else:
        with _mock.patch('src.core.panel_assist.call_cloud_vision', return_value=('ok', 'm', '')) as _ccv2:
            _camp_vision('p', 'A' * 200)
            ck('D3 auto+外脑阵营→外脑', _ccv2.called)

print('== E. 写死清除确认 ==')
import inspect
src_pa = inspect.getsource(sys.modules['src.core.panel_assist'])
ck('E1 call_cloud_vision 不再写死 glm-4v-flash', "payload = {'model': 'glm-4v-flash'" not in src_pa)
import re
hits = [ln for ln in src_pa.splitlines() if 'glm-4v-flash' in ln and 'payload' in ln]
ck('E2 无残留写死调用', not hits, hits)

print(f'\n===== 结果 {len(PASS)} PASS / {len(FAIL)} FAIL =====')
for n, d in FAIL:
    print('  FAIL:', n, '|', d)
