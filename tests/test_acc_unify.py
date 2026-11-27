import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')
ok = fail = 0
def check(name, cond, extra=''):
    global ok, fail
    if cond: ok += 1; print('PASS', name)
    else: fail += 1; print('FAIL', name, extra)

from src.core.acc_categories import classify
check('早餐→餐饮', classify('早餐') == ('餐饮', '支出'), classify('早餐'))
check('今日社保→缴费', classify('今日社保') == ('缴费', '支出'), classify('今日社保'))
check('买菜56→买菜', classify('买菜') == ('买菜', '支出'))
check('发了工资→工资收入', classify('发了工资') == ('工资', '收入'), classify('发了工资'))
check('随礼→人情', classify('朋友结婚随礼') == ('人情', '支出'), classify('朋友结婚随礼'))
check('无关键词退回AI分类', classify('abc', '娱乐') == ('娱乐', '支出'))
check('全无→其他支出', classify('', '') == ('其他', '支出'))

# 智能拆分端到端：★ #1587 更新——#1377 待确认机制改版后，识别结果先进 assist_pending
# 待确认队列（用户确认后才入库），不再直入面板表；断言随之对齐新机制。
from src.core.engine import _try_smart_split
import sqlite3, json as _json
before = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db').execute(
    "SELECT COUNT(*) FROM panel_accounting_daily_1").fetchone()[0]
p_before = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db').execute(
    "SELECT COUNT(*) FROM assist_pending WHERE panel_id='accounting_daily_1'").fetchone()[0]
out = _try_smart_split('今天买水果花了35元', 'test_conv')
check('智能拆分返回待确认话术', out and '待确认' in out, out)
after = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db').execute(
    "SELECT COUNT(*) FROM panel_accounting_daily_1").fetchone()[0]
check('面板表不直入（待确认机制）', after == before, f'{before}->{after}')
p_after = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db').execute(
    "SELECT COUNT(*) FROM assist_pending WHERE panel_id='accounting_daily_1'").fetchone()[0]
check('待确认队列进队(+1)', p_after == p_before + 1, f'{p_before}->{p_after}')
prow = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db').execute(
    "SELECT records_json FROM assist_pending WHERE panel_id='accounting_daily_1' "
    "AND records_json LIKE '%水果%' ORDER BY id DESC LIMIT 1").fetchone()
_rj = (prow[0] if prow else '') or ''
check('水果抽取进卡（项目/金额齐）', '水果' in _rj and '35' in _rj, _rj[:120])
_cl = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
_cl.execute("DELETE FROM assist_pending WHERE panel_id='accounting_daily_1' AND records_json LIKE '%水果%'")
_cl.commit(); _cl.close()   # ★ #1587 修复：原写法 execute/commit 用了两个连接=没提交，残留卡坑下一轮去重
print(f'RESULT {ok} pass / {fail} fail')
