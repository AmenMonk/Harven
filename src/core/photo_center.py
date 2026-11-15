# -*- coding: utf-8 -*-
"""
照片中心：全模块照片统一索引与检索
Copyright 2026 AmenMonk <38552540@qq.com>
Licensed under the Apache License, Version 2.0

设计（2026-09-18 定版拍板）：
- 生成面板：扫 panel.json 里 type==image 的字段，从 panel_<id> 表捞 data:image 记录
- 定制面板：kid 文件照片（data/media/kid/）、pet 表（pets/pet_logs 的 photo 列）
- 全本地、不复制不搬移，索引即扫描（当前数据量小，直扫足够快）

★ 2026-09-19 #771 照片视觉打标签（二期最小实现）：
- minicpm-v 对照片出 3-6 个中文标签，存 photo_tags 表（以 src 摘要为键，不复制图片）
- 扫描结果附带 tags，搜索词同时匹配标签
- POST /api/photos/tag/auto 自动给没标签的照片补（后台线程每30分钟最多5张，幂等）
"""
import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# ★ #840: 动态项目根（不再写死开发机路径，任意电脑都能用）
BASE = Path(__file__).parent.parent.parent
PANELS_DIR = BASE / 'data' / 'panels'
DB_PATH = BASE / 'data' / 'sqlite' / 'lingzhi.db'
KID_MEDIA = BASE / 'data' / 'media' / 'kid'

bp = Blueprint('photo_center', __name__, url_prefix='/api/photos')

_tag_lock = threading.Lock()
_tag_thread_started = False

# 定制面板的照片来源（panel.json fields 为空，单独声明）
CUSTOM_SOURCES = [
    {'table': 'pets', 'panel': 'pet_care_1', 'title_fields': ['name'], 'photo_fields': ['photo'], 'date_field': None},
    {'table': 'pet_logs', 'panel': 'pet_care_1', 'title_fields': ['type', 'detail'], 'photo_fields': ['photo'], 'date_field': 'date'},
]

IMG_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


def _scan_table(conn, table, panel_id, panel_name, title_fields, photo_fields, date_field):
    """从一个表里捞照片记录；photo 列既支持 dataURL 也支持 URL 路径"""
    items = []
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    if not cols:
        return items
    pf = [f for f in photo_fields if f in cols]
    tf = [t for t in title_fields if t in cols]
    df = date_field if date_field in cols else None
    if not pf:
        return items
    for r in conn.execute(f'SELECT rowid AS rid, * FROM "{table}"'):
        d = dict(r)
        for f in pf:
            v = (d.get(f) or '').strip()
            if not (v.startswith('data:image') or v.startswith('/')):
                continue
            title = ' · '.join(str(d.get(t, '')) for t in tf if d.get(t))
            items.append({
                'panel': panel_id, 'panel_name': panel_name,
                'record_id': d['rid'], 'field': f,
                'title': title or panel_name,
                'date': (d.get(df, '') or '')[:10] if df else '',
                'src': v,
            })
    return items


