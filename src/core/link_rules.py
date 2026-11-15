# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
# -*- coding: utf-8 -*-
"""★ 2026-10-11 #1104 联动规则表 + 取数中枢（定版拍板「焦点上下文模式」）

设计三铁律（讨论定稿）：
1. 同一条因果链才连 —— 链①吃→动→身体（餐饮⇄冰箱⇄健身⇄健康档案⇄药箱）；
   链②吃→长（餐饮/冰箱⇄孩子成长⇄健康档案）；链外（记账/车辆/宠物等）一律不连。
2. 取数限 30 天窗口 —— 横幅和 AI 上下文只看近况，不翻旧账。
3. 按成员过滤 —— 传入 member 时只取该成员相关行（成员/member 两列任一命中）。

对外唯一入口 get_cross_context(panel_id) 返回两个产物：
- banners  : 关联动态横幅条目（icon+一句话+跳转目标），前端按当前面板渲染
- ai_context : AI 交织点评用的配角数据文本（panel_assist 注入，零额外 token 成本
  由后台 SQL 组装，不烧任何模型）

数据全部实时查 lingzhi.db 的 panel_* 通用表（数据真家唯一），纯 SQL 零模型调用。
空表/空字段一律优雅跳过——新装用户四表全空也不报错、横幅自动隐藏。
"""
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'

# 默认取数窗口（天）
WINDOW_DAYS = 30
# 「紧急」窗口：药效/食材到期、复查接种提前量
URGENT_MED_DAYS = 30      # 药品效期提前 30 天亮（与药箱提醒 advance_days 对齐）
URGENT_FOOD_DAYS = 7      # 冰箱食材 7 天内到期
URGENT_VISIT_DAYS = 14    # 复查/接种提前 14 天亮（与健康档案提醒对齐）

# ── 配角名片：横幅渲染与 AI 上下文共用（name/icon 与面板定义一致） ──
PARTNERS = {
    'health_file':    {'name': '健康档案', 'icon': '🩺'},
    'med_cabinet':    {'name': '药箱', 'icon': '💊'},
    'fitness_log':    {'name': '健身', 'icon': '🏃'},
    'child_growth':   {'name': '孩子成长', 'icon': '🌱'},
    'meal_diary':     {'name': '餐饮', 'icon': '🍽️'},
    'fridge_stock_1': {'name': '冰箱', 'icon': '🧊'},
}

# ── 联动规则表：主角面板 → 带哪些配角（顺序=横幅展示顺序） ──
# 每条规则：partner 配角表 / kinds 要取的摘要种类 / days 窗口
LINK_RULES = {
    # 健康档案：紧急卡主角，带药箱效期+冰箱临期+健身近况+孩子接种
    'health_file': [
        {'partner': 'med_cabinet', 'kinds': ['expiry'], 'days': URGENT_MED_DAYS},
        {'partner': 'fridge_stock_1', 'kinds': ['expiry'], 'days': URGENT_FOOD_DAYS},
        {'partner': 'fitness_log', 'kinds': ['recent'], 'days': WINDOW_DAYS},
        {'partner': 'child_growth', 'kinds': ['visit'], 'days': URGENT_VISIT_DAYS},
    ],
    # 家庭药箱：带健康档案异常/随访 + 健身近况（链①吃动身体）
    'med_cabinet': [
        {'partner': 'health_file', 'kinds': ['abnormal', 'visit'], 'days': WINDOW_DAYS},
        {'partner': 'fitness_log', 'kinds': ['recent'], 'days': WINDOW_DAYS},
    ],
    # 健身记录：带餐饮近况 + 健康档案异常（吃→动→身体闭环）
    'fitness_log': [
        {'partner': 'meal_diary', 'kinds': ['recent'], 'days': WINDOW_DAYS},
        {'partner': 'fridge_stock_1', 'kinds': ['expiry'], 'days': URGENT_FOOD_DAYS},
        {'partner': 'health_file', 'kinds': ['abnormal'], 'days': WINDOW_DAYS},
    ],
    # 孩子成长：带孩子餐饮 + 健康档案孩子部分（定家引用卡：疫苗/体检去健康档案）
    'child_growth': [
        {'partner': 'meal_diary', 'kinds': ['recent'], 'days': WINDOW_DAYS},
        {'partner': 'health_file', 'kinds': ['kid_visit', 'kid_abnormal'], 'days': WINDOW_DAYS},
        {'partner': 'fridge_stock_1', 'kinds': ['expiry'], 'days': URGENT_FOOD_DAYS},
    ],
}

