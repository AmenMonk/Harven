# -*- coding: utf-8 -*-
"""
孩子成长档案系统 · 数据层
=====================================
四层架构：记录入口层 → 档案库(分类档案+时间轴) → AI分析层 → 呈现层

三条红线（设计铁律）：
1. 不逐句上报 —— 聊天归档只存"摘要"，不存原始对话
2. 原始对话、照片永远不出本机
3. 心理类暗档分析默认只走本地模型

Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import os
import json
import re
import sqlite3
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'
MEDIA_DIR = BASE_DIR / 'data' / 'media' / 'kid'

_lock = threading.Lock()

# ══════════════════════════ 建表 ══════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    nickname TEXT DEFAULT '',
    gender TEXT DEFAULT '',
    birthday TEXT DEFAULT '',
    school TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    class_name TEXT DEFAULT '',
    avatar TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    is_default INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    ts TEXT DEFAULT (datetime('now','localtime')),
    category TEXT DEFAULT '',
    event_type TEXT DEFAULT '',
    title TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    source TEXT DEFAULT 'parent',
    importance TEXT DEFAULT 'normal',
    ref_table TEXT DEFAULT '',
    ref_id INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_timeline_child ON kid_timeline(child_id, ts DESC);
CREATE TABLE IF NOT EXISTS kid_learning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    score TEXT DEFAULT '',
    weakness TEXT DEFAULT '',
    action TEXT DEFAULT '',
    status TEXT DEFAULT '进行中',
    photo TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_mistakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    question TEXT DEFAULT '',
    wrong_reason TEXT DEFAULT '',
    correct TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    mastered INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    htype TEXT DEFAULT '体检',
    item TEXT DEFAULT '',
    value TEXT DEFAULT '',
    place TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_growth (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    height REAL DEFAULT 0,
    weight REAL DEFAULT 0,
    head_circ REAL DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_traits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    tcategory TEXT DEFAULT '喜好',
    content TEXT DEFAULT '',
    note TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    rtype TEXT DEFAULT '同学',
    name TEXT DEFAULT '',
    relation_desc TEXT DEFAULT '',
    note TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_clothing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    ctype TEXT DEFAULT '上装',
    size TEXT DEFAULT '',
    brand TEXT DEFAULT '',
    note TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    category TEXT DEFAULT '学习用品',
    name TEXT DEFAULT '',
    location TEXT DEFAULT '',
    status TEXT DEFAULT '使用中',
    photo TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    age_rating TEXT DEFAULT '',
    daily_limit INTEGER DEFAULT 30,
    time_window TEXT DEFAULT '',
    status TEXT DEFAULT '允许',
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_game_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    game_id INTEGER DEFAULT 0,
    date TEXT DEFAULT '',
    minutes INTEGER DEFAULT 0,
    over_limit INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_interests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    name TEXT DEFAULT '',
    icategory TEXT DEFAULT '美术',
    place TEXT DEFAULT '',
    teacher TEXT DEFAULT '',
    schedule TEXT DEFAULT '',
    start_date TEXT DEFAULT '',
    status TEXT DEFAULT '在学',
    cost TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_interest_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    interest_id INTEGER DEFAULT 0,
    date TEXT DEFAULT '',
    mood TEXT DEFAULT '喜欢',
    progress TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    rtype TEXT DEFAULT 'event',
    title TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    due_at TEXT DEFAULT '',
    repeat_rule TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    rtype TEXT DEFAULT 'on_demand',
    domain TEXT DEFAULT 'overview',
    content TEXT DEFAULT '',
    model TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_digest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT (date('now','localtime')),
    visible_summary TEXT DEFAULT '',
    hidden_analysis TEXT DEFAULT '',
    level TEXT DEFAULT 'normal',
    model TEXT DEFAULT '',
    conv_id TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_diet (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    dcategory TEXT DEFAULT '喜欢',
    food TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
-- ★ 2026-09-18 学习域扩容：作业/考试/进度 + 健康域扩容：视力
CREATE TABLE IF NOT EXISTS kid_homework (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    content TEXT DEFAULT '',
    duration TEXT DEFAULT '',
    status TEXT DEFAULT '已完成',
    quality TEXT DEFAULT '',
    hw_type TEXT DEFAULT '',
    independent TEXT DEFAULT '',
    wrong_count TEXT DEFAULT '',
    mood TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    exam_name TEXT DEFAULT '',
    score TEXT DEFAULT '',
    full_score TEXT DEFAULT '',
    rank TEXT DEFAULT '',
    class_avg TEXT DEFAULT '',
    vs_last TEXT DEFAULT '',
    weakness TEXT DEFAULT '',
    action TEXT DEFAULT '',
    parent_note TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    grade_level TEXT DEFAULT '',
    chapter TEXT DEFAULT '',
    progress_pct TEXT DEFAULT '',
    mastery TEXT DEFAULT '',
    expect_date TEXT DEFAULT '',
    teacher_progress TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
-- ★ 2026-09-18 学习域 P0：阅读打卡（书名/时长/页码/孩子自评/一句话感想）
CREATE TABLE IF NOT EXISTS kid_reading (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    book_name TEXT DEFAULT '',
    minutes TEXT DEFAULT '',
    pages TEXT DEFAULT '',
    rating TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
-- ★ 2026-09-19 #792 学习P1：习惯打卡（阅读30分钟/练字/口算/跳绳…每天一条，streak=连续天数自动算）
CREATE TABLE IF NOT EXISTS kid_habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    habit TEXT DEFAULT '',
    done TEXT DEFAULT '',
    minutes TEXT DEFAULT '',
    streak TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_vision (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    vtype TEXT DEFAULT '裸眼',
    left_eye TEXT DEFAULT '',
    right_eye TEXT DEFAULT '',
    degree TEXT DEFAULT '',
    lens_type TEXT DEFAULT '',
    outdoor_min TEXT DEFAULT '',
    screen_min TEXT DEFAULT '',
    recheck_date TEXT DEFAULT '',
    place TEXT DEFAULT '',
    need_glasses TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
-- ★ 2026-09-18 成长档案加强：零花钱台账（财商启蒙，压岁钱/给钱/花销/结余）
CREATE TABLE IF NOT EXISTS kid_money (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    mtype TEXT DEFAULT '花销',
    amount TEXT DEFAULT '',
    item TEXT DEFAULT '',
    source TEXT DEFAULT '',
    balance TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kid_settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
-- ★ 2026-10-14 #1147 学习中枢：周课表（周几·节次·科目；term=学期标记）
CREATE TABLE IF NOT EXISTS kid_timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    weekday INTEGER DEFAULT 1,
    period TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    teacher TEXT DEFAULT '',
    term TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
-- ★ 2026-10-14 #1147 学习中枢：校历（放假/考试周/开学/活动；date 起，days 天）
CREATE TABLE IF NOT EXISTS kid_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    date TEXT DEFAULT '',
    days INTEGER DEFAULT 1,
    ctype TEXT DEFAULT '放假',
    title TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
-- ★ 2026-10-14 #1149 学习中枢：薄弱点图谱（学科×知识点×错误计数，批改/错题/考试自动汇入）
CREATE TABLE IF NOT EXISTS kid_weakpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    subject TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    wrong_count INTEGER DEFAULT 0,
    last_date TEXT DEFAULT '',
    mastered INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_weakpoints_child ON kid_weakpoints(child_id, subject);
"""

