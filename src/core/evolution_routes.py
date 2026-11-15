# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 受控进化管理路由

提供受控进化管理的 RESTful API：
- GET  /api/evolution/version          获取当前版本信息
- GET  /api/evolution/check            检测新版本
- POST /api/evolution/request          请求进化（需要用户批准）
- POST /api/evolution/<log_id>/approve  批准进化
- POST /api/evolution/<log_id>/reject   拒绝进化
- POST /api/evolution/<log_id>/rollback 回滚进化
- GET  /api/evolution/history          获取进化历史
"""

import logging
from flask import Blueprint, request, jsonify

from src.core.evolution_manager import get_evolution_manager

logger = logging.getLogger(__name__)

bp = Blueprint('evolution', __name__, url_prefix='/api')


def _pwd_gate():
    """★ 2026-11-15 受保护动作密码门（方案A①+②）：
    security.password_protected_actions 含 'evolution' 时，修改类端点
    （批准/回滚/请求进化/批准提案）必须携带管理员密码：
    body.password 或 X-Admin-Password 请求头。
    拒绝/创建提案不拦（无修改能力，保持受控进化 UX：AI 可提提案，人批密码才生效）。"""
    from src.core.security_guard import is_protected_action, verify_admin_password
    if not is_protected_action('evolution'):
        return None
    data = request.get_json(silent=True) or {}
    pwd = (str(data.get('password') or '').strip()
           or (request.headers.get('X-Admin-Password') or '').strip())
    if not pwd:
        return jsonify({'success': False,
                        'error': '此操作受管理员密码保护，请输入密码后重试',
                        'code': 'password_required'}), 401
    if not verify_admin_password(pwd):
        return jsonify({'success': False, 'error': '密码错误', 'code': 'password_wrong'}), 403
    return None


@bp.route('/evolution/version', methods=['GET'])
def get_version():
    """获取当前版本信息"""
    try:
        mgr = get_evolution_manager()
        version = mgr.get_current_version()
        return jsonify({'success': True, 'version': version})
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 获取版本信息失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/check', methods=['GET'])
def check_updates():
    """检测新版本"""
    try:
        mgr = get_evolution_manager()
        result = mgr.check_updates()
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 检测新版本失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/request', methods=['POST'])
def request_evolution():
    """请求进化"""
    try:
        _g = _pwd_gate()
        if _g:
            return _g
        data = request.get_json(force=True, silent=True) or {}
        # ★ 2026-11-15: 密码门放行后剔除密码，避免明文落盘（package 会整体存进 evolution_log.json）
        data.pop('password', None)
        mgr = get_evolution_manager()
        result = mgr.request_evolution(data)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 请求进化失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/<int:log_id>/approve', methods=['POST'])
def approve_evolution(log_id):
    """批准进化"""
    try:
        _g = _pwd_gate()
        if _g:
            return _g
        mgr = get_evolution_manager()
        result = mgr.approve_evolution(log_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 批准进化失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/<int:log_id>/reject', methods=['POST'])
def reject_evolution(log_id):
    """拒绝进化"""
    try:
        mgr = get_evolution_manager()
        result = mgr.reject_evolution(log_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 拒绝进化失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/<int:log_id>/rollback', methods=['POST'])
def rollback_evolution(log_id):
    """回滚进化"""
    try:
        _g = _pwd_gate()
        if _g:
            return _g
        mgr = get_evolution_manager()
        result = mgr.rollback_evolution(log_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 回滚进化失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/history', methods=['GET'])
def get_evolution_history():
    """获取进化历史"""
    try:
        mgr = get_evolution_manager()
        history = mgr.get_evolution_history()
        return jsonify({'success': True, 'history': history})
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 获取进化历史失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── 进化提案系统 ───────────────────────────────────────────────────────

@bp.route('/evolution/proposals', methods=['GET'])
def get_proposals():
    """获取进化提案列表（?status=pending/approved/rejected/applied）"""
    try:
        status = request.args.get('status', None)
        mgr = get_evolution_manager()
        proposals = mgr.get_proposals(status=status)
        return jsonify({'success': True, 'proposals': proposals})
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 获取提案失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/proposals', methods=['POST'])
def create_proposal():
    """创建进化提案"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        mgr = get_evolution_manager()
        result = mgr.create_proposal(data)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 创建提案失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/proposals/<int:proposal_id>/approve', methods=['POST'])
def approve_proposal(proposal_id):
    """批准进化提案"""
    try:
        _g = _pwd_gate()
        if _g:
            return _g
        data = request.get_json(force=True, silent=True) or {}
        comment = data.get('comment', '')
        mgr = get_evolution_manager()
        result = mgr.approve_proposal(proposal_id, comment=comment)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 批准提案失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/evolution/proposals/<int:proposal_id>/reject', methods=['POST'])
def reject_proposal(proposal_id):
    """拒绝进化提案"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        comment = data.get('comment', '')
        mgr = get_evolution_manager()
        result = mgr.reject_proposal(proposal_id, comment=comment)
        return jsonify(result)
    except Exception as e:
        logger.error(f'[EvolutionRoutes] 拒绝提案失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
