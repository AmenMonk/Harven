# Copyright 2026 AmenMonk. Licensed under the Apache License 2.0.
"""
随身记 · 收件箱（#1133，2026-10-14）
定位：未分类碎片的「中转站」，不是台账——分类后的数据真家在各面板/成长档案。
三通道：①对话框说（哈文分流：稳=直达对应模块+回显可撤销；半=进收件箱+建议标签）②+号随身记（全落收件箱）③语音转写落收件箱。
原则：宁进收件箱不硬塞——本地关键词命中 ≥2 个特征才给直达建议，转走永远用户确认。
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'sqlite' / 'lingzhi.db'
_lock = threading.Lock()

bp = Blueprint('inbox', __name__, url_prefix='/api/inbox')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'open',
    target TEXT DEFAULT '',
    resolved_to TEXT DEFAULT '',
    remind_at TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_notes(status, id DESC);
"""


def _lazy_upgrade():
    """老表懒升级：补 remind_at 列（#1145 随身记挂提醒）"""
    c = _conn()
    try:
        cols = {r[1] for r in c.execute('PRAGMA table_info(inbox_notes)').fetchall()}
        if 'remind_at' not in cols:
            c.execute("ALTER TABLE inbox_notes ADD COLUMN remind_at TEXT DEFAULT ''")
            c.commit()
    finally:
        c.close()

# ── 分流规则（本地关键词，0 token）：命中 ≥2 个同类特征才建议直达，否则进收件箱 ──
_RULES = [
    ('账本', ['记账', '记一笔', '花了', '消费', '账单', '工资', '红包', '转账', '退款']),
    ('日程待办', ['提醒', '别忘', '明天', '后天', '下周', '预约', '约会', '接孩子', '交', '到期']),
    ('孩子成长档案', ['孩子', '儿子', '女儿', '作业', '错题', '考试', '成绩', '打卡', '跳绳', '身高', '体重']),
    ('车辆保养', ['加油', '保养', '轮胎', '机油', '洗车', '年检', '保险到期', '途锐', '车子']),
    ('冰箱库存', ['冰箱', '吃完', '用完', '过期', '库存', '买菜', '食材']),
    ('健康档案', ['血压', '吃药', '体检', '复查', '用药', '就医']),
]

_OPEN_STATUSES = ('open', 'done', 'deleted')


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def ensure_db():
    with _lock:
        c = _conn()
        try:
            c.executescript(_SCHEMA)
            c.commit()
        finally:
            c.close()


ensure_db()
try:
    _lazy_upgrade()
except Exception as e:
    logger.warning(f'[Inbox] remind_at 懒升级跳过: {e}')


_REMIND_HINT_PAT = None   # 懒编译（★ 2026-10-17 A. 随身记语义提醒）


def extract_remind(content: str) -> str:
    """★ 2026-10-17 A. 随身记语义提醒：碎片文字里解析出明确时间 → 返回 'YYYY-MM-DD HH:MM'；解析不出返回 ''。
    门闸：只在含提醒意图词（提醒/记得/别忘了/到点/准时）时才尝试——纯「明天下午开会」这类
    日程性内容不强行挂（免得记事本变闹钟轰炸）。解析复用 home_reminder.parse_reminder_text
    （实测认识「N分钟后/明天下午5点半」等）。失败静默返回 ''，绝不阻塞记事。"""
    global _REMIND_HINT_PAT
    t = str(content or '').strip()
    if not t:
        return ''
    try:
        if _REMIND_HINT_PAT is None:
            import re as _re_inbox
            _REMIND_HINT_PAT = _re_inbox.compile(r'提醒|记得|别忘了|到点|准时')
        if not _REMIND_HINT_PAT.search(t):
            return ''
        from src.core.home_reminder import parse_reminder_text
        info, err = parse_reminder_text(t)
        if err or not info or not info.get('date'):
            return ''
        return f"{info['date']} {info.get('time') or '09:00'}"[:16]
    except Exception:
        return ''


def set_remind(note_id: int, remind_at: str = '') -> dict:
    """给碎片挂/取消提醒。remind_at: 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD'，空串=取消"""
    remind_at = str(remind_at or '').strip()[:16]
    with _lock:
        c = _conn()
        try:
            row = c.execute('SELECT content FROM inbox_notes WHERE id=?', (int(note_id),)).fetchone()
            if not row:
                return {'success': False, 'message': '条目不存在'}
            _content = row['content']
            cur = c.execute("UPDATE inbox_notes SET remind_at=?, updated_at=datetime('now','localtime') WHERE id=?",
                            (remind_at, int(note_id)))
            c.commit()
        finally:
            c.close()
    # ★ 2026-10-15 B. 转正钩子：同步到 home_reminders（提醒唯一真家）
    _sync_home_reminder(note_id, _content, remind_at)
    return {'success': True, 'remind_at': remind_at,
            'message': ('已挂提醒 ' + remind_at + '（到点主动叫你）') if remind_at else '已取消提醒'}


