# -*- coding: utf-8 -*-
"""面板教练引擎（2026-10-22 定版拍板「开发·一批」）
使命：让面板从「录入列表」变成「会主动说话的教练」——数据录进去必须有人消费。
三层能力：
  ① observe(panel_id)：打开面板时的实时观察（纯本地统计 0 token）+ 1~3 个行动按钮
  ② daily_brief()：每日早报（生日/临期/目标节点/计划日聚合）
  ③ REMINDER_SOURCES：提醒源注册表——面板声明日期字段，自动进 reminder_daemon
行动按钮协议 actions=[{label, action:'navigate'|'prefill'|'api', ...}]
  navigate: {path}  |  prefill: {q}（跳聊天自动发）  |  api: {method,url,body}
诚实原则：观察全部基于真实数据本地计算；措辞先肯定再建议，不吓唬人。
"""
import json, logging, sqlite3
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _rows(panel_id, limit=60):
    """读面板最近记录（panel_<id> 表，容错空表/缺表）"""
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            f'SELECT * FROM panel_{panel_id} ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _date_of(row):
    for k in ('日期', 'created_at', 'date'):
        v = str(row.get(k) or '')[:10]
        if len(v) == 10 and v[4] == '-':
            return v
    return ''


# ════════════════ 健身 ════════════════
def observe_fitness_log():
    rows = _rows('fitness_log')
    if not rows:
        return {'text': '还没有运动记录。录第一次开始，我来帮你盯节奏、看趋势（频次/体重/睡眠都算）。', 'level': 'info',
                'actions': [{'label': '📷 录今天运动', 'action': 'form'}]}
    today = datetime.now().date()
    week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    week = [r for r in rows if _date_of(r) >= week_ago]
    last = rows[0]
    last_date = _date_of(last)
    try:
        gap = (today - datetime.strptime(last_date, '%Y-%m-%d').date()).days
    except Exception:
        gap = 99
    # 体重趋势（最近≥3条有体重值）
    weights = [(r['_d'], float(r['体重kg'])) for r in
               [(dict(**r, _d=_date_of(r)) for r in rows) ] if False]  # 占位防错
    weights = []
    for r in rows:
        try:
            w = float(r.get('体重kg') or 0)
            d = _date_of(r)
            if w > 20 and d:
                weights.append((d, w))
        except Exception:
            pass
    weights.sort()
    trend = ''
    if len(weights) >= 3:
        diff = weights[-1][1] - weights[0][1]
        if abs(diff) >= 0.3:
            trend = f'，体重{"降" if diff < 0 else "涨"}了 {abs(diff):.1f}kg'
    # 小腿（计划呼应 assist_hint：练两天歇一天，练小腿）
    calf = [r for r in week if '小腿' in str(r.get('运动项目') or '')]
    text = f'本周练了 {len(week)} 次{trend}，上次运动是 {last_date}。'
    level = 'info'
    actions = [{'label': '📷 记录今天', 'action': 'prefill',
                'q': f'记健身：' }]
    if gap >= 2:
        text = f'已经 {gap} 天没动了。本周 {len(week)} 次{trend}。休息可以，别断链——今天动 20 分钟就算赢。'
        level = 'urge'
        actions.insert(0, {'label': '💪 就练 20 分钟', 'action': 'prefill', 'q': '记健身：今天运动了20分钟，'})
    elif calf and len(calf) == 0:
        pass
    if '小腿' not in json.dumps([r.get('运动项目') for r in week], ensure_ascii=False):
        actions.append({'label': '🦵 今天练小腿？', 'action': 'prefill', 'q': '记健身：今天练了小腿，'})
    actions.append({'label': '📊 看周报', 'action': 'navigate', 'path': '/panel/fitness_log'})
    return {'text': text, 'level': level, 'actions': actions[:3]}


