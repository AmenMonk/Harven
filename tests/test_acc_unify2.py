import io, sys, sqlite3, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')
ok = fail = 0
def check(name, cond, extra=''):
    global ok, fail
    if cond: ok += 1; print('PASS', name)
    else: fail += 1; print('FAIL', name, extra)

from src.core.engine import _try_smart_split
conn = sqlite3.connect(r'H:\HarvenAI\data\sqlite\lingzhi.db'); conn.row_factory = sqlite3.Row
# ★ #1587 对齐待确认机制（#1377 改版）：智能拆分进 assist_pending，用户确认后才入账本
before = conn.execute("SELECT COUNT(*) c FROM panel_accounting_daily_1").fetchone()['c']
p_before = conn.execute("SELECT COUNT(*) c FROM assist_pending WHERE panel_id='accounting_daily_1'").fetchone()['c']
out = _try_smart_split('买海鲜50元 买书包120元', 't2')
after = conn.execute("SELECT COUNT(*) c FROM panel_accounting_daily_1").fetchone()['c']
check('两笔不直入面板表（待确认机制）', after == before, f'{before}->{after}')
p_after = conn.execute("SELECT COUNT(*) c FROM assist_pending WHERE panel_id='accounting_daily_1'").fetchone()['c']
check('两笔进待确认队列(+2)', p_after == p_before + 2, f'{p_before}->{p_after}')
cards = [r[0] for r in conn.execute(
    "SELECT records_json FROM assist_pending WHERE panel_id='accounting_daily_1' "
    "AND (records_json LIKE '%海鲜%' OR records_json LIKE '%书包%') ORDER BY id DESC LIMIT 2").fetchall()]
items = []
for _c in cards:
    if '海鲜' in _c: items.append('海鲜')     # ★ #1587 修复：原列表推导 for x in '海鲜' 逐字迭代成 ['海','鲜']
    if '书包' in _c: items.append('书包')
items = sorted(items)
check('项目名干净无动词尾巴', items == ['书包', '海鲜'], items)
amts = ''.join(cards)
check('两笔金额齐（50/120）', '50' in amts and '120' in amts, amts[:160])
conn.execute("DELETE FROM assist_pending WHERE panel_id='accounting_daily_1' AND (records_json LIKE '%海鲜%' OR records_json LIKE '%书包%')")
conn.commit()

# 小窗抽取链路：insert_records 分类兜底
from src.core.panel_assist import insert_records
n, err, _extra = insert_records('accounting_daily_1', '', [{'日期': '2026-09-16', '项目': '早餐', '金额': '12', '分类': '买菜', '备注': ''}])
check('小窗抽取入库成功', n == 1 and not err, (n, err))
row = conn.execute("SELECT id,分类,kind,方向,来源 FROM panel_accounting_daily_1 WHERE 项目='早餐' AND 来源='哈文助手' ORDER BY id DESC LIMIT 1").fetchone()
check('早餐被兜底修正为餐饮+kind/来源齐', row and row['分类'] == '餐饮' and row['kind'] == 'flow' and row['方向'] == '支出', dict(row) if row else 'none')
conn.execute("DELETE FROM panel_accounting_daily_1 WHERE id=?", [row['id']]); conn.commit(); conn.close()
print(f'RESULT {ok} pass / {fail} fail')
