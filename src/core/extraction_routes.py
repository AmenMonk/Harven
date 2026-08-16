# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 知识萃取路由（存根）
★ 2026-07-25: 前端app-extraction.js调用的API，后端暂未实现完整功能
返回空数据避免404错误
"""

from flask import Blueprint, jsonify, request
import logging

logger = logging.getLogger(__name__)

bp = Blueprint('extraction', __name__, url_prefix='/api/extraction')


@bp.route('/knowledge', methods=['GET'])
def get_knowledge():
    """获取萃取知识列表（存根）"""
    return jsonify({"success": True, "items": [], "total": 0})


@bp.route('/knowledge/<knowledge_id>', methods=['DELETE'])
def delete_knowledge(knowledge_id):
    """删除萃取知识（存根）"""
    return jsonify({"success": True, "deleted": knowledge_id})


@bp.route('/log', methods=['GET'])
def get_log():
    """获取萃取日志（存根）"""
    return jsonify({"success": True, "items": [], "total": 0})


@bp.route('/extract-document', methods=['POST'])
def extract_document():
    """从文档萃取知识（存根）"""
    return jsonify({"success": False, "error": "知识萃取功能开发中", "items": []})