# 领域注册表：域名 → (表名, 时间轴分类, 时间轴事件模板, 字段列表)
DOMAINS = {
    'learning':      ('kid_learning', 'learning', '学习记录',
                      ['date', 'subject', 'topic', 'score', 'weakness', 'action', 'status', 'photo', 'notes']),
    # ★ 2026-09-18 学习域扩容：作业/考试/进度拆开（原 learning 保留兼容旧数据）
    # ★ 2026-09-18 加强：选填字段全部入域（表单折叠展开，对话只说核心字段也能录）
    'homework':      ('kid_homework', 'learning', '作业记录',
                      ['date', 'subject', 'content', 'duration', 'status', 'quality',
                       'hw_type', 'independent', 'wrong_count', 'mood', 'photo', 'notes']),
    'exam':          ('kid_exams', 'learning', '考试测验',
                      ['date', 'subject', 'exam_name', 'score', 'full_score', 'rank',
                       'class_avg', 'vs_last', 'weakness', 'action', 'parent_note', 'photo', 'notes']),
    'progress':      ('kid_progress', 'learning', '学习进度',
                      ['date', 'subject', 'grade_level', 'chapter', 'progress_pct', 'mastery',
                       'expect_date', 'teacher_progress', 'notes']),
    # ★ 2026-09-18 学习域 P0：阅读打卡（借鉴阅读考级/习惯打卡类 App）
    'reading':       ('kid_reading', 'learning', '阅读打卡',
                      ['date', 'book_name', 'minutes', 'pages', 'rating', 'comment', 'notes']),
    # ★ 2026-09-19 #792 学习P1：习惯打卡（连续天数 streak 由 insert_domain 自动计算）
    'habits':        ('kid_habits', 'learning', '习惯打卡',
                      ['date', 'habit', 'done', 'minutes', 'streak', 'notes']),
    'mistakes':      ('kid_mistakes', 'learning', '错题',
                      ['date', 'subject', 'question', 'wrong_reason', 'correct', 'photo', 'mastered', 'notes']),
    'health':        ('kid_health', 'health', '健康记录',
                      ['date', 'htype', 'item', 'value', 'temp', 'medicine', 'next_visit', 'place', 'photo', 'notes']),
    # ★ 2026-09-18 健康域扩容：视力
    'vision':        ('kid_vision', 'health', '视力记录',
                      ['date', 'vtype', 'left_eye', 'right_eye', 'degree', 'lens_type',
                       'outdoor_min', 'screen_min', 'recheck_date', 'place', 'need_glasses', 'notes']),
    'growth':        ('kid_growth', 'health', '生长发育',
                      ['date', 'height', 'weight', 'head_circ', 'notes']),
    'traits':        ('kid_traits', 'trait', '特性',
                      ['tcategory', 'content', 'scene', 'response', 'followup', 'note']),
    'relations':     ('kid_relations', 'trait', '人际关系',
                      ['rtype', 'name', 'relation_desc', 'closeness', 'attention', 'note']),
    'clothing':      ('kid_clothing', 'clothing', '服饰尺码',
                      ['ctype', 'size', 'brand', 'season', 'buy_date', 'price', 'next_size', 'note']),
    'items':         ('kid_items', 'item', '物品',
                      ['category', 'name', 'location', 'status', 'buy_date', 'price', 'dispose', 'photo', 'note']),
    'games':         ('kid_games', 'game', '游戏档案',
                      ['name', 'platform', 'age_rating', 'daily_limit', 'time_window', 'status', 'note']),
    'game_logs':     ('kid_game_logs', 'game', '游戏时长',
                      ['game_id', 'date', 'minutes', 'over_limit', 'note']),
    'interests':     ('kid_interests', 'interest', '兴趣课程',
                      ['name', 'icategory', 'place', 'teacher', 'schedule', 'start_date', 'status',
                       'total_lessons', 'used_lessons', 'expire_date', 'absence', 'cost', 'note']),
    'interest_logs': ('kid_interest_logs', 'interest', '兴趣反馈',
                      ['interest_id', 'date', 'mood', 'progress', 'note']),
    'diet':          ('kid_diet', 'diet', '饮食',
                      ['date', 'dcategory', 'food', 'reason', 'reaction', 'substitute', 'notes']),
    # ★ 2026-09-18 财商启蒙：零花钱台账（压岁钱/给钱/花销/结余）
    'money':         ('kid_money', 'money', '零花钱',
                      ['date', 'mtype', 'amount', 'item', 'source', 'balance', 'notes']),
}