# ════════════════ 冰箱 ════════════════
def observe_fridge_stock_1():
    rows = _rows('fridge_stock_1')
    today = datetime.now().date()
    expiring, expired, names = [], [], []
    for r in rows:
        name = r.get('名称') or r.get('物品') or ''
        exp = str(r.get('保质期') or r.get('到期日') or '')[:10]
        if not name or len(exp) != 10:
            continue
        try:
            d = datetime.strptime(exp, '%Y-%m-%d').date()
        except ValueError:
            continue
        days = (d - today).days
        if days < 0:
            expired.append(name)
        elif days <= 3:
            expiring.append((name, days, exp))
    low_txt = ''
    if not rows:
        return {'text': '冰箱还是空的。把买回来的东西拍进来，临期我会提前一天喊你。', 'level': 'info',
                'actions': [{'label': '📷 记入冰箱', 'action': 'form'}]}
    if expired:
        level = 'urge'
        text = f'有 {len(expired)} 项已过期（{("、".join(expired[:3]))}{'…' if len(expired) > 3 else ""}），建议清掉。'
        actions = [{'label': '🧹 已清理，记一下', 'action': 'prefill', 'q': '冰箱清理：刚扔掉'}]
    elif expiring:
        level = 'urge'
        soon = expiring[0]
        day_txt = '今天' if soon[1] == 0 else f'{soon[1]} 天后'
        text = f'{soon[0]}{day_txt}到期' + (f'，共 {len(expiring)} 项临期' if len(expiring) > 1 else '') + '——今天吃掉正好。'
        actions = [{'label': '🍳 用临期食材出菜谱', 'action': 'prefill',
                    'q': '冰箱里有这些快到期的食材：' + '、'.join(n for n, _, _ in expiring[:4]) + '。基于它们给我出今晚的菜谱'},
                   {'label': '🛒 补货清单', 'action': 'prefill', 'q': '看看冰箱里缺什么，给我一份补货清单'}]
    else:
        level = 'info'
        text = f'冰箱 {len(rows)} 项库存都健康，3 天内没有临期。'
        actions = [{'label': '🍳 今天吃什么', 'action': 'prefill', 'q': '看看冰箱库存给我出今晚的菜单'},
                   {'label': '📷 记入新食材', 'action': 'navigate', 'path': '/panel/fridge_stock_1'}]
    return {'text': text, 'level': level, 'actions': actions[:3]}


# ════════════════ 家人健康 ════════════════
def observe_health_file():
    try:
        from src.core.panel_manager import get_panel_manager
        from src.core.panel_manager import get_panel_manager as _g  # 兼容
        members = get_panel_manager().get_members()
    except Exception:
        members = []
    if not members:
        return {'text': '还没有家人档案。录上生日自动提醒、忌口做饭自动避开。', 'level': 'info',
                'actions': [{'label': '👨‍👩‍👧 去添加家人', 'action': 'form'}]}
    today = datetime.now().date()
    lines = []
    level = 'info'
    for m in members:
        name = m.get('name', '')
        b = str(m.get('birthday') or '')
        if len(b) >= 10:
            try:
                bd = datetime.strptime(b[:10], '%Y-%m-%d').date()
                this_year = bd.replace(year=today.year)
                days = (this_year - today).days
                if 0 <= days <= 30:
                    lines.append(f'{name}生日 {days} 天后（记得准备）')
                    level = 'urge'
            except ValueError:
                pass
    actions = [{'label': '📋 看家人资料', 'action': 'navigate', 'path': '/panel/health_file'},
               {'label': '🩺 录健康事件', 'action': 'prefill', 'q': '记一条健康事件：'}]
    if lines:
        return {'text': '；'.join(lines[:2]) + '。其他家人数据都正常。', 'level': level, 'actions': actions}
    updated = 0
    return {'text': f'{len(members)} 位家人的资料都在，生日/复查我会自动盯着。', 'level': 'info', 'actions': actions}




