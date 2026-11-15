# -*- coding: utf-8 -*-
"""
提醒主动触达守护线程 ★ 2026-10-15 #1150（随身记×提醒中枢一体化·D块）
=========================================
- 每 30 秒扫一次 home_reminders 到期未提醒的条目（含聊天直设/随身记转正/各面板挂的）
- 触达通道：① TTS 语音播报（Windows 自带 SAPI，config 开关 reminder_voice 默认开）
            ② 通知队列 → SPA 前端 60s 轮询 /api/reminders/due 弹横幅+响铃
- 打钩与 engine.inject_due 共用 complete 机制（notified 字段互斥，谁先处理谁打钩，不重复弹）
- 零新增依赖：TTS=PowerShell System.Speech 子进程；不用 plyer/pyttsx3
红线：全程本地，数据不出本机。
"""
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_lock = threading.Lock()
_daemon_started = False
_recent = []           # 最近已触达通知 [{id,title,remind_at,ts}]
_MAX_RECENT = 20


def _voice_enabled():
    """config.yaml 顶层 reminder_voice，缺省开。手动加 `reminder_voice: false` 可关。"""
    try:
        import yaml
        cfg = yaml.safe_load((BASE_DIR / 'config.yaml').read_text(encoding='utf-8')) or {}
        return bool(cfg.get('reminder_voice', True))
    except Exception:
        return True


