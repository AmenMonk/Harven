# -*- coding: utf-8 -*-
"""聊天模式无单位金额记账 + 守卫 测试。结果落盘 UTF-8。"""
import sys, sqlite3, json
sys.path.insert(0, r'H:\HarvenAI')
L = []
fails = []
DB = r'H:\HarvenAI\data\sqlite\lingzhi.db'

def cnt():
    return sqlite3.connect(DB).execute('SELECT COUNT(*) FROM panel_accounting_daily_1').fetchone()[0]

def last_row():
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    r = conn.execute('SELECT * FROM panel_accounting_daily_1 ORDER BY id DESC LIMIT 1').fetchone()
    d = dict(r); conn.close(); return d

def dele():
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM panel_accounting_daily_1 WHERE 来源='哈文快记' AND 日期=date('now','localtime') AND (项目 LIKE '%模拟飞行%' OR 项目 LIKE '%水果%' OR 项目 LIKE '%买菜%')")
    conn.commit(); conn.close()

from src.core.engine import _try_smart_split

# 1. 无单位尾随数字（用户实锤场景）
before = cnt()
out = _try_smart_split('去酷航避避暑搜模拟飞行1200', 'test_conv')
ok = out and '已处理' in out and '1200' in out
L.append(('PASS' if ok else 'FAIL') + ' 模拟飞行1200 返回汇总: ' + repr(out))
if not ok: fails.append('t1')
row = last_row()
ok = cnt() == before + 1 and row['项目'] == '模拟飞行' and row['金额'] == 1200.0 and row['kind'] == 'flow'
L.append(('PASS' if ok else 'FAIL') + ' 落库正确: ' + json.dumps({k: row.get(k) for k in ('项目', '金额', '分类', 'kind', '来源')}, ensure_ascii=False))
if not ok: fails.append('t2')

# 2. 疑问句不记账
before = cnt()
out = _try_smart_split('iPhone 7999值得买吗', 'test_conv')
ok = out is None and cnt() == before
L.append(('PASS' if ok else 'FAIL') + ' 疑问句不触发: ' + repr(out))
if not ok: fails.append('t3')

# 3. 时间数字不记账
before = cnt()
out = _try_smart_split('今天13:20开会', 'test_conv')
ok = out is None and cnt() == before
L.append(('PASS' if ok else 'FAIL') + ' 时间13:20不触发: ' + repr(out))
if not ok: fails.append('t4')

# 4. 元结尾回归
out = _try_smart_split('买水果花了35元', 'test_conv')
ok = out and '35' in out
L.append(('PASS' if ok else 'FAIL') + ' 买水果35元回归: ' + repr(out))
if not ok: fails.append('t5')

# 5. 裸数字+动词前缀
out = _try_smart_split('买菜120', 'test_conv')
ok = out and '已处理' in out and '120' in out
L.append(('PASS' if ok else 'FAIL') + ' 买菜120: ' + repr(out))
if not ok: fails.append('t6')

dele()
L.append('清理完成，剩余行数=%d' % cnt())
L.append('----')
L.append('FAILED %d %s' % (len(fails), fails if fails else ''))
with open(r'H:\HarvenAI\tests\_smart_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
sys.exit(1 if fails else 0)
