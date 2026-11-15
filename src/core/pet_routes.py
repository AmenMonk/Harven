# -*- coding: utf-8 -*-
"""
宠物系统 API 路由（/api/pet）
Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0
"""
import logging
from flask import Blueprint, request, jsonify

from src.core import pet_care as pc

logger = logging.getLogger(__name__)

bp = Blueprint('pet_care', __name__, url_prefix='/api/pet')


def _ok(data=None, **extra):
    r = {'success': True}
    if data is not None:
        r.update(data if isinstance(data, dict) else {'data': data})
    r.update(extra)
    return jsonify(r)


def _err(msg, code=400):
    return jsonify({'success': False, 'message': msg}), code


# ════════════════ 档案 ════════════════

@bp.route('/pets', methods=['GET'])
def list_pets():
    return _ok(pets=pc.get_pets())


@bp.route('/pets', methods=['POST'])
def create_pet():
    data = request.get_json(silent=True) or {}
    if not data.get('name'):
        return _err('宠物名字不能为空')
    pid = pc.create_pet(data)
    return _ok(pet_id=pid)


@bp.route('/pets/<int:pid>', methods=['PUT'])
def update_pet(pid):
    pc.update_pet(pid, request.get_json(silent=True) or {})
    return _ok()


@bp.route('/pets/<int:pid>', methods=['DELETE'])
def delete_pet(pid):
    pc.delete_pet(pid)
    return _ok()


# ════════════════ 三域日志 ════════════════

@bp.route('/<int:pet_id>/<domain>', methods=['GET'])
def query_domain(pet_id, domain):
    try:
        return _ok(records=pc.query_logs(pet_id, domain, limit=int(request.args.get('limit', 500))))
    except ValueError as e:
        return _err(str(e), 404)


@bp.route('/<int:pet_id>/<domain>', methods=['POST'])
def insert_domain(pet_id, domain):
    data = request.get_json(silent=True) or {}
    try:
        rid = pc.insert_log(pet_id, domain, data)
        return _ok(record_id=rid, next_date=data.get('next_date') or '')
    except ValueError as e:
        return _err(str(e), 404)
    except Exception as e:
        logger.error(f"[PetAPI] 写入失败: {e}")
        return _err(str(e), 500)


@bp.route('/<int:pet_id>/<domain>/<int:rid>', methods=['PUT'])
def update_domain(pet_id, domain, rid):
    try:
        pc.update_log(pet_id, domain, rid, request.get_json(silent=True) or {})
        return _ok()
    except ValueError as e:
        return _err(str(e), 404)


@bp.route('/<int:pet_id>/<domain>/<int:rid>', methods=['DELETE'])
def delete_domain(pet_id, domain, rid):
    try:
        pc.delete_log(pet_id, domain, rid)
        return _ok()
    except ValueError as e:
        return _err(str(e), 404)


# ════════════════ 提醒 ════════════════

@bp.route('/alerts', methods=['GET'])
def alerts():
    return _ok(records=pc.pending_alerts(days=int(request.args.get('days', 3))))