def scan_photos(q='', panel='', limit=300):
    items = []
    for pdir in sorted(PANELS_DIR.iterdir()):
        pj = pdir / 'panel.json'
        if not pj.is_file():
            continue
        try:
            j = json.loads(pj.read_text(encoding='utf-8'))
        except Exception:
            continue
        pid = j.get('id') or pdir.name
        pname = j.get('name', pid)
        if panel and pid != panel:
            continue
        img_fields = [f['name'] for f in (j.get('fields') or []) if f.get('type') == 'image']
        if img_fields:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            try:
                title_f = [f['name'] for f in (j.get('fields') or [])
                           if f.get('type') in ('text', 'select')][:2]
                date_f = next((f['name'] for f in (j.get('fields') or [])
                               if f.get('type') == 'date'), None)
                items += _scan_table(conn, 'panel_' + pid, pid, pname,
                                     title_f, img_fields, date_f)
            except Exception:
                pass
            finally:
                conn.close()
        # 定制来源
        for src in CUSTOM_SOURCES:
            if src['panel'] != pid:
                continue
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            try:
                items += _scan_table(conn, src['table'], pid, pname,
                                     src['title_fields'], src['photo_fields'], src['date_field'])
            except Exception:
                pass
            finally:
                conn.close()
    # 孩子照片（文件存储）
    if KID_MEDIA.is_dir() and (not panel or panel == 'kid_archive'):
        for sub in sorted(KID_MEDIA.iterdir()):
            if not sub.is_dir():
                continue
            for f in sub.iterdir():
                if f.suffix.lower() in IMG_EXTS:
                    items.append({
                        'panel': 'kid_archive', 'panel_name': '孩子成长档案',
                        'record_id': 0, 'field': 'photo',
                        'title': f'孩子照片（{sub.name}）',
                        'date': sub.name if sub.name[:4].isdigit() else '',
                        'src': f'/data/media/kid/{sub.name}/{f.name}',
                    })
    # ★ 2026-09-19 #771 附带已有标签（搜索词也匹配标签）
    try:
        _init_tag_table()
        tmap = _load_tags_map(items)
        for it in items:
            it['tags'] = tmap.get(_pid(it['panel'], it['record_id'], it['field'], it['src']), '')
    except Exception as e:
        logger.debug(f"[PhotoCenter] 附带标签失败: {e}")
        for it in items:
            it['tags'] = ''
    if q:
        items = [i for i in items if q in i['title'] or q in i['panel_name'] or q in (i.get('tags') or '')]
    items.sort(key=lambda x: x.get('date') or '', reverse=True)
    return items[:limit]


# ══════════════════ ★ 2026-09-19 #771 视觉打标签（minicpm-v） ══════════════════

def _pid(panel, record_id, field, src):
    """照片唯一键：同一照片重复打标签只留一条"""
    h = hashlib.md5(f"{panel}|{record_id}|{field}|{src[:200]}".encode('utf-8')).hexdigest()
    return h


def _init_tag_table():
    with _tag_lock:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS photo_tags (
                        pid TEXT PRIMARY KEY,
                        panel TEXT, record_id TEXT, field TEXT,
                        tags TEXT, model TEXT,
                        created_at TEXT DEFAULT (datetime('now','localtime')))""")
            conn.commit()
        finally:
            conn.close()


def _src_to_b64(src):
    """dataURL 或本地 /data/... 路径 → 纯 base64（视觉模型入参）"""
    import base64
    src = (src or '').strip()
    if src.startswith('data:image'):
        _, _, b64 = src.partition(',')
        return b64
    if src.startswith('/'):
        p = BASE / src.lstrip('/')
        try:
            return base64.b64encode(p.read_bytes()).decode('ascii')
        except Exception:
            return None
    return None


def _load_tags_map(items):
    """批量取这批照片已有标签"""
    if not items:
        return {}
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        out = {}
        for it in items:
            pid = _pid(it['panel'], it['record_id'], it['field'], it['src'])
            row = conn.execute("SELECT tags FROM photo_tags WHERE pid=?", (pid,)).fetchone()
            if row:
                out[pid] = row[0]
        return out
    finally:
        conn.close()


TAG_PROMPT = ("看这张家庭生活照片，给3到6个中文标签：内容、场景、物品、动物或人物（如：狗、宠物、"
              "户外、玩具、生日蛋糕）。只输出标签，用逗号分隔，不要句子不要解释。")


def tag_photo(panel, record_id, field, src):
    """给一张照片打标签（已打过直接返回）。返回 (tags_list, model, err)
    ★ #1155 跟随用户看图路由偏好；引擎不可用 → 返回 err，调用方跳过留白不擅自烧钱"""
    from src.core.panel_assist import _camp_vision_bg
    _init_tag_table()
    pid = _pid(panel, record_id, field, src)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        row = conn.execute("SELECT tags FROM photo_tags WHERE pid=?", (pid,)).fetchone()
        if row:
            return row[0].split(','), '', ''
    finally:
        conn.close()
    b64 = _src_to_b64(src)
    if not b64:
        return None, '', '照片读不出来'
    text, model, err = _camp_vision_bg(TAG_PROMPT, b64, timeout=120)
    if err or not text:
        return None, model, err or '视觉模型没有返回'
    tags = [t.strip() for t in text.replace('，', ',').replace('、', ',').split(',') if t.strip()]
    tags = [t[:12] for t in tags][:6]
    if not tags:
        return None, model, '没解析出标签'
    with _tag_lock:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        try:
            conn.execute("INSERT OR REPLACE INTO photo_tags(pid, panel, record_id, field, tags, model) "
                         "VALUES(?,?,?,?,?,?)",
                         (pid, panel, str(record_id), field, ','.join(tags), model))
            conn.commit()
        finally:
            conn.close()
    return tags, model, ''


