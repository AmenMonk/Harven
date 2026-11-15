# -*- coding: utf-8 -*-
"""
家庭通用提醒引擎 + 日历日程 ★ 2026-09-16
=============================================
- home_reminders 表：全家庭共用的提醒（一次/每天/每周/每月/间隔天数），任何面板/日历/对话都可挂
- home_events 表：家庭日历日程（带前置提醒分钟数）
- inject_due()：engine.send() 入口把到期提醒注入主聊天（方案N同款，失败静默）
- create_from_text()：一句话设提醒（"明天下午3点提醒我交物业费"），本地模型解析，零 token

红线：全程本地模型（Ollama），数据不出本机。
"""
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'

_lock = threading.Lock()

REPEATS = ('once', 'daily', 'weekly', 'monthly', 'interval_days')
# 中文 → repeat 值（对话抽取/面板 select 都用中文）
REPEAT_ZH_MAP = {
    '不重复': 'once', '一次': 'once', '每天': 'daily', '每日': 'daily', '天天': 'daily',
    '每周': 'weekly', '每星期': 'weekly', '每月': 'monthly', '每个月': 'monthly',
    '隔天': 'interval_days', '隔一天': 'interval_days',
}
ADVANCE_ZH_MAP = {
    '不提醒': 0, '提前5分钟': 5, '提前15分钟': 15, '提前30分钟': 30,
    '提前1小时': 60, '提前2小时': 120, '提前1天': 1440,
}


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    with _lock:
        c = _conn()
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS home_reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        module TEXT DEFAULT '',
                        ref_id INTEGER,
                        title TEXT NOT NULL,
                        remind_at TEXT NOT NULL,
                        repeat TEXT DEFAULT 'once',
                        interval_value INTEGER DEFAULT 0,
                        advance INTEGER DEFAULT 0,
                        note TEXT DEFAULT '',
                        notified INTEGER DEFAULT 0,
                        source TEXT DEFAULT 'manual',
                        created_at TEXT DEFAULT (datetime('now','localtime')))""")
            c.execute("""CREATE TABLE IF NOT EXISTS home_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        module TEXT DEFAULT 'home_calendar',
                        title TEXT NOT NULL,
                        event_date TEXT NOT NULL,
                        event_time TEXT DEFAULT '',
                        advance_minutes INTEGER DEFAULT 0,
                        note TEXT DEFAULT '',
                        repeat TEXT DEFAULT 'once',
                        reminder_id INTEGER,
                        created_at TEXT DEFAULT (datetime('now','localtime')))""")
            c.commit()
        finally:
            c.close()


try:
    init_db()
except Exception as _e:
    logger.warning(f"[HomeReminder] 初始化建表延迟: {_e}")


# ══════════════════════════ 时间计算 ══════════════════════════

def _fmt(dt):
    return dt.strftime('%Y-%m-%d %H:%M')


def _parse_dt(s):
    """'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' → datetime（缺时间默认 09:00）"""
    s = (s or '').strip()
    for f in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def next_occurrence(dt, repeat, interval_value=0):
    """重复提醒的下一个时点"""
    if repeat == 'daily':
        return dt + timedelta(days=1)
    if repeat == 'weekly':
        return dt + timedelta(days=7)
    if repeat == 'monthly':
        y, m = dt.year, dt.month + 1
        if m > 12:
            y, m = y + 1, 1
        day = min(dt.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
        return dt.replace(year=y, month=m, day=day)
    if repeat == 'interval_days':
        return dt + timedelta(days=max(1, int(interval_value or 1)))
    return None


# ══════════════════════════ 提醒 CRUD ══════════════════════════

def add_reminder(module='', title='', remind_at='', repeat='once', interval_value=0,
                 advance=0, note='', ref_id=None, source='manual'):
    """新增提醒。remind_at: 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD'（默认09:00）"""
    title = (title or '').strip()
    dt = _parse_dt(remind_at)
    if not title:
        return None, '提醒内容不能为空'
    if not dt:
        return None, f'时间格式不对: {remind_at}（要 YYYY-MM-DD HH:MM）'
    if repeat not in REPEATS:
        repeat = REPEAT_ZH_MAP.get(repeat, 'once')
    if repeat == 'once' and dt < datetime.now():
        return None, '这个时间已经过去了，请选个未来的时间'
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "INSERT INTO home_reminders(module, ref_id, title, remind_at, repeat, interval_value, advance, note, source) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (module or '', ref_id, title[:200], _fmt(dt), repeat, int(interval_value or 0),
                 int(advance or 0), (note or '')[:500], source or 'manual'))
            c.commit()
            # ★ #1220-A：提醒写入 → 数据版本 +1（手机端感知刷新）
            try:
                from src.core.sync_bus import bump
                bump('reminder')
            except Exception:
                pass
            return cur.lastrowid, ''
        finally:
            c.close()


