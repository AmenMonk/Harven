# -*- coding: utf-8 -*-
"""家庭提醒/日历 API 路由 ★ 2026-09-16
前缀 /api/home：
  GET  /reminders?module=&pending=1     提醒列表
  POST /reminders                       新增提醒（{text:"一句话"} 或结构化字段）
  POST /reminders/<id>/complete         完成（重复的自动滚到下一次）
  DELETE /reminders/<id>                删除
  GET  /events?module=&upcoming=7       日历日程列表
  POST /events                          新增日程（{text} 或结构化，支持前置提醒分钟）
  DELETE /events/<id>                   删除（连带提醒）
  GET  /due                             到期提醒（数量+内容，角标/横幅轮询）
"""
import logging

from flask import Blueprint, jsonify, request

from src.core import home_reminder as hr

logger = logging.getLogger(__name__)

bp = Blueprint('home_reminder', __name__, url_prefix='/api/home')


@bp.route('/reminders', methods=['GET'])
def reminders():
    try:
        module = request.args.get('module', '')
        pending = request.args.get('pending', '') == '1'
        return jsonify({'success': True, 'reminders': hr.list_reminders(module, pending_only=pending)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/reminders', methods=['POST'])
def add_reminder():
    try:
        d = request.get_json(force=True, silent=True) or {}
        text = (d.get('text') or '').strip()
        if text and not d.get('title'):
            # 一句话模式：本地模型解析
            rid, info, err = hr.create_from_text(text, module=d.get('module') or '',
                                                 source=d.get('source') or 'manual')
            if err:
                return jsonify({'success': False, 'error': err}), 200
            return jsonify({'success': True, 'id': rid, 'info': info,
                            'describe': hr.describe_info(info)})
        rid, err = hr.add_reminder(
            module=d.get('module') or '', title=d.get('title') or '',
            remind_at=d.get('remind_at') or '',
            repeat=d.get('repeat') or 'once',
            interval_value=d.get('interval_value') or 0,
            advance=d.get('advance') or 0,
            note=d.get('note') or '', ref_id=d.get('ref_id'),
            source=d.get('source') or 'manual')
        if err:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'id': rid})
    except Exception as e:
        logger.error(f"[HomeAPI] add_reminder 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/reminders/<int:rid>/complete', methods=['POST'])
def complete_reminder(rid):
    try:
        ok, msg = hr.complete_reminder(rid)
        return jsonify({'success': ok, 'error': msg}), (200 if ok else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/reminders/<int:rid>', methods=['DELETE'])
def delete_reminder(rid):
    try:
        hr.delete_reminder(rid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/events', methods=['GET'])
def events():
    try:
        module = request.args.get('module', '')
        upcoming = int(request.args.get('upcoming', '0') or 0)
        return jsonify({'success': True, 'events': hr.list_events(module, upcoming_days=upcoming)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/events', methods=['POST'])
def add_event():
    try:
        d = request.get_json(force=True, silent=True) or {}
        text = (d.get('text') or '').strip()
        if text and not d.get('title'):
            # 一句话模式：先解析提醒结构，date/time 直接复用
            rid, info, err = hr.create_from_text(text, module=d.get('module') or 'home_calendar',
                                                 source='event')
            if err:
                return jsonify({'success': False, 'error': err}), 200
            adv = hr.ADVANCE_ZH_MAP.get(info.get('advance', '不提醒'), 0)
            eid, _rid2, err2 = hr.add_event(
                module=d.get('module') or 'home_calendar', title=info['title'],
                event_date=info['date'], event_time=info.get('time') or '',
                advance_minutes=adv, note=f"原话：{text[:200]}",
                repeat=hr.REPEAT_ZH_MAP.get(info.get('repeat', '不重复'), 'once'))
            if err2:
                return jsonify({'success': False, 'error': err2}), 200
            if rid:
                hr.delete_reminder(rid)  # 撤掉 create_from_text 单独建的提醒，用日程关联的
            return jsonify({'success': True, 'id': eid, 'info': info,
                            'describe': hr.describe_info(info)})
        eid, rid, err = hr.add_event(
            module=d.get('module') or 'home_calendar', title=d.get('title') or '',
            event_date=d.get('event_date') or d.get('date') or '',
            event_time=d.get('event_time') or d.get('time') or '',
            advance_minutes=d.get('advance_minutes') or 0,
            note=d.get('note') or '', repeat=d.get('repeat') or 'once')
        if err:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'id': eid, 'reminder_id': rid})
    except Exception as e:
        logger.error(f"[HomeAPI] add_event 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/events/<int:eid>', methods=['DELETE'])
def delete_event(eid):
    try:
        hr.delete_event(eid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/due', methods=['GET'])
def due():
    try:
        items = hr.due_reminders(20)
        return jsonify({'success': True, 'count': len(items), 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
