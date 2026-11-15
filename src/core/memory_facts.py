# -*- coding: utf-8 -*-
"""
memory_facts.py — 长期语义记忆 ★ #1220-C（2026-10-25）
=====================================================
开发者随口说的长期事实（孩子过敏药、家里 WiFi 密码、丈母娘生日…）：
- remember_fact FC 工具写入（外脑判断「这是值得长期记住的事实」时调用）
- chat() 入口 recall() 关键词召回 top3，注入上下文 → 哈文「记得你说过的事」

设计：
- 存 data/sqlite/lingzhi.db 表 memory_facts（数据真家唯一）
- 召回 = 关键词匹配（2字滑窗打分），零依赖零网络；嵌入模型路线（bge）后置可选
- 去重 = 相似度 ≥0.75 视为同一条事实 → 覆盖更新（防「每天记一遍」刷表）

红线：数据只存本机 lingzhi.db。
"""
import difflib
import logging
import re
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    with _lock:
        c = _conn()
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS memory_facts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        fact TEXT NOT NULL,
                        keywords TEXT DEFAULT '',
                        source TEXT DEFAULT 'chat',
                        created_at TEXT DEFAULT (datetime('now','localtime')),
                        recall_count INTEGER DEFAULT 0,
                        last_recalled_at TEXT DEFAULT ''
                        )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_mf_id ON memory_facts(id DESC)")
            c.commit()
        finally:
            c.close()


init_db()


def _extract_keywords(text: str) -> str:
    """粗提关键词：去掉标点/虚词，按 2 字滑窗取样（存原文为主，keywords 备用）。"""
    t = re.sub(r'[，。！？、；：\u201c\u201d\u2018\u2019（）\s\d]+', ' ', str(text or ''))
    words = [w for w in t.split() if len(w) >= 2]
    return ' '.join(words[:8])


def _bigrams(text: str):
    """中文 2 字滑窗（英文按整词）。"""
    s = re.sub(r'[，。！？、；：\u201c\u201d\u2018\u2019（）\s]+', '', str(text or ''))
    grams = set()
    buf = ''
    for ch in s:
        if re.match(r'[a-zA-Z0-9]', ch):
            buf += ch
        else:
            if buf:
                grams.add(buf.lower())
                buf = ''
            grams.add(ch)
    if buf:
        grams.add(buf.lower())
    for i in range(len(s) - 1):
        grams.add(s[i:i + 2])
    return {g for g in grams if len(g) >= 1}


def add_fact(fact: str, source: str = 'chat') -> dict:
    """存一条长期事实。高相似（≥0.75）已存在 → 覆盖更新而非新增。"""
    fact = str(fact or '').strip()[:300]
    if len(fact) < 2:
        return {'success': False, 'error': '内容太短，没记住'}
    with _lock:
        c = _conn()
        try:
            rows = c.execute("SELECT id, fact FROM memory_facts ORDER BY id DESC LIMIT 200").fetchall()
            for r in rows:
                if difflib.SequenceMatcher(None, fact, r['fact']).ratio() >= 0.75:
                    c.execute("UPDATE memory_facts SET fact=?, keywords=?, source=? WHERE id=?",
                              (fact, _extract_keywords(fact), source, r['id']))
                    c.commit()
                    return {'success': True, 'id': r['id'], 'updated': True,
                            'message': f'记住了（覆盖旧版 #{r["id"]}）'}
            cur = c.execute("INSERT INTO memory_facts(fact, keywords, source) VALUES(?,?,?)",
                            (fact, _extract_keywords(fact), source))
            c.commit()
            return {'success': True, 'id': cur.lastrowid, 'updated': False, 'message': '已长期记住'}
        finally:
            c.close()


def recall(query: str, limit: int = 3, min_score: int = 2) -> list:
    """按问句关键词召回长期事实（打分：问句 2 字窗命中事实的字数）。
    返回 [{'id','fact','score'}]，分数不足的丢掉（宁缺勿滥，不往上下文塞噪声）。"""
    qgrams = _bigrams(query)
    if not qgrams:
        return []
    out = []
    with _lock:
        c = _conn()
        try:
            rows = c.execute("SELECT id, fact FROM memory_facts ORDER BY id DESC LIMIT 300").fetchall()
        finally:
            c.close()
    for r in rows:
        fgrams = _bigrams(r['fact'])
        score = len(qgrams & fgrams)
        if score >= min_score:
            out.append({'id': r['id'], 'fact': r['fact'], 'score': score})
    out.sort(key=lambda x: -x['score'])
    top = out[:max(1, int(limit))]
    if top:  # 召回命中 → 计数（诊断用）
        try:
            with _lock:
                c = _conn()
                try:
                    for t in top:
                        c.execute("UPDATE memory_facts SET recall_count=recall_count+1, "
                                  "last_recalled_at=datetime('now','localtime') WHERE id=?", (t['id'],))
                    c.commit()
                finally:
                    c.close()
        except Exception:
            pass
    return top


def list_facts(limit: int = 50) -> list:
    with _lock:
        c = _conn()
        try:
            rows = c.execute("SELECT * FROM memory_facts ORDER BY id DESC LIMIT ?",
                             (max(1, min(int(limit or 50), 200)),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def forget_fact(fid: int) -> dict:
    with _lock:
        c = _conn()
        try:
            cur = c.execute("DELETE FROM memory_facts WHERE id=?", (int(fid),))
            c.commit()
            ok = cur.rowcount > 0
            return {'success': ok, 'message': '已忘掉这条' if ok else f'没有 #{fid} 这条记忆'}
        finally:
            c.close()