def list_reminders(module='', pending_only=False, limit=200):
    c = _conn()
    try:
        sql, params = "SELECT * FROM home_reminders", []
        wh = []
        if module:
            wh.append("module=?")
            params.append(module)
        if pending_only:
            wh.append("notified=0")
        if wh:
            sql += " WHERE " + " AND ".join(wh)
        sql += " ORDER BY remind_at ASC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def delete_reminder(rid):
    with _lock:
        c = _conn()
        try:
            c.execute("DELETE FROM home_reminders WHERE id=?", (rid,))
            c.commit()
        finally:
            c.close()
    return True


def _complete_row(c, row):
    """完成一条：一次性→打钩；重复→滚到下一次"""
    repeat = row['repeat'] or 'once'
    if repeat == 'once':
        c.execute("UPDATE home_reminders SET notified=1 WHERE id=?", (row['id'],))
    else:
        dt = _parse_dt(row['remind_at']) or datetime.now()
        nxt = next_occurrence(dt, repeat, row['interval_value'])
        if nxt:
            c.execute("UPDATE home_reminders SET remind_at=?, notified=0 WHERE id=?",
                      (_fmt(nxt), row['id']))
        else:
            c.execute("UPDATE home_reminders SET notified=1 WHERE id=?", (row['id'],))


def complete_reminder(rid):
    c = _conn()
    try:
        row = c.execute("SELECT * FROM home_reminders WHERE id=?", (rid,)).fetchone()
        if not row:
            return False, '提醒不存在'
        with _lock:
            _complete_row(c, row)
            c.commit()
        return True, ''
    finally:
        c.close()


# ══════════════════════════ 到期注入 ══════════════════════════

def due_reminders(limit=20):
    """到点未提醒的（只读，不打钩）"""
    now = _fmt(datetime.now())
    c = _conn()
    try:
        rows = c.execute(
            "SELECT * FROM home_reminders WHERE notified=0 AND remind_at<=? ORDER BY remind_at ASC LIMIT ?",
            (now, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def due_count():
    c = _conn()
    try:
        return c.execute(
            "SELECT COUNT(*) FROM home_reminders WHERE notified=0 AND remind_at<=?",
            (_fmt(datetime.now()),)).fetchone()[0]
    finally:
        c.close()


def inject_due(limit=10):
    """engine.send() 入口调用：把到期提醒格式化成消息并打钩（重复的滚到下一轮）。
    返回消息列表（可为空）。失败静默——绝不影响正常聊天。"""
    out = []
    try:
        rows = due_reminders(limit)
        if not rows:
            return out
        with _lock:
            c = _conn()
            try:
                for r in rows:
                    label = {'daily': '每日提醒', 'weekly': '每周提醒', 'monthly': '每月提醒',
                             'interval_days': '循环提醒'}.get(r['repeat'] or 'once', '提醒')
                    adv = f"（提前{r['advance']}分钟）" if r.get('advance') else ''
                    note = f"｜{r['note']}" if r.get('note') else ''
                    out.append(f"⏰ **{label}**{adv}：{r['title']}{note}")
                    _complete_row(c, r)
                c.commit()
            finally:
                c.close()
        if out:
            logger.info(f"[HomeReminder] 注入 {len(out)} 条到期提醒")
    except Exception as e:
        logger.debug(f"[HomeReminder] inject_due 失败: {e}")
        return []
    return out


# ══════════════════════════ 日历日程 CRUD ══════════════════════════

def add_event(module='home_calendar', title='', event_date='', event_time='',
              advance_minutes=0, note='', repeat='once'):
    """新增日程（自动挂一条带前置提醒的提醒）"""
    title = (title or '').strip()
    d = _parse_dt(event_date)
    if not title:
        return None, None, '日程内容不能为空'
    if not d:
        return None, None, f'日期格式不对: {event_date}'
    hh, mm = 9, 0
    if event_time:
        m = re.match(r'^(\d{1,2}):(\d{2})', str(event_time).strip())
        if m:
            hh, mm = min(23, int(m.group(1))), int(m.group(2))
    dt = d.replace(hour=hh, minute=mm)
    adv = max(0, int(advance_minutes or 0))
    remind_dt = dt - timedelta(minutes=adv)
    if repeat not in REPEATS:
        repeat = REPEAT_ZH_MAP.get(repeat, 'once')
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "INSERT INTO home_events(module, title, event_date, event_time, advance_minutes, note, repeat) "
                "VALUES(?,?,?,?,?,?,?)",
                (module or 'home_calendar', title[:200], d.strftime('%Y-%m-%d'),
                 str(event_time or ''), adv, (note or '')[:500], repeat))
            eid = cur.lastrowid
            # 过去的一次性日程不挂提醒
            rid = None
            if not (repeat == 'once' and remind_dt < datetime.now()):
                rc = c.execute(
                    "INSERT INTO home_reminders(module, ref_id, title, remind_at, repeat, interval_value, advance, note, source) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (module or 'home_calendar', eid, f"日程：{title[:180]}", _fmt(remind_dt),
                     repeat, 0, adv, note[:300], 'event'))
                rid = rc.lastrowid
                c.execute("UPDATE home_events SET reminder_id=? WHERE id=?", (rid, eid))
            c.commit()
            return eid, rid, ''
        finally:
            c.close()