TIMELINE_IMPORTANT = {
    # 出现这些关键词的记录 → 关注级
    'weakness': 'watch', 'wrong_reason': 'watch',
}


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ★ 2026-09-18 成长档案加强：老库补列（CREATE IF NOT EXISTS 不会给已存在的表加新字段）
# 表名 → [(列名, 声明), ...]；与 _SCHEMA 里的新表定义保持一致
_MIGRATE_COLUMNS = {
    'kid_homework': [('hw_type', "TEXT DEFAULT ''"), ('independent', "TEXT DEFAULT ''"),
                     ('wrong_count', "TEXT DEFAULT ''"), ('mood', "TEXT DEFAULT ''")],
    'kid_exams': [('class_avg', "TEXT DEFAULT ''"), ('vs_last', "TEXT DEFAULT ''"),
                  ('parent_note', "TEXT DEFAULT ''")],
    'kid_progress': [('expect_date', "TEXT DEFAULT ''"), ('teacher_progress', "TEXT DEFAULT ''")],
    'kid_vision': [('degree', "TEXT DEFAULT ''"), ('lens_type', "TEXT DEFAULT ''"),
                   ('outdoor_min', "TEXT DEFAULT ''"), ('screen_min', "TEXT DEFAULT ''"),
                   ('recheck_date', "TEXT DEFAULT ''")],
    'kid_growth': [('head_circ', 'REAL DEFAULT 0')],
    'kid_health': [('temp', "TEXT DEFAULT ''"), ('medicine', "TEXT DEFAULT ''"),
                   ('next_visit', "TEXT DEFAULT ''")],
    'kid_diet': [('reaction', "TEXT DEFAULT ''"), ('substitute', "TEXT DEFAULT ''")],
    'kid_traits': [('scene', "TEXT DEFAULT ''"), ('response', "TEXT DEFAULT ''"),
                   ('followup', "TEXT DEFAULT ''")],
    'kid_relations': [('closeness', "TEXT DEFAULT ''"), ('attention', "TEXT DEFAULT ''")],
    'kid_clothing': [('season', "TEXT DEFAULT ''"), ('buy_date', "TEXT DEFAULT ''"),
                     ('price', "TEXT DEFAULT ''"), ('next_size', "TEXT DEFAULT ''")],
    'kid_items': [('buy_date', "TEXT DEFAULT ''"), ('price', "TEXT DEFAULT ''"),
                  ('dispose', "TEXT DEFAULT ''")],
    'kid_interests': [('total_lessons', "TEXT DEFAULT ''"), ('used_lessons', "TEXT DEFAULT ''"),
                      ('expire_date', "TEXT DEFAULT ''"), ('absence', "TEXT DEFAULT ''")],
}


def init_db():
    with _lock:
        c = _conn()
        try:
            c.executescript(_SCHEMA)
            for table, cols in _MIGRATE_COLUMNS.items():
                try:
                    existing = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
                except Exception:
                    continue
                for name, decl in cols:
                    if name not in existing:
                        c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {decl}')
            c.commit()
        finally:
            c.close()
    logger.info("[KidArchive] 数据表就绪")


# ══════════════════════════ 设置 ══════════════════════════

DEFAULT_SETTINGS = {
    'chat_archival_enabled': 'false',   # 聊天自动归档（默认关）
    'digest_level': 'balanced',         # 暗档分析级别 strict/balanced/full
    'ai_engine': 'local',               # AI分析引擎 local（红线3：默认本地）
    'quiet_start': '21:30',             # 孩子端免打扰开始
    'quiet_end': '07:00',               # 免打扰结束
    'weekly_report_day': 'sun',         # 周报生成日
    'default_model': 'qwen2.5:14b',     # 本地分析模型
    'role': 'parent',                   # 当前角色 parent/child（前端状态镜像）
}


def get_settings():
    c = _conn()
    try:
        rows = c.execute("SELECT key, value FROM kid_settings").fetchall()
        s = dict(DEFAULT_SETTINGS)
        for r in rows:
            s[r['key']] = r['value']
        return s
    finally:
        c.close()


