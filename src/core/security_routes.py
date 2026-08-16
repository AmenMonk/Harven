# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 安全防护API路由
"""

from flask import Blueprint, request, jsonify
from src.core.security_guard import (
    get_permission_mode, enable_full_access, disable_full_access,
    get_logs, check_command, DANGEROUS_COMMANDS,
    ALLOWED_PATHS, FORBIDDEN_PATHS
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


@bp.route('/api/security/log', methods=['GET'])
def get_security_log():
    """获取操作日志"""
    limit = request.args.get('limit', 50, type=int)
    logs = get_logs(limit)
    return jsonify({'success': True, 'logs': logs})


@bp.route('/api/security/check-command', methods=['POST'])
def check_cmd():
    """检查命令是否安全（预览用）"""
    data = request.get_json(silent=True) or {}
    command = data.get('command', '')
    ok, reason = check_command(command)
    return jsonify({'success': True, 'safe': ok, 'reason': reason})
