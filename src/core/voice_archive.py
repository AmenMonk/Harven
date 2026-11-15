# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
语音档案库（#929-A4，2026-09-29）
开发者原则：语音默认不保存、转完即走；只有用户明确说「这段话存下来」或点 💾 才入档案。

- 表：lingzhi.db → voice_archive（id/时间/文字/音频文件/时长/来源）
- 音频按月落盘：data/media/voice_archive/YYYYMM/
- API：/api/voice/archive（见 voice_routes.py）
- FC 工具：save_voice_note / search_voice_notes（见 builtin_tools.py）
"""
import os
import re
import sqlite3
import base64
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _ROOT / 'data' / 'sqlite' / 'lingzhi.db'
_AUDIO_ROOT = _ROOT / 'data' / 'media' / 'voice_archive'

# 内存暂存最近一次转写（供「存下来」不重复录音直接入库；不做持久化，重启即清）
_last = {'text': '', 'ts': 0.0}


def _db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS voice_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        text TEXT NOT NULL,
        audio_file TEXT,
        duration REAL,
        source TEXT DEFAULT 'manual'
    )""")
    return conn


def stash_last_transcription(text: str):
    """转写完成后暂存文字（供 💾 / 「存下来」用）"""
    import time
    _last['text'] = (text or '').strip()
    _last['ts'] = time.time()


def get_last_transcription(max_age=1800):
    """取最近一次转写文字；超过 max_age 秒视为过期"""
    import time
    if _last['text'] and (time.time() - _last['ts']) <= max_age:
        return _last['text']
    return ''


def save_voice_note(text: str, audio_b64: str = '', ext: str = 'webm',
                    duration: float = None, source: str = 'manual') -> dict:
    """存一条语音档案。audio_b64 有值则按月落盘；没有就只存文字。"""
    text = (text or '').strip()
    if not text:
        # 没传文字 → 尝试用最近一次转写兜底
        text = get_last_transcription()
    if not text:
        raise ValueError('没有可保存的语音内容（最近一次转写为空或已过期）')

    audio_file = ''
    if audio_b64:
        try:
            raw = base64.b64decode(audio_b64)
            ext = re.sub(r'[^a-z0-9]', '', (ext or 'webm').lower())[:5] or 'webm'
            month_dir = _AUDIO_ROOT / datetime.now().strftime('%Y%m')
            month_dir.mkdir(parents=True, exist_ok=True)
            fname = f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.{ext}"
            (month_dir / fname).write_bytes(raw)
            audio_file = f"{month_dir.name}/{fname}"
        except Exception as e:
            logger.warning(f"[VoiceArchive] 音频落盘失败（只存文字）: {e}")

    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO voice_archive (created_at, text, audio_file, duration, source) VALUES (?,?,?,?,?)",
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), text, audio_file, duration, source))
        conn.commit()
        rid = cur.lastrowid
    finally:
        conn.close()
    logger.info(f"[VoiceArchive] 已存 #{rid} audio={audio_file or '无'} text={text[:40]!r}")
    return {'id': rid, 'text': text, 'audio_file': audio_file}


def list_voice_notes(query: str = '', limit: int = 50) -> list:
    """倒序列出语音档案；query 非空时按文字模糊搜"""
    conn = _db()
    try:
        if query:
            rows = conn.execute(
                "SELECT id, created_at, text, audio_file, duration, source FROM voice_archive "
                "WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f'%{query}%', int(limit))).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, created_at, text, audio_file, duration, source FROM voice_archive "
                "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [{'id': r[0], 'created_at': r[1], 'text': r[2],
                 'audio_file': r[3], 'duration': r[4], 'source': r[5]} for r in rows]
    finally:
        conn.close()


def delete_voice_note(rid: int) -> bool:
    """删一条档案（连带删音频文件，失败不阻断）"""
    conn = _db()
    try:
        row = conn.execute("SELECT audio_file FROM voice_archive WHERE id=?", (int(rid),)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM voice_archive WHERE id=?", (int(rid),))
        conn.commit()
    finally:
        conn.close()
    if row and row[0]:
        try:
            f = _AUDIO_ROOT / row[0]
            if f.exists():
                f.unlink()
        except Exception as e:
            logger.warning(f"[VoiceArchive] 音频文件清理失败: {e}")
    return True