# 各配角表的日期列（用于窗口过滤；None=无日期列不做窗口过滤）
_DATE_COL = {
    'health_file': '日期',
    'med_cabinet': '有效期',
    'fitness_log': '日期',
    'child_growth': '日期',
    'meal_diary': '日期',
    'fridge_stock_1': '到期日',
}

# 未来日期列：到期/复查/接种这类「看将来」的字段
_FUTURE_COL = {
    'med_cabinet': '有效期',
    'health_file': '下次日期',
    'child_growth': '下次接种日期',
    'fridge_stock_1': '到期日',
}

_TODAY = None  # 可注入测试日期，生产为 None=今天


def _today() -> date:
    return _TODAY or date.today()


def _parse_date(val) -> 'date | None':
    """宽松解析 2026-10-11 / 2026/10/11 / 2026.10.11 / 带时间等格式。"""
    if not val:
        return None
    s = str(val).strip()
    m = re.match(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})', s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _fetch_rows(panel_id: str, days: int) -> list[dict]:
    """取配角近 N 天行（含未来日期列命中项），按成员过滤由调用方做。"""
    table = f'panel_{panel_id}'
    try:
        from src.models.database import get_connection
        conn = get_connection(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        if not cols:
            conn.close()
            return []
        where, params = [], []
        dcol = _DATE_COL.get(panel_id)
        if dcol and dcol in cols:
            since = (_today() - timedelta(days=days)).isoformat()
            where.append(f'("{dcol}" >= ? OR "{dcol}" = "" OR "{dcol}" IS NULL)')
            params.append(since)
        sql = f'SELECT * FROM "{table}"'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY id DESC LIMIT 200'
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f'[link_rules] 取数失败 {panel_id}: {e}')
        return []


def _member_ok(row: dict, member: str | None) -> bool:
    """成员过滤：member 为空=全家庭；否则 成员/member 列任一包含即算命中。"""
    if not member:
        return True
    vals = [str(row.get('成员') or ''), str(row.get('member') or ''),
            str(row.get('服用成员') or '')]
    return any(member in v or v in member for v in vals if v)


def _days_left(val) -> 'int | None':
    d = _parse_date(val)
    if not d:
        return None
    return (d - _today()).days


# ── 各配角摘要生成器：返回横幅一句话列表 ──

def _summarize_expiry(rows: dict, days: int) -> list[str]:
    """效期类：只留 N 天内到期/临期，剩天数人话化。"""
    out = []
    for r in rows:
        dl = _days_left(r.get('到期日') or r.get('有效期'))
        if dl is None or dl > days:
            continue
        name = r.get('药品名称') or r.get('名称') or '物品'
        if dl < 0:
            out.append(f'{name} 已过期{abs(dl)}天，该清理了')
        elif dl <= 3:
            out.append(f'{name} 还剩{dl}天到期，抓紧')
        else:
            out.append(f'{name} 剩{dl}天到期')
    return out


def _summarize_recent_meal(rows: dict) -> list[str]:
    """餐饮近况：次数+外卖次数+热量均值。"""
    if not rows:
        return []
    takeout = sum(1 for r in rows if '外卖' in str(r.get('备注') or ''))
    cals = [float(r['估算热量']) for r in rows
            if r.get('估算热量') not in (None, '')]
    line = f'近30天记录{len(rows)}餐'
    if takeout:
        line += f'、外卖{takeout}次'
    if cals:
        line += f'、均热量约{int(sum(cals)/len(cals))}千卡'
    return [line]


def _summarize_recent_fitness(rows: dict) -> list[str]:
    """健身近况：次数+最近一次体重。"""
    if not rows:
        return []
    line = f'近30天运动{len(rows)}次'
    for r in rows:  # id DESC，第一条带体重的就是最近体重
        w = r.get('体重kg')
        if w not in (None, ''):
            line += f'，最近体重{w}kg'
            break
    return [line]