def list_events(module='', upcoming_days=0, limit=200):
    c = _conn()
    try:
        sql, params = "SELECT * FROM home_events", []
        wh = []
        if module:
            wh.append("module=?")
            params.append(module)
        if upcoming_days:
            wh.append("event_date>=?")
            params.append(datetime.now().strftime('%Y-%m-%d'))
            wh.append("event_date<=?")
            params.append((datetime.now() + timedelta(days=upcoming_days)).strftime('%Y-%m-%d'))
        if wh:
            sql += " WHERE " + " AND ".join(wh)
        sql += " ORDER BY event_date ASC, event_time ASC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def delete_event(eid):
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT reminder_id FROM home_events WHERE id=?", (eid,)).fetchone()
            if row and row['reminder_id']:
                c.execute("DELETE FROM home_reminders WHERE id=?", (row['reminder_id'],))
            c.execute("DELETE FROM home_events WHERE id=?", (eid,))
            c.commit()
        finally:
            c.close()
    return True


# ══════════════════════════ 一句话设提醒（本地模型） ══════════════════════════

_TIME_WORDS = r'(\d{1,2}[点时][:：]?\d{0,2}分?|上午|下午|晚上|中午|早上|凌晨|中午\d{1,2}点|正午)'


def _regex_fallback(text, today):
    """本地模型不可用时的兜底解析（覆盖常用说法）"""
    now = datetime.now()
    date_s, time_s, repeat = None, '', 'once'
    t = text
    m = re.search(r'每天|每日|天天', t)
    if m:
        repeat = 'daily'
    else:
        m = re.search(r'每周|每星期|每周([一二三四五六日天])', t)
        if m:
            repeat = 'weekly'
        else:
            m = re.search(r'每月', t)
            if m:
                repeat = 'monthly'
    m = re.search(r'(\d+)天后', t)
    if m:
        date_s = (now + timedelta(days=int(m.group(1)))).strftime('%Y-%m-%d')
    elif re.search(r'大后天', t):
        date_s = (now + timedelta(days=3)).strftime('%Y-%m-%d')
    elif re.search(r'后天', t):
        date_s = (now + timedelta(days=2)).strftime('%Y-%m-%d')
    elif re.search(r'明天', t):
        date_s = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    elif re.search(r'今天|今晚', t):
        date_s = today
    else:
        m = re.search(r'周([一二三四五六日天])|星期([一二三四五六日天])', t)
        if m:
            wd = m.group(1) or m.group(2)
            target = '一二三四五六日天'.index(wd) % 7
            delta = (target - now.weekday()) % 7 or 7
            date_s = (now + timedelta(days=delta)).strftime('%Y-%m-%d')
        else:
            m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', t)
            if m:
                mo, dy = int(m.group(1)), int(m.group(2))
                y = now.year if mo >= now.month else now.year + 1
                date_s = f'{y:04d}-{mo:02d}-{dy:02d}'
            else:
                date_s = today
    m = re.search(r'(?:上午|早上)(\d{1,2})[点时](半)?', t)
    if not m:
        m = re.search(r'(?:下午|晚上)(\d{1,2})[点时](半)?', t)
    if not m:
        m = re.search(r'(\d{1,2})[点时](半)?', t)
    if m:
        hh = int(m.group(1))
        if re.search(r'下午|晚上', t) and hh < 12:
            hh += 12
        mm = 30 if m.group(2) else 0
        time_s = f'{hh:02d}:{mm:02d}'
    else:
        m = re.search(r'(\d{1,2}):(\d{2})', t)
        if m:
            time_s = f'{int(m.group(1)):02d}:{m.group(2)}'
    # 标题：去掉时间词后的部分
    title = re.sub(r'(提醒我|提醒|帮我?|记得|别忘了|每天|每周|每月|每星期|今天|明天|后天|大后天|\d+天后|'
                   r'周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}月\d{1,2}[日号]|'
                   r'(上午|早上|下午|晚上|中午)?\d{1,2}[点时](半|:\d{2})?分?|去|要|一下|设定|设置|日程)',
                   '', t).strip(' ，。,.')
    title = title or '提醒'
    return {'title': title[:100], 'date': date_s, 'time': time_s,
            'repeat': {'once': '不重复', 'daily': '每天', 'weekly': '每周', 'monthly': '每月'}.get(repeat, '不重复'),
            'advance': '不提醒'}


