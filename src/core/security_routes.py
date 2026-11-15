# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 安全防护API路由
"""

from flask import Blueprint, request, jsonify
from src.core.security_guard import (
    get_permission_mode, enable_full_access, disable_full_access,
    get_logs, check_command, DANGEROUS_COMMANDS,
    ALLOWED_PATHS, FORBIDDEN_PATHS, change_admin_password
)

bp = Blueprint('security', __name__)


@bp.route('/api/security/mode', methods=['GET'])
def get_mode():
    """获取当前权限模式"""
    mode = get_permission_mode()
    return jsonify({
        'success': True,
        'mode': mode,
        'mode_label': '全权模式' if mode == 'full' else '安全模式',
        'dangerous_commands': DANGEROUS_COMMANDS[:10],
        'allowed_paths': ALLOWED_PATHS[:5],
        'forbidden_paths': FORBIDDEN_PATHS[:5],
    })


@bp.route('/api/security/full-access', methods=['POST'])
def enable_full():
    """开启全权模式"""
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    if not password:
        return jsonify({'success': False, 'error': '请输入密码'}), 400

    result = enable_full_access(password)
    return jsonify(result)


@bp.route('/api/security/disable', methods=['POST'])
def disable_full():
    """关闭全权模式"""
    result = disable_full_access()
    return jsonify(result)


@bp.route('/api/security/change-password', methods=['POST'])
def change_admin_pwd():
    """修改管理员密码（同步影响保险箱锁/解锁、面板卸载确认、全权模式）
    ★ 2026-09-17 新增：以前首次强制改密后再无改密入口"""
    data = request.get_json(silent=True) or {}
    old_pwd = data.get('old_password', '')
    new_pwd = data.get('new_password', '')
    if not old_pwd or not new_pwd:
        return jsonify({'success': False, 'error': '请填写旧密码和新密码'})
    result = change_admin_password(old_pwd, new_pwd)
    return jsonify(result)


@bp.route('/api/security/log', methods=['GET'])
def get_security_log():
    """获取操作日志"""
    limit = request.args.get('limit', 50, type=int)
    logs = get_logs(limit)
    return jsonify({'success': True, 'logs': logs})


# ── ★ #1362 密码恢复（2026-11-02）：安全问题 + 恢复码双通道 ──────────

@bp.route('/api/security/recovery/status', methods=['POST'])
def recovery_status():
    """设置页用：查恢复机制状态 + 取恢复码（需验证当前密码，防止别人偷看恢复盘）"""
    from src.core import password_recovery as pr
    from src.core.security_guard import get_admin_password
    data = request.get_json(silent=True) or {}
    if (data.get('password') or '') != get_admin_password():
        return jsonify({'success': False, 'error': '密码不对'}), 401
    return jsonify({'success': True, **pr.get_status()})


@bp.route('/api/security/recovery/setup', methods=['POST'])
def recovery_setup():
    """设置/更新安全问题（需验证当前密码）。body: {password, q1, a1, q2, a2}"""
    from src.core import password_recovery as pr
    from src.core.security_guard import get_admin_password
    data = request.get_json(silent=True) or {}
    if (data.get('password') or '') != get_admin_password():
        return jsonify({'success': False, 'error': '当前密码不对'}), 401
    return jsonify(pr.setup_questions(data.get('q1'), data.get('a1'), data.get('q2'), data.get('a2')))


@bp.route('/api/security/recovery/hints', methods=['GET'])
def recovery_hints():
    """登录页「忘记密码」用（无门槛）：只返回两个问题文本"""
    from src.core import password_recovery as pr
    return jsonify({'success': True, **pr.get_hints()})


@bp.route('/api/security/recovery/reset-by-answers', methods=['POST'])
def recovery_reset_answers():
    """忘记密码·答题重置。body: {a1, a2, new_password}，两题全对才放行"""
    from src.core import password_recovery as pr
    data = request.get_json(silent=True) or {}
    if not pr.verify_answers(data.get('a1'), data.get('a2')):
        return jsonify({'success': False, 'error': '答案不对，再想想'}), 403
    return jsonify(pr.reset_password(data.get('new_password') or ''))


@bp.route('/api/security/recovery/reset-by-code', methods=['POST'])
def recovery_reset_code():
    """忘记密码·恢复码重置。body: {code, new_password}"""
    from src.core import password_recovery as pr
    data = request.get_json(silent=True) or {}
    if not pr.verify_code(data.get('code') or ''):
        return jsonify({'success': False, 'error': '恢复码不对'}), 403
    return jsonify(pr.reset_password(data.get('new_password') or ''))


@bp.route('/api/security/check-command', methods=['POST'])
def check_cmd():
    """检查命令是否安全（预览用）"""
    data = request.get_json(silent=True) or {}
    command = data.get('command', '')
    ok, reason = check_command(command)
    return jsonify({'success': True, 'safe': ok, 'reason': reason})
