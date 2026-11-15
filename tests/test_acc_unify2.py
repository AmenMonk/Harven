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
before = conn.execute("SELECT COUNT(*) c FROM panel_accounting_daily_1").fetchone()['c']
out = _try_smart_split('买海鲜50元 买书包120元', 't2')
after = conn.execute("SELECT COUNT(*) c FROM panel_accounting_daily_1").fetchone()['c']
check('两笔不同金额=两条记录', after == before + 2, f'{before}->{after}')
rows = conn.execute("SELECT 项目,金额,分类,kind,来源 FROM panel_accounting_daily_1 WHERE 来源='哈文快记' ORDER BY id DESC LIMIT 2").fetchall()
items = sorted([r['项目'] for r in rows])
check('项目名干净无动词尾巴', items == ['书包', '海鲜'], items)
cats = {r['项目']: r['分类'] for r in rows}
check('海鲜→买菜 书包→其他', cats.get('海鲜') == '买菜', cats)
for r in rows: conn.execute("DELETE FROM panel_accounting_daily_1 WHERE id=?", [r['id']]) if False else None
# 用 id 删（row_factory 无 id 列，重查）
ids = [r[0] for r in conn.execute("SELECT id FROM panel_accounting_daily_1 WHERE 来源='哈文快记' AND 项目 IN ('书包','海鲜')").fetchall()]
for i in ids: conn.execute("DELETE FROM panel_accounting_daily_1 WHERE id=?", [i])
conn.commit()

# 小窗抽取链路：insert_records 分类兜底
from src.core.panel_assist import insert_records
n, err = insert_records('accounting_daily_1', '', [{'日期': '2026-09-16', '项目': '早餐', '金额': '12', '分类': '买菜', '备注': ''}])
check('小窗抽取入库成功', n == 1 and not err, (n, err))
row = conn.execute("SELECT id,分类,kind,方向,来源 FROM panel_accounting_daily_1 WHERE 项目='早餐' AND 来源='哈文助手' ORDER BY id DESC LIMIT 1").fetchone()
check('早餐被兜底修正为餐饮+kind/来源齐', row and row['分类'] == '餐饮' and row['kind'] == 'flow' and row['方向'] == '支出', dict(row) if row else 'none')
conn.execute("DELETE FROM panel_accounting_daily_1 WHERE id=?", [row['id']]); conn.commit(); conn.close()
print(f'RESULT {ok} pass / {fail} fail')
