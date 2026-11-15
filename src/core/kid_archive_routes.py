# -*- coding: utf-8 -*-
"""
孩子成长档案系统 · API 路由（/api/kid）
Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import logging
from flask import Blueprint, request, jsonify

from src.core import kid_archive as ka

logger = logging.getLogger(__name__)

bp = Blueprint('kid_archive', __name__, url_prefix='/api/kid')


def _ok(data=None, **extra):
    r = {'success': True}
    if data is not None:
        r.update(data if isinstance(data, dict) else {'data': data})
    r.update(extra)
    return jsonify(r)


def _err(msg, code=400):
    return jsonify({'success': False, 'message': msg}), code


# ══════════════════════════ 模式切换密码门 ══════════════════════════

@bp.route('/verify_parent', methods=['POST'])
def verify_parent():
    """★ #1140（2026-10-14）孩子→家长模式密码门：服务端校验管理员密码（与锁定级面板同源），纯前端 confirm 时代结束"""
    data = request.get_json(silent=True) or {}
    from src.core.security_guard import get_admin_password
    if str(data.get('password') or '') != get_admin_password():
        logger.info('[kid_archive] 家长模式密码校验失败')
        return _err('密码不对', 403)
    return _ok({'verified': True})


# ══════════════════════════ 学习中枢（★ #1147，2026-10-14）════════════════════════

@bp.route('/<int:child_id>/timetable', methods=['GET'])
def get_timetable(child_id):
    weekday = request.args.get('weekday', 0)
    try:
        weekday = int(weekday)
    except (TypeError, ValueError):
        weekday = 0
    return _ok({'items': ka.list_timetable(child_id, weekday)})


@bp.route('/<int:child_id>/timetable', methods=['PUT'])
def put_timetable(child_id):
    data = request.get_json(silent=True) or {}
    return _ok(ka.save_timetable(child_id, data.get('items', [])))


@bp.route('/<int:child_id>/calendar', methods=['GET'])
def get_calendar(child_id):
    upcoming = request.args.get('upcoming') == '1'
    return _ok({'items': ka.list_calendar(child_id, upcoming)})


@bp.route('/<int:child_id>/calendar', methods=['POST'])
def post_calendar(child_id):
    data = request.get_json(silent=True) or {}
    if isinstance(data.get('items'), list):
        # ★ #1147 批量覆盖式保存（前端校历编辑整表提交）
        ka.save_calendar(child_id, data['items'])
        return _ok({'count': len(data['items'])})
    if not data.get('date'):
        return _err('date 必填（YYYY-MM-DD）')
    cid = ka.add_calendar(child_id, data.get('date'), data.get('ctype', '放假'),
                          data.get('days', 1), data.get('title', ''), data.get('notes', ''))
    return _ok({'id': cid})


@bp.route('/<int:child_id>/school_today', methods=['GET'])
def school_today(child_id):
    """一句话问答引擎：今天课表+是否放假+下次放假（本地毫秒级）"""
    return _ok(ka.today_school_info(child_id))


@bp.route('/<int:child_id>/weakpoints', methods=['GET'])
def get_weakpoints(child_id):
    return _ok({'items': ka.weakpoint_report(child_id)})


@bp.route('/<int:child_id>/grade', methods=['POST'])
def grade_homework(child_id):
    """★ #1148 AI 批改：text=题目+学生答案文本（拍照识别先出文本），错题自动进错题域+图谱"""
    data = request.get_json(silent=True) or {}
    text = str(data.get('text') or '').strip()
    if not text:
        return _err('缺少作业文本 text（先拍照识别出文字再批改）')
    return _ok(ka.grade_homework(child_id, data.get('subject', ''), text))


@bp.route('/<int:child_id>/tutor', methods=['POST'])
def tutor(child_id):
    """★ #1150 问小哈：引导式学习伙伴（不直接给答案），对话记时间轴家长可见"""
    data = request.get_json(silent=True) or {}
    return _ok(ka.tutor_reply(child_id, data.get('question', '')))


