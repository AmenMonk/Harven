# -*- coding: utf-8 -*-
"""方案A 冒烟测试：自定义分类关键词（后端）。结果落盘 UTF-8，规避终端吞输出。"""
import sys, io, traceback
sys.path.insert(0, r'H:\HarvenAI')
lines = []
try:
    from src.core.acc_categories import classify, load_custom_rules
except Exception:
    lines.append('IMPORT FAIL: ' + traceback.format_exc())
    with open(r'H:\HarvenAI\tests\_acc_custom_log.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    sys.exit(2)

fails = []

def check(desc, got, exp):
    ok = got == exp
    lines.append(('PASS' if ok else 'FAIL') + ' ' + desc + ' -> ' + repr(got) + ' 期望 ' + repr(exp))
    if not ok:
        fails.append(desc)

check('早餐→餐饮', classify('早餐', ''), ('餐饮', '支出'))
check('社保→缴费', classify('今日社保', '其他'), ('缴费', '支出'))
check('发了工资→工资', classify('发了工资', ''), ('工资', '收入'))
check('AI 分类退回', classify('杂项', '杂货'), ('杂货', '支出'))

rules = load_custom_rules()
lines.append('当前库内自定义规则: ' + repr(rules))

fake = [{'name': '旅游', 'kw': '门票,酒店,机票', 'dir': '支出'},
        {'name': '压岁钱', 'kw': '', 'dir': '收入'},
        {'name': '老格式分类', 'kw': '', 'dir': ''}]
check('自定义kw命中', classify('酒店订金', '', fake), ('旅游', '支出'))
check('自定义kw逗号', classify('买门票', '', fake), ('旅游', '支出'))
check('自定义名称兜底命中', classify('去旅游买东西', '', fake), ('旅游', '支出'))
check('自定义dir收入', classify('收到压岁钱', '', fake), ('压岁钱', '收入'))
check('老格式默认支出', classify('老格式分类一笔', '', fake), ('老格式分类', '支出'))
check('自定义优先于内置', classify('物业费', '', [{'name': '房贷', 'kw': '物业', 'dir': '支出'}]), ('房贷', '支出'))
check('无命中走内置', classify('早餐花了10块', '', fake), ('餐饮', '支出'))
check('全空走其他', classify('莫名其妙', '', []), ('其他', '支出'))
check('空规则None', classify('买菜', ''), ('买菜', '支出'))

lines.append('----')
lines.append('TOTAL %d  FAILED %d %s' % (len(fails) + 12, len(fails), fails if fails else ''))
with open(r'H:\HarvenAI\tests\_acc_custom_log.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
sys.exit(1 if fails else 0)
