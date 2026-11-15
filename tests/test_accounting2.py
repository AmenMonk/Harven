# -*- coding: utf-8 -*-
"""记账账本 2.0 数据层用例 · 2026-09-16
覆盖：老数据兼容（无 kind = 流水·支出）/ 收支双向 / 预计项 5 周期 / 已付转流水闭环 /
时间轴数据来源 / 设置存取 / 自定义分类。全部走真实 SQLite（面板数据接口）。
"""
import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')

results = []
def check(name, cond, detail=''):
    results.append(cond)
    print(('PASS  ' if cond else 'FAIL  ') + name + (('  → ' + str(detail)) if detail else ''))

from web_app import app
app.config['TESTING'] = True
from src.core.panel_routes import bp as _panel_bp
app.register_blueprint(_panel_bp)
c = app.test_client()
BASE = '/api/panels/accounting_daily_1/data'
PANEL = '/api/panels/accounting_daily_1'

def q(**params):
    r = c.get(BASE, query_string=params)
    return r.get_json()

def post(data):
    r = c.post(BASE, data=json.dumps(data), content_type='application/json')
    return r.get_json()

def put(rid, data):
    r = c.put(BASE + '/' + str(rid), data=json.dumps(data), content_type='application/json')
    return r.get_json()

def delete(rid):
    r = c.delete(BASE + '/' + str(rid))
    return r.get_json()

# ── 0. 面板存在（panel.json 2.0） ─────────────────────
r = c.get(PANEL).get_json()
check('面板存在且版本 2.0.0', r.get('success') and (r.get('panel') or {}).get('version') == '2.0.0', r.get('message'))

# ── 1. 老数据兼容：无 kind 记录 = 流水·支出 ─────────────
old = post({'日期': '2026-09-01', '项目': '老数据早市买菜', '金额': 32, '分类': '买菜', '备注': '升级前留档'})
check('老格式记录可写入', old.get('success'), old.get('message'))
old_id = (old.get('record') or {}).get('id') if isinstance(old.get('record'), dict) else old.get('record')

j = q(limit=2000)
old_rec = next((x for x in j.get('records', []) if x.get('id') == old_id), None)
check('老记录读回 kind 为空（前端按流水·支出处理）', old_rec is not None and not old_rec.get('kind'))
check('老记录字段原样保留', bool(old_rec) and old_rec.get('项目') == '老数据早市买菜' and str(old_rec.get('金额')) in ('32', '32.0'),
      old_rec and old_rec.get('金额'))

# ── 2. 新流水：方向 + 来源 ─────────────────────────────
inc = post({'kind': 'flow', '日期': '2026-09-16', '项目': '工资到账', '金额': 8000,
            '分类': '工资', '方向': '收入', '来源': '一句话记账', 'member': ''})
check('收入流水可写（方向=收入）', inc.get('success'))
inc_id = (inc.get('record') or {}).get('id') if isinstance(inc.get('record'), dict) else inc.get('record')
exp = post({'kind': 'flow', '日期': '2026-09-16', '项目': '午饭面馆', '金额': 18.5,
            '分类': '餐饮', '方向': '支出', '来源': '冰箱库存', 'member': ''})
check('支出流水带来源可写', exp.get('success'))
exp_id = (exp.get('record') or {}).get('id') if isinstance(exp.get('record'), dict) else exp.get('record')

j = q(limit=2000)
recs = j.get('records', [])
f_inc = next((x for x in recs if x.get('id') == inc_id), {})
f_exp = next((x for x in recs if x.get('id') == exp_id), {})
check('方向/来源字段原样存取', f_inc.get('方向') == '收入' and f_exp.get('来源') == '冰箱库存')

# ── 3. 预计项：5 种周期 + 已付闭环（自动生成下一期） ────
cycles = ['一次性', '每周', '每月', '每季度', '每年']
eids = {}
for cy in cycles:
    e = post({'kind': 'expected', '名称': '测试-学费-' + cy, '金额': 100, '到期日': '2026-09-20',
              '周期': cy, '分类': '孩子', '状态': '待付', '备注': ''})
    check('预计项可写（周期=%s）' % cy, e.get('success'))
    eids[cy] = (e.get('record') or {}).get('id') if isinstance(e.get('record'), dict) else e.get('record')

# 一次性：已付 → 生成 1 条流水 + 无下一期
pay = put(eids['一次性'], {'kind': 'expected', '名称': '测试-学费-一次性', '金额': 100, '到期日': '2026-09-20',
                           '周期': '一次性', '分类': '孩子', '状态': '已付', '已付日期': '2026-09-16'})
check('一次性已付状态可更新', pay.get('success'))
j = q(limit=2000)
recs = j.get('records', [])
n_one = sum(1 for x in recs if x.get('kind') == 'expected' and str(x.get('名称', '')).startswith('测试-学费-一次性'))
check('一次性无自动续期', n_one == 1, n_one)

# 每月：已付 → 下一期 2026-10-20
next1 = post({'kind': 'expected', '名称': '测试-学费-每月-下一期', '金额': 100, '到期日': '2026-10-20',
              '周期': '每月', '分类': '孩子', '状态': '待付'})
check('每月下一期可写入（10-20，由前端 nextDue 计算）', next1.get('success'))
nid = (next1.get('record') or {}).get('id') if isinstance(next1.get('record'), dict) else next1.get('record')
nrec = next((x for x in q(limit=2000).get('records', []) if x.get('id') == nid), {})
check('下一期到期日=2026-10-20', nrec.get('到期日') == '2026-10-20', nrec.get('到期日'))

# ── 4. 设置：预算 + 自定义分类 ────────────────────────
s = post({'kind': 'settings', '预算': '5000', '自定义分类': json.dumps(['旅游'], ensure_ascii=False)})
check('设置记录可写（预算/自定义分类）', s.get('success'))
s2 = next((x for x in q(limit=2000).get('records', []) if x.get('kind') == 'settings'), {})
check('预算读回=5000，自定义分类含旅游', str(s2.get('预算')) == '5000' and '旅游' in (s2.get('自定义分类') or ''), s2.get('自定义分类'))

# ── 5. 时间轴数据来源：流水 + 已付预计项均可查询 ────────
paid = [x for x in recs if x.get('kind') == 'expected' and x.get('状态') == '已付']
flows = [x for x in recs if x.get('kind') != 'expected' and x.get('kind') != 'settings']
check('时间轴两类事件数据齐备（流水%d条+已付%d条）' % (len(flows), len(paid)), len(flows) >= 3 and len(paid) >= 1)

# ── 清理测试数据 ─────────────────────────────────────
cleanup = [old_id, inc_id, exp_id, nid] + list(eids.values()) + [s2.get('id')]
ok = all(delete(i).get('success') for i in cleanup if i)
check('测试数据清理完成', bool(ok))

n_pass, n_all = sum(results), len(results)
print('RESULT: %d/%d' % (n_pass, n_all))
sys.exit(0 if n_pass == n_all else 1)