# ════════════════ 二批：记账/学习/药箱/车辆 ════════════════
def observe_accounting_daily_1():
    rows = _rows('accounting_daily_1', 200)
    if not rows:
        return {'text': '还没有记账。说一句「47.6 炸酱面」就能记，攒起来我帮你看出钱去向。', 'level': 'info',
                'actions': [{'label': '💰 记一笔', 'action': 'form'}]}
    today = datetime.now().date()
    month_ago = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    this_month = [(str(r.get('分类') or '其他'), float(r.get('金额') or 0)) for r in rows if _date_of(r) >= month_ago]
    total = sum(a for _, a in this_month if a > 0)
    by_cat = {}
    for cat, a in this_month:
        if a > 0:
            by_cat[cat] = by_cat.get(cat, 0) + a
    top = sorted(by_cat.items(), key=lambda x: -x[1])[:1]
    text = f'本月 {len(this_month)} 笔共 {total:.0f} 元。'
    level = 'info'
    if top and total > 0 and top[0][1] / total > 0.4:
        text += f'「{top[0][0]}」占了大头（{top[0][1]:.0f} 元，{top[0][1]/total*100:.0f}%）。'
    # ★ 2026-10-22 花销目标进度：横幅直接显示「已花 X / 目标 Y」
    actions = [{'label': '💰 记一笔', 'action': 'form'},
               {'label': '📊 看分类占比', 'action': 'navigate', 'path': '/panel/accounting_daily_1'}]
    try:
        from src.core.goal_manager import list_goals, query_metric
        for g in (list_goals(status='active', limit=30) or []):
            if (g.get('panel_id') or '') != 'accounting_daily_1':
                continue
            for i, n in enumerate(g.get('plan') or []):
                # ★ 2026-10-23 日预算：daily_spend_sum（当日求和）与月度 spend_sum 一起展示
                if n.get('metric') not in ('spend_sum', 'daily_spend_sum') or n.get('status') == 'done':
                    continue
                spent, target, _ = query_metric(g['id'], i)
                if not target:
                    continue
                pct = int(spent / target * 100)
                scope = '今天' if n.get('metric') == 'daily_spend_sum' else '本月'
                text = f'{scope}已花 {spent:.0f} / 目标 {target} 元（{pct}%）。' + text
                if pct >= 100:
                    level = 'urge'
                    text += f'⚠️ 已超目标 {spent - target:.0f} 元！'
                    actions.insert(0, {'label': '📉 看钱花哪了', 'action': 'prefill',
                                       'q': f'本月已花 {spent:.0f} 元超出目标 {target} 元，帮我看分类占比，指出最该省的地方'})
                elif pct >= 80:
                    level = 'urge'
                    text += f'接近上限，剩 {target - spent:.0f} 元额度。'
    except Exception:
        pass
    return {'text': text, 'level': level, 'actions': actions}


def observe_study_center():
    try:
        from src.core.study_coach import wrongbook_summary
        wb = wrongbook_summary()
    except Exception:
        wb = {'total': 0, 'by_knowledge': []}
    rows = _rows('study_center')
    n_grades = len(rows)
    last_rate = rows[0].get('正确率') if rows else None
    if not wb['total'] and not n_grades:
        return {'text': '还没有批改记录。拍一次作业，错题本和学情分析就自动开始了。', 'level': 'info',
                'actions': [{'label': '📸 拍作业批改', 'action': 'navigate', 'path': '/panel/study_center'}]}
    text = f'已批改 {n_grades} 次'
    if last_rate not in (None, ''):
        text += f'，最近正确率 {last_rate}%'
    if wb['total']:
        weak = wb['by_knowledge'][0]
        text += f'。错题本 {wb["total"]} 道待复习，最薄弱：「{weak["knowledge_point"]}」（错 {weak["count"]} 次）'
    level = 'urge' if wb['total'] >= 5 else 'info'
    actions = [{'label': '📕 复习错题', 'action': 'navigate', 'path': '/panel/study_center'}]
    if wb['total']:
        actions.append({'label': '🎯 针对薄弱设目标', 'action': 'prefill',
                        'q': f'孩子错题本里「{wb["by_knowledge"][0]["knowledge_point"]}」错了 {wb["by_knowledge"][0]["count"]} 次，帮我定一个两周攻克它的阶段目标，节奏别太赶，带休息安排。'})
    return {'text': text, 'level': level, 'actions': actions[:3]}


