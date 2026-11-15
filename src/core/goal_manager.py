# -*- coding: utf-8 -*-
"""
HarvenAI Goals 目标管理（2026-10-20 第二批 2.2）
愿望→节点计划→日常盯催（本地0token）→节点审核→偏差三选
数据家：lingzhi.db 新表 goals；structured_tasks 加 goal_id 列
token 铁律：日常盯催全本地硬指标查询，外脑只在拆解(~3k)与节点审核(~2k)出场
"""
import os, json, sqlite3, logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    from src.models.database import DEFAULT_DB_PATH
    _DB_PATH = os.path.normpath(DEFAULT_DB_PATH)
except ImportError:
    _DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'sqlite', 'lingzhi.db')

_HARDFIELD_PANEL_PREFIX = 'panel_'  # 面板记录表前缀（数据真家）


def _conn():
    return sqlite3.connect(_DB_PATH)


def ensure_tables():
    """建 goals 表 + structured_tasks 补 goal_id 列（幂等，import 即就绪）"""
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            plan TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            panel_id TEXT DEFAULT '',
            token_spent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )''')
        # structured_tasks 补 goal_id
        cols = [r[1] for r in c.execute("PRAGMA table_info(structured_tasks)").fetchall()]
        if cols and 'goal_id' not in cols:
            c.execute("ALTER TABLE structured_tasks ADD COLUMN goal_id INTEGER DEFAULT NULL")
    logger.info("[Goals] 数据表就绪")


ensure_tables()


# ---------- CRUD ----------

def create_goal(title, nodes, panel_id='', token_spent=0, auto_tasks=True):
    """nodes: [{name, due, metric, target}]  metric: task_done|record_count|photo_count|manual
    ★ 2026-10-20 补实装：auto_tasks=True 时每个节点自动生成一条任务（带 🎯 标签+goal_id），
      进任务中枢统一列表（方案 2.2 步骤6）"""
    plan = []
    for n in nodes:
        plan.append({
            'name': str(n.get('name', '')).strip(),
            'due': str(n.get('due', ''))[:10],
            'metric': n.get('metric', 'manual'),
            'target': int(n.get('target', 1) or 1),
            'status': 'pending',   # pending|done|missed
        })
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO goals (title, plan, status, panel_id, token_spent) VALUES (?,?,?,?,?)",
            (title.strip(), json.dumps(plan, ensure_ascii=False), 'active', panel_id, token_spent))
        gid = cur.lastrowid
    # ★ 节点→任务自动生成（失败不阻塞建档）
    if auto_tasks:
        try:
            from src.core.task_manager import get_task_manager
            tm = get_task_manager()
            for idx, n in enumerate(plan):
                tid = tm.create_task(conversation_id='', title=f"{title} · {n['name']}",
                                     description=f"目标节点 {idx+1}/{len(plan)}",
                                     priority=2, tags=[f'🎯{title}'], due_date=n['due'])
                with _conn() as c:
                    c.execute("UPDATE structured_tasks SET goal_id=? WHERE id=?", (gid, tid))
        except Exception as e:
            logger.warning(f"[Goals] 节点任务生成失败(不影响目标): {e}")
    logger.info(f"[Goals] 建目标 #{gid}: {title[:30]} 节点x{len(plan)} 自动任务={'Y' if auto_tasks else 'N'}")
    return gid


def get_goal(gid):
    with _conn() as c:
        row = c.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    return _row2dict(row) if row else None


def list_goals(status=None, limit=50, panel_id=None):
    with _conn() as c:
        sql, params = "SELECT * FROM goals WHERE 1=1", []
        if status:
            sql += " AND status=?"; params.append(status)
        if panel_id:
            sql += " AND panel_id=?"; params.append(panel_id)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        rows = c.execute(sql, params).fetchall()
    return [_row2dict(r) for r in rows]


def _row2dict(row):
    """按列名安全转换"""
    with _conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(goals)").fetchall()]
    raw = dict(zip(cols, row))
    try:
        raw['plan'] = json.loads(raw.get('plan') or '[]')
    except Exception:
        raw['plan'] = []
    return raw


def update_goal(gid, title=None, status=None, plan=None, token_add=0):
    with _conn() as c:
        if title is not None:
            c.execute("UPDATE goals SET title=?, updated_at=datetime('now','localtime') WHERE id=?", (title, gid))
        if status is not None:
            c.execute("UPDATE goals SET status=?, updated_at=datetime('now','localtime') WHERE id=?", (status, gid))
        if plan is not None:
            c.execute("UPDATE goals SET plan=?, updated_at=datetime('now','localtime') WHERE id=?",
                      (json.dumps(plan, ensure_ascii=False), gid))
        if token_add:
            c.execute("UPDATE goals SET token_spent=token_spent+?, updated_at=datetime('now','localtime') WHERE id=?",
                      (token_add, gid))
    return get_goal(gid)


def delete_goal(gid):
    with _conn() as c:
        c.execute("UPDATE structured_tasks SET goal_id=NULL WHERE goal_id=?", (gid,))
        c.execute("DELETE FROM goals WHERE id=?", (gid,))
    return True


# ---------- 硬指标（本地毫秒级，0 token） ----------

def query_metric(gid, node_idx):
    """查节点硬指标完成数。锁 panel_id+时间窗（节点创建/上节点完成 → 节点 due）"""
    g = get_goal(gid)
    if not g:
        return 0, 0, '目标不存在'
    plan = g.get('plan') or []
    if node_idx >= len(plan):
        return 0, 0, '节点不存在'
    node = plan[node_idx]
    target = int(node.get('target', 1))
    metric = node.get('metric', 'manual')

    if metric == 'manual':
        return (target if node.get('status') == 'done' else 0), target, 'manual'

    # 时间窗：窗口起点=上一节点 due（无则目标创建日），终点=本节点 due
    try:
        win_end = node.get('due') or datetime.now().strftime('%Y-%m-%d')
        win_start = (plan[node_idx - 1].get('due') if node_idx > 0 else None) \
                    or (g.get('created_at') or '')[:10] or '2000-01-01'
        # ★ #1508（2026-11-13）：节点已过期时把窗口终点顶到今天——到期后补录的记录
        #   也算完成（验收发生在今天；死守过期日会让"创建即过期"的目标指标永远 0）
        _today_s = datetime.now().strftime('%Y-%m-%d')
        if win_end < _today_s:
            win_end = _today_s
        if metric == 'task_done':
            with _conn() as c:
                n = c.execute(
                    "SELECT COUNT(*) FROM structured_tasks WHERE goal_id=? AND status='completed' "
                    "AND substr(created_at,1,10)>=? AND substr(created_at,1,10)<=?",
                    (gid, win_start, win_end)).fetchone()[0]
            return min(n, target), target, 'task_done'
        if metric in ('record_count', 'photo_count'):
            panel_id = g.get('panel_id') or ''
            if not panel_id:
                return 0, target, metric
            table = f"{_HARDFIELD_PANEL_PREFIX}{panel_id}"
            with _conn() as c:
                cols = [r[1] for r in c.execute(f"PRAGMA table_info(\"{table}\")").fetchall()]
                if not cols:
                    return 0, target, metric
                tcol = 'created_at' if 'created_at' in cols else ('时间' if '时间' in cols else None)
                if not tcol:
                    n = c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                else:
                    n = c.execute(f'SELECT COUNT(*) FROM "{table}" WHERE substr("{tcol}",1,10)>=? AND substr("{tcol}",1,10)<=?',
                                  (win_start, win_end)).fetchone()[0]
            return min(n, target), target, metric
        # ★ 2026-10-23 日预算（定版拍板「开发全部」）：daily_spend_sum=当日 0 点起金额求和
        #   （target=每日上限），与月度 spend_sum 同一套金额查询，仅时间窗固定为今天
        if metric == 'daily_spend_sum':
            _today = datetime.now().strftime('%Y-%m-%d')
            win_start = win_end = _today
        # ★ 2026-10-22 花销型指标（定版拍板）：spend_sum=窗口内金额求和（target=上限）
        #   进度=真实已花金额（不截断）；「花满 target」触达一次/天 → 「你超预算了」
        if metric in ('spend_sum', 'daily_spend_sum'):
            panel_id = g.get('panel_id') or ''
            if not panel_id:
                return 0, target, metric
            table = f"{_HARDFIELD_PANEL_PREFIX}{panel_id}"
            with _conn() as c:
                cols = [r[1] for r in c.execute(f"PRAGMA table_info(\"{table}\")").fetchall()]
                if not cols:
                    return 0, target, metric
                acol = '金额' if '金额' in cols else None
                tcol = 'created_at' if 'created_at' in cols else ('日期' if '日期' in cols else None)
                if not acol:
                    return 0, target, metric
                where = f'CAST("{acol}" AS REAL) > 0'
                args = []
                if tcol:
                    where += ' AND substr("{t}",1,10)>=? AND substr("{t}",1,10)<=?'.replace('{t}', tcol)
                    args = [win_start, win_end]
                total = c.execute(f'SELECT COALESCE(SUM(CAST("{acol}" AS REAL)),0) FROM "{table}" WHERE {where}',
                                  args).fetchone()[0]
            return round(float(total or 0), 1), target, metric
    except Exception as e:
        logger.warning(f"[Goals] 硬指标查询异常: {e}")
    return 0, target, metric


def audit_node(gid, node_idx):
    """节点审核（本地部分）：返回审核卡数据（外脑总结由调用方决定是否调）"""
    g = get_goal(gid)
    node = (g or {}).get('plan', [])[node_idx]
    done, target, metric = query_metric(gid, node_idx)
    return {
        'goal_id': gid, 'node_idx': node_idx, 'node_name': node.get('name', ''),
        'due': node.get('due', ''), 'done': done, 'target': target,
        'metric': metric, 'pass': done >= target,
    }


def set_node_status(gid, node_idx, status):
    g = get_goal(gid)
    plan = g.get('plan', [])
    if 0 <= node_idx < len(plan):
        plan[node_idx]['status'] = status
        update_goal(gid, plan=plan)
    return get_goal(gid)


def defer_node(gid, node_idx, new_due):
    """偏差卡·顺延：本地重排 0 token；后续节点自动顺延同样天数"""
    g = get_goal(gid)
    plan = g.get('plan', [])
    if not (0 <= node_idx < len(plan)):
        return g
    try:
        old = datetime.strptime(plan[node_idx]['due'], '%Y-%m-%d')
        new = datetime.strptime(new_due, '%Y-%m-%d')
        delta = (new - old).days
    except Exception:
        delta = 7
    for i in range(node_idx, len(plan)):
        try:
            d = datetime.strptime(plan[i]['due'], '%Y-%m-%d') + timedelta(days=delta)
            plan[i]['due'] = d.strftime('%Y-%m-%d')
        except Exception:
            pass
        if i == node_idx:
            plan[i]['status'] = 'pending'
    return update_goal(gid, plan=plan)


def lower_target(gid, node_idx, new_target):
    """偏差卡·降目标：本地改参数 0 token"""
    g = get_goal(gid)
    plan = g.get('plan', [])
    if 0 <= node_idx < len(plan):
        plan[node_idx]['target'] = max(1, int(new_target))
        return update_goal(gid, plan=plan)
    return g


def due_nodes(days_ahead=0):
    """调度用：查未来 N 天内到期（或已过期）的活跃节点（reminder_daemon 挂钩）"""
    today = datetime.now().strftime('%Y-%m-%d')
    horizon = (datetime.now() + timedelta(days=max(0, days_ahead))).strftime('%Y-%m-%d')
    out = []
    for g in list_goals(status='active', limit=200):
        for i, node in enumerate(g.get('plan') or []):
            if node.get('status') != 'pending':
                continue
            due = node.get('due', '')
            if due and today <= due <= horizon:
                out.append({'goal_id': g['id'], 'goal_title': g['title'],
                            'node_idx': i, 'node_name': node.get('name', ''),
                            'due': due, 'panel_id': g.get('panel_id', '')})
    return out


def summary_card(g):
    """进度卡数据（工作台/手机）"""
    plan = g.get('plan') or []
    total = len(plan)
    done = sum(1 for n in plan if n.get('status') == 'done')
    next_node = next((n for n in plan if n.get('status') == 'pending'), None)
    # 状态灯：末节点已过期未完成=🔴脱轨；当前节点临期(≤2天)=🟡；否则🟢
    light = '🟢'
    today = datetime.now().strftime('%Y-%m-%d')
    overdue = [n for n in plan if n.get('status') == 'pending' and n.get('due', '9999') < today]
    if overdue:
        light = '🔴'
    elif next_node and (next_node.get('due', '9999-99') <= (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')):
        light = '🟡'
    return {
        'id': g['id'], 'title': g['title'], 'status': g['status'],
        'progress': round(done * 100 / total) if total else 0,
        'done': done, 'total': total,
        'next_node': next_node, 'light': light,
        'token_spent': g.get('token_spent', 0), 'panel_id': g.get('panel_id', ''),
    }