def _sync_home_reminder(note_id, content: str, remind_at: str):
    """★ 2026-10-15 B. 随身记转正钩子：挂/改/删提醒同步到 home_reminders（提醒唯一真家）。
    remind_at 非空 → 先清旧的再建新；空 → 清。失败静默不阻塞收件箱操作。"""
    try:
        from src.core.home_reminder import add_reminder, delete_reminder, list_reminders
        olds = [r for r in list_reminders(module='inbox', limit=500)
                if r.get('ref_id') == int(note_id)]
        for r in olds:
            delete_reminder(r['id'])
        if str(remind_at or '').strip():
            rid, err = add_reminder(module='inbox', title=str(content or '')[:80],
                                    remind_at=str(remind_at), ref_id=int(note_id), source='inbox')
            if err:
                logger.warning(f'[Inbox] 转正提醒建失败(note {note_id}): {err}')
    except Exception as e:
        logger.warning(f'[Inbox] 转正提醒同步失败(note {note_id}): {e}')


def to_task(note_id: int) -> dict:
    """★ #1159（2026-10-17）甲案：碎片一键转正为任务（任务真家 = task_manager structured_tasks，
    SPA /api/tasks 同源）。碎片办结归档、target 标 task。双轨打通：日常速记走随身记，值得跟的一键转任务。"""
    with _lock:
        c = _conn()
        try:
            row = c.execute('SELECT content FROM inbox_notes WHERE id=?', (int(note_id),)).fetchone()
            if not row:
                return {'success': False, 'message': '条目不存在'}
            _content = str(row['content'] or '').strip()
        finally:
            c.close()
    if not _content:
        return {'success': False, 'message': '内容为空，转不了'}
    try:
        from src.core.task_manager import get_task_manager
        tm = get_task_manager()
        tid = tm.create_task(conversation_id='', title=_content[:60],
                             description=_content, priority=2)
    except Exception as e:
        logger.warning(f'[Inbox] 碎片转任务失败(note {note_id}): {e}')
        return {'success': False, 'message': f'任务建失败: {e}'}
    with _lock:
        c = _conn()
        try:
            c.execute("UPDATE inbox_notes SET status='done', target='task', updated_at=datetime('now','localtime') WHERE id=?",
                      (int(note_id),))
            c.commit()
        finally:
            c.close()
    logger.info(f'[Inbox] 碎片已转任务 note={note_id} → task={tid}')
    return {'success': True, 'task_id': tid, 'message': '已转成任务：' + _content[:30]}


def classify_note(text: str) -> dict:
    """本地毫秒级分流：返回 {target, hits, confident}。confident=命中≥2 特征才 True。"""
    t = str(text or '')
    best, best_hits = '', 0
    for target, kws in _RULES:
        n = sum(1 for k in kws if k in t)
        if n > best_hits:
            best, best_hits = target, n
    if best_hits >= 2:
        return {'target': best, 'hits': best_hits, 'confident': True}
    if best_hits == 1:
        return {'target': best, 'hits': 1, 'confident': False}
    return {'target': '', 'hits': 0, 'confident': False}


