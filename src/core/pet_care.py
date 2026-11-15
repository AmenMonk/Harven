# -*- coding: utf-8 -*-
"""
宠物档案与喂养/疫苗驱虫系统（pet_care P0）
Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0

仿 kid_archive 的域模式：
- pets          宠物档案（多宠、照片 dataURL）
- pet_logs      统一日志表（domain = feeding / shots / care）
- 疫苗驱虫自动排期：记一次按科学时间表自动算「下次时间」（可手动改）
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# ★ #840: 动态项目根（不再写死开发机路径，任意电脑都能用）
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / 'data' / 'sqlite' / 'lingzhi.db'

DOMAINS = {
    'feeding': '喂养打卡',
    'shots':   '疫苗驱虫',
    'care':    '洗澡就医',
}

# ★ 自动排期规则（新瑞鹏/CAPC 通行指南，成体默认；幼体可手动改下次时间）
AUTO_NEXT = {
    '体外驱虫': 30,
    '体内驱虫': 90,
    '疫苗': 365,
    '狂犬疫苗': 365,
    '疫苗加强': 365,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    species TEXT DEFAULT '',
    breed TEXT DEFAULT '',
    birthday TEXT DEFAULT '',
    gender TEXT DEFAULT '',
    neutered TEXT DEFAULT '',
    weight TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS pet_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    date TEXT DEFAULT '',
    type TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    amount TEXT DEFAULT '',
    place TEXT DEFAULT '',
    cost TEXT DEFAULT '',
    next_date TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pet_logs_pet ON pet_logs(pet_id, domain);
"""


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.executescript(_SCHEMA)


# ══════════════════ 宠物档案 ══════════════════

def get_pets():
    with _conn() as c:
        rows = c.execute('SELECT * FROM pets ORDER BY id').fetchall()
    return [dict(r) for r in rows]


def create_pet(data):
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO pets(name, species, breed, birthday, gender, neutered, weight, photo, notes)'
            ' VALUES(?,?,?,?,?,?,?,?,?)',
            (data.get('name', '未命名'), data.get('species', ''), data.get('breed', ''),
             data.get('birthday', ''), data.get('gender', ''), data.get('neutered', ''),
             data.get('weight', ''), data.get('photo', ''), data.get('notes', '')))
        return cur.lastrowid


def update_pet(pid, data):
    fields = ['name', 'species', 'breed', 'birthday', 'gender', 'neutered', 'weight', 'photo', 'notes']
    sets, vals = [], []
    for f in fields:
        if f in data:
            sets.append(f'{f}=?')
            vals.append(data[f])
    if not sets:
        return
    vals.append(pid)
    with _conn() as c:
        c.execute(f"UPDATE pets SET {', '.join(sets)} WHERE id=?", vals)


def delete_pet(pid):
    """删除宠物档案（含全部日志，不可逆）"""
    with _conn() as c:
        c.execute('DELETE FROM pet_logs WHERE pet_id=?', (pid,))
        c.execute('DELETE FROM pets WHERE id=?', (pid,))


# ══════════════════ 日志三域 ══════════════════

def _compute_next(dstr, rtype):
    """按科学时间表自动算下次日期（ISO 字符串）；算不出返回空"""
    rule = AUTO_NEXT.get(rtype or '')
    if not rule:
        return ''
    try:
        d0 = date.fromisoformat((dstr or date.today().isoformat())[:10])
        return (d0 + timedelta(days=rule)).isoformat()
    except ValueError:
        return ''


def insert_log(pet_id, domain, data):
    if domain not in DOMAINS:
        raise ValueError(f'未知域: {domain}')
    dstr = (data.get('date') or date.today().isoformat())[:10]
    rtype = data.get('type', '')
    nxt = data.get('next_date') or _compute_next(dstr, rtype)
    with _conn() as c:
        cur = c.execute(
            'INSERT INTO pet_logs(pet_id, domain, date, type, detail, amount, place, cost, next_date, photo, notes)'
            ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (pet_id, domain, dstr, rtype, data.get('detail', ''), data.get('amount', ''),
             data.get('place', ''), data.get('cost', ''), nxt, data.get('photo', ''),
             data.get('notes', '')))
        return cur.lastrowid


def update_log(pet_id, domain, rid, data):
    if domain not in DOMAINS:
        raise ValueError(f'未知域: {domain}')
    fields = ['date', 'type', 'detail', 'amount', 'place', 'cost', 'next_date', 'photo', 'notes']
    sets, vals = [], []
    for f in fields:
        if f in data:
            sets.append(f'{f}=?')
            vals.append(data[f])
    if 'type' in data and 'next_date' not in data:
        # 类型改了且没显式给下次时间 → 重新自动排
        with _conn() as c:
            row = c.execute('SELECT date FROM pet_logs WHERE id=?', (rid,)).fetchone()
        dstr = (row['date'] if row else '') or date.today().isoformat()
        sets.append('next_date=?')
        vals.append(_compute_next(dstr, data['type']))
    if not sets:
        return
    vals.extend([pet_id, domain, rid])
    with _conn() as c:
        c.execute(f"UPDATE pet_logs SET {', '.join(sets)} WHERE pet_id=? AND domain=? AND id=?", vals)


def delete_log(pet_id, domain, rid):
    if domain not in DOMAINS:
        raise ValueError(f'未知域: {domain}')
    with _conn() as c:
        c.execute('DELETE FROM pet_logs WHERE pet_id=? AND domain=? AND id=?', (pet_id, domain, rid))


def query_logs(pet_id, domain, limit=500):
    if domain not in DOMAINS:
        raise ValueError(f'未知域: {domain}')
    with _conn() as c:
        rows = c.execute(
            'SELECT * FROM pet_logs WHERE pet_id=? AND domain=? ORDER BY date DESC, id DESC LIMIT ?',
            (pet_id, domain, limit)).fetchall()
    return [dict(r) for r in rows]


def pending_alerts(days=3):
    """全部宠物未来 N 天内到期的疫苗/驱虫/复诊提醒"""
    today = date.today()
    due = (today + timedelta(days=days)).isoformat()
    with _conn() as c:
        rows = c.execute(
            'SELECT l.*, p.name AS pet_name FROM pet_logs l LEFT JOIN pets p ON p.id=l.pet_id'
            " WHERE l.next_date != '' AND l.next_date <= ? ORDER BY l.next_date LIMIT 50",
            (due,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        nd = d.get('next_date', '')
        d['urgent'] = bool(nd and nd <= today.isoformat())
        out.append(d)
    return out


try:
    init_db()
except Exception:
    pass
