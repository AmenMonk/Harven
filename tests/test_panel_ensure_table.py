# -*- coding: utf-8 -*-
"""#1427 记账面板建表失败回归冒烟 · 2026-11-04 r48
笔记本实锤：panel_accounting_daily_1 建表「duplicate column name: member」
→ no such table。根因 _ensure_table 无条件补 member 列 + panel.json 自带 member 字段。
"""
import io, sys, sqlite3, tempfile, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'H:\HarvenAI')

results = []
def check(name, cond, detail=''):
    results.append(cond)
    print(('PASS  ' if cond else 'FAIL  ') + name + (('  → ' + str(detail)) if detail and not cond else ''))

# 用临时库模拟全新装机（不碰真库）
tmpdb = tempfile.mktemp(suffix='.db')

import src.core.panel_manager as pm
from src.models import database as _dbmod
_orig_get_conn = _dbmod.get_connection
_dbmod.get_connection = lambda *a, **k: sqlite3.connect(tmpdb)
pm.DB_PATH = __import__('pathlib').Path(tmpdb)

class FakePanel:
    id = 'accounting_daily_1'
    type = 'table'

m = pm.PanelManager() if hasattr(pm, 'PanelManager') else None
# 直接用内部函数（与笔记本故障同路径）
fields = json.load(open(r'H:\HarvenAI\data\panels\accounting_daily_1\panel.json', encoding='utf-8'))['fields']

inst = None
for attr in ('_ensure_table',):
    fn = getattr(m, attr, None) if m else None
check('PanelManager 实例可用', fn is not None)

fn(tmpdb.replace('\\\\', '/'), fields) if False else None
# _ensure_table 签名 (panel_id, fields)
m._ensure_table('accounting_daily_1', fields)

c = sqlite3.connect(tmpdb)
cols = [r[1] for r in c.execute('PRAGMA table_info("panel_accounting_daily_1")').fetchall()]
check('表已建成', len(cols) > 0, cols)
check('member 列恰好一个', cols.count('member') == 1, cols)
check('业务列在场（日期/金额/kind）', all(x in cols for x in ['日期', '金额', 'kind']), cols)
# 可写入（走一遍 insert 语义）
c.execute('INSERT INTO "panel_accounting_daily_1" ("日期","项目","金额") VALUES (?,?,?)', ('2026-11-04', '冒烟', 1.0))
c.commit()
n = c.execute('SELECT COUNT(*) FROM "panel_accounting_daily_1"').fetchone()[0]
check('插入可用（no such table 已根治）', n == 1)
c.close()

# 幂等：重复调用不炸
m._ensure_table('accounting_daily_1', fields)
check('重复建表幂等', True)

print(f"--- {sum(results)}/{len(results)} passed ---")
sys.exit(0 if all(results) else 1)