def observe_med_cabinet():
    rows = _rows('med_cabinet', 100)
    if not rows:
        return {'text': '药箱还是空的。把家里的常备药录进来，有效期前 30 天我自动提醒补货。', 'level': 'info',
                'actions': [{'label': '💊 录药品', 'action': 'form'}]}
    today = datetime.now().date()
    soon, expired = [], []
    for r in rows:
        d = str(r.get('有效期') or '')[:10]
        name = r.get('药品名称') or ''
        if len(d) != 10 or not name:
            continue
        try:
            days = (datetime.strptime(d, '%Y-%m-%d').date() - today).days
        except ValueError:
            continue
        if days < 0:
            expired.append(name)
        elif days <= 90:
            soon.append((name, days))
    if expired:
        return {'text': f'{("、".join(expired[:3]))} 已过期，建议清掉补新。', 'level': 'urge',
                'actions': [{'label': '💊 去处理', 'action': 'navigate', 'path': '/panel/med_cabinet'}]}
    if soon:
        s0 = soon[0]
        return {'text': f'{s0[0]} {s0[1]} 天后到期' + (f'（共 {len(soon)} 项临近效期）' if len(soon) > 1 else '') + '，到 30 天内我会再提醒。', 'level': 'info',
                'actions': [{'label': '💊 看药箱', 'action': 'navigate', 'path': '/panel/med_cabinet'}]}
    return {'text': f'药箱 {len(rows)} 项，效期都在安全范围。', 'level': 'info',
            'actions': [{'label': '💊 看药箱', 'action': 'navigate', 'path': '/panel/med_cabinet'}]}


def observe_car_fuel_1():
    rows = _rows('car_fuel_1', 60)
    if not rows:
        return {'text': '还没有加油记录。录两次就能算真实油耗和每公里成本。', 'level': 'info',
                'actions': [{'label': '⛽ 记加油', 'action': 'form'}]}
    last = rows[0]
    last_d = _date_of(last)
    try:
        gap = (datetime.now().date() - datetime.strptime(last_d, '%Y-%m-%d').date()).days
    except Exception:
        gap = 99
    eco = [r.get('油耗') for r in rows[:3] if r.get('油耗')]
    text = f'上次加油 {last_d}（{gap} 天前）'
    if eco:
        text += f'，近期油耗 {eco[0]}'
    text += '。'
    level = 'info'
    actions = [{'label': '⛽ 记加油', 'action': 'form'}]
    return {'text': text, 'level': level, 'actions': actions}


def observe_car_maintenance_1():
    rows = _rows('car_maintenance_1', 20)
    if not rows:
        return {'text': '还没有保养记录。录上次保养的里程和日期，到里程我提醒你。', 'level': 'info',
                'actions': [{'label': '🔧 记保养', 'action': 'form'}]}
    today = datetime.now().date()
    last = rows[0]
    last_d = _date_of(last)
    nxt = str(last.get('下次保养日') or '')[:10]
    if len(nxt) == 10:
        try:
            days = (datetime.strptime(nxt, '%Y-%m-%d').date() - today).days
            if days < 0:
                return {'text': f'保养已过期 {abs(days)} 天（计划 {nxt}）——该去保养了。', 'level': 'urge',
                        'actions': [{'label': '🔧 记本次保养', 'action': 'form'}]}
            if days <= 14:
                return {'text': f'下次保养 {nxt}（还有 {days} 天），可以预约了。', 'level': 'urge',
                        'actions': [{'label': '🔧 记上次里程', 'action': 'form'}]}
            return {'text': f'上次保养 {last_d}，下次 {nxt}（还有 {days} 天），正常。', 'level': 'info',
                    'actions': [{'label': '🔧 看保养史', 'action': 'navigate', 'path': '/panel/car_maintenance_1'}]}
        except ValueError:
            pass
    return {'text': f'上次保养 {last_d}。补上「下次保养日」，我就能自动盯着。', 'level': 'info',
            'actions': [{'label': '🔧 补保养计划', 'action': 'form'}]}




