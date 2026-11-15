# -*- coding: utf-8 -*-
"""engine.py 快车道防误触发 + 导航兜底 验证用例（2026-09-16 开发者bug）"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')

from src.core.engine import ConversationEngine as CE

results = []
def check(name, cond, detail=''):
    results.append((name, cond, detail))
    print(('PASS  ' if cond else 'FAIL  ') + name + ('  → ' + str(detail) if detail else ''))

# ── 1. 上下文剥离：还原本bug的完整污染消息 ──
polluted = ('（用户此刻在「家庭生活」面板的对话框里说话） 打开家庭生活\n\n'
            '【背景补充（供参考，不用复述）】\n当前页面：面板管理 · 模板目录…… 记账账本：记录每天收支目录，'
            '支持导出报表，字段包含分类/金额/应用场景')
stripped = CE._strip_assist_context(polluted)
check('剥离后=用户原话', stripped == '打开家庭生活', repr(stripped))
check('剥离含目录等触发词', '目录' not in stripped)

# 无污染消息不受影响
check('普通消息原样保留', CE._strip_assist_context('打开D盘') == '打开D盘')
check('空串兜底', CE._strip_assist_context('') == '')

# ── 2. 导航兜底 _match_nav_target ──
r = CE._match_nav_target('打开家庭生活')
check('打开家庭生活 → /space/family', r == ('/space/family', '家庭生活'), r)
check('打开家庭生活面板 → 同上', CE._match_nav_target('打开家庭生活面板') == ('/space/family', '家庭生活'))
check('帮我打开家庭生活 → 同上', CE._match_nav_target('帮我打开家庭生活') == ('/space/family', '家庭生活'))
check('打开面板 → /panels', CE._match_nav_target('打开面板') == ('/panels', '面板管理'))
check('打开D盘 → None（留给computer_use）', CE._match_nav_target('打开D盘') is None)
check('打开设置 → None（防撞车Windows设置）', CE._match_nav_target('打开设置') is None)
check('问句不跳', CE._match_nav_target('家庭面板里有什么？') is None)
check('提及不跳', CE._match_nav_target('家庭面板里有什么') is None)
check('长句不跳', CE._match_nav_target('帮我打开家庭生活面板看看今天记了啥东西') is None)
check('打开思路文档 → None', CE._match_nav_target('打开思路文档') is None)

# 已装面板（动态）：打印实际命中情况
try:
    from src.core.panel_manager import get_panel_manager as _gpm_dbg
    _pls = _gpm_dbg().get_all_panels() or []
    print('INFO  已装面板: %s' % [(p.get('id'), p.get('name')) for p in _pls][:10])
except Exception as _e:
    print('INFO  面板清单获取失败: %s' % _e)
for kw in ['打开冰箱', '打开记账', '打开成长档案']:
    print('INFO  动态面板命中 %s → %s' % (kw, CE._match_nav_target(kw)))

# ── 3. 快车道端到端：导航命中直接出 [NAVIGATE:]，不进 computer_use ──
evts = list(CE._try_smart_route(CE, CE._strip_assist_context(polluted), stream=True))
texts = [c for t, c in evts if t == 'text']
joined = ''.join(texts)
check('快车道输出导航文本', '已为你打开「家庭生活」' in joined, joined[:60])
import re
m = re.search(r'\[NAVIGATE:([^\]\s]+)\]\s*$', joined)
check('末尾带[NAVIGATE:]标记(web_app可提取)', bool(m) and m.group(1) == '/space/family', m.group(1) if m else None)

# 污染消息直传（防回归：万一别处忘了剥离，长度闸门也不能让它进computer_use）
evts2 = list(CE._try_smart_route(CE, polluted, stream=True))
j2 = ''.join(c for t, c in evts2 if t == 'text')
check('污染消息也不触发ComputerOps', 'Computer Ops' not in j2 and '打开哪个目录' not in j2, j2[:60])

n_pass = sum(1 for _, ok_, _ in results if ok_)
print('\n结果: %d/%d 通过' % (n_pass, len(results)))
sys.exit(0 if n_pass == len(results) else 1)
