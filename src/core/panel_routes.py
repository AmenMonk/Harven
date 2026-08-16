# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - Panel Routes (面板路由)
Flask蓝图，提供面板安装/卸载/数据CRUD API
"""

import os
import json
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify, send_from_directory, current_app

logger = logging.getLogger(__name__)

bp = Blueprint('panels', __name__, url_prefix='/api/panels')

PANELS_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent / 'data' / 'panels'


def _get_manager():
    """延迟导入，避免循环依赖"""
    from src.core.panel_manager import get_panel_manager
    return get_panel_manager()


# ══════════════════════════ 面板管理API ══════════════════════════

@bp.route('', methods=['GET'])
def list_panels():
    """获取已安装面板列表"""
    mgr = _get_manager()
    panels = mgr.get_all_panels()
    return jsonify({'success': True, 'panels': panels})


@bp.route('/<panel_id>', methods=['GET'])
def get_panel_info(panel_id):
    """获取单个面板信息"""
    mgr = _get_manager()
    panel = mgr.get_panel(panel_id)
    if not panel:
        return jsonify({'success': False, 'message': '面板不存在'}), 404
    return jsonify({'success': True, 'panel': panel.to_dict()})


@bp.route('/install', methods=['POST'])
def install_panel():
    """上传ZIP安装面板"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '请选择ZIP文件'}), 400

    file = request.files['file']
    if not file.filename or not file.filename.endswith('.zip'):
        return jsonify({'success': False, 'message': '只支持ZIP格式'}), 400

    # 保存到临时文件
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        file.save(tmp.name)
        zip_path = tmp.name

    try:
        mgr = _get_manager()
        result = mgr.install_panel(zip_path)
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    finally:
        try:
            os.unlink(zip_path)
        except OSError as _e:
            logger.debug(f"[panel_routes] 忽略: {_e}")


@bp.route('/<panel_id>', methods=['DELETE'])
def uninstall_panel(panel_id):
    """卸载面板"""
    mgr = _get_manager()
    result = mgr.uninstall_panel(panel_id)
    if result['success']:
        return jsonify(result)
    return jsonify(result), 400


@bp.route('/create', methods=['POST'])
def create_panel():
    """通过模板创建面板"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    panel_id = data.get('id', '').strip()
    fields = data.get('fields', [])
    description = data.get('description', '').strip()
    icon = data.get('icon', 'file')
    with_chart = data.get('with_chart', False)  # ★ 2026-08-08: 统计图表
    with_search = data.get('with_search', True)  # ★ 搜索筛选

    if not name:
        return jsonify({'success': False, 'message': '面板名称不能为空'}), 400
    if not panel_id:
        return jsonify({'success': False, 'message': '面板ID不能为空'}), 400
    if not fields:
        return jsonify({'success': False, 'message': '至少需要定义一个字段'}), 400

    mgr = _get_manager()
    result = mgr.create_panel_from_template(name, panel_id, fields, description, icon,
                                             with_chart=with_chart, with_search=with_search)
    if result['success']:
        return jsonify(result)
    return jsonify(result), 400


# ══════════════════════════ 面板数据CRUD API ══════════════════════════

@bp.route('/<panel_id>/data', methods=['GET'])
def query_panel_data(panel_id):
    """查询面板数据"""
    mgr = _get_manager()
    if not mgr.panel_exists(panel_id):
        return jsonify({'success': False, 'message': '面板不存在'}), 404

    # 从查询参数提取过滤条件
    filters = {}
    for key, value in request.args.items():
        if key not in ('limit', 'offset'):
            filters[key] = value

    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)

    records = mgr.query_panel_data(panel_id, filters or None, limit, offset)
    total = mgr.get_panel_data_count(panel_id)
    return jsonify({
        'success': True,
        'records': records,
        'total': total,
        'limit': limit,
        'offset': offset,
    })


@bp.route('/<panel_id>/data', methods=['POST'])
def insert_panel_data(panel_id):
    """新增面板数据"""
    mgr = _get_manager()
    if not mgr.panel_exists(panel_id):
        return jsonify({'success': False, 'message': '面板不存在'}), 404

    data = request.get_json() or {}
    if not data:
        return jsonify({'success': False, 'message': '数据不能为空'}), 400

    try:
        result = mgr.insert_panel_data(panel_id, data)
        return jsonify({'success': True, 'record': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/<panel_id>/data/<int:row_id>', methods=['PUT'])
def update_panel_data(panel_id, row_id):
    """更新面板数据"""
    mgr = _get_manager()
    if not mgr.panel_exists(panel_id):
        return jsonify({'success': False, 'message': '面板不存在'}), 404

    data = request.get_json() or {}
    if not data:
        return jsonify({'success': False, 'message': '数据不能为空'}), 400

    try:
        result = mgr.update_panel_data(panel_id, row_id, data)
        return jsonify({'success': True, 'record': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/<panel_id>/data/<int:row_id>', methods=['DELETE'])
def delete_panel_data(panel_id, row_id):
    """删除面板数据"""
    mgr = _get_manager()
    if not mgr.panel_exists(panel_id):
        return jsonify({'success': False, 'message': '面板不存在'}), 404

    try:
        ok = mgr.delete_panel_data(panel_id, row_id)
        if ok:
            return jsonify({'success': True, 'message': '已删除'})
        return jsonify({'success': False, 'message': '删除失败'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ══════════════════════════ 面板静态文件服务 ══════════════════════════

@bp.route('/<panel_id>/file/<path:filename>', methods=['GET'])
def serve_panel_file(panel_id, filename):
    """提供面板的静态文件（CSS/JS）"""
    panel_dir = PANELS_DIR / panel_id
    file_path = panel_dir / filename
    if not file_path.is_file():
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    return send_from_directory(str(panel_dir), filename)