# ════════════════ 三批：植物/宠物/孩子成长 ════════════════
def observe_plants_1():
    rows = _rows('plants_1', 60)
    if not rows:
        return {'text': '还没有植物档案。录上名字和浇水周期，该浇水我提前喊你。', 'level': 'info',
                'actions': [{'label': '🌿 录植物', 'action': 'form'}]}
    today = datetime.now().date()
    due, overdue, ok = [], [], 0
    for r in rows:
        name = r.get('植物名') or ''
        try:
            cycle = int(float(r.get('浇水周期天数') or 0))
        except Exception:
            cycle = 0
        last = str(r.get('最近浇水') or '')[:10]
        if not name or not cycle or len(last) != 10:
            continue
        try:
            next_d = datetime.strptime(last, '%Y-%m-%d').date() + timedelta(days=cycle)
        except ValueError:
            continue
        days = (next_d - today).days
        if days < 0:
            overdue.append(f'{name}（晚了 {abs(days)} 天）')
        elif days <= 1:
            due.append(name)
        else:
            ok += 1
    if overdue:
        return {'text': f'{("、".join(overdue[:2]))} 该浇水了——植物渴了不会说话，我替它们喊。', 'level': 'urge',
                'actions': [{'label': '💧 浇好了，记录', 'action': 'navigate', 'path': '/panel/plants_1'}]}
    if due:
        return {'text': f'今天该浇水：{"、".join(due[:3])}。', 'level': 'urge',
                'actions': [{'label': '💧 浇好了，记录', 'action': 'navigate', 'path': '/panel/plants_1'}]}
    return {'text': f'{len(rows)} 盆植物都喝饱了，近期没有浇水任务。', 'level': 'info',
            'actions': [{'label': '🌿 看植物', 'action': 'navigate', 'path': '/panel/plants_1'}]}


def observe_pet_care_1():
    rows = _rows('pet_care_1', 60)
    if not rows:
        return {'text': '还没有宠物记录。疫苗/驱虫/洗澡录上「下次时间」，到点我提醒。', 'level': 'info',
                'actions': [{'label': '🐶 录宠物事项', 'action': 'form'}]}
    today = datetime.now().date()
    soon, overdue = [], []
    for r in rows:
        nxt = str(r.get('下次时间') or '')[:10]
        item = r.get('事项') or '事项'
        name = r.get('宠物名') or '宠物'
        if len(nxt) != 10:
            continue
        try:
            days = (datetime.strptime(nxt, '%Y-%m-%d').date() - today).days
        except ValueError:
            continue
        if days < 0:
            overdue.append(f'{name}的{item}（过期 {abs(days)} 天）')
        elif days <= 7:
            soon.append(f'{name}的{item} {days} 天后')
    if overdue:
        return {'text': '；'.join(overdue[:2]) + '——该安排了。', 'level': 'urge',
                'actions': [{'label': '🐶 去安排', 'action': 'navigate', 'path': '/panel/pet_care_1'}]}
    if soon:
        return {'text': '；'.join(soon[:2]) + '。', 'level': 'urge',
                'actions': [{'label': '🐶 看宠物档案', 'action': 'navigate', 'path': '/panel/pet_care_1'}]}
    return {'text': f'宠物事项 {len(rows)} 条都在正轨上。', 'level': 'info',
            'actions': [{'label': '🐶 看宠物档案', 'action': 'navigate', 'path': '/panel/pet_care_1'}]}