def parse_reminder_text(text):
    """一句话 → 提醒结构。返回 (info_dict, err)。info 键：title/date/time/repeat/advance"""
    text = (text or '').strip()
    if not text:
        return None, '没有听到要提醒的内容'
    today = datetime.now().strftime('%Y-%m-%d')
    now_hm = datetime.now().strftime('%H:%M')
    from src.core.kid_archive import call_local_llm
    system = (
        "你是家庭提醒/日程助手，把用户的一句话解析成提醒设置。\n"
        f"今天是 {today}（{'周' + '一二三四五六日'[datetime.now().weekday()]}），现在 {now_hm}。\n"
        "只输出 JSON：{\"title\": \"提醒内容（简洁，去掉时间词）\", \"date\": \"YYYY-MM-DD\", "
        "\"time\": \"HH:MM 或空\", \"repeat\": \"不重复/每天/每周/每月\", "
        "\"advance\": \"不提醒/提前5分钟/提前15分钟/提前30分钟/提前1小时/提前1天\"}。\n"
        "规则：相对时间（明天/后天/周X/3天后）按今天换算；没说时间默认当天 09:00；"
        "没说提前量默认 不提醒；'每天都要…'→每天；'每周X'→每周。只输出 JSON。")
    raw, _model, err = call_local_llm(f"用户说：{text}", system, timeout=120, temperature=0.1)
    info = None
    if not err and raw:
        try:
            start, end = raw.find('{'), raw.rfind('}')
            if start >= 0 and end > start:
                obj = json.loads(raw[start:end + 1])
                info = {
                    'title': str(obj.get('title', ''))[:100],
                    'date': str(obj.get('date', '')),
                    'time': str(obj.get('time', '') or ''),
                    'repeat': str(obj.get('repeat', '不重复') or '不重复'),
                    'advance': str(obj.get('advance', '不提醒') or '不提醒'),
                }
        except Exception:
            info = None
    if not info:
        info = _regex_fallback(text, today)
    # 校验
    if not info.get('date') or not _parse_dt(info['date']):
        info['date'] = today
    if info.get('time') and not re.match(r'^\d{1,2}:\d{2}$', info['time']):
        info['time'] = ''
    if info['repeat'] not in REPEAT_ZH_MAP:
        info['repeat'] = '不重复'
    if info['advance'] not in ADVANCE_ZH_MAP:
        info['advance'] = '不提醒'
    if not info.get('title'):
        info['title'] = text[:50]
    return info, ''


def create_from_text(text, module='', source='chat'):
    """一句话创建提醒。返回 (reminder_id, info, err)"""
    info, err = parse_reminder_text(text)
    if err:
        return None, None, err
    adv = ADVANCE_ZH_MAP.get(info['advance'], 0)
    dt = _parse_dt(info['date'])
    if dt and info['time']:
        hh, mm = info['time'].split(':')
        dt = dt.replace(hour=int(hh), minute=int(mm))
    remind_at = _fmt(dt - timedelta(minutes=adv)) if adv else _fmt(dt) if dt else ''
    rid, err = add_reminder(module=module, title=info['title'], remind_at=remind_at,
                            repeat=REPEAT_ZH_MAP.get(info['repeat'], 'once'),
                            advance=adv, note=f"原话：{text[:200]}", source=source)
    if err:
        return None, info, err
    return rid, info, ''


def describe_info(info):
    """把解析结果转成人话（前端确认气泡用）"""
    if not info:
        return ''
    t = info.get('time') or '09:00'
    adv = info.get('advance') or '不提醒'
    rep = info.get('repeat') or '不重复'
    line = f"📅 {info.get('date', '')} {t}"
    if rep != '不重复':
        line += f"｜{rep}"
    if adv != '不提醒':
        line += f"｜{adv}"
    return f"{line}\n内容：{info.get('title', '')}"