def add_note(content: str, source: str = 'manual', remind_at: str = '') -> dict:
    content = str(content or '').strip()[:500]
    if not content:
        return {'success': False, 'message': '内容不能为空'}
    cls = classify_note(content)
    remind_at = str(remind_at or '').strip()[:16]
    # ★ 2026-10-17 A. 随身记语义提醒：调用方没给时间就试着从文字里解析
    #   （实锤根因：「5分钟后提醒我接孩子」记进随身记只是纯文字，永远不响——现在自动转正）
    if not remind_at:
        remind_at = extract_remind(content)
    # ★ #1217（2026-10-25）重复记录防线：主聊天+管家浮层先后问同一句 → 同内容落两条
    #   （10-25 实锤：「后天练胸」22:45 主聊天 + 22:46 管家各一条）。
    #   15 分钟内已存在高相似(≥0.6)且同提醒时间的 open 记录 → 不重复插，返回已有条
    import difflib
    with _lock:
        c = _conn()
        try:
            recent = c.execute(
                "SELECT id, content, remind_at, created_at FROM inbox_notes "
                "WHERE status='open' AND created_at >= datetime('now','localtime','-15 minutes') "
                "ORDER BY id DESC LIMIT 30").fetchall()
            for rid, rtxt, rrem, rcreated in recent:
                if remind_at and rrem and remind_at != rrem:
                    continue  # 提醒时间不同 = 不同的事
                if not remind_at and rrem:
                    continue  # 旧条带提醒新条不带 = 可能是补时间，放行
                sim = difflib.SequenceMatcher(None, content, rtxt or '').ratio()
                if sim >= 0.5:  # ★ 0.5：外脑会改写用户原话（「后天提醒我练背」→「后天练背（今晚练的腿…）」），0.6 实测漏拦
                    mins = max(0, int((datetime.now().timestamp() - datetime.strptime(
                        rcreated, '%Y-%m-%d %H:%M:%S').timestamp()) / 60)) if rcreated else 0
                    return {'success': True, 'id': rid, 'dedup': True,
                            'remind': rrem or remind_at,
                            'message': f'这条 {mins} 分钟前刚记过（随身记 #{rid}），没有重复记录'
                                       + (f'，提醒时间 {rrem}' if rrem else '')}
        except Exception as _e:
            logger.debug(f'[Inbox] 去重检查失败(不影响写入): {_e}')
        finally:
            try:
                c.close()
            except Exception:
                pass
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                'INSERT INTO inbox_notes(content, source, target, remind_at) VALUES(?,?,?,?)',
                (content, source, cls.get('target', ''), remind_at))
            nid = cur.lastrowid
            c.commit()
        finally:
            c.close()
    # ★ 2026-10-15 B. 带时间入库时同步转正到 home_reminders
    if remind_at:
        _sync_home_reminder(nid, content, remind_at)
    # ★ #1220-A：随身记写入 → 数据版本 +1（手机端感知刷新）
    try:
        from src.core.sync_bus import bump
        bump('inbox_note')
    except Exception:
        pass
    return {'success': True, 'id': nid, 'suggest': cls.get('target') or '',
            'confident': cls.get('confident', False), 'remind': remind_at,
            'message': '已放入随身记' + ('并设提醒 ' + remind_at + '（到点主动叫你）' if remind_at else '')}


def list_notes(status: str = 'open', limit: int = 100) -> list:
    if status not in _OPEN_STATUSES:
        status = 'open'
    limit = max(1, min(int(limit or 100), 300))
    with _lock:
        c = _conn()
        try:
            rows = c.execute(
                'SELECT * FROM inbox_notes WHERE status=? ORDER BY id DESC LIMIT ?',
                (status, limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            c.close()


def resolve_note(note_id: int, resolved_to: str = '') -> dict:
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "UPDATE inbox_notes SET status='done', resolved_to=?, updated_at=datetime('now','localtime') "
                'WHERE id=? AND status=?', (str(resolved_to or '')[:40], int(note_id), 'open'))
            ok = cur.rowcount > 0
            c.commit()
        finally:
            c.close()
    if ok:
        # ★ 2026-10-15 B. 转走=事项已办，连带清掉挂的提醒
        _sync_home_reminder(note_id, '', '')
    return {'success': ok, 'message': '已标记转走' if ok else '条目不存在或已处理'}


def reopen_note(note_id: int) -> dict:
    with _lock:
        c = _conn()
        try:
            cur = c.execute(
                "UPDATE inbox_notes SET status='open', resolved_to='', updated_at=datetime('now','localtime') "
                'WHERE id=?', (int(note_id),))
            c.commit()
            return {'success': cur.rowcount > 0, 'message': '已撤回收件箱' if cur.rowcount else '条目不存在'}
        finally:
            c.close()


def delete_note(note_id: int) -> dict:
    with _lock:
        c = _conn()
        try:
            cur = c.execute('DELETE FROM inbox_notes WHERE id=?', (int(note_id),))
            ok = cur.rowcount > 0
            c.commit()
        finally:
            c.close()
    if ok:
        # ★ 2026-10-15 B. 删条目连带删挂的提醒（防僵尸提醒继续响）
        _sync_home_reminder(note_id, '', '')
    return {'success': ok, 'message': '已删除' if ok else '条目不存在'}


def count_open() -> int:
    with _lock:
        c = _conn()
        try:
            return c.execute("SELECT COUNT(*) FROM inbox_notes WHERE status='open'").fetchone()[0]
        finally:
            c.close()


# ════════════════ API 路由 ════════════════

@bp.route('/list', methods=['GET'])
def api_list():
    status = request.args.get('status', 'open')
    return jsonify({'success': True, 'notes': list_notes(status),
                    'open_count': count_open()})


@bp.route('/add', methods=['POST'])
def api_add():
    data = request.get_json(silent=True) or {}
    r = add_note(data.get('content', ''), data.get('source', 'manual'))
    return jsonify(r), (200 if r['success'] else 400)


@bp.route('/resolve', methods=['POST'])
def api_resolve():
    data = request.get_json(silent=True) or {}
    try:
        r = resolve_note(int(data.get('id', 0)), data.get('to', ''))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'id 非法'}), 400
    return jsonify(r), (200 if r['success'] else 400)