def observe_child_growth():
    try:
        conn = _conn()
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM kid_timeline ORDER BY id DESC LIMIT 1').fetchall()]
        n_tl = conn.execute('SELECT COUNT(*) FROM kid_timeline').fetchone()[0]
        n_gr = conn.execute('SELECT COUNT(*) FROM kid_growth').fetchone()[0]
        conn.close()
    except Exception:
        rows, n_tl, n_gr = [], 0, 0
    if not rows and not n_gr:
        return {'text': '孩子的成长档案还是空的——第一次走路、第一次获奖，都值得记下来。', 'level': 'info',
                'actions': [{'label': '🌱 去记录', 'action': 'navigate', 'path': '/panel/kid_archive'}]}
    gap_txt = ''
    if rows:
        ts = str(rows[0].get('ts') or '')[:10]
        try:
            gap = (datetime.now().date() - datetime.strptime(ts, '%Y-%m-%d').date()).days
            if gap >= 14:
                gap_txt = f'成长时间轴已经 {gap} 天没记了——最近有什么小进步？'
        except ValueError:
            pass
    text = f'成长时间轴 {n_tl} 条、体格记录 {n_gr} 条。' + gap_txt
    return {'text': text, 'level': 'urge' if gap_txt else 'info',
            'actions': [{'label': '🌱 记一笔成长', 'action': 'navigate', 'path': '/panel/kid_archive'},
                        {'label': '📚 去学习中心', 'action': 'navigate', 'path': '/panel/study_center'}]}


COACH_RULES = {
    'fitness_log': observe_fitness_log,
    'fridge_stock_1': observe_fridge_stock_1,
    'health_file': observe_health_file,
    'accounting_daily_1': observe_accounting_daily_1,
    'study_center': observe_study_center,
    'med_cabinet': observe_med_cabinet,
    'car_fuel_1': observe_car_fuel_1,
    'car_maintenance_1': observe_car_maintenance_1,
    'plants_1': observe_plants_1,
    'pet_care_1': observe_pet_care_1,
    'child_growth': observe_child_growth,
}


def _strip_form_actions(actions):
    """★ 2026-10-24 定版拍板方案 A：手动录入只留面板「＋ 添加」一个入口——
    教练横幅不再出「录一条/记一笔」类 form 按钮（点了打开的也是同一个表单，纯重复）。
    各 observe 规则里的 form 动作不用挨个删，出口统一过滤；navigate/photo 等动作保留。"""
    return [a for a in (actions or []) if a.get('action') != 'form']


def observe(panel_id):
    fn = COACH_RULES.get(panel_id)
    if not fn:
        # ★ 2026-10-22 通用兜底：没有专属规则的面板也给观察+指标（数据项没有也显示）
        rows = _rows(panel_id)
        n = len(rows)
        week = _count_since(rows, 7)
        text = (f'共 {n} 条记录，本周新增 {week} 条。' if n else
                '这个模块还没有记录——数据录进来我才能帮你分析和提醒。')
        return {'success': True, 'panel_id': panel_id, 'level': 'info', 'text': text,
                'stats': stats_for(panel_id),
                'actions': _strip_form_actions(
                    [{'label': '＋ 录一条', 'action': 'form', 'path': f'/panel/{panel_id}'}])}
    try:
        r = fn()
        r['success'] = True
        r['panel_id'] = panel_id
        r['stats'] = stats_for(panel_id)
        r['actions'] = _strip_form_actions(r.get('actions'))
        return r
    except Exception as e:
        logger.exception('[CoachEngine] 观察失败')
        return {'success': False, 'error': str(e)[:150]}