# ══════════════════════════ 孩子 ══════════════════════════

@bp.route('/children', methods=['GET'])
def list_children():
    return _ok(children=ka.get_children())


@bp.route('/children', methods=['POST'])
def create_child():
    data = request.get_json(silent=True) or {}
    if not data.get('name'):
        return _err('孩子名字不能为空')
    cid = ka.create_child(data)
    return _ok(child_id=cid, child=ka.get_child(cid))


@bp.route('/children/<int:child_id>', methods=['PUT'])
def update_child(child_id):
    data = request.get_json(silent=True) or {}
    ka.update_child(child_id, data)
    return _ok(child=ka.get_child(child_id))


@bp.route('/children/<int:child_id>', methods=['DELETE'])
def delete_child(child_id):
    """⚠️ 删除孩子档案（含全部记录，不可逆）"""
    ka.delete_child(child_id)
    return _ok()


@bp.route('/default-child', methods=['GET'])
def default_child():
    kids = ka.get_children()
    if not kids:
        return _ok(child=None)
    kid = next((k for k in kids if k.get('is_default')), kids[0])
    return _ok(child=kid)


# ══════════════════════════ 分类档案 CRUD ══════════════════════════

@bp.route('/<int:child_id>/<domain>', methods=['GET'])
def query_domain(child_id, domain):
    try:
        filters = {k: v for k, v in request.args.items() if k not in ('limit', 'offset')}
        rows = ka.query_domain(child_id, domain, filters,
                               limit=int(request.args.get('limit', 500)),
                               offset=int(request.args.get('offset', 0)))
        return _ok(records=rows)
    except ValueError as e:
        return _err(str(e), 404)


@bp.route('/<int:child_id>/<domain>', methods=['POST'])
def insert_domain(child_id, domain):
    data = request.get_json(silent=True) or {}
    try:
        rid = ka.insert_domain(child_id, domain, data)
        return _ok(record_id=rid)
    except ValueError as e:
        return _err(str(e), 404)
    except Exception as e:
        logger.error(f"[KidAPI] 写入失败: {e}")
        return _err(str(e), 500)


@bp.route('/<int:child_id>/<domain>/<int:rid>', methods=['PUT'])
def update_domain(child_id, domain, rid):
    data = request.get_json(silent=True) or {}
    try:
        ka.update_domain(child_id, domain, rid, data)
        return _ok()
    except ValueError as e:
        return _err(str(e), 404)


@bp.route('/<int:child_id>/<domain>/<int:rid>', methods=['DELETE'])
def delete_domain(child_id, domain, rid):
    try:
        ka.delete_domain(child_id, domain, rid)
        return _ok()
    except ValueError as e:
        return _err(str(e), 404)


# ══════════════════════════ 时间轴 ══════════════════════════

@bp.route('/<int:child_id>/timeline', methods=['GET'])
def timeline(child_id):
    rows = ka.query_timeline(child_id,
                             category=request.args.get('category', ''),
                             limit=int(request.args.get('limit', 100)),
                             before_id=int(request.args.get('before_id', 0)))
    return _ok(records=rows)


# ══════════════════════════ 提醒 / 红点 ══════════════════════════

@bp.route('/<int:child_id>/reminders', methods=['POST'])
def add_reminder(child_id):
    data = request.get_json(silent=True) or {}
    if not data.get('title'):
        return _err('提醒内容不能为空')
    rid = ka.add_reminder(child_id, data.get('rtype', 'event'), data['title'],
                          data.get('detail', ''), data.get('due_at', ''),
                          data.get('repeat_rule', ''))
    return _ok(record_id=rid)


@bp.route('/<int:child_id>/reminders/<int:rid>/done', methods=['POST'])
def done_reminder(child_id, rid):
    ka.complete_reminder(child_id, rid)
    return _ok()


@bp.route('/<int:child_id>/alerts', methods=['GET'])
def alerts(child_id):
    return _ok(ka.pending_alerts(child_id))


# ══════════════════════════ 报告 ══════════════════════════

