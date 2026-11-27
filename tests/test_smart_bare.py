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

def pending_cnt():
    return sqlite3.connect(DB).execute(
        "SELECT COUNT(*) FROM assist_pending WHERE panel_id='accounting_daily_1'").fetchone()[0]

def clear_pending():
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM assist_pending WHERE panel_id='accounting_daily_1' AND "
              "(records_json LIKE '%模拟飞行%' OR records_json LIKE '%水果%' OR records_json LIKE '%买菜%')")
    c.commit(); c.close()

# 1. 无单位尾随数字（用户实锤场景） ★ #1587 对齐待确认机制（#1377 改版后不直入）
# 预清理：clear 上一轮残留卡，规避 add_pending 10 分钟去重窗口（重跑可重复）
clear_pending()
before = cnt()
p_before = pending_cnt()
out = _try_smart_split('去酷航避避暑搜模拟飞行1200', 'test_conv')
ok = out and '待确认' in out and '1200' in out
L.append(('PASS' if ok else 'FAIL') + ' 模拟飞行1200 返回待确认话术: ' + repr(out))
if not ok: fails.append('t1')
ok = cnt() == before and pending_cnt() == p_before + 1
L.append(('PASS' if ok else 'FAIL') + ' 进待确认队列且面板表不直入: ' + f'{p_before}->{pending_cnt()} 表{before}->{cnt()}')
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

# 5. 裸数字+动词前缀 ★ #1587 话术断言对齐待确认机制
out = _try_smart_split('买菜120', 'test_conv')
ok = out and '待确认' in out and '120' in out
L.append(('PASS' if ok else 'FAIL') + ' 买菜120: ' + repr(out))
if not ok: fails.append('t6')

clear_pending()
dele()
L.append('清理完成，剩余行数=%d' % cnt())
L.append('----')
L.append('FAILED %d %s' % (len(fails), fails if fails else ''))
with open(r'H:\HarvenAI\tests\_smart_result.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
sys.exit(1 if fails else 0)