# ════════════════ 数据指标（数据项即使没有也显示 0/—，参考成熟 App 指标集）════════════════
def _count_since(rows, days):
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    return sum(1 for r in rows if _date_of(r) >= since)


def stats_for(panel_id):
    """[{label, value, unit}] 指标 chips——通用指标 + 域专属，没数据显示 0/—"""
    rows = _rows(panel_id, 200)
    out = [
        {'label': '总记录', 'value': len(rows), 'unit': '条'},
        {'label': '本周新增', 'value': _count_since(rows, 7), 'unit': '条'},
        {'label': '本月新增', 'value': _count_since(rows, 30), 'unit': '条'},
    ]
    if rows:
        last_d = _date_of(rows[0])
        if last_d:
            try:
                gap = (datetime.now().date() - datetime.strptime(last_d, '%Y-%m-%d').date()).days
                out.append({'label': '最近记录', 'value': '今天' if gap == 0 else f'{gap}天前', 'unit': ''})
            except Exception:
                pass
    if panel_id == 'fitness_log':
        durs = []
        wts = []
        sleep_n = 0
        for r in rows:
            try:
                du = float(r.get('时长分钟') or 0)
                if du > 0:
                    durs.append(du)
            except Exception:
                pass
            try:
                w = float(r.get('体重kg') or 0)
                if w > 20:
                    wts.append(w)
            except Exception:
                pass
            if r.get('昨晚睡眠小时'):
                sleep_n += 1
        out.append({'label': '30天平均时长', 'value': round(sum(durs) / len(durs)) if durs else 0, 'unit': '分钟'})
        out.append({'label': '最新体重', 'value': wts[0] if wts else '—', 'unit': 'kg' if wts else ''})
        out.append({'label': '有睡眠记录', 'value': sleep_n, 'unit': '条'})
    elif panel_id == 'fridge_stock_1':
        today = datetime.now().date()
        exp, expiring = 0, 0
        for r in rows:
            d = str(r.get('到期日') or '')[:10]
            if len(d) != 10:
                continue
            try:
                days = (datetime.strptime(d, '%Y-%m-%d').date() - today).days
            except ValueError:
                continue
            if days < 0:
                exp += 1
            elif days <= 3:
                expiring += 1
        out += [{'label': '临期(3天内)', 'value': expiring, 'unit': '项'},
                {'label': '已过期', 'value': exp, 'unit': '项'}]
    elif panel_id == 'health_file':
        try:
            from src.core.panel_manager import get_panel_manager
            ms = get_panel_manager().get_members()
            out.append({'label': '家人', 'value': len(ms), 'unit': '位'})
            out.append({'label': '有生日', 'value': sum(1 for m in ms if m.get('birthday')), 'unit': '位'})
            out.append({'label': '有忌口', 'value': sum(1 for m in ms if m.get('allergy')), 'unit': '位'})
        except Exception:
            pass
    elif panel_id == 'accounting_daily_1':
        try:
            amts = []
            for r in rows:
                try:
                    amts.append(float(r.get('金额') or 0))
                except Exception:
                    pass
            out.append({'label': '累计支出', 'value': round(sum(amts), 1), 'unit': '元'})
        except Exception:
            pass
    return out