def _speak(text):
    """Windows 自带 SAPI 语音播报（PowerShell 子进程，后台不阻塞）。单引号转义防注入。"""
    if not _voice_enabled():
        return
    try:
        safe = str(text).replace("'", "''")[:120]
        ps = ("Add-Type -AssemblyName System.Speech;"
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              f"$s.Speak('{safe}')")
        subprocess.Popen(['powershell', '-NoProfile', '-Command', ps],
                         creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception as e:
        logger.debug(f'[ReminderDaemon] TTS 失败: {e}')


def push_notification(title, item_id='ext'):
    """★ 2026-10-24 #1176 管家统一分发出口：任何引擎（管家/教练）要出声都走这里——
    TTS 播报 + 进 _recent 队列（SPA 轮询弹横幅）。勿扰时段由调用方（管家）自己判。"""
    title = (title or '提醒').strip()[:100]
    _speak(title)
    with _lock:
        _recent.insert(0, {'id': str(item_id), 'title': title, 'remind_at': '',
                           'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
        del _recent[_MAX_RECENT:]
    logger.info(f'[ReminderDaemon] 分发通知: {title}')


def _loop():
    # 延迟导入：避免循环依赖（home_reminder → kid_archive → …）
    from src.core.home_reminder import complete_reminder, due_reminders
    _goal_last_date = {}  # ★ goal 节点审核去重 {(gid,idx): 'YYYY-MM-DD'}
    while True:
        try:
            rows = due_reminders(limit=5)
            for r in rows:
                title = (r.get('title') or '提醒').strip()[:60]
                _speak(f'提醒：{title}')
                with _lock:
                    _recent.insert(0, {'id': r['id'], 'title': title,
                                       'remind_at': r.get('remind_at', ''),
                                       'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
                    del _recent[_MAX_RECENT:]
                try:
                    complete_reminder(r['id'])
                except Exception as e:
                    logger.warning(f'[ReminderDaemon] 打钩失败 id={r["id"]}: {e}')
                logger.info(f"[ReminderDaemon] 已触达: {title}")
        except Exception as e:
            logger.debug(f'[ReminderDaemon] 扫描失败: {e}')
        # ★ 2026-10-20 第三批3.2: 提醒统一出口——goal 节点到期触达（本地硬指标 0 token）
        try:
            from src.core.goal_manager import due_nodes, query_metric
            today = datetime.now().strftime('%Y-%m-%d')
            for n in due_nodes(days_ahead=0):
                key = (n['goal_id'], n['node_idx'])
                if _goal_last_date.get(key) == today:
                    continue
                done, target, _ = query_metric(n['goal_id'], n['node_idx'])
                title = f"目标节点：{n['node_name']}（进度 {done}/{target}）"
                _speak(f'目标提醒：{n["node_name"]}，当前进度 {done} 比 {target}')
                with _lock:
                    _recent.insert(0, {'id': f"goal_{n['goal_id']}_{n['node_idx']}", 'title': title,
                                       'remind_at': n.get('due', ''),
                                       'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
                    del _recent[_MAX_RECENT:]
                _goal_last_date[key] = today
                logger.info(f"[ReminderDaemon] goal 节点已触达: {title}")
        except Exception as e:
            logger.debug(f'[ReminderDaemon] goal 扫描失败: {e}')
        # ★ 2026-10-24 #1176 教练源扫描已收编管家（butler_agenda.DECLARATIONS 统一扫描分发）
        # 原 due_source_reminders 块删除：面板日期源由管家 30s 心跳统一盯，避免双头提醒
        # ★ 2026-10-20 家人档案·生日触达（每天最多播一次/人）
        try:
            from src.core.panel_manager import get_panel_manager
            _today = datetime.now().strftime('%Y-%m-%d')
            for b in get_panel_manager().upcoming_birthdays(7):
                key = ('bd', b['id'])
                if _goal_last_date.get(key) == _today:
                    continue
                rel = b.get('relation') or ''
                who = f"{rel}{b['name']}".strip() or b['name']
                age = f"（满 {b['age']} 岁）" if b.get('age') else ''
                when = '今天' if b['days_until'] == 0 else f"还有 {b['days_until']} 天"
                title = f"🎂 {who}的生日{when}{age}"
                _speak(f'生日提醒：{who}的生日{when}')
                with _lock:
                    _recent.insert(0, {'id': f"bd_{b['id']}_{today}", 'title': title,
                                       'remind_at': today,
                                       'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
                    del _recent[_MAX_RECENT:]
                _goal_last_date[key] = _today
                logger.info(f"[ReminderDaemon] 生日触达: {title}")
        except Exception as e:
            logger.debug(f'[ReminderDaemon] 生日扫描失败: {e}')
        time.sleep(30)


def ensure_daemon_loop():
    """启动扫描线程（幂等，web_app 挂点调用）"""
    global _daemon_started
    if _daemon_started:
        return
    _daemon_started = True
    threading.Thread(target=_loop, daemon=True, name='reminder-daemon').start()
    logger.info('[ReminderDaemon] 主动触达线程已启动（30s 扫描 + TTS 播报）')


def recent_notifications(minutes=5, limit=10):
    """SPA 轮询用：最近 N 分钟内已播报的提醒（前端弹横幅+响铃）"""
    cutoff = time.time() - minutes * 60
    out = []
    with _lock:
        for n in _recent:
            try:
                ts = datetime.strptime(n['ts'], '%Y-%m-%d %H:%M:%S').timestamp()
            except Exception:
                continue
            if ts >= cutoff:
                out.append(n)
    return out[:limit]


bp = Blueprint('reminder_daemon', __name__, url_prefix='/api/reminders')


@bp.route('/pending', methods=['GET'])
def api_pending():
    """★ #1217（2026-10-25）管家中心「提醒」区数据源：待响提醒列表（含随身记转正的）

    痛点实锤：「后天提醒我练胸」存进 home_reminders 后，管家中心没有「提醒」展示区，
    用户全程看不见自己设过的提醒 → 以为哈文没记录/不知道提醒功能怎么用。
    """
    try:
        from src.core.home_reminder import list_reminders
        rows = list_reminders(pending_only=True, limit=50)
        return jsonify({'success': True, 'reminders': rows})
    except Exception as e:
        return jsonify({'success': False, 'reminders': [], 'error': str(e)}), 200


@bp.route('/for-panel/<panel_id>', methods=['GET'])
def api_for_panel(panel_id):
    """★ #1219（2026-10-25）通用「相关提醒」聚合：面板域标签 → 双源合并

    ① 用户口头设的（home_reminders.module 按域匹配）
    ② 面板数据自动算的（butler_agenda.source_panel 匹配，open 状态）
    一个提醒系统两个来源，前端 PanelReminders 组件按面板接入零新建系统。
    """
    # 面板 → 域标签映射（一处集中管理，新面板加一行）
    PANEL_MODULE_MAP = {
        'fitness_log': 'fitness', 'fridge_stock_1': 'fridge', 'home_items': 'items',
        'kid_archive': 'kid', 'study_center': 'kid', 'meal_diary': 'meal',
        'home_calendar': 'calendar',
    }
    module = PANEL_MODULE_MAP.get(panel_id, '')
    try:
        from src.core.home_reminder import list_reminders
        out = []
        if module:
            for r in list_reminders(module=module, pending_only=True, limit=10):
                out.append({'id': r['id'], 'title': r['title'], 'time': r.get('remind_at', ''),
                            'repeat': r.get('repeat', 'once'), 'src': 'user', 'rid': r['id']})
        try:
            conn = __import__('sqlite3').connect(
                str(__import__('pathlib').Path(__file__).resolve().parent.parent.parent
                    / 'data' / 'sqlite' / 'lingzhi.db'))
            rows = conn.execute(
                "SELECT id, title, detail, date FROM butler_agenda "
                "WHERE source_panel=? AND status='open' ORDER BY date LIMIT 10",
                (panel_id,)).fetchall()
            conn.close()
            for rid, title, detail, date in rows:
                out.append({'id': f'a{rid}', 'title': title, 'time': date or '',
                            'repeat': '', 'src': 'agenda', 'detail': (detail or '')[:60]})
        except Exception:
            pass
        return jsonify({'success': True, 'module': module, 'reminders': out[:12]})
    except Exception as e:
        return jsonify({'success': False, 'module': module, 'reminders': [], 'error': str(e)}), 200


@bp.route('/<int:rid>', methods=['DELETE'])
def api_reminder_delete(rid):
    """★ #1217 管家中心「提醒」区：删掉不要的提醒"""
    try:
        from src.core.home_reminder import delete_reminder
        r = delete_reminder(rid)
        return jsonify({'success': bool(r), 'message': '已删除' if r else '删除失败（可能已不存在）'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200


@bp.route('/due', methods=['GET'])
def api_due():
    """SPA 轮询端点：最近 5 分钟已触达的提醒列表"""
    try:
        return jsonify({'success': True, 'notifications': recent_notifications(minutes=5, limit=10)})
    except Exception as e:
        return jsonify({'success': False, 'notifications': [], 'error': str(e)}), 200
