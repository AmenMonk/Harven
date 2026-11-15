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

# 智能拆分端到端：写面板表而非 skill_accounting
from src.core.engine import _try_smart_split
import sqlite3
before = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db').execute(
    "SELECT COUNT(*) FROM panel_accounting_daily_1").fetchone()[0]
out = _try_smart_split('今天买水果花了35元', 'test_conv')
check('智能拆分返回汇总', out and '已处理' in out, out)
after = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db').execute(
    "SELECT COUNT(*) FROM panel_accounting_daily_1").fetchone()[0]
check('写入面板表(+1)', after == before + 1, f'{before}->{after}')
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db')
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM panel_accounting_daily_1 WHERE 项目 LIKE '%水果%' ORDER BY id DESC LIMIT 1").fetchone()
check('水果分类正确+元数据齐', row and row['分类'] == '买菜' and row['kind'] == 'flow' and row['方向'] == '支出' and row['来源'] == '哈文快记',
      dict(row) if row else 'none')
conn.execute("DELETE FROM panel_accounting_daily_1 WHERE id = ?", [row['id']])
conn.commit(); conn.close()
print(f'RESULT {ok} pass / {fail} fail')