# ════════════════ 花销目标核查（记账入库钩子）════════════════
def check_spend_goals():
    """★ 2026-10-22 记账入库后调用：核查所有 spend_sum 花销型目标。
    返回超支/接近告警列表 [{title, spent, target, pct}]，供当场反馈与横幅。"""
    import sys as _sys
    try:
        from src.core.goal_manager import list_goals, query_metric
    except Exception:
        return []
    alerts = []
    for g in (list_goals(status='active', limit=50) or []):
        plan = g.get('plan') or []
        for i, n in enumerate(plan):
            # ★ 2026-10-23 日预算核查：daily_spend_sum（今天已花）与月度 spend_sum 一起盯
            if n.get('metric') not in ('spend_sum', 'daily_spend_sum') or n.get('status') == 'done':
                continue
            try:
                spent, target, _ = query_metric(g['id'], i)
            except Exception:
                continue
            if not target:
                continue
            pct = round(spent / target * 100)
            scope = '今天' if n.get('metric') == 'daily_spend_sum' else '本月'
            # ★ 2026-10-25 bug A 修复：告警带稳定字段（goal_id/metric/kind），
            #   供 butler_agenda 生成不含动态金额的稳定 dedup_key，杜绝「一件事不断提示」
            if pct >= 100:
                alerts.append({'goal_id': g['id'], 'goal_title': g['title'], 'metric': n.get('metric'),
                               'scope': scope, 'kind': 'over',
                               'title': f'⚠️ 花销目标「{g["title"]}」已超：{scope}已花 {spent:.0f} / 上限 {target} 元',
                               'spent': spent, 'target': target, 'pct': pct})
            elif pct >= 80:
                alerts.append({'goal_id': g['id'], 'goal_title': g['title'], 'metric': n.get('metric'),
                               'scope': scope, 'kind': 'near',
                               'title': f'🟠 接近上限：「{g["title"]}」{scope}已花 {spent:.0f} / {target} 元（{pct}%）',
                               'spent': spent, 'target': target, 'pct': pct})
    return alerts


# ════════════════ 每日早报 ════════════════
def daily_brief():
    """今日清单：生日/临期/目标节点/计划日——聚合为早报条目"""
    items = []
    today = datetime.now().date()
    # 生日（公历+农历已由 upcoming_birthdays 双轨）
    try:
        from src.core.panel_manager import get_panel_manager
        for b in get_panel_manager().upcoming_birthdays(days_ahead=3):
            items.append({'kind': 'birthday', 'text': f"🎂 {b['name']}的生日还有 {b['days_until']} 天"})
    except Exception:
        pass
    # 冰箱临期
    rows = _rows('fridge_stock_1', 120)
    for r in rows:
        exp = str(r.get('保质期') or r.get('到期日') or '')[:10]
        name = r.get('名称') or ''
        if len(exp) == 10 and name:
            try:
                d = datetime.strptime(exp, '%Y-%m-%d').date()
                days = (d - today).days
                if 0 <= days <= 1:
                    items.append({'kind': 'expiring', 'text': f"🥛 {name}{'今天' if days == 0 else '明天'}到期"})
            except ValueError:
                pass
    # 目标节点（今天到期）
    try:
        from src.core.goal_manager import due_nodes
        for n in due_nodes():
            items.append({'kind': 'goal', 'text': f"🎯 目标节点：{n.get('title', '')}（进度 {n.get('done')}/{n.get('total')}）"})
    except Exception:
        pass
    return {'success': True, 'date': today.strftime('%Y-%m-%d'), 'items': items}


# ════════════════ 提醒源注册表（已收编）════════════════
# ★ 2026-10-24 #1176 原 REMINDER_SOURCES + due_source_reminders 整体迁入
#   src/core/butler_agenda.py 的 DECLARATIONS 声明注册表（管家统一扫描分发），
#   此处不再保留副本，避免双头维护。reminder_daemon 也不再调用本模块扫描。


# ════════════════ 路由 ════════════════
from flask import Blueprint, request, jsonify

bp = Blueprint('coach_engine', __name__, url_prefix='/api/coach')


@bp.route('/observe', methods=['GET'])
def api_observe():
    return jsonify(observe(request.args.get('panel_id', '').strip()))


@bp.route('/brief', methods=['GET'])
def api_brief():
    return jsonify(daily_brief())


@bp.route('/stats', methods=['GET'])
def api_stats():
    return jsonify({'success': True, 'panel_id': request.args.get('panel_id', ''),
                    'stats': stats_for(request.args.get('panel_id', '').strip())})
