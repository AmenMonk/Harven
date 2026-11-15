# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 物品存放位置档案（#1056 批1 后端地基）
四级位置模型：L0 房产 → L1 房间 → L2 容器（柜/箱/床下/抽屉） → L3 格位（左门第2层等）
- panel_storage_places 表（惰性建表，存 lingzhi.db）
- CRUD + 树查询 + 格位模板五类（层数可改） + 别名归一（衣橱=衣柜）
- home_items 三级位置列与档案打通（normalize 函数供录入链调用）
API 端点挂在 panel_routes.py 的 panels 蓝图（/api/panels/storage-places/*）。
"""

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

BASE_DIR = None  # 延迟解析，避免循环导入；首次 _conn() 时填
DB_PATH = None


def _lazy_paths():
    global BASE_DIR, DB_PATH
    if DB_PATH is None:
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent.parent
        DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'
    return DB_PATH


def _conn():
    c = sqlite3.connect(str(_lazy_paths()), timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── 建表（惰性，模块级只跑一次） ──────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS panel_storage_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_name TEXT NOT NULL DEFAULT '家',
    room TEXT NOT NULL,
    container TEXT DEFAULT '',
    slot TEXT DEFAULT '',
    aliases TEXT DEFAULT '',
    photo TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
)
"""

_schema_done = False


def ensure_schema():
    global _schema_done
    if _schema_done:
        return
    try:
        c = _conn()
        try:
            c.execute(_SCHEMA)
            # 同组合唯一（逻辑层已挡，这里补索引加速树查询）
            c.execute('CREATE INDEX IF NOT EXISTS idx_spp_path '
                      'ON panel_storage_places(property_name, room, container, slot)')
            c.commit()
        finally:
            c.close()
        _schema_done = True
    except Exception as e:
        logger.warning(f'[storage_places] 建表失败: {e}')


# ── 格位模板五类（层数可改） ─────────────────────────────────

CONTAINER_TEMPLATES = {
    '衣柜': {'label': '衣柜（左右门）', 'default_levels': 4,
             'gen': lambda n: ([f'左门第{i}层' for i in range(1, n + 1)] +
                               [f'右门第{i}层' for i in range(1, n + 1)])},
    '抽屉柜': {'label': '抽屉柜', 'default_levels': 5,
               'gen': lambda n: [f'第{i}层抽屉' for i in range(1, n + 1)]},
    '书柜': {'label': '书柜', 'default_levels': 5,
             'gen': lambda n: [f'第{i}层' for i in range(1, n + 1)]},
    '储物箱': {'label': '储物箱（不细分层）', 'default_levels': 1,
               'gen': lambda n: ['箱内']},
    '床下': {'label': '床下收纳', 'default_levels': 1,
             'gen': lambda n: ['床下']},
}


def gen_slots(container_type, levels=None):
    """按模板类型生成格位列表；未知类型返回空（用户手填）。"""
    tpl = CONTAINER_TEMPLATES.get(container_type)
    if not tpl:
        return []
    n = levels if isinstance(levels, int) and 1 <= levels <= 10 else tpl['default_levels']
    try:
        return list(tpl['gen'](n))
    except Exception:
        return []


# ── 别名归一 ─────────────────────────────────────────────────

def normalize_alias(name):
    """'衣橱' → '衣柜'（若档案里某节点的别名命中）。查不到原样返回。"""
    name = (name or '').strip()
    if not name:
        return name
    try:
        ensure_schema()
        c = _conn()
        try:
            rows = c.execute('SELECT property_name, room, container, slot, aliases '
                             'FROM panel_storage_places WHERE aliases != ""').fetchall()
        finally:
            c.close()
        for r in rows:
            try:
                alist = [a.strip() for a in json.loads(r['aliases'] or '[]')]
            except (ValueError, TypeError):
                alist = [a.strip() for a in (r['aliases'] or '').split(',') if a.strip()]
            if name in alist:
                # 返回命中层级的规范名（格位别名命中返回格位名，容器命中返回容器名）
                for col in ('slot', 'container', 'room'):
                    if r[col]:
                        return r[col]
        return name
    except Exception as e:
        logger.debug(f'[storage_places] 别名归一失败: {e}')
        return name


def resolve_place_input(room, container='', slot=''):
    """录入链打通口：三级位置文本经别名归一后返回规范名元组。
    供默录链/表单保存时调用——不强制档案已建档（没建档原样返回）。"""
    return (normalize_alias(room),
            normalize_alias(container) if container else '',
            normalize_alias(slot) if slot else '')


# ── CRUD + 树查询 ────────────────────────────────────────────

def add_place(property_name, room, container='', slot='', aliases=None, photo='', sort_order=0):
    """新建位置节点。同路径已存在则直接返回已有 id（幂等）。"""
    ensure_schema()
    property_name = (property_name or '家').strip() or '家'
    room = (room or '').strip()
    container = (container or '').strip()
    slot = (slot or '').strip()
    if not room:
        return {'success': False, 'message': '房间名不能为空'}
    exist = find_place(property_name, room, container, slot)
    if exist:
        return {'success': True, 'id': exist['id'], 'existed': True}
    alist = json.dumps([a.strip() for a in (aliases or []) if a.strip()], ensure_ascii=False)
    try:
        c = _conn()
        try:
            cur = c.execute(
                'INSERT INTO panel_storage_places '
                '(property_name, room, container, slot, aliases, photo, sort_order) '
                'VALUES (?,?,?,?,?,?,?)',
                (property_name, room, container, slot, alist, photo, sort_order))
            c.commit()
            return {'success': True, 'id': cur.lastrowid}
        finally:
            c.close()
    except Exception as e:
        logger.warning(f'[storage_places] 新建失败: {e}')
        return {'success': False, 'message': str(e)}


def find_place(property_name, room, container='', slot=''):
    ensure_schema()
    try:
        c = _conn()
        try:
            r = c.execute(
                'SELECT * FROM panel_storage_places WHERE property_name=? AND room=? '
                'AND IFNULL(container,"")=? AND IFNULL(slot,"")=?',
                (property_name, room, container, slot)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()
    except Exception:
        return None


def update_place(place_id, **fields):
    """可改字段：property_name/room/container/slot/aliases(list)/photo/sort_order。"""
    ensure_schema()
    allowed = {'property_name', 'room', 'container', 'slot', 'photo', 'sort_order'}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f'{k}=?')
            vals.append(str(v) if k != 'sort_order' else int(v))
        elif k == 'aliases' and v is not None:
            sets.append('aliases=?')
            vals.append(json.dumps([a.strip() for a in (v if isinstance(v, list) else str(v).split(',')) if a.strip()],
                                   ensure_ascii=False))
    if not sets:
        return {'success': False, 'message': '没有可更新的字段'}
    try:
        c = _conn()
        try:
            c.execute(f'UPDATE panel_storage_places SET {", ".join(sets)} WHERE id=?', vals + [place_id])
            c.commit()
            return {'success': True}
        finally:
            c.close()
    except Exception as e:
        logger.warning(f'[storage_places] 更新失败: {e}')
        return {'success': False, 'message': str(e)}


def delete_place(place_id):
    """删除节点。子节点（同房产下以该节点为前缀的更深层）一并删除由调用方确认——
    这里返回受影响数量供确认卡显示。"""
    ensure_schema()
    try:
        c = _conn()
        try:
            r = c.execute('SELECT * FROM panel_storage_places WHERE id=?', (place_id,)).fetchone()
            if not r:
                return {'success': False, 'message': '节点不存在'}
            # 子节点 = 同房产同房间下，container 相同且 slot 非空的（删容器时）；或删房间时同房产同房间全部
            kids = c.execute(
                'SELECT COUNT(*) FROM panel_storage_places WHERE property_name=? AND room=? '
                'AND (container=? OR container="") AND id != ?',
                (r['property_name'], r['room'], r['container'], place_id)).fetchone()[0]
            c.execute('DELETE FROM panel_storage_places WHERE id=?', (place_id,))
            c.commit()
            return {'success': True, 'children_affected': kids}
        finally:
            c.close()
    except Exception as e:
        logger.warning(f'[storage_places] 删除失败: {e}')
        return {'success': False, 'message': str(e)}


def get_tree():
    """全量位置树：房产 → 房间 → 容器 → 格位。前端三级联动/管理 Tab 直接吃这个结构。"""
    ensure_schema()
    try:
        c = _conn()
        try:
            rows = c.execute('SELECT * FROM panel_storage_places ORDER BY property_name, sort_order, id').fetchall()
        finally:
            c.close()
        tree = {}
        for r in rows:
            d = dict(r)
            prop = tree.setdefault(d['property_name'], {'name': d['property_name'], 'rooms': {}})
            room = prop['rooms'].setdefault(d['room'], {'name': d['room'], 'containers': {}, 'loose_slots': []})
            if d['container']:
                cont = room['containers'].setdefault(d['container'], {
                    'name': d['container'], 'id': d['id'], 'photo': d['photo'],
                    'aliases': _parse_aliases(d['aliases']), 'slots': []})
                if d['slot']:
                    cont['slots'].append({'id': d['id'], 'name': d['slot'],
                                          'photo': d['photo'], 'aliases': _parse_aliases(d['aliases'])})
            elif d['slot']:
                # 房间直接放格位（如"玄关·钥匙盘"这类无容器场景）
                room['loose_slots'].append({'id': d['id'], 'name': d['slot'],
                                            'photo': d['photo'], 'aliases': _parse_aliases(d['aliases'])})
        # dict → list 保持顺序
        out = []
        for prop in tree.values():
            p = {'name': prop['name'], 'rooms': []}
            for room in prop['rooms'].values():
                p['rooms'].append({
                    'name': room['name'],
                    'containers': list(room['containers'].values()),
                    'loose_slots': room['loose_slots']})
            out.append(p)
        return out
    except Exception as e:
        logger.warning(f'[storage_places] 树查询失败: {e}')
        return []


def _parse_aliases(raw):
    try:
        v = json.loads(raw or '[]')
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return [a.strip() for a in (raw or '').split(',') if a.strip()]


def apply_template(property_name, room, container_name, container_type, levels=None, aliases=None):
    """一键建档：容器 + 模板生成的全部格位。已存在的节点幂等跳过。"""
    slots = gen_slots(container_type, levels)
    if not slots:
        return {'success': False, 'message': f'未知容器类型：{container_type}'}
    r = add_place(property_name, room, container_name, '', aliases)
    if not r.get('success'):
        return r
    ok, skipped = 0, 0
    for s in slots:
        sr = add_place(property_name, room, container_name, s)
        if sr.get('success'):
            ok += 0 if sr.get('existed') else 1
            skipped += 1 if sr.get('existed') else 0
        else:
            return sr
    return {'success': True, 'container_id': r.get('id'), 'slots_created': ok, 'slots_existed': skipped}


def list_properties():
    """房产名清单（去重）——首次引导第 1 步用。"""
    ensure_schema()
    try:
        c = _conn()
        try:
            rows = c.execute('SELECT DISTINCT property_name FROM panel_storage_places').fetchall()
            return [r[0] for r in rows] or ['家']
        finally:
            c.close()
    except Exception:
        return ['家']