@bp.route('/<int:child_id>/reports', methods=['GET'])
def reports(child_id):
    return _ok(records=ka.get_reports(child_id))


@bp.route('/<int:child_id>/ai/report', methods=['POST'])
def ai_report(child_id):
    """AI 分析报告（本地模型）"""
    data = request.get_json(silent=True) or {}
    rtype = data.get('rtype', 'on_demand')  # on_demand / weekly
    domain = data.get('domain', 'overview')
    rid, err = ka.generate_report(child_id, rtype=rtype, domain=domain)
    if err:
        # ★ 2026-09-16 数据门闸：记录太少不硬编，友好告知
        if err.startswith('ARCHIVE_TOO_THIN:'):
            n = err.split(':', 1)[1]
            return _ok(too_thin=True, count=n,
                       message=f'孩子档案里现在只有 {("0" if n == "0" else n)} 条记录，AI 写报告只能靠"通用套话"。'
                               f'先跟哈文聊聊孩子的日常（比如"今天数学考了95，应用题又马虎了"），'
                               f'攒够 3 条再来生成，报告才有根有据。')
        return _err(err, 500)
    recs = ka.get_reports(child_id, limit=1)
    return _ok(report=recs[0] if recs else None)


@bp.route('/<int:child_id>/ai/meal-plan', methods=['POST'])
def ai_meal_plan(child_id):
    """AI 一周菜谱建议（结合过敏/忌口/喜好/健康，本地模型）"""
    rid, err = ka.generate_meal_plan(child_id)
    if err:
        return _err(err, 500)
    recs = ka.get_reports(child_id, limit=1)
    return _ok(report=recs[0] if recs else None)


# ══════════════════════════ 对话摘记 ══════════════════════════

@bp.route('/<int:child_id>/digests', methods=['GET'])
def digests(child_id):
    include_hidden = request.args.get('include_hidden', '1') == '1'
    return _ok(records=ka.get_digests(child_id, include_hidden=include_hidden))


@bp.route('/<int:child_id>/digests', methods=['POST'])
def manual_digest(child_id):
    """手动记一条对话摘记（家长手动归档）"""
    data = request.get_json(silent=True) or {}
    rid = ka.save_digest(child_id, data.get('visible', ''), data.get('hidden', ''),
                         level=data.get('level', 'normal'), model='manual',
                         conv_id=data.get('conv_id', ''))
    return _ok(record_id=rid)


# ══════════════════════════ AI 记录整理（拍照/口述辅助） ══════════════════════════

@bp.route('/ai/organize', methods=['POST'])
def ai_organize():
    """把口述内容整理成结构化建议（本地模型，免费）"""
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return _err('内容不能为空')
    result, err = ka.analyze_photo_note(data.get('child_id', 0), text, data.get('domain', 'learning'))
    if err:
        return _err(err, 500)
    return _ok(result=result)


# ══════════════════════════ 照片上传 ══════════════════════════

@bp.route('/<int:child_id>/photo', methods=['POST'])
def upload_photo(child_id):
    f = request.files.get('file')
    if not f:
        # 兼容 base64 dataURL 方式
        import base64, re
        data = request.get_json(silent=True) or {}
        b64 = data.get('dataUrl', '')
        m = re.match(r'data:image/(\w+);base64,(.+)', b64)
        if not m:
            return _err('没有文件')
        ext = 'png' if m.group(1) == 'png' else 'jpg'
        url = ka.save_photo(child_id, base64.b64decode(m.group(2)), ext)
        return _ok(url=url)
    ext = 'png' if f.filename and f.filename.lower().endswith('.png') else 'jpg'
    url = ka.save_photo(child_id, f.read(), ext)
    return _ok(url=url)


# ══════════════════════════ 设置 ══════════════════════════

@bp.route('/settings', methods=['GET'])
def get_settings():
    return _ok(settings=ka.get_settings())


@bp.route('/settings', methods=['POST'])
def save_settings():
    data = request.get_json(silent=True) or {}
    return _ok(settings=ka.save_settings(data))