def tag_untagged(limit=5):
    """给没标签的照片补标签（后台自动用，最多 limit 张）。返回 (done, skipped, errs)"""
    items = scan_photos(limit=1000)
    with_tags = _load_tags_map(items)
    done, errs = [], []
    for it in items:
        if len(done) >= limit:
            break
        pid = _pid(it['panel'], it['record_id'], it['field'], it['src'])
        if pid in with_tags:
            continue
        tags, model, err = tag_photo(it['panel'], it['record_id'], it['field'], it['src'])
        if err:
            errs.append(f"{it['title']}: {err}")
        else:
            done.append({'title': it['title'], 'tags': tags})
    return done, len(items) - len(with_tags) - len(done), errs


@bp.route('/tag', methods=['POST'])
def api_tag_photo():
    """手动给一张照片打标签。body: {panel, record_id, field, src}"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        for k in ('panel', 'field', 'src'):
            if not d.get(k):
                return jsonify({'success': False, 'error': f'缺少 {k}'}), 400
        tags, model, err = tag_photo(d['panel'], d.get('record_id', 0), d['field'], d['src'])
        if err:
            return jsonify({'success': False, 'error': err}), 200
        return jsonify({'success': True, 'tags': tags, 'model': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/tag/auto', methods=['POST'])
def api_tag_auto():
    """自动补标签（也可当手动批量按钮用）。body: {limit: 5}"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        limit = max(1, min(int(d.get('limit', 5)), 30))
        done, remaining, errs = tag_untagged(limit=limit)
        return jsonify({'success': True, 'tagged': done, 'remaining': remaining, 'errors': errs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/tags', methods=['GET'])
def api_tags_list():
    """全部照片标签（词频序，照片墙筛选可用）"""
    _init_tag_table()
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        rows = conn.execute("SELECT tags, COUNT(*) FROM photo_tags GROUP BY tags").fetchall()
    finally:
        conn.close()
    freq = {}
    for tags, _n in rows:
        for t in (tags or '').split(','):
            if t.strip():
                freq[t.strip()] = freq.get(t.strip(), 0) + 1
    return jsonify({'success': True, 'tags': sorted(freq.items(), key=lambda x: -x[1])})


def ensure_tag_loop():
    """★ 打标签守护线程：每30分钟最多补5张（幂等，web_app 启动时调用）"""
    global _tag_thread_started
    with _tag_lock:
        if _tag_thread_started:
            return
        _tag_thread_started = True
    _init_tag_table()

    def _loop():
        while True:
            try:
                done, _remaining, errs = tag_untagged(limit=5)
                if done or errs:
                    logger.info(f"[PhotoCenter] 自动打标签 {len(done)} 张"
                                + (f"，{len(errs)} 张失败（{errs[0]}）" if errs else ''))
            except Exception as e:
                logger.error(f"[PhotoCenter] 自动打标签异常: {e}")
            time.sleep(1800)

    t = threading.Thread(target=_loop, name='photo-tag-loop', daemon=True)
    t.start()
    logger.info('[PhotoCenter] 照片自动打标签线程已启动（跟随用户看图路由偏好，每30分钟一批）')


@bp.route('/list', methods=['GET'])
def list_photos():
    q = request.args.get('q', '').strip()
    panel = request.args.get('panel', '').strip()
    try:
        limit = min(int(request.args.get('limit', 300)), 1000)
    except ValueError:
        limit = 300
    items = scan_photos(q=q, panel=panel, limit=limit)
    return jsonify({'success': True, 'photos': items, 'total': len(items)})