def save_settings(d):
    with _lock:
        c = _conn()
        try:
            for k, v in d.items():
                c.execute("INSERT INTO kid_settings(key,value) VALUES(?,?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
            c.commit()
        finally:
            c.close()
    return get_settings()


# ══════════════════════════ 时间轴 ══════════════════════════

def add_timeline(child_id, category, event_type, title, detail='', source='parent',
                 importance='normal', ref_table='', ref_id=0):
    with _lock:
        c = _conn()
        try:
            c.execute(
                "INSERT INTO kid_timeline(child_id, category, event_type, title, detail, source, importance, ref_table, ref_id) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (child_id, category, event_type, title, detail, source, importance, ref_table, ref_id))
            c.commit()
            return c.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            c.close()


# ══════════════════════════ 孩子主档案 ══════════════════════════

def get_children():
    c = _conn()
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM children ORDER BY is_default DESC, id").fetchall()]
        return rows
    finally:
        c.close()


def get_child(child_id):
    c = _conn()
    try:
        r = c.execute("SELECT * FROM children WHERE id=?", (child_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


# ══════════════════════════ 学习中枢（★ #1147，2026-10-14）════════════════════════

def list_timetable(child_id, weekday=0):
    with _lock:
        c = _conn()
        try:
            if weekday:
                rows = c.execute('SELECT * FROM kid_timetable WHERE child_id=? AND weekday=? ORDER BY id',
                                 (child_id, int(weekday))).fetchall()
            else:
                rows = c.execute('SELECT * FROM kid_timetable WHERE child_id=? ORDER BY weekday, id',
                                 (child_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def save_timetable(child_id, items):
    """整表覆盖式保存（前端一次提交一周课表）"""
    with _lock:
        c = _conn()
        try:
            c.execute('DELETE FROM kid_timetable WHERE child_id=?', (child_id,))
            for it in (items or [])[:80]:
                c.execute('INSERT INTO kid_timetable(child_id,weekday,period,subject,teacher,term,notes) '
                          'VALUES(?,?,?,?,?,?,?)',
                          (child_id, int(it.get('weekday', 1) or 1), str(it.get('period', ''))[:20],
                           str(it.get('subject', ''))[:20], str(it.get('teacher', ''))[:20],
                           str(it.get('term', ''))[:20], str(it.get('notes', ''))[:100]))
            c.commit()
        finally:
            c.close()
    return {'success': True, 'count': len(items or [])}


def list_calendar(child_id, upcoming_only=False):
    with _lock:
        c = _conn()
        try:
            sql = 'SELECT * FROM kid_calendar WHERE child_id=?'
            if upcoming_only:
                sql += " AND date >= date('now','localtime','-1 day')"
            rows = c.execute(sql + ' ORDER BY date', (child_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def save_calendar(child_id, items):
    """整表覆盖式保存（前端校历编辑一次提交）"""
    with _lock:
        c = _conn()
        try:
            c.execute('DELETE FROM kid_calendar WHERE child_id=?', (child_id,))
            for it in (items or [])[:100]:
                if not it.get('date'):
                    continue
                c.execute('INSERT INTO kid_calendar(child_id,date,ctype,days,title,notes) VALUES(?,?,?,?,?,?)',
                          (child_id, str(it.get('date', ''))[:10], str(it.get('ctype', '放假'))[:20],
                           int(it.get('days', 1) or 1), str(it.get('title', ''))[:50], str(it.get('notes', ''))[:200]))
            c.commit()
        finally:
            c.close()
    return {'success': True, 'count': len(items or [])}


def add_calendar(child_id, date, ctype='放假', days=1, title='', notes=''):
    with _lock:
        c = _conn()
        try:
            cur = c.execute('INSERT INTO kid_calendar(child_id,date,ctype,days,title,notes) VALUES(?,?,?,?,?,?)',
                            (child_id, str(date)[:10], str(ctype)[:20], int(days or 1),
                             str(title)[:50], str(notes)[:200]))
            c.commit()
            return cur.lastrowid
        finally:
            c.close()


def today_school_info(child_id):
    """一句话问答引擎（本地毫秒级）：今天课表 + 是否放假 + 最近放假"""
    from datetime import datetime, timedelta
    now = datetime.now()
    wd = now.isoweekday()  # 1=周一
    today_classes = [r for r in list_timetable(child_id, wd)]
    off = []
    for r in list_calendar(child_id):
        try:
            d0 = datetime.strptime(r['date'], '%Y-%m-%d')
        except Exception:
            continue
        d1 = d0 + timedelta(days=int(r.get('days', 1) or 1) - 1)
        if d0 <= now <= d1:
            off.append(r)
    next_off = [r for r in list_calendar(child_id) if r.get('date', '') >= now.strftime('%Y-%m-%d') and r not in off]
    return {'weekday': wd, 'is_school_day': not off, 'off_today': off,
            'today_classes': today_classes, 'next_off': next_off[:2]}


def bump_weakpoint(child_id, subject, topic, delta=1):
    """薄弱点计数（批改/错题/考试自动汇入）：同科同知识点累加，mastered 置 0"""
    subject, topic = str(subject or '')[:30], str(topic or '')[:50]
    if not topic:
        return
    with _lock:
        c = _conn()
        try:
            row = c.execute('SELECT id, wrong_count FROM kid_weakpoints WHERE child_id=? AND subject=? AND topic=?',
                            (child_id, subject, topic)).fetchone()
            from datetime import datetime as _dt
            today = _dt.now().strftime('%Y-%m-%d')
            if row:
                c.execute('UPDATE kid_weakpoints SET wrong_count=wrong_count+?, last_date=?, mastered=0 WHERE id=?',
                          (int(delta), today, row[0]))
            else:
                c.execute('INSERT INTO kid_weakpoints(child_id,subject,topic,wrong_count,last_date) VALUES(?,?,?,?,?)',
                          (child_id, subject, topic, int(delta), today))
            c.commit()
        finally:
            c.close()


def weakpoint_report(child_id, top=8):
    """学习长期记忆的正体：按错误次数排的薄弱点热力清单"""
    with _lock:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT subject, topic, wrong_count, last_date, mastered FROM kid_weakpoints "
                'WHERE child_id=? AND mastered=0 ORDER BY wrong_count DESC, last_date DESC LIMIT ?',
                (child_id, int(top))).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


# ══════════════════════════ 学习中枢 · AI 批改 + 问小哈（★ #1148/#1150，2026-10-14）════════════════════════

def grade_homework(child_id, subject, homework_text):
    """AI 批改：本地 14B 判客观题；错题自动进 mistakes 域 + 薄弱点图谱。
    homework_text: 题目+学生答案的文本（拍照识别由前端/识别链先出文本）"""
    subject = str(subject or '')[:20] or '未分类'
    prompt = (f'你是一位小学{subject}批改老师。下面是作业题目和学生答案，请逐题批改。\n'
              '只判对错并指出错在哪，不要重新讲课。只输出 JSON，格式：\n'
              '{"total": 题数, "right": 对的题数, "wrongs": [{"question": "题目", "student": "学生答案", "correct": "正确答案", "topic": "考查的知识点(如:分数应用题/竖式进位)"}]}\n\n'
              f'作业内容：\n{str(homework_text)[:3000]}')
    text, backend, err = call_local_llm(prompt, system='你是严谨又温和的小学批改老师，只输出 JSON。')
    if err:
        return {'success': False, 'message': f'本地模型批改失败: {err}'}
    try:
        m = re.search(r'\{[\s\S]*\}', text)
        result = json.loads(m.group(0)) if m else json.loads(text)
    except Exception:
        return {'success': False, 'message': '批改结果解析失败（模型没按格式回）', 'raw': text[:300]}
    wrongs = result.get('wrongs') or []
    inserted = []
    for w in wrongs[:10]:
        topic = str(w.get('topic') or '')[:50] or '综合'
        try:
            insert_domain(child_id, 'mistakes', {
                'date': datetime.now().strftime('%Y-%m-%d'), 'subject': subject,
                'question': str(w.get('question') or '')[:200],
                'wrong_reason': topic,
                'correct': f"学生答:{w.get('student','')} / 正确:{w.get('correct','')}",
                'source': 'ai_grade'})
            inserted.append(topic)
        except Exception:
            pass
    return {'success': True, 'backend': backend, 'total': result.get('total'),
            'right': result.get('right'), 'wrong_n': len(wrongs),
            'topics': inserted, 'raw': text[:500]}


def tutor_reply(child_id, question):
    """问小哈：引导式学习伙伴（不直接给答案，苏格拉底三步），对话记入时间轴供家长查看"""
    question = str(question or '')[:500]
    if not question:
        return {'success': False, 'message': '想问什么呀？'}
    weak = weakpoint_report(child_id, top=5)
    weak_ctx = '；'.join(f"{w['subject']}·{w['topic']}(错{w['wrong_count']}次)" for w in weak) or '暂无记录'
    prompt = (f'孩子（小学生）问：{question}\n\n'
              f'孩子的薄弱点参考：{weak_ctx}\n\n'
              '请以「小哈」身份回答。三条铁律：①不直接给最终答案，用提问引导孩子自己想（最多提示第一步）；'
              '②语气鼓励温暖，夸具体的点；③说人话，一段不超过 120 字。')
    text, backend, err = call_local_llm(prompt, system='你是小学生学习伙伴「小哈」，温和、鼓励、从不直接给答案。', temperature=0.6)
    if err:
        return {'success': False, 'message': f'小哈没上线: {err}'}
    try:
        add_timeline(child_id, 'learning', 'system', f'问小哈：{question[:40]}',
                     text[:200], source='child', importance='normal')
    except Exception:
        pass
    return {'success': True, 'reply': text, 'backend': backend}


def create_child(data):
    with _lock:
        c = _conn()
        try:
            has_default = c.execute("SELECT COUNT(*) FROM children WHERE is_default=1").fetchone()[0]
            is_default = 1 if (not has_default or data.get('is_default')) else 0
            if is_default:
                c.execute("UPDATE children SET is_default=0")
            cols = ['name', 'nickname', 'gender', 'birthday', 'school', 'grade', 'class_name', 'avatar', 'notes']
            vals = {k: str(data.get(k, '') or '') for k in cols}
            vals['is_default'] = is_default
            cur = c.execute(
                "INSERT INTO children(name,nickname,gender,birthday,school,grade,class_name,avatar,notes,is_default) "
                "VALUES(:name,:nickname,:gender,:birthday,:school,:grade,:class_name,:avatar,:notes,:is_default)", vals)
            cid = cur.lastrowid
            c.commit()
        finally:
            c.close()
    add_timeline(cid, 'milestone', 'system', f"建档：{data.get('name', '孩子')}",
                 '成长档案建立', source='parent', importance='watch')
    return cid


def update_child(child_id, data):
    allowed = ['name', 'nickname', 'gender', 'birthday', 'school', 'grade', 'class_name', 'avatar', 'notes', 'is_default']
    sets, vals = [], []
    for k in allowed:
        if k in data:
            sets.append(f"{k}=?")
            vals.append(str(data[k] if data[k] is not None else ''))
    if not sets:
        return False
    sets.append("updated_at=datetime('now','localtime')")
    vals.append(child_id)
    with _lock:
        c = _conn()
        try:
            if data.get('is_default'):
                c.execute("UPDATE children SET is_default=0")
            c.execute(f"UPDATE children SET {', '.join(sets)} WHERE id=?", vals)
            c.commit()
        finally:
            c.close()
    return True


def delete_child(child_id):
    """删除孩子：档案主记录删除，时间轴与分类记录一并删除（不可逆）"""
    with _lock:
        c = _conn()
        try:
            c.execute("DELETE FROM kid_timeline WHERE child_id=?", (child_id,))
            for d in DOMAINS.values():
                c.execute(f"DELETE FROM {d[0]} WHERE child_id=?", (child_id,))
            for t in ('kid_reminders', 'kid_reports', 'kid_digest'):
                c.execute(f"DELETE FROM {t} WHERE child_id=?", (child_id,))
            c.execute("DELETE FROM children WHERE id=?", (child_id,))
            c.commit()
        finally:
            c.close()
    return True


# ══════════════════════════ 分类档案 CRUD（通用） ══════════════════════════

def query_domain(child_id, domain, filters=None, limit=500, offset=0):
    d = DOMAINS.get(domain)
    if not d:
        raise ValueError(f'未知领域: {domain}')
    table = d[0]
    sql = f"SELECT * FROM {table} WHERE child_id=?"
    vals = [child_id]
    for k, v in (filters or {}).items():
        if k in d[3] and v:
            sql += f" AND {k} LIKE ?"
            vals.append(f"%{v}%")
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [limit, offset]
    c = _conn()
    try:
        return [dict(r) for r in c.execute(sql, vals).fetchall()]
    finally:
        c.close()


def insert_domain(child_id, domain, data):
    d = DOMAINS.get(domain)
    if not d:
        raise ValueError(f'未知领域: {domain}')
    table, category, label, fields = d
    rec = {k: str(data.get(k, '') if data.get(k) is not None else '') for k in fields}
    rec['child_id'] = child_id
    # ★ 2026-09-19 #792 习惯打卡：连续天数自动算（从打卡日往前数连续同习惯的天数，断了重新从1算）
    if domain == 'habits':
        try:
            _habit = str(rec.get('habit') or '').strip()
            _d0 = str(rec.get('date') or datetime.now().strftime('%Y-%m-%d'))[:10]
            if _habit and str(rec.get('done') or '') != '没做':
                datetime.strptime(_d0, '%Y-%m-%d')  # 日期不合法就不算 streak（抛异常走兜底）
                with _lock:
                    _c = _conn()
                    try:
                        _past = {str(r[0])[:10] for r in _c.execute(
                            "SELECT date FROM kid_habits WHERE child_id=? AND habit=? "
                            "AND (done IS NULL OR done NOT IN ('没做'))", (child_id, _habit))}
                    finally:
                        _c.close()
                _streak = 1
                _cur = datetime.strptime(_d0, '%Y-%m-%d').date() - timedelta(days=1)
                while _cur.isoformat() in _past:
                    _streak += 1
                    _cur -= timedelta(days=1)
                rec['streak'] = str(_streak)
        except Exception as e:
            logger.debug(f"[kid_archive] streak 计算失败(不影响入库): {e}")
    keys = list(rec.keys())
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                f"INSERT INTO {table}({', '.join(keys)}) VALUES({', '.join(':' + k for k in keys)})", rec)
            rid = cur.lastrowid
            c.commit()
        finally:
            c.close()
    # 时间轴：自动追加事件
    title = _record_title(domain, rec)
    importance = 'normal'
    text_blob = json.dumps(rec, ensure_ascii=False)
    if any(w in text_blob for w in ('不及格', '退步', '抗拒', '害怕', '吵架', '欺负', '生病', '发烧')):
        importance = 'watch'
    add_timeline(child_id, category, 'add', f"{label}：{title}", '', source=data.get('source', 'parent'),
                 importance=importance, ref_table=table, ref_id=rid)
    # ★ #1149（2026-10-14）错题入库自动汇入薄弱点图谱（学科=subject，知识点=wrong_reason 优先、无则 question 前 12 字）
    if domain == 'mistakes':
        try:
            _topic = str(rec.get('wrong_reason') or '').strip() or str(rec.get('question') or '').strip()[:12]
            if _topic:
                bump_weakpoint(child_id, str(rec.get('subject') or '未分类'), _topic)
        except Exception as e:
            logger.debug(f"[kid_archive] 薄弱点汇入跳过: {e}")
    return rid


def update_domain(child_id, domain, rid, data):
    d = DOMAINS.get(domain)
    if not d:
        raise ValueError(f'未知领域: {domain}')
    table, category, label, fields = d
    sets, vals = [], []
    for k in fields:
        if k in data:
            sets.append(f"{k}=?")
            vals.append(str(data[k] if data[k] is not None else ''))
    if 'updated_at' in _table_cols(table):
        sets.append("updated_at=datetime('now','localtime')")
    if not sets:
        return False
    vals += [rid, child_id]
    with _lock:
        c = _conn()
        try:
            c.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=? AND child_id=?", vals)
            c.commit()
        finally:
            c.close()
    add_timeline(child_id, category, 'update', f"{label}更新", '', ref_table=table, ref_id=rid)
    return True


def delete_domain(child_id, domain, rid):
    d = DOMAINS.get(domain)
    if not d:
        raise ValueError(f'未知领域: {domain}')
    table = d[0]
    with _lock:
        c = _conn()
        try:
            c.execute(f"DELETE FROM {table} WHERE id=? AND child_id=?", (rid, child_id))
            c.commit()
        finally:
            c.close()
    return True


def _table_cols(table):
    c = _conn()
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    finally:
        c.close()


def _record_title(domain, rec):
    if domain == 'learning':
        return f"{rec.get('date','')} {rec.get('subject','')} {rec.get('topic','')}".strip()
    if domain == 'homework':
        # ★ #1194（2026-10-25）补作业域标题（此前时间轴一律「作业记录：」空标题）
        return f"{rec.get('subject','')} {rec.get('content','')[:24]}".strip()
    if domain == 'reading':
        return f"{rec.get('book_name','')}（读了{rec.get('minutes','?')}分钟）"
    if domain == 'habits':
        # ★ 2026-09-19 #792 习惯打卡
        _s = f"（连续{rec.get('streak')}天）" if str(rec.get('streak') or '').strip() else ''
        return f"{rec.get('habit','')} {rec.get('done','')}{_s}".strip()
    if domain == 'mistakes':
        return f"{rec.get('subject','')} {rec.get('question','')[:20]}".strip()
    if domain == 'health':
        return f"{rec.get('htype','')} {rec.get('item','')}".strip()
    if domain == 'growth':
        return f"身高{rec.get('height','?')}cm 体重{rec.get('weight','?')}kg"
    if domain == 'traits':
        return f"{rec.get('tcategory','')}: {rec.get('content','')[:20]}"
    if domain == 'relations':
        return f"{rec.get('rtype','')} {rec.get('name','')}"
    if domain == 'clothing':
        return f"{rec.get('ctype','')} {rec.get('size','')}"
    if domain == 'items':
        return rec.get('name', '')
    if domain == 'games':
        return rec.get('name', '')
    if domain == 'game_logs':
        return f"玩了{rec.get('minutes','0')}分钟"
    if domain == 'interests':
        return f"{rec.get('name','')}（{rec.get('icategory','')}）"
    if domain == 'interest_logs':
        return f"{rec.get('mood','')} {rec.get('progress','')[:20]}"
    if domain == 'diet':
        return f"{rec.get('dcategory','')} {rec.get('food','')}"
    return ''


# ══════════════════════════ 时间轴查询 ══════════════════════════

def query_timeline(child_id, category='', limit=100, before_id=0):
    c = _conn()
    try:
        sql = "SELECT * FROM kid_timeline WHERE child_id=?"
        vals = [child_id]
        if category:
            sql += " AND category=?"
            vals.append(category)
        if before_id:
            sql += " AND id<?"
            vals.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        vals.append(limit)
        return [dict(r) for r in c.execute(sql, vals).fetchall()]
    finally:
        c.close()


# ══════════════════════════ 提醒 ══════════════════════════

def add_reminder(child_id, rtype, title, detail='', due_at='', repeat_rule=''):
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "INSERT INTO kid_reminders(child_id, rtype, title, detail, due_at, repeat_rule) VALUES(?,?,?,?,?,?)",
                (child_id, rtype, title, detail, due_at, repeat_rule))
            rid = cur.lastrowid
            c.commit()
        finally:
            c.close()
    add_timeline(child_id, 'reminder', 'add', f"提醒：{title}", detail,
                 importance='urgent' if rtype == 'urgent' else 'normal')
    return rid


def pending_alerts(child_id):
    """红点数据：到期未完成提醒 + 7天内关注/紧急级时间轴"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M')
    c = _conn()
    try:
        reminders = [dict(r) for r in c.execute(
            "SELECT * FROM kid_reminders WHERE child_id=? AND status='pending' AND due_at!='' AND due_at<=? "
            "ORDER BY due_at LIMIT 50", (child_id, now)).fetchall()]
        urgent = [dict(r) for r in c.execute(
            "SELECT * FROM kid_timeline WHERE child_id=? AND importance IN ('watch','urgent') AND ts>=? "
            "ORDER BY id DESC LIMIT 20", (child_id, week_ago)).fetchall()]
        return {'reminders': reminders, 'attention': urgent}
    finally:
        c.close()


def complete_reminder(child_id, rid):
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE kid_reminders SET status='done' WHERE id=? AND child_id=?", (rid, child_id))
            c.commit()
        finally:
            c.close()
    return True


# ══════════════════════════ 报告 ══════════════════════════

def save_report(child_id, rtype, domain, content, model=''):
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "INSERT INTO kid_reports(child_id, rtype, domain, content, model) VALUES(?,?,?,?,?)",
                (child_id, rtype, domain, content, model))
            rid = cur.lastrowid
            c.commit()
        finally:
            c.close()
    return rid


def get_reports(child_id, limit=30):
    c = _conn()
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM kid_reports WHERE child_id=? ORDER BY id DESC LIMIT ?", (child_id, limit)).fetchall()]
    finally:
        c.close()


# ══════════════════════════ 对话摘记（明档/暗档） ══════════════════════════

def save_digest(child_id, visible, hidden, level='normal', model='', conv_id=''):
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "INSERT INTO kid_digest(child_id, visible_summary, hidden_analysis, level, model, conv_id) "
                "VALUES(?,?,?,?,?,?)", (child_id, visible, hidden, level, model, conv_id))
            rid = cur.lastrowid
            c.commit()
        finally:
            c.close()
    imp = {'normal': 'normal', 'watch': 'watch', 'urgent': 'urgent'}.get(level, 'normal')
    add_timeline(child_id, 'digest', 'chat', f"对话摘记（{level}）", visible[:100],
                 source='chat', importance=imp)
    return rid


def get_digests(child_id, limit=50, include_hidden=True):
    c = _conn()
    try:
        if include_hidden:
            rows = c.execute("SELECT * FROM kid_digest WHERE child_id=? ORDER BY id DESC LIMIT ?",
                             (child_id, limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT id, child_id, date, visible_summary, level, model, conv_id, created_at "
                "FROM kid_digest WHERE child_id=? ORDER BY id DESC LIMIT ?", (child_id, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ══════════════════════════ AI 分析 ══════════════════════════

def _resolve_ollama_model(preferred=''):
    """解析可用的本地 Ollama 模型"""
    try:
        import requests
        r = requests.get('http://127.0.0.1:11434/api/tags', timeout=3)
        models = [m.get('name', '') for m in r.json().get('models', [])]
        if not models:
            return None, None
        # ★ 2026-09-26 审计：删除内定 qwen2.5:14b 兜底——preferred 不中时
        # 落用户已装列表第一个（用户自己装的模型），不再是哈文指定的名字
        for m in ([preferred] if preferred else []):
            for have in models:
                if have.startswith(m):
                    return have, models
        return models[0], models
    except Exception:
        return None, None


def call_local_llm(prompt, system='', timeout=300, temperature=0.4):
    """★ 2026-09-19 (#796) 本地兜底统一入口 — 不再裸调 Ollama
    四级优先：进程内 GGUF → Ollama（兼容老用户）→ 按需加载本地 GGUF → 明确报错
    （红线3：心理类默认只走本地，语义不变）
    """
    try:
        from src.core.local_infer import chat_local
        settings = get_settings()
        prefer = (settings.get('default_model', '') or '').strip() or None
        text, backend, err = chat_local(prompt, system=system, timeout=timeout,
                                        temperature=temperature, prefer_model=prefer)
        if text is None:
            return None, '', err
        return text, backend, ''
    except Exception as e:
        return None, '', f'本地模型调用失败: {e}'


def collect_child_context(child_id, days=30):
    """汇总孩子近况，作为 AI 分析素材（全部本机数据）"""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    c = _conn()
    try:
        child = c.execute("SELECT * FROM children WHERE id=?", (child_id,)).fetchone()
        lines = [f"【基本信息】{dict(child)['name']} {dict(child).get('nickname','')} "
                 f"生日{dict(child).get('birthday','')} 学校{dict(child).get('school','')}"
                 f"{dict(child).get('grade','')}{dict(child).get('class_name','')}"]
        # 特性快照
        traits = c.execute("SELECT tcategory, content FROM kid_traits WHERE child_id=?", (child_id,)).fetchall()
        if traits:
            lines.append("【特性】" + '；'.join(f"{r['tcategory']}:{r['content']}" for r in traits))
        # 近30天时间轴
        tl = c.execute(
            "SELECT ts, category, title, importance FROM kid_timeline WHERE child_id=? AND ts>=? ORDER BY ts DESC LIMIT 120",
            (child_id, since)).fetchall()
        if tl:
            tl_lines = [f"{r['ts'][:10]}[{r['category']}]{r['title']}{'!' if r['importance'] != 'normal' else ''}" for r in tl]
            lines.append("【近30天大事记】\n" + '\n'.join(tl_lines))
        # 最近学习弱点
        wk = c.execute(
            "SELECT date, subject, weakness, action FROM kid_learning WHERE child_id=? AND weakness!='' ORDER BY id DESC LIMIT 10",
            (child_id,)).fetchall()
        if wk:
            lines.append("【近期薄弱点】\n" + '\n'.join(
                f"{r['date']} {r['subject']}: {r['weakness']} → {r['action']}" for r in wk))
        # 兴趣课程
        ints = c.execute("SELECT name, icategory, status FROM kid_interests WHERE child_id=?", (child_id,)).fetchall()
        if ints:
            lines.append("【兴趣课程】" + '；'.join(f"{r['name']}({r['icategory']},{r['status']})" for r in ints))
        # 游戏近况
        gl = c.execute(
            "SELECT SUM(minutes) FROM kid_game_logs WHERE child_id=? AND date>=?",
            (child_id, (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'))).fetchone()
        if gl and gl[0]:
            lines.append(f"【近7天游戏】共{int(gl[0])}分钟")
    finally:
        c.close()
    return '\n'.join(lines)


def generate_meal_plan(child_id):
    """AI 菜谱建议：根据过敏/忌口/喜好/讨厌 + 近期健康 + 生长，生成一周家常菜谱（本地模型）"""
    c = _conn()
    try:
        kid = c.execute("SELECT name, nickname, gender, birthday FROM children WHERE id=?", (child_id,)).fetchone()
        lines = [f"【孩子】{dict(kid)['name']} {dict(kid).get('nickname','')} {dict(kid).get('gender','')} 生日{dict(kid).get('birthday','')}"]
        diet = c.execute("SELECT dcategory, food, reason FROM kid_diet WHERE child_id=?", (child_id,)).fetchall()
        if diet:
            lines.append("【饮食档案】" + '；'.join(
                f"{r['dcategory']}:{r['food']}{('（' + r['reason'] + '）') if r['reason'] else ''}" for r in diet))
        traits = c.execute("SELECT tcategory, content FROM kid_traits WHERE child_id=?", (child_id,)).fetchall()
        if traits:
            lines.append("【口味特性】" + '；'.join(f"{r['tcategory']}:{r['content']}" for r in traits))
        recents = c.execute(
            "SELECT date, htype, item, value FROM kid_health WHERE child_id=? ORDER BY id DESC LIMIT 8",
            (child_id,)).fetchall()
        if recents:
            lines.append("【近期健康】" + '；'.join(
                f"{r['date']}{r['htype']}:{r['item']}{(' ' + r['value']) if r['value'] else ''}" for r in recents))
        growth = c.execute(
            "SELECT height, weight FROM kid_growth WHERE child_id=? ORDER BY id DESC LIMIT 1",
            (child_id,)).fetchone()
        if growth and (growth['height'] or growth['weight']):
            lines.append(f"【体格】身高{growth['height']}cm 体重{growth['weight']}kg")
    finally:
        c.close()
    system = ("你是儿童营养师，为普通中国家庭设计家常菜谱。根据孩子的饮食档案（过敏必须严格避开）、近期健康和体格，"
              "输出：【饮食原则】2-3条；【一周菜谱】周一到周日，每天早晚餐各一道，格式如\"周一 早：xx 晚：xx\"；"
              "【温馨提示】2条。家常易做、营养均衡，语言简洁。")
    text, model, err = call_local_llm('\n'.join(lines), system, timeout=300)
    if err:
        return None, err
    rid = save_report(child_id, 'on_demand', 'diet', text, model)
    add_timeline(child_id, 'diet', 'ai', '生成AI菜谱建议', '', source='ai')
    return rid, ''


def _count_child_records(child_id):
    """★ 2026-09-16 孩子档案全部域记录总数（AI 报告数据门闸用）"""
    n = 0
    c = _conn()
    try:
        for _key, spec in DOMAINS.items():
            table = spec[0] if isinstance(spec, (tuple, list)) else None
            if not table:
                continue
            try:
                n += c.execute(f"SELECT COUNT(*) FROM {table} WHERE child_id=?", (child_id,)).fetchone()[0]
            except Exception:
                pass
    finally:
        c.close()
    return n


def generate_report(child_id, rtype='on_demand', domain='overview'):
    """生成 AI 报告（周报/深度/按需）。返回 (report_id, error)
    ★ 2026-09-16 数据充足度门闸：档案记录不足 3 条时拒绝生成（返回 ARCHIVE_TOO_THIN:数量），
    宁可不出报告也不让 AI 硬编"通用套话"——修复"什么都没填却写满分析"的编造问题。"""
    n_rec = _count_child_records(child_id)
    if n_rec < 3:
        return None, f'ARCHIVE_TOO_THIN:{n_rec}'
    ctx = collect_child_context(child_id)
    if rtype == 'weekly':
        system = "你是家庭成长顾问，用温暖务实的中文写给家长看。输出分【本周亮点】【需要关注】【下周建议】三节，每节2-4条，每条不超过40字。"
        prompt = f"以下是孩子近30天成长档案数据，请生成本周成长周报：\n\n{ctx}"
    else:
        system = "你是家庭教育顾问，用务实中文写给家长。分析孩子的学习状态、兴趣倾向、社交情绪，找出问题并给出可操作的建议。输出分【现状分析】【问题诊断】【具体建议】三节，语言具体、不说空话。"
        prompt = f"以下是孩子成长档案数据，请做一次全面分析（聚焦{domain}）：\n\n{ctx}"
    text, model, err = call_local_llm(prompt, system)
    if err:
        return None, err
    rid = save_report(child_id, rtype, domain, text, model)
    add_timeline(child_id, 'milestone', 'ai', f"生成{('周报' if rtype == 'weekly' else '分析报告')}",
                 '', source='ai')
    return rid, ''


def analyze_photo_note(child_id, note_text, domain='learning'):
    """拍照/记录辅助：把家长随手写的口语化内容整理成结构化记录建议（本地模型，免费）"""
    system = "你是记录助手。把家长口述的孩子情况整理成简洁要点，输出JSON：{\"要点\": [\"...\"], \"建议分类\": \"学习/健康/特性/其他\", \"需要跟进\": \"...或空\"}。只输出JSON。"
    text, _model, err = call_local_llm(f"口述内容：{note_text}\n场景：{domain}", system, timeout=120)
    if err:
        return None, err
    return text, ''


# ══════════════════════════ 聊天归档钩子（engine 调用） ══════════════════════════

_archiving = set()  # 防重入


def maybe_archive_chat(conv_id, user_message, assistant_reply):
    """
    聊天归档钩子：异步、默认关、本地模型初筛。
    红线1：不逐句上报——只存摘要；红线2：原文不出本机；红线3：只走本地模型。
    """
    try:
        s = get_settings()
        if s.get('chat_archival_enabled') != 'true':
            return
        if conv_id in _archiving:
            return
        _archiving.add(conv_id)
        t = threading.Thread(target=_archive_worker, args=(conv_id, user_message, assistant_reply), daemon=True)
        t.start()
    except Exception as e:
        logger.debug(f"[KidArchive] 归档钩子跳过: {e}")


def _archive_worker(conv_id, user_message, assistant_reply):
    try:
        children = get_children()
        if not children:
            return
        kid_names = '、'.join(c['name'] for c in children)
        system = ("你是儿童成长档案的归档筛选器。判断这段对话是否与孩子的成长有关（学习/健康/情绪/兴趣/社交/物品/游戏等）。"
                  f"家中的孩子：{kid_names}。"
                  "只输出JSON：{\"relevant\": true/false, \"child\": \"孩子名或空\", "
                  "\"visible\": \"给鼓励性摘要，30字内（面向孩子，明档）\", "
                  "\"hidden\": \"给家长的行为观察摘要，60字内（暗档，客观不评判）\", "
                  "\"level\": \"normal/watch/urgent\"}。只输出JSON。")
        prompt = f"孩子消息：{user_message[:500]}\nAI回复：{assistant_reply[:500]}"
        text, _model, err = call_local_llm(prompt, system, timeout=180)
        if err or not text:
            return
        start, end = text.find('{'), text.rfind('}')
        if start < 0 or end <= start:
            return
        import json as _json
        result = _json.loads(text[start:end + 1])
        if not result.get('relevant'):
            return
        # 找孩子
        target = None
        wanted = result.get('child', '')
        for c in children:
            if wanted and (wanted in c['name'] or wanted in (c.get('nickname') or '')):
                target = c['id']
                break
        if not target:
            target = next((c['id'] for c in children if c.get('is_default')), children[0]['id'])
        save_digest(target, result.get('visible', '')[:200], result.get('hidden', '')[:300],
                    level=result.get('level', 'normal'), model=_model or 'local', conv_id=str(conv_id))
        logger.info(f"[KidArchive] 对话已归档: conv={conv_id} level={result.get('level')}")
    except Exception as e:
        logger.debug(f"[KidArchive] 归档worker失败: {e}")
    finally:
        _archiving.discard(conv_id)


# ══════════════════════════ 照片 ══════════════════════════

def save_photo(child_id, file_bytes, ext='jpg'):
    """照片保存到 data/media/kid/<child_id>/（不出本机）"""
    d = MEDIA_DIR / str(child_id)
    d.mkdir(parents=True, exist_ok=True)
    fname = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')[:-3]}.{ext}"
    (d / fname).write_bytes(file_bytes)
    return f"/data/media/kid/{child_id}/{fname}"


# 启动建表
try:
    init_db()
except Exception as _e:
    logger.warning(f"[KidArchive] 初始化建表延迟: {_e}")