def _summarize_visit(rows: dict, date_key: str, name_key: str,
                     who_key: str, days: int) -> list[str]:
    """复查/接种类：N 天内将到期的下次日期。"""
    out = []
    for r in rows:
        dl = _days_left(r.get(date_key))
        if dl is None or dl > days:
            continue
        who = r.get(who_key) or ''
        what = r.get(name_key) or '复查'
        when = r.get(date_key)
        if dl < 0:
            out.append(f'{who} {what} 已过期，记得安排')
        else:
            out.append(f'{who} {what} 约在{when}（还剩{dl}天）')
    return out


def _summarize_abnormal(rows: dict) -> list[str]:
    """健康档案异常提示：只挑 异常提示 列非空的。"""
    out = []
    for r in rows:
        ab = str(r.get('异常提示') or '').strip()
        if not ab:
            continue
        who = r.get('成员') or ''
        item = r.get('项目') or r.get('类型') or '健康事件'
        out.append(f'{who} {item}：{ab}')
    return out[:3]  # 最多 3 条，横幅不刷屏


def _build_partner_lines(partner: str, kinds: list[str], days: int,
                         member: str | None) -> list[str]:
    """一个配角按 kinds 产出横幅短句列表（已含前缀由调用方拼）。"""
    rows = _fetch_rows(partner, days)
    rows = [r for r in rows if _member_ok(r, member)]
    lines = []
    for kind in kinds:
        if kind == 'expiry':
            lines += _summarize_expiry(rows, days)
        elif kind == 'recent':
            if partner == 'meal_diary':
                lines += _summarize_recent_meal(rows)
            elif partner == 'fitness_log':
                lines += _summarize_recent_fitness(rows)
        elif kind == 'visit':
            if partner == 'health_file':
                lines += _summarize_visit(rows, '下次日期', '项目', '成员', days)
            elif partner == 'child_growth':
                lines += _summarize_visit(rows, '下次接种日期', '疫苗记录', '成员', days)
        elif kind == 'abnormal':
            lines += _summarize_abnormal(rows)
        elif kind == 'kid_visit':
            kid_rows = [r for r in rows if '孩' in str(r.get('成员') or '')
                        or '娃' in str(r.get('成员') or '')]
            lines += _summarize_visit(kid_rows, '下次日期', '项目', '成员', days)
        elif kind == 'kid_abnormal':
            kid_rows = [r for r in rows if '孩' in str(r.get('成员') or '')
                        or '娃' in str(r.get('成员') or '')]
            lines += _summarize_abnormal(kid_rows)
    return lines


# ── 对外唯一入口 ──

def get_cross_context(panel_id: str, member: str | None = None) -> dict:
    """★ 联动取数中枢：主角面板 → {banners, ai_context}

    - banners: [{'icon','name','text','target'}]，前端横幅 chip 渲染+点击跳转
    - ai_context: 多行文本，供 panel_assist/对话注入（空数据返回 ''）
    纯 SQL 组装，零模型调用；任何异常都安全降级为空结果。
    """
    rules = LINK_RULES.get(panel_id)
    if not rules:
        return {'panel_id': panel_id, 'banners': [], 'ai_context': ''}

    banners, ctx_lines = [], []
    try:
        for rule in rules:
            partner = rule['partner']
            info = PARTNERS.get(partner, {'name': partner, 'icon': '📌'})
            lines = _build_partner_lines(partner, rule['kinds'], rule['days'], member)
            if not lines:
                continue
            for ln in lines[:3]:  # 每配角横幅最多 3 条
                banners.append({'icon': info['icon'], 'name': info['name'],
                                'text': ln, 'target': partner})
            ctx_lines.append(f'【{info["name"]}】' + '；'.join(lines[:5]))
        ai_context = '\n'.join(f'- {l}' for l in ctx_lines)
    except Exception as e:
        logger.warning(f'[link_rules] get_cross_context({panel_id}) 异常: {e}')
        return {'panel_id': panel_id, 'banners': [], 'ai_context': ''}

    return {'panel_id': panel_id, 'banners': banners, 'ai_context': ai_context}


def get_context_for_ai(panel_id: str, member: str | None = None) -> str:
    """便捷封装：只要 AI 上下文文本（panel_assist 注入用）。"""
    return get_cross_context(panel_id, member).get('ai_context', '')
