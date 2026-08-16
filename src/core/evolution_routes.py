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
        data = request.get_json(force=True, silent=True) or {}
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