@bp.route('/reopen', methods=['POST'])
def api_reopen():
    data = request.get_json(silent=True) or {}
    try:
        r = reopen_note(int(data.get('id', 0)))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'id 非法'}), 400
    return jsonify(r), (200 if r['success'] else 400)


@bp.route('/delete', methods=['POST'])
def api_delete():
    data = request.get_json(silent=True) or {}
    try:
        r = delete_note(int(data.get('id', 0)))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'id 非法'}), 400
    return jsonify(r), (200 if r['success'] else 400)


@bp.route('/remind', methods=['POST'])
def api_remind():
    """★ 2026-10-17 C. 本地碎片手动挂/取消提醒：text=自然语言时间（「明天17:30」），空=取消。
    与 relay inbox.remind 同逻辑（同一解析器），set_remind 内部同步 home_reminders 真家。"""
    data = request.get_json(silent=True) or {}
    try:
        nid = int(data.get('id', 0))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'id 非法'}), 400
    txt = str(data.get('text', '') or '').strip()[:60]
    if not txt:
        r = set_remind(nid, '')
        return jsonify(r), (200 if r['success'] else 400)
    from src.core.home_reminder import parse_reminder_text
    info, perr = parse_reminder_text(txt)
    if perr or not info or not info.get('date'):
        return jsonify({'success': False, 'message': '时间没看懂，试试「明天17:30」或「明天下午5点半」'}), 400
    try:
        rdt = datetime.strptime(f"{info['date']} {info.get('time') or '09:00'}", '%Y-%m-%d %H:%M')
    except ValueError:
        return jsonify({'success': False, 'message': '时间格式没解出来，再试一次'}), 400
    r = set_remind(nid, rdt.strftime('%Y-%m-%d %H:%M'))
    return jsonify(r), (200 if r['success'] else 400)


@bp.route('/count', methods=['GET'])
def api_count():
    return jsonify({'success': True, 'open_count': count_open()})


# ★ 2026-10-17 清理：旧版 /remind（只认标准格式）与本日新版（自然语言）重复，
#   Flask 后注册覆盖前者导致新版被顶——删旧留新，本地页自然语言挂提醒才真正生效。


@bp.route('/to_task', methods=['POST'])
def api_to_task():
    """★ #1159 甲案：碎片一键转任务（本地页/SPA 用，与 relay inbox.to_task 同逻辑）"""
    data = request.get_json(silent=True) or {}
    try:
        r = to_task(int(data.get('id', 0)))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'id 非法'}), 400
    return jsonify(r), (200 if r['success'] else 400)


@bp.route('/reminders', methods=['GET'])
def api_reminders():
    """★ #1145 聚合视图：随身记挂的提醒 + 全家庭到点/未办提醒（home_reminders，含冰箱/孩子/日历等各处挂的）"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    with _lock:
        c = _conn()
        try:
            inbox_rows = [dict(r) for r in c.execute(
                "SELECT id, content, remind_at, status FROM inbox_notes "
                "WHERE remind_at != '' AND status = 'open' ORDER BY remind_at LIMIT 50").fetchall()]
        finally:
            c.close()
    home = []
    try:
        from src.core.home_reminder import list_reminders
        for r in list_reminders(pending_only=True, limit=50):
            home.append({'id': r.get('id'), 'title': r.get('title'), 'remind_at': r.get('remind_at'),
                         'module': r.get('module'), 'repeat': r.get('repeat')})
    except Exception as e:
        logger.warning(f'[Inbox] 聚合 home_reminders 失败: {e}')
    return jsonify({'success': True, 'now': now,
                    'inbox': inbox_rows, 'home': home,
                    'total': len(inbox_rows) + len(home)})
