# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""★ #916 2026-09-27 统一图库 OneImg（已批准「通用开发」）

痛点（实测实锤）：哈文的图散落各处——知识库 4 张、面板附件 1 张、
聊天发图发完即走、照片中心只是扫表现有数据——没有统一的存储/分类/目录/检索。

本模块 = 全家图片的唯一总账：
- 表 images_index：sha256 去重 + source 来源 + path 本体位置 + album 相册 + tags 标签
  + note 描述 + panel_id/record_id 面板关联
- 两条入库存档路线：
  A. 上传/聊天/手机图 → 收进库仓 data/media/img_store/YYYYMM/（PIL 压缩+缩略图）
  B. 原地登记（知识库已有图等）→ 本体不搬家，只登记路径（零破坏迁移）
- 自动分类：描述关键词 + 面板映射先判相册；后台 minicpm-v 本地视觉打标签兜底（零 API 费）
- 默认相册集：物品存放/冰箱/孩子学习/餐饮/车辆/未分类（开发者确认），img_albums 支持自定义
- 检索：search() 关键词同时匹配 note/tags/album/filename，给 FC search_images 工具用
"""
import base64
import hashlib
import io
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'
VAULT_ROOT = BASE_DIR / 'data' / 'media' / 'img_store'

DEFAULT_ALBUMS = ['账单', '餐饮', '孩子学习', '冰箱', '物品存放', '生活照', '车辆', '未分类']

# 面板 → 相册映射（登记时带 panel_id 自动归相册）
_PANEL_ALBUM = {
    'home_items': '物品存放',
    'fridge_stock_1': '冰箱',
    'kid_study_1': '孩子学习',
    'kid_archive': '孩子学习',
    'meal_diary': '餐饮',
    'car_fuel_1': '车辆',
    'car_maintenance_1': '车辆',
}

# 描述关键词 → 相册（note/描述驱动自动分类第一优先级）
# ★ #1188（2026-10-25）定版：账单置顶（支付凭证不再错进餐饮）；生活照入库
_KEYWORD_ALBUM = [
    (('账单', '支付', '付款', '消费', '订单', '缴费', '发票', '收款'), '账单'),
    (('冰箱', '冷藏', '冷冻', '食材', '蔬菜', '水果', '醋', '酱油', '牛奶', '鸡蛋', '过期'), '冰箱'),
    (('学习', '作业', '考试', '试卷', '成绩', '课本', '学校', ' homework'), '孩子学习'),
    (('外卖', '美团', '饿了么', '早餐', '午餐', '晚餐', '吃饭', '饭菜', '零食', '点餐'), '餐饮'),
    (('加油', '保养', '轮胎', '途锐', '机油', '洗车', '行车', '车辆'), '车辆'),
    (('存放', '收纳', '物品', '放在哪', '找不到', '箱子', '柜子', '抽屉'), '物品存放'),
    (('生活照', '合照', '全家福', '出门玩', '旅游'), '生活照'),
]

# ★ #1188（2026-10-25）标签 → 相册自动归位规则（AI 打标完成后跑）。
# 有序表：先中者胜——账单置顶（支付/付款凭证不吃餐饮的「食品」关键词），
# 生活照殿后兜底（自拍/风景/家人类随手照）。
_TAG_ALBUM_RULES = [
    ('账单', ('支付', '付款', '账单', '消费', '缴费', '发票', '收款', '订单详情')),
    ('冰箱', ('冰箱', '食材', '食品包装', '冷冻', '冷藏')),
    ('孩子学习', ('练习册', '作业', '教辅', '书写', '书法', '试卷', '课本', '书包', '批改', '课程')),
    ('物品存放', ('家电', '家具', '椅子', '桌子', '书架', '电脑', '笔记本', '键盘', '显示器',
                  '耳机', '麦克风', '电饭煲', '卫生间', '书房', '收纳', '工具', '染发', '药膏', '喷雾')),
    ('餐饮', ('美食', '餐桌', '外卖', '点餐', '餐厅', '菜品', '食品', '早餐', '午餐', '晚餐',
              '蛋糕', '冰淇淋', '饮料', '咖啡', '火锅')),
    ('生活照', ('自拍', '风景', '家人', '海边', '日落', '海滩', '孩子', '宠物', '户外', '野餐',
                '旅游', '公园', '表演')),
]


def autosort_album(tags):
    """★ #1188 标签自动归位：按有序规则返回相册名；没匹配返回 ''（保持未分类等手动分）。"""
    text = ' '.join(t for t in (tags or []) if t)
    if not text:
        return ''
    for album, keys in _TAG_ALBUM_RULES:
        if any(k in text for k in keys):
            return album
    return ''


# ══════════ ★ #1257（2026-10-28 批4）CLIP 零样本归位 —— 相册名称库 ══════════
# 定版拍板的「分类名称库」落地：动态层=img_albums 表相册名（建相册即生效），
# 别名组=预置口语化增强（可后续走 Hub 视觉包升级）。数据真家唯一：哈文本表。
_CLIP_ALBUM_THRESHOLD = 0.43          # ★ 六例实测定版（2026-10-28）：强信号线——火锅 0.443/作业纸
                                      # 0.440 命中；吉他 0.398/音箱 0.403/眼镜 0.413/平板 0.401 全挡。
                                      # 实测规律：模糊区 0.39~0.42 的图「账单/生活照」虚高乱跳，
                                      # 低于此线宁可留未分类（等手动归类升 gold），防误归位
_ALBUM_ALIASES = {
    '账单': ('发票', '小票', '收据', '付款凭证', '订单详情', '缴费单'),
    '餐饮': ('饭菜', '菜品', '吃东西', '下馆子', '外卖', '美食佳肴', '蛋糕甜点'),
    '孩子学习': ('作业本', '练习册', '课本教材', '试卷', '写字作业', '儿童书桌'),
    '冰箱': ('冰箱里的食物', '冷藏食材', '冷冻食品', '蔬果存儲'),
    '物品存放': ('家具家电', '收纳整理', '工具箱', '日用品', '室内物品'),
    '车辆': ('汽车外观', '车内饰', '发动机舱', '里程表', '轮胎'),
    '生活照': ('生活随手拍', '自然风景', '家人合影', '自拍照', '户外游玩'),
}


def _album_candidates():
    """相册名称库候选（动态层）：img_albums 全表 + 别名组；未分类不参与。"""
    c = _conn()
    try:
        rows = c.execute('SELECT name FROM img_albums').fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        name = r['name'] if isinstance(r, dict) else r[0]
        if not name or name == '未分类':
            continue
        out.append({'name': name, 'aliases': list(_ALBUM_ALIASES.get(name, ()))})
    return out


def _clip_classify_album(image_path):
    """第二级归位：Eye /classify（CLIP 图文相似度，~60ms）。Eye 离线/失败返回 ''（不硬塞）。"""
    try:
        from src.core import eye_client
        r = eye_client.classify(str(image_path), _album_candidates(),
                                threshold=_CLIP_ALBUM_THRESHOLD)
        if r.get('ok') and r.get('hit'):
            name = (r.get('top') or {}).get('name', '')
            if name:
                logger.info(f'[ImgStore] #1257 CLIP 归位命中: {name} '
                            f'(sim={ (r.get("top") or {}).get("similarity")}, via={(r.get("top") or {}).get("via")})')
                return name
    except Exception as e:
        logger.debug(f'[ImgStore] CLIP 归位跳过: {e}')
    return ''


def auto_place(img_id, img_path=None):
    """★ #1188 归位引擎：只修正 auto_placed=1（从未被手动整理）且当前在未分类的图。
    用户手动 set_album 会置 auto_placed=0，此后引擎永不再动这张图。返回 (旧相册, 新相册)。
    ★ #1257 批4 三级瀑布：① 词典快车道（毫秒）→ ② CLIP 零样本（~60ms，Eye 在线时）→
    ③ 未命中保持未分类（candidate 形态，等手动归类升 gold，同款下次秒配）。"""
    try:
        ensure_table()
        c = _conn()
        r = c.execute('SELECT album, tags, path, auto_placed FROM images_index WHERE id=?', (img_id,)).fetchone()
        if not r or r['auto_placed'] != 1 or (r['album'] not in ('', '未分类')):
            c.close()
            return '', ''
        # ① 词典快车道（#1188 原链，零成本优先）
        new_album = autosort_album([t for t in (r['tags'] or '').split(',') if t])
        # ② CLIP 零样本分类（#1257：不依赖标签，向量直比相册名）
        if not new_album:
            _p = img_path or r['path']
            if _p and os.path.exists(str(_p)):
                new_album = _clip_classify_album(_p)
        if not new_album or new_album == r['album']:
            c.close()
            return r['album'], ''
        c.execute('UPDATE images_index SET album=? WHERE id=?', (new_album, img_id))
        c.commit()
        c.close()
        logger.info(f'[ImgStore] 自动归位 id={img_id}: {r["album"]} → {new_album}')
        return r['album'], new_album
    except Exception as e:
        logger.error(f'[ImgStore] 自动归位失败 id={img_id}: {e}')
        return '', ''


def reauto_place_all(max_tries=3):
    """★ #1257 批4：存量重跑——对全部「未分类且 auto_placed=1」的图重跑三级瀑布。
    ★ #1522（2026-11-18）跑过即跳过：每张图自动尝试计入 auto_tried，满 max_tries 次
    不再重跑（日志实锤：同一批 21 张每天重跑归位 0，纯空转）。用户手动归类或词库
    升级后可调 reset_auto_tried() 给全部图重开机会。返回 (总数, 归位数)。"""
    ensure_table()
    c = _conn()
    rows = c.execute(
        "SELECT id FROM images_index WHERE auto_placed=1 AND album IN ('', '未分类') "
        "AND IFNULL(auto_tried,0) < ?", (max_tries,)).fetchall()
    if rows:
        c.executemany('UPDATE images_index SET auto_tried=IFNULL(auto_tried,0)+1 WHERE id=?',
                      [(r['id'],) for r in rows])
        c.commit()
    c.close()
    moved = 0
    for r in rows:
        try:
            _old, new = auto_place(r['id'])
            if new:
                moved += 1
        except Exception:
            continue
    logger.info(f'[ImgStore] #1257 存量重跑完成: {len(rows)} 张候选，归位 {moved} 张')
    return len(rows), moved


def reset_auto_tried():
    """★ #1522 清零全部归位尝试计数（词库升级/CLIP 模型更换后调，给图重开自动归位机会）。
    返回清零张数。"""
    ensure_table()
    c = _conn()
    try:
        c.execute('UPDATE images_index SET auto_tried=0')
        c.commit()
        return c.total_changes
    finally:
        c.close()


def unprocessed_bills(limit=500):
    """★ #1377（2026-11-02）账单相册里「还没发给账单模块分析过」的照片 id 列表。
    A 手动按钮 / C 心跳扫描共用同一取数口径：账单相册 + 未软删 + bill_processed=0。"""
    ensure_table()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id FROM images_index WHERE album='账单' AND deleted_at='' "
            "AND (bill_processed IS NULL OR bill_processed=0) ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [r['id'] for r in rows]
    finally:
        c.close()


def mark_bills_processed(ids, note=''):
    """★ #1377 分析完成即打标记（成功/失败/重复都打——保证一张照片全生命周期只被扫一次）。
    note 落 bill_note 便于回查为什么跳过。返回实际标记张数。"""
    if not ids:
        return 0
    ensure_table()
    c = _conn()
    try:
        c.executemany("UPDATE images_index SET bill_processed=1, bill_note=? WHERE id=?",
                      [(note, i) for i in ids])
        c.commit()
        return c.total_changes
    finally:
        c.close()


def reprocess_bills(ids):
    """★ #1377 把照片退回「未分析」状态（bill_processed=0），下次扫描/手动按钮会重跑。"""
    if not ids:
        return 0
    ensure_table()
    c = _conn()
    try:
        c.executemany("UPDATE images_index SET bill_processed=0, bill_note='' WHERE id=?",
                      [(i,) for i in ids])
        c.commit()
        return c.total_changes
    finally:
        c.close()


def migrate_autosort_v1():
    """★ #1188（2026-10-25）存量一次性清洗（幂等，可重复跑）：
    ① 相册表补账单/生活照、删重复的「人物风景照片」
    ② 人物风景照片的图并入生活照
    ③ 全部存量 auto_placed 置 1（老图多数是当初 note 关键词自动归的，交给引擎统一修正）
    ④ 按标签规则把错位图归位（只动未分类）
    返回统计 dict。"""
    try:
        ensure_table()
        c = _conn()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for a in ('账单', '生活照'):
            c.execute('INSERT OR IGNORE INTO img_albums(name, created_at) VALUES(?,?)', (a, now))
        c.execute("DELETE FROM img_albums WHERE name='人物风景照片'")
        merged = c.execute("UPDATE images_index SET album='生活照' WHERE album='人物风景照片'").rowcount
        c.execute('UPDATE images_index SET auto_placed=1')
        rows = c.execute(
            "SELECT id, tags FROM images_index WHERE deleted_at='' AND album='未分类'").fetchall()
        moved = {}
        for r in rows:
            tags = [t for t in (r['tags'] or '').split(',') if t]
            new_album = autosort_album(tags)
            if new_album:
                c.execute('UPDATE images_index SET album=? WHERE id=?', (new_album, r['id']))
                moved[r['id']] = new_album
        c.commit()
        c.close()
        stat = {'merged_person_scenery': merged, 'moved_count': len(moved), 'moved': moved}
        logger.info(f'[ImgStore] #1188 存量清洗完成: {stat["merged_person_scenery"]} 张并入生活照, '
                    f'{len(moved)} 张重新归位')
        return stat
    except Exception as e:
        logger.error(f'[ImgStore] 存量清洗失败: {e}')
        return {'error': str(e)}

_img_lock = threading.Lock()
_tag_thread_started = False


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def ensure_table():
    """建总登记表 + 相册表（幂等）"""
    with _img_lock:
        c = _conn()
        try:
            c.execute('''CREATE TABLE IF NOT EXISTS images_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL,
                thumb_path TEXT DEFAULT '',
                source TEXT DEFAULT 'upload',
                album TEXT DEFAULT '未分类',
                tags TEXT DEFAULT '',
                note TEXT DEFAULT '',
                panel_id TEXT DEFAULT '',
                record_id INTEGER,
                created_at TEXT)''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_img_album ON images_index(album)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_img_panel ON images_index(panel_id, record_id)')
            c.execute('''CREATE TABLE IF NOT EXISTS img_albums (
                name TEXT PRIMARY KEY,
                created_at TEXT)''')
            # ★ #1307（2026-10-29）相册级「外脑深析」开关列（幂等补齐老库）：
            #   1=该相册新图后台空闲时外脑补一句描述进 note；0=默认只走本地标签链
            _a_cols = [r[1] for r in c.execute('PRAGMA table_info(img_albums)').fetchall()]
            if 'deep_analyze' not in _a_cols:
                c.execute('ALTER TABLE img_albums ADD COLUMN deep_analyze INTEGER DEFAULT 0')
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for a in DEFAULT_ALBUMS:
                c.execute('INSERT OR IGNORE INTO img_albums(name, created_at) VALUES(?,?)', (a, now))
            # ★ #1003 回收站（2026-10-05）：软删列，幂等补齐老库
            cols = [r[1] for r in c.execute('PRAGMA table_info(images_index)').fetchall()]
            if 'deleted_at' not in cols:
                c.execute("ALTER TABLE images_index ADD COLUMN deleted_at TEXT DEFAULT ''")
            # ★ #1188 归位引擎（2026-10-25）：0=用户手动整理过（引擎不再动），1=归位引擎可管
            if 'auto_placed' not in cols:
                c.execute('ALTER TABLE images_index ADD COLUMN auto_placed INTEGER DEFAULT 1')
            # ★ #1377（2026-11-02）账单照片「已发给账单模块分析」标记（幂等补齐老库）：
            #   A 手动按钮 / C 心跳扫描共用——谁分析过就置 1，扫描只认 0（防 A/C 重复提醒实锤口径）
            if 'bill_processed' not in cols:
                c.execute('ALTER TABLE images_index ADD COLUMN bill_processed INTEGER DEFAULT 0')
            if 'bill_note' not in cols:
                c.execute("ALTER TABLE images_index ADD COLUMN bill_note TEXT DEFAULT ''")
            # ★ #1407-B（2026-11-03）照片组：一物多照成组（实测实锤：柜子/抽屉/物品特写
            #   同属一个物品）。group_id = 组令牌（建组时随机生成，组内共享），''=未成组
            if 'group_id' not in cols:
                c.execute("ALTER TABLE images_index ADD COLUMN group_id TEXT DEFAULT ''")
                c.execute('CREATE INDEX IF NOT EXISTS idx_img_group ON images_index(group_id)')
            # ★ #1491（2026-11-12）记忆相册按人翻回忆：人脸↔照片映射表（数据真家=lingzhi.db，
            #   与 images_index 同库好 join；人脸向量本体仍在 data/vision/face.db 不动）
            # ★ #1522（2026-11-18）归位尝试计数：跑过即跳过——同一张图最多自动尝试 3 次
            #   （日志实锤：21 张候选每天重跑归位 0，白烧 CLIP 调用；3 次归不了=词典/CLIP
            #   都拿它没办法，静等用户手动归类或词库升级后 reset_auto_tried() 重开机会）
            if 'auto_tried' not in cols:
                c.execute('ALTER TABLE images_index ADD COLUMN auto_tried INTEGER DEFAULT 0')
            c.execute('''CREATE TABLE IF NOT EXISTS face_photo_map (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                img_id INTEGER NOT NULL,
                person_name TEXT NOT NULL,
                similarity REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(img_id, person_name))''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_fpm_person ON face_photo_map(person_name)')
            c.commit()
        finally:
            c.close()


def ensure_albums():
    ensure_table()


def _guess_album(note, panel_id):
    """相册判定：显式指定 > 关键词 > 面板映射 > 未分类"""
    text = (note or '').strip()
    if text:
        for keys, album in _KEYWORD_ALBUM:
            if any(k in text for k in keys):
                return album
    if panel_id and _PANEL_ALBUM.get(panel_id):
        return _PANEL_ALBUM[panel_id]
    return '未分类'


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _rel(p):
    """转相对 BASE 的 posix 路径，便于存库与跨盘迁移"""
    return str(Path(p).resolve().relative_to(BASE_DIR)).replace('\\', '/')


def _save_to_vault(raw):
    """压缩存进库仓，返回 (主图相对路径, 缩略图相对路径)"""
    from PIL import Image
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    base_name = uuid.uuid4().hex
    sub = VAULT_ROOT / datetime.now().strftime('%Y%m')
    sub.mkdir(parents=True, exist_ok=True)
    for max_side, quality, suffix in ((1600, 82, '.jpg'), (320, 70, '_thumb.jpg')):
        im = img.copy()
        im.thumbnail((max_side, max_side))
        im.save(sub / (base_name + suffix), 'JPEG', quality=quality)
    return _rel(sub / (base_name + '.jpg')), _rel(sub / (base_name + '_thumb.jpg'))


def register(image_data=None, src_file=None, source='upload', album=None,
             note='', panel_id='', record_id=None, tags=None, auto_tag=True):
    """登记/入库一张图。三种用法：
    - image_data：bytes 或 base64 字符串（可带 data:image 前缀）→ 收进库仓
    - src_file：已有文件 Path → 原地登记不搬家
    - 两者同给：sha 按原始字节算（跨入口去重），本体用现文件不复制（panel 附件档案用）
    返回 {'id','url','thumb_url','duplicate'}，失败返回 None。
    重复图（sha256 相同）：不重复存，合并 panel 关联与标签后返回原条目。"""
    try:
        ensure_table()
        raw = None
        if image_data is not None:
            if isinstance(image_data, str):
                if ',' in image_data and image_data.strip().startswith('data:'):
                    image_data = image_data.split(',', 1)[1]
                raw = base64.b64decode(image_data)
            else:
                raw = bytes(image_data)
            if not raw:
                return None
        if src_file is not None:
            src_file = Path(src_file)
            if not src_file.is_file():
                logger.warning(f'[ImgStore] 原地登记文件不存在: {src_file}')
                return None
            if raw is None:
                raw = src_file.read_bytes()
        if raw is None:
            return None
        sha = _sha_bytes(raw)

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        note = str(note or '')[:200]
        tags_str = ','.join(t.strip() for t in (tags or []) if t.strip())[:200]
        album = album or _guess_album(note, panel_id)

        c = _conn()
        try:
            row = c.execute('SELECT * FROM images_index WHERE sha256=?', (sha,)).fetchone()
            if row:
                # 去重：合并面板关联与标签，不重复存
                sets, params = [], []
                if panel_id and not row['panel_id']:
                    sets += ['panel_id=?', 'record_id=?']
                    params += [panel_id, record_id]
                if tags_str:
                    merged = row['tags'] or ''
                    add = [t for t in tags_str.split(',') if t and t not in merged]
                    if add:
                        sets.append('tags=?')
                        params.append((merged + ',' + ','.join(add)).strip(',')[:200])
                if note and (not row['note'] or row['note'] in ('微信图片', '面板附件图', '图片')):
                    sets.append('note=?')
                    params.append(note)
                # 原条目还是未分类而新描述/面板能判出更准相册 → 升级归类
                if row['album'] == '未分类':
                    better = _guess_album(note or row['note'], panel_id or row['panel_id'])
                    if better != '未分类':
                        sets.append('album=?')
                        params.append(better)
                if sets:
                    params.append(row['id'])
                    c.execute(f'UPDATE images_index SET {", ".join(sets)} WHERE id=?', params)
                    c.commit()
                logger.info(f'[ImgStore] 重复图去重 sha={sha[:12]} 复用 id={row["id"]}')
                return {'id': row['id'], 'duplicate': True,
                        'url': f'/api/images/file/{row["id"]}',
                        'thumb_url': f'/api/images/file/{row["id"]}?thumb=1'}

            inserted = False
            for _attempt in range(3):  # 哈文运行中偶发库锁/瞬时只读，重试兜底
                try:
                    if src_file is not None:
                        path = _rel(src_file)
                        thumb_path = ''
                    else:
                        path, thumb_path = _save_to_vault(raw)
                    cur = c.execute(
                        'INSERT INTO images_index(sha256, path, thumb_path, source, album, tags,'
                        ' note, panel_id, record_id, created_at, auto_placed) VALUES(?,?,?,?,?,?,?,?,?,?,1)',
                        (sha, path, thumb_path, source, album, tags_str, note,
                         panel_id or '', record_id, now))
                    c.commit()
                    new_id = cur.lastrowid
                    inserted = True
                    break
                except sqlite3.OperationalError as oe:
                    if _attempt < 2:
                        time.sleep(0.6)
                        continue
                    raise
            if not inserted:
                return None
        finally:
            c.close()

        logger.info(f'[ImgStore] 登记图 id={new_id} source={source} album={album}'
                    f' {path} ({len(raw)//1024}KB)')
        if auto_tag and not tags_str:
            # ★ #1306（2026-10-29）统一后台打标链（photo_namer 下线后收编，见 auto_tag_full）
            auto_tag_full(new_id)
        # ★ #1234 M4-2（2026-10-26）相册互认：新图后台送 HarvenEye 视觉索引
        #   （fire-and-forget，Eye 离线静默跳过；去重分支不送，Eye 侧自带 dhash 去重）
        try:
            from src.core import eye_client as _eye
            _eye.notify_index(str((BASE_DIR / path).resolve()), img_id=new_id)
        except Exception:
            pass
        return {'id': new_id, 'duplicate': False,
                'url': f'/api/images/file/{new_id}',
                'thumb_url': f'/api/images/file/{new_id}?thumb=1'}
    except Exception as e:
        logger.error(f'[ImgStore] 登记失败: {e}')
        return None


# ══════════════ Eye 快速档标签 + 统一后台打标链（★ #1306 photo_namer 收编） ══════════════

# COCO 80 类常用 → 中文（没映射的英文原样兜底）
_COCO_ZH = {
    'person': '人', 'bicycle': '自行车', 'car': '汽车', 'motorcycle': '摩托车', 'airplane': '飞机',
    'bus': '公交车', 'train': '火车', 'truck': '卡车', 'boat': '船', 'traffic light': '红绿灯',
    'fire hydrant': '消防栓', 'stop sign': '停车牌', 'parking meter': '停车计费器', 'bench': '长椅',
    'bird': '鸟', 'cat': '猫', 'dog': '狗', 'horse': '马', 'sheep': '羊', 'cow': '牛',
    'elephant': '大象', 'bear': '熊', 'zebra': '斑马', 'giraffe': '长颈鹿', 'backpack': '背包',
    'umbrella': '雨伞', 'handbag': '手提包', 'tie': '领带', 'suitcase': '行李箱', 'frisbee': '飞盘',
    'skis': '滑雪板', 'snowboard': '滑雪单板', 'sports ball': '球', 'kite': '风筝',
    'baseball bat': '棒球棒', 'baseball glove': '棒球手套', 'skateboard': '滑板', 'surfboard': '冲浪板',
    'tennis racket': '网球拍', 'bottle': '瓶子', 'wine glass': '酒杯', 'cup': '杯子', 'fork': '叉子',
    'knife': '刀', 'spoon': '勺子', 'bowl': '碗', 'banana': '香蕉', 'apple': '苹果',
    'sandwich': '三明治', 'orange': '橙子', 'broccoli': '西兰花', 'carrot': '胡萝卜', 'hot dog': '热狗',
    'pizza': '披萨', 'donut': '甜甜圈', 'cake': '蛋糕', 'chair': '椅子', 'couch': '沙发',
    'potted plant': '盆栽', 'bed': '床', 'dining table': '餐桌', 'toilet': '马桶', 'tv': '电视',
    'laptop': '笔记本电脑', 'mouse': '鼠标', 'remote': '遥控器', 'keyboard': '键盘', 'cell phone': '手机',
    'microwave': '微波炉', 'oven': '烤箱', 'toaster': '烤面包机', 'sink': '水槽', 'refrigerator': '冰箱',
    'book': '书', 'clock': '挂钟', 'vase': '花瓶', 'scissors': '剪刀', 'teddy bear': '玩偶',
    'hair drier': '吹风机', 'toothbrush': '牙刷',
}

_TAG_FAIL_TAG = '🏷标签失败'   # 失败也落标记：手机浮条见它即停转不卡屏（#1244 沿革）
_TAG_QUEUE_TAG = '⏳待深识'   # ★ #1542（2026-11-25）失败照片进 VLM 补标队列的中转标记：
                              # 快速档认不出 → 换此标记 + auto_tried=2 入队；VLM 成功后清除


def _strip_b64(image_data):
    """dataURL 或纯 base64 → 纯 base64；空/非法返回 ''"""
    s = (image_data or '').strip()
    if not s:
        return ''
    if ',' in s and s.startswith('data:'):
        s = s.split(',', 1)[1]
    return s[:6_000_000]


def _eye_fast_tags(image_b64):
    """★ #1245 Eye recognize 快速档打标签：只产出标准名录标签（COCO 中文 + L2 历史中文
    tags），0.1~1s。返回 (tags_list, err)；素材全无时如实报 err 不硬编。"""
    import os as _os
    import tempfile as _tmp
    from src.core import eye_client
    path = ''
    try:
        raw = base64.b64decode(_strip_b64(image_b64) or '')
        if not raw:
            return [], '图片读不出来'
        fd, path = _tmp.mkstemp(suffix='.jpg', prefix='hv_eye_tag_')
        with _os.fdopen(fd, 'wb') as f:
            f.write(raw)
        r = eye_client.recognize(path, preset='fast', timeout=45.0)
        if not r.get('ok'):
            return [], r.get('error', 'Eye 识别失败')
        d = r.get('data') or {}
        tags = []
        # ★ #1249 主镜头：照片标的是「拍摄主体」——L1 按 bbox 面积×置信度排序，
        #   最大者排最前；占比 <8% 的碎小目标（背景杂物）丢弃
        dets = []
        for t in (d.get('trace') or []):
            if t.get('source_layer') == 'L1':
                for det in (t.get('detections') or []):
                    bb = det.get('bbox') or [0, 0, 0, 0]
                    area = max(0.0, (bb[2] - bb[0]) * (bb[3] - bb[1])) if len(bb) >= 4 else 0.0
                    dets.append((area, float(det.get('conf', 0) or 0), str(det.get('label', '')).strip()))
        dets.sort(key=lambda x: -(x[0] * x[1]))
        max_area = dets[0][0] if dets else 0.0
        for area, _cf, en in dets[:5]:
            if max_area > 0 and area / max_area < 0.08:
                continue
            zh = _COCO_ZH.get(en.lower(), '')
            if zh and zh not in tags:
                tags.append(zh)
            if len(tags) >= 3:
                break
        for t in (d.get('trace') or []):
            if t.get('source_layer') != 'L2':
                continue
            for s in (t.get('similar') or [])[:3]:
                for tag in str(s.get('tags') or '').split(','):
                    tag = tag.strip()
                    if tag and tag not in ('无', '(无标签)') and not tag.startswith('#') \
                            and not tag.isascii() and tag not in tags:
                        tags.append(tag)
        if not tags:
            return [], '识别不出（L1 无物体、相似图无标签），如实不硬编'
        return tags[:6], ''
    except Exception as e:
        return [], f'Eye 快速标签失败: {e}'
    finally:
        if path:
            try:
                _os.remove(path)
            except OSError:
                pass


def _mark_tag_fail(img_id, err=''):
    """标签失败落标记 tag——手机浮条见它即停转（#1244 沿革：失败也要有个说法）。
    ★ #1542：失败同时把图标成 auto_tried=2（VLM 补标队列），不再永久放弃——
    后台低峰队列会用 L3 理解环（跟用户所选看图模型）二次打标，成功后清标记。"""
    try:
        cur = get(img_id) or {}
        raw = cur.get('tags') or []
        old = [str(t).strip() for t in (raw if isinstance(raw, list) else raw.split(','))]
        if _TAG_FAIL_TAG in old:
            # 已有失败标记 → 升级成队列标记（保留 auto_tried=2 幂等）
            _queue_tag(img_id)
            return
        if _TAG_QUEUE_TAG not in old:
            set_tags(img_id, old + [_TAG_FAIL_TAG], merge=False)
        _queue_tag(img_id)
        logger.info(f'[ImgStore] 标签失败标记已落库 id={img_id}: {str(err)[:50]}')
    except Exception as e:
        logger.debug(f'[ImgStore] 标签失败标记跳过 id={img_id}: {e}')


def _queue_tag(img_id):
    """#1542 队列化：tags 里 🏷标签失败 → ⏳待深识 + auto_tried=2（幂等）。"""
    try:
        cur = get(img_id) or {}
        raw = cur.get('tags') or []
        old = [str(t).strip() for t in (raw if isinstance(raw, list) else raw.split(','))]
        if _TAG_QUEUE_TAG in old and (cur.get('auto_tried') or 0) == 2:
            return
        new = [_TAG_QUEUE_TAG if t == _TAG_FAIL_TAG else t for t in old]
        if _TAG_QUEUE_TAG not in new:
            new.append(_TAG_QUEUE_TAG)
        set_tags(img_id, new, merge=False)
        _update(img_id, auto_tried=2)
    except Exception as e:
        logger.debug(f'[ImgStore] 队列标记跳过 id={img_id}: {e}')


def _ensure_panel_record(pid, title):
    """★ #1249 沿革（photo_namer 收编）：无说明的面板照片用主镜头标签补建面板记录——
    照片不再是孤儿（实锤：只挂照片不建记录，电脑端面板记录列表永远空）。"""
    try:
        from src.core import panel_images as _pi
        c = _pi._conn()
        try:
            row = c.execute('SELECT panel_id, record_id FROM panel_images WHERE id=?',
                            (int(pid),)).fetchone()
        finally:
            c.close()
        if row and not row['record_id']:
            panel = str(row['panel_id'] or '')
            if panel and panel not in ('mobile', 'fridge', 'account'):
                from src.core.cloud_link import _photo_panel_record, _link_photo_record
                rid = _photo_panel_record(panel, title)
                if rid:
                    _link_photo_record(int(pid), rid)
                    logger.info(f'[ImgStore] 主镜头补建面板记录 {panel}: {rid}（{title}）')
    except Exception as e:
        logger.debug(f'[ImgStore] 主镜头补建记录跳过 pid={pid}: {e}')


def auto_tag_full(img_id, pid=None, delay=0):
    """★ #1306（2026-10-29）统一后台打标入口（photo_namer 下线后收编三合一）：
    ① retag 打标签（失败落 🏷标签失败，浮条停转）② auto_place CLIP 归位
    ③ pid 带了且有标签 → 主镜头补建面板记录（#1249 沿革）。
    ④ ★ #1491 人脸映射挂钩（记忆相册按人翻回忆）：本地 FaceEngine 认人 → face_photo_map
    delay>0 时延迟启动——让看图回答独占 GPU 先跑完（#1248 沿革）。"""
    def _run():
        tags = []
        try:
            tags, _m, err = retag(img_id)
            if err or not tags:
                logger.debug(f'[ImgStore] 自动打标签 id={img_id} 失败: {err}')
                _mark_tag_fail(img_id, err or '无标签')
            else:
                logger.info(f'[ImgStore] 自动打标签 id={img_id}: {tags}')
        except Exception as _te:
            logger.debug(f'[ImgStore] 自动打标签异常 id={img_id}: {_te}')
        try:
            auto_place(img_id)
        except Exception as _pe:
            logger.debug(f'[ImgStore] 独立归位失败 id={img_id}: {_pe}')
        try:
            face_map_hook(img_id)
        except Exception as _fe:
            logger.debug(f'[ImgStore] 人脸映射挂钩失败 id={img_id}: {_fe}')
        if pid and tags:
            _ensure_panel_record(pid, tags[0])
    if delay > 0:
        threading.Timer(delay, lambda: threading.Thread(
            target=_run, name=f'img-tag-{img_id}', daemon=True).start()).start()
    else:
        threading.Thread(target=_run, name=f'img-tag-{img_id}', daemon=True).start()


# ═══ ★ #1491（2026-11-12）记忆相册按人翻回忆：人脸↔照片映射 ═════════════════
# 数据真家 = lingzhi.db face_photo_map（与 images_index 同库）；向量本体在 data/vision/face.db。
# 本地 FaceEngine（YuNet+SFace，CPU 毫秒级），失败软降绝不挡主链。

_face_engine_inst = None
_face_scan_state = {'running': False, 'done': 0, 'total': 0, 'hits': 0, 'finished_at': ''}


def _get_face_engine():
    """FaceEngine 进程内单例（模型加载 ~1s 只干一次；没开功能/没模型返回 None）"""
    global _face_engine_inst
    try:
        from src.vision import face_store as _fs
        if not _fs.is_enabled():
            return None
        from src.vision.engines.face_engine import FaceEngine
        if not FaceEngine.models_ready():
            return None
        if _face_engine_inst is None:
            _face_engine_inst = FaceEngine()
        return _face_engine_inst
    except Exception:
        return None


def face_map_hook(img_id):
    """增量挂钩：入库/打标后对单张图认人 → 写 face_photo_map（已在映射里的跳过）。
    纯本地 CPU，失败软降返回 0。返回本次新写入条数。"""
    eng = _get_face_engine()
    if eng is None:
        return 0
    ensure_table()
    c = _conn()
    try:
        n = c.execute('SELECT COUNT(*) FROM face_photo_map WHERE img_id=?', (img_id,)).fetchone()[0]
    finally:
        c.close()
    if n:
        return 0
    p = get_file(img_id)
    if not p:
        return 0
    r = eng.infer(str(p))
    names = (r.data or {}).get('matched_names') or []
    if not names:
        return 0
    sims = {}
    for f in (r.data or {}).get('faces') or []:
        if f.get('matched') and f.get('identity') in names:
            sims[f['identity']] = max(sims.get(f['identity'], 0), float(f.get('similarity') or 0))
    c = _conn()
    try:
        for nm in names:
            c.execute('INSERT OR IGNORE INTO face_photo_map (img_id, person_name, similarity) VALUES (?,?,?)',
                      (img_id, str(nm)[:30], round(sims.get(nm, 0), 4)))
        c.commit()
    finally:
        c.close()
    logger.info(f'[ImgStore] 人脸映射 id={img_id}: {names}')
    return len(names)


def face_people() -> list:
    """人物清单：名字 + 照片数 + 最近一张缩略图（按张数排序）"""
    ensure_table()
    c = _conn()
    try:
        rows = c.execute(
            """SELECT m.person_name, COUNT(*) n, MAX(m.img_id) last_id
               FROM face_photo_map m JOIN images_index i ON i.id = m.img_id AND i.deleted_at=''
               GROUP BY m.person_name ORDER BY n DESC""").fetchall()
        out = []
        for r in rows:
            out.append({'name': r['person_name'], 'count': r['n'],
                        'thumb_url': f'/api/images/file/{r["last_id"]}?thumb=1'})
        return out
    finally:
        c.close()


def list_by_person(name: str, limit: int = 500) -> list:
    """按人物筛照片（记忆相册时光轴用，形状与 search() 的 items 一致）"""
    ensure_table()
    c = _conn()
    try:
        rows = c.execute(
            """SELECT i.* FROM face_photo_map m JOIN images_index i ON i.id = m.img_id
               WHERE m.person_name=? AND i.deleted_at=''
               ORDER BY i.created_at DESC LIMIT ?""", (name, max(1, min(int(limit), 500)))).fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        c.close()


def face_scan_async():
    """存量全量扫描（后台线程）：全部未删照片逐张认人 → 写映射（幂等，可重复跑）。
    返回 (ok, message)；进度用 face_scan_status() 轮询。"""
    if _face_scan_state['running']:
        return False, '扫描已在进行中'
    eng = _get_face_engine()
    if eng is None:
        return False, '人脸识别没开或模型没就位——先去 设置→人脸识别 录入家人'
    ensure_table()
    c = _conn()
    try:
        ids = [r[0] for r in c.execute(
            "SELECT id FROM images_index WHERE deleted_at='' ORDER BY id").fetchall()]
    finally:
        c.close()

    def _run():
        _face_scan_state.update(running=True, done=0, total=len(ids), hits=0)
        from src.vision import face_store as _fs
        for i, img_id in enumerate(ids):
            try:
                # 已在映射里的图跳过推理（幂等重跑只补新图）
                c = _conn()
                try:
                    n = c.execute('SELECT COUNT(*) FROM face_photo_map WHERE img_id=?', (img_id,)).fetchone()[0]
                finally:
                    c.close()
                if not n and face_map_hook(img_id):
                    _face_scan_state['hits'] += 1
            except Exception as _e:
                logger.debug(f'[ImgStore] 人脸扫描单张失败 id={img_id}: {_e}')
            _face_scan_state['done'] = i + 1
        _face_scan_state['running'] = False
        _face_scan_state['finished_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"[ImgStore] 人脸全量扫描完成: {_face_scan_state['done']} 张 / 命中 {_face_scan_state['hits']}")
    threading.Thread(target=_run, name='face-scan-all', daemon=True).start()
    return True, f'扫描已启动（共 {len(ids)} 张，后台跑，不耽误用）'


def face_scan_status() -> dict:
    return dict(_face_scan_state)


def retag(img_id):
    """手动/自动打标签（★ #1155 跟随用户看图路由偏好）。返回 (tags_list, model, err)；成功即合并入库。
    ★ #925 T4 词表扩展：覆盖记忆相册生活照六类（自拍/风景/美食/家人/宠物/截图）
    ★ #1245（2026-10-27 定版拍板）：主模型=HarvenEye → 走 recognize 快速档（L1+L2 标准名录标签，
    0.1~1s）——**不再调 VLM 补标**（原先每张照片多花 13s+）。识别不出如实留空不硬编。"""
    try:
        p = get_file(img_id)
        if not p:
            return None, '', '图片读不出来'
        # ★ #1245 Eye 快速档：标准名录标签秒试（L1+L2）
        # ★ #1257 批4：Eye 认不出 → **默认不再落 VLM**（吉他实锤 13s+ 冷加载只换几个标签，
        #   向量已入 Eye 库等手动归类升 gold）；要恢复旧行为开 config vision.vlm_retag_fallback=true
        try:
            from src.utils.config import get_config as _gc
            if ((_gc() or {}).get('current_model', '') or '').strip() == 'harveneye':
                b64 = base64.b64encode(p.read_bytes()).decode('ascii')
                tags, err = _eye_fast_tags(b64)
                if not err and tags:
                    set_tags(img_id, tags, merge=True)
                    return tags, 'HarvenEye(L1+L2)', ''
                # VLM 兜底开关（默认关）
                if not ((_gc() or {}).get('vision.vlm_retag_fallback', False)):
                    logger.debug(f'[ImgStore] Eye 快速档搞不定，VLM 兜底已按 #1257 默认关闭 id={img_id}: {err}')
                    return None, '', err or 'Eye 快速档识别不出'
                logger.debug(f'[ImgStore] Eye 快速档搞不定（VLM 兜底开关已开，落 VLM）id={img_id}: {err}')
        except Exception as _ee:
            logger.debug(f'[ImgStore] Eye 快速标签失败（落回原链路）: {_ee}')
        b64 = base64.b64encode(p.read_bytes()).decode('ascii')
        from src.core.panel_assist import _camp_vision_bg
        text, model, err = _camp_vision_bg(
            '看这张照片，给4到6个中文标签，标的是「拍摄主体」——拍摄者主要想拍的那个物体（画面最大或居中的），'
            '必须覆盖：'
            '①照片类型：自拍/风景/美食/家人/宠物/截图/物品中至少一个'
            '②核心主体（如沙发、电脑、冰箱、书包、蛋糕、富贵竹），背景里的杂物（路人、边角物品）不要标'
            '③房间/场所（如书房、客厅、卧室、厨房、海边、公园）'
            '④人物（如有，如孩子、大人）。只输出标签，逗号分隔，不要句子不要解释。', b64, timeout=120)
        if err or not text:
            return None, model, err or '视觉模型没有返回'
        tags = [t.strip()[:12] for t in text.replace('，', ',').replace('、', ',').split(',') if t.strip()][:6]
        if not tags:
            return None, model, '没解析出标签'
        set_tags(img_id, tags, merge=True)
        return tags, model, ''
    except Exception as e:
        return None, '', str(e)


def retag_deep(img_id):
    """★ #1542（2026-11-25）VLM 补标队列消化：快速档认不出的照片（auto_tried=2 /
    ⏳待深识）由后台低峰队列逐张交给 L3 理解环（_camp_vision 跟用户所选看图模型，
    铁律0）二次打标。成功 → 真标签入库 + 清 ⏳待深识 + CLIP 归位重跑；失败 → 退避
    30 分钟（_deep_tag_fail），连败 5 次如实留 🏷识别不出 不再空转。
    返回 (tags_list, model, err)。"""
    try:
        p = get_file(img_id)
        if not p:
            return None, '', '图片读不出来'
        b64 = base64.b64encode(p.read_bytes()).decode('ascii')
        from src.core.panel_assist import _camp_vision_bg
        text, model, err = _camp_vision_bg(
            '看这张照片，给4到6个中文标签，标的是「拍摄主体」——拍摄者主要想拍的那个物体（画面最大或居中的），'
            '必须覆盖：'
            '①照片类型：自拍/风景/美食/家人/宠物/截图/物品中至少一个'
            '②核心主体（如牛奶、鸡蛋、青菜、作业本、单据、玩具、工具——画面里真实存在的东西），'
            '背景里的杂物不要标'
            '③房间/场所（如厨房、书房、客厅、冰箱内部）'
            '④人物（如有）。只输出标签，逗号分隔，不要句子不要解释。', b64, timeout=120)
        if err or not text:
            return None, model, err or '视觉模型没有返回'
        tags = [t.strip()[:12] for t in text.replace('，', ',').replace('、', ',').split(',') if t.strip()][:6]
        if not tags:
            return None, model, '没解析出标签'
        # 成功：真标签入库（去掉所有过程标记）+ 清队列户口 + 归位重跑
        cur = get(img_id) or {}
        raw = cur.get('tags') or []
        old = [str(t).strip() for t in (raw if isinstance(raw, list) else raw.split(','))]
        clean = [t for t in old if t not in (_TAG_FAIL_TAG, _TAG_QUEUE_TAG) and not t.startswith('🏷') and t != '已压缩']
        set_tags(img_id, (clean + [t for t in tags if t not in clean])[:8], merge=False)
        _update(img_id, auto_tried=3)   # 3=深识完成，队列 SQL 不再捞
        try:
            auto_place(img_id)
        except Exception:
            pass
        logger.info(f'[ImgStore] VLM 补标成功 id={img_id}: {tags}（model={model}）')
        return tags, model, ''
    except Exception as e:
        return None, '', str(e)


# ══════════════ ★ #925 T4 记忆相册：AI 看图说话 + 本地修图 ══════════════

_DESCRIBE_STYLES = {
    'story': ('编故事', '看这张照片，用温暖的语气编一个 100 字左右的小故事，就像这个家庭的相册旁白。直接输出故事正文。'),
    'poem': ('写首诗', '看这张照片，写一首 4 到 6 行的中文小诗，捕捉画面里的生活气息。直接输出诗。'),
    'kid': ('讲给孩子听', '看这张照片，用给 6 岁小朋友讲故事的口吻，讲 80 字左右，句子短一点，生动一点。直接输出。'),
    'desc': ('认真描述', '看这张照片，客观描述画面内容：在哪里、有什么、什么氛围，80 字以内。直接输出。'),
}


def describe(img_id, style='story'):
    """AI 看图说话（minicpm-v 本地视觉，零费用）。返回 (text, model, err)"""
    try:
        p = get_file(img_id)
        if not p:
            return '', '', '图片读不出来'
        style_info = _DESCRIBE_STYLES.get(style) or _DESCRIBE_STYLES['story']
        b64 = base64.b64encode(p.read_bytes()).decode('ascii')
        from src.core.panel_assist import _camp_vision_bg
        return _camp_vision_bg(style_info[1], b64, timeout=180)
    except Exception as e:
        return '', '', str(e)


# 本地修图动作表：name -> (中文名, 实现函数)
def _pil_open(img_id):
    p = get_file(img_id)
    if not p:
        return None, None
    from PIL import Image
    try:
        img = Image.open(p)
        img.load()
    except Exception:
        return None, None
    if img.mode != 'RGB':
        img = img.convert('RGB')
    return img, p


# ★ #994 图片编辑一期（2026-10-05）：覆盖写回 + 换图
def _overwrite_image_file(img_id, img):
    """把 PIL 图覆盖写回该图的库仓文件（原图 + 缩略图），并刷新 sha256。
    只允许覆盖哈文库仓内文件（data/media/ 下）；原地登记的外部原件
    （知识库/用户上传的原位置文件）绝不覆盖。
    返回 (ok, err)。"""
    try:
        it = get(img_id)
        if not it:
            return False, '图片不存在'
        rel = str(it.get('path') or '')
        if not rel.startswith('data/media/'):
            return False, '这张图是外部原件（不在哈文库仓），不能覆盖，请用「存成新图」'
        main_path = BASE_DIR / rel
        if not main_path.is_file():
            return False, '本体文件丢了'
        import os as _os
        # 原子覆盖：先写临时文件再替换，写一半断电不至于毁原图
        tmp = main_path.with_name(main_path.name + '.tmp.jpg')
        img.save(tmp, 'JPEG', quality=88)
        _os.replace(str(tmp), str(main_path))
        # 缩略图同步重生成
        c = _conn()
        try:
            r = c.execute('SELECT thumb_path FROM images_index WHERE id=?', (img_id,)).fetchone()
            thumb_rel = (r['thumb_path'] if r else '') or ''
        finally:
            c.close()
        if thumb_rel.startswith('data/media/'):
            tp = BASE_DIR / thumb_rel
            if tp.is_file():
                im2 = img.copy()
                im2.thumbnail((320, 320))
                tmp2 = tp.with_name(tp.name + '.tmp.jpg')
                im2.save(tmp2, 'JPEG', quality=70)
                _os.replace(str(tmp2), str(tp))
        # sha 刷新（覆盖后字节变了，去重账要跟上，否则下次存同图会误判重复）
        import io as _io2
        buf = _io2.BytesIO()
        img.save(buf, 'JPEG', quality=88)
        new_sha = _sha_bytes(buf.getvalue())
        c2 = _conn()
        try:
            c2.execute('UPDATE images_index SET sha256=? WHERE id=?', (new_sha, img_id))
            c2.commit()
        finally:
            c2.close()
        return True, ''
    except Exception as e:
        logger.error(f'[ImgStore] 覆盖写回失败: {e}')
        return False, str(e)


def replace_with_upload(img_id, image_data):
    """★ #994 换图：上传的新图覆盖该图库仓文件（原图+缩略图）。
    登记行/相册/标签/面板关联全保留（引用文件名不变自动生效）。
    只允许库仓内文件；外部原件拒绝。返回 (ok, err)。"""
    try:
        if isinstance(image_data, str):
            if ',' in image_data and image_data.strip().startswith('data:'):
                image_data = image_data.split(',', 1)[1]
            raw = base64.b64decode(image_data)
        else:
            raw = bytes(image_data)
        if not raw:
            return False, '新图数据为空'
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        return _overwrite_image_file(img_id, img)
    except Exception as e:
        logger.error(f'[ImgStore] 换图失败: {e}')
        return False, str(e)


def _tint(img, r_mul, g_mul, b_mul):
    """通道乘法调色（暖/冷/复古底色都用它）"""
    from PIL import ImageMath
    r, g, b = img.split()
    r = r.point(lambda x: min(255, int(x * r_mul)))
    g = g.point(lambda x: min(255, int(x * g_mul)))
    b = b.point(lambda x: min(255, int(x * b_mul)))
    from PIL import Image as _I
    return _I.merge('RGB', (r, g, b))


def edit_local(img_id, action, params=None, replace=False):
    """★ #925 T4 本地修图（PIL，零费用秒出）。返回 (new_img_id, 新图url, err)。
    action: warm/cool/bw/vivid/retro/brighten/contrast/rotate/crop/frame
    params: strength(0~1 默认0.5) angle box[x1,y1,x2,y2 比例] color width
    ★ #994（2026-10-05 已批准「图片编辑一期」）：replace=True 时处理结果直接覆盖
    原文件（原图+缩略图），不另存新图——面板记录/相册引用的文件名不变自动生效，
    即「换图/改图」。只允许覆盖哈文库仓内文件（data/media/ 下），外部原件拒绝。"""
    params = params or {}
    strength = max(0.0, min(1.0, float(params.get('strength', 0.5) or 0.5)))
    img, src_path = _pil_open(img_id)
    if img is None:
        return None, '', '图片读不出来'
    try:
        from PIL import ImageEnhance, ImageOps, Image as _I
        if action == 'warm':
            img = _tint(img, 1 + 0.18 * strength, 1.0, 1 - 0.15 * strength)
        elif action == 'cool':
            img = _tint(img, 1 - 0.12 * strength, 1.0, 1 + 0.18 * strength)
        elif action == 'bw':
            img = _I.merge('RGB', (img.convert('L'),) * 3)
        elif action == 'vivid':
            img = ImageEnhance.Color(img).enhance(1 + 0.6 * strength)
            img = ImageEnhance.Contrast(img).enhance(1 + 0.12 * strength)
        elif action == 'retro':
            img = ImageEnhance.Color(img).enhance(1 - 0.35 * strength)
            img = _tint(img, 1 + 0.10 * strength, 1 + 0.02 * strength, 1 - 0.10 * strength)
            img = ImageEnhance.Brightness(img).enhance(1 + 0.05 * strength)
        elif action == 'brighten':
            img = ImageEnhance.Brightness(img).enhance(1 + 0.45 * strength)
        elif action == 'contrast':
            img = ImageEnhance.Contrast(img).enhance(1 + 0.5 * strength)
        elif action == 'rotate':
            angle = float(params.get('angle', 90) or 90)
            img = img.rotate(-angle, expand=True, fillcolor=(255, 255, 255))
        elif action == 'crop':
            box = params.get('box') or [0.05, 0.05, 0.95, 0.95]
            w, h = img.size
            x1 = max(0, min(1, float(box[0]))) * w
            y1 = max(0, min(1, float(box[1]))) * h
            # ★ 修正（实测抓虫）：x1 是像素、比例上限必须先乘完再比，
            #   否则 max(x1+8, 0.95) 会把比例当像素 → 裁出 11G 像素巨图触发 PIL 炸弹保护
            x2 = max(x1 + 8, min(1.0, float(box[2])) * w)
            y2 = max(y1 + 8, min(1.0, float(box[3])) * h)
            img = img.crop((int(x1), int(y1), int(x2), int(y2)))
        elif action == 'frame':
            width = max(4, min(120, int(params.get('width', 24) or 24)))
            color = params.get('color', '#ffffff')
            color = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(color, str) and color.startswith('#') else (255, 255, 255)
            img = ImageOps.expand(img, border=width, fill=color)
        else:
            return None, '', f'不支持的动作: {action}'

        import io as _io
        buf = _io.BytesIO()
        img.save(buf, 'JPEG', quality=88)
        raw = buf.getvalue()
        cur = get(img_id)
        label = {'warm': '暖色', 'cool': '冷色', 'bw': '黑白', 'vivid': '鲜艳', 'retro': '复古',
                 'brighten': '调亮', 'contrast': '增强对比', 'rotate': '旋转', 'crop': '裁剪',
                 'frame': '加相框'}.get(action, action)
        # ★ #994 替换模式：覆盖原文件，登记行不动（sha 同步刷新）
        if replace:
            ok, err = _overwrite_image_file(img_id, img)
            if not ok:
                return None, '', err
            logger.info(f'[ImgStore] 本地修图覆盖原图 #{img_id} {action}（引用不变）')
            return img_id, f'/api/images/file/{img_id}', ''
        r = register(image_data=raw, source='edited',
                     album=(cur or {}).get('album') or None,
                     note=f"{((cur or {}).get('note') or '图片')[:80]}（{label}版）",
                     auto_tag=False)
        if not r:
            return None, '', '修图结果登记失败'
        logger.info(f'[ImgStore] 本地修图 #{img_id} {action} -> 新图 #{r["id"]}')
        return r['id'], r.get('url', ''), ''
    except Exception as e:
        logger.error(f'[ImgStore] 本地修图失败: {e}')
        return None, '', str(e)



# ══════════════ 检索与查询 ══════════════

def _row_to_item(r):
    return {
        'id': r['id'], 'source': r['source'], 'album': r['album'],
        'tags': [t for t in (r['tags'] or '').split(',') if t],
        'note': r['note'], 'panel_id': r['panel_id'], 'record_id': r['record_id'],
        'group_id': (r['group_id'] if 'group_id' in r.keys() else '') or '',  # ★ #1407-B
        'created_at': r['created_at'],
        'url': f'/api/images/file/{r["id"]}',
        'thumb_url': f'/api/images/file/{r["id"]}?thumb=1',
        'path': r['path'],
    }


def search(q='', album='', source='', panel_id='', limit=200, tag='', trash=False, group_id=''):
    """图库检索：关键词同时匹配 note/tags/album/path 文件名。全部条件可选。
    ★ #1003（2026-10-05）：tag=精确标签筛选（点标签过滤）；trash=True 只看回收站（软删行）。
    ★ #1407-B（2026-11-03）：group_id=照片组筛选（'__none__' 只看未成组）。"""
    try:
        ensure_table()
        c = _conn()
        sql = 'SELECT * FROM images_index WHERE 1=1'
        params = []
        # ★ #1003 软删隔离：默认只看正常图，回收站视图只看已删
        sql += " AND deleted_at=''" if not trash else " AND deleted_at!=''"
        if album:
            sql += ' AND album=?'
            params.append(album)
        if source:
            sql += ' AND source=?'
            params.append(source)
        if panel_id:
            sql += ' AND panel_id=?'
            params.append(panel_id)
        if tag:
            # 精确标签：前后加逗号圈死整词，防「猫」误中「猫咪」
            sql += " AND (',' || tags || ',') LIKE ?"
            params.append(f'%,{tag},%')
        # ★ #1407-B 照片组筛选
        if group_id == '__none__':
            sql += " AND (group_id='' OR group_id IS NULL)"
        elif group_id:
            sql += ' AND group_id=?'
            params.append(group_id)
        if q:
            # ★ #924 修复（2026-09-27 实锤）：原版整串 LIKE——「书房电脑」四个字连在一起
            #   匹配不到 note/tags 里的「书房」「电脑」，照片明明在库却搜不出（实测踩坑）。
            #   改为分词 OR：任一词命中即返回，原串优先整串匹配。
            terms = [q.strip()]
            try:
                import jieba
                terms += [w.strip() for w in jieba.lcut(q) if len(w.strip()) >= 2]
            except Exception:
                import re as _re
                terms += [w for w in _re.split(r'[\s，,。的了吗呢吧啊呢？?！!]+', q) if len(w) >= 2]
            conds = []
            for t in dict.fromkeys(terms):
                like = f'%{t}%'
                conds.append('(note LIKE ? OR tags LIKE ? OR album LIKE ? OR path LIKE ?)')
                params += [like, like, like, like]
            sql += ' AND (' + ' OR '.join(conds) + ')'
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(max(1, min(int(limit), 500)))
        rows = c.execute(sql, params).fetchall()
        c.close()
        return [_row_to_item(r) for r in rows]
    except Exception as e:
        logger.error(f'[ImgStore] 检索失败: {e}')
        return []


def get(img_id):
    try:
        ensure_table()
        c = _conn()
        r = c.execute('SELECT * FROM images_index WHERE id=?', (img_id,)).fetchone()
        c.close()
        return _row_to_item(r) if r else None
    except Exception as e:
        logger.error(f'[ImgStore] get 失败: {e}')
        return None


def list_albums():
    """相册清单（含每册张数；空相册也列出）"""
    try:
        ensure_table()
        c = _conn()
        albums = [r['name'] for r in c.execute('SELECT name FROM img_albums ORDER BY created_at, name')]
        counts = {r['album']: r['n'] for r in c.execute(
            "SELECT album, COUNT(*) AS n FROM images_index WHERE deleted_at='' GROUP BY album")}
        c.close()
        return [{'name': a, 'count': counts.get(a, 0)} for a in albums]
    except Exception as e:
        logger.error(f'[ImgStore] 列相册失败: {e}')
        return []


def add_album(name):
    try:
        name = (name or '').strip()[:30]
        if not name:
            return False
        ensure_table()
        c = _conn()
        c.execute('INSERT OR IGNORE INTO img_albums(name, created_at) VALUES(?,?)',
                  (name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        c.commit()
        c.close()
        return True
    except Exception as e:
        logger.error(f'[ImgStore] 加相册失败: {e}')
        return False


# ══════════════ 修改与删除 ══════════════

def set_album(img_id, album):
    """★ #1188 手动移相册 = 用户拍板，置 auto_placed=0，归位引擎永不再动这张图。"""
    return _update(img_id, album=(album or '').strip()[:30] or '未分类', auto_placed=0)


def album_move_from_text(text):
    """★ 2026-11-12 归档指令快车道（反吹牛实锤修复）：从用户原话解析「分到/放进/
    归类到 XX 相册」意图。实锤：HarvenEye 看图直答谎称「已归类至老谭相册、存储位置
    data\\media\\family\\221.jpg」——目录根本不存在，照片实际躺在生活照（id=221）。
    直答链无执行权，模型光说不做；本函数给引擎做本地毫秒级真归档用。
    返回 (target, err_hint)：target=验证过的相册名（可直接归档）或 ''；
    err_hint=失败时的用户提示（带现有相册清单）。
    目标必须已存在，或是已录家人名（自动建人名相册）——防「不存在的路人」式垃圾相册。"""
    import re as _re
    if not text:
        return '', ''
    _m = _re.search(r'(?:分到|放进|归到|归类到|移到|换到|转入|归入|收进)\s*[:：]?\s*([^\s，。,；;？?！!]{1,12})', text)
    if not _m:
        return '', ''
    name = _m.group(1).strip()
    for suf in ('里面去', '里面', '相册里', '相册', '里去', '里', '中去', '中', '内'):
        if name.endswith(suf) and len(name) > len(suf):
            name = name[:-len(suf)].strip()
            break
    if not name or name in ('哪个', '哪', '什么', '这儿', '那里'):
        return '', ''
    try:
        ensure_table()
        c = _conn()
        ok = c.execute('SELECT 1 FROM img_albums WHERE name=?', (name,)).fetchone()
        c.close()
        if ok:
            return name, ''
        # 已录家人名 → 自动建人名相册（人脸识别链同款惯例）
        try:
            from src.vision.face_store import list_members as _lm
            for _mem in (_lm() or []):
                if (_mem.get('name') or '').strip() == name:
                    add_album(name)
                    return name, ''
        except Exception:
            pass
        existing = list_album_names()
        return '', (f'没有「{name}」这个相册。现有相册：{"、".join(existing)}；'
                    f'已录家人的名字也可以直接说（自动建人名相册）。')
    except Exception:
        return '', ''


def list_album_names():
    """★ #1306 相册名清单（identify_images 建议相册候选用）。未分类不参与。
    ★ #1409（2026-11-03）原与上方 #1006 list_albums() 同名互相覆盖（后定义者胜），
    导致 /api/images/albums 返回形状被顶成纯字符串、前端 a.name 全 undefined 实锤——改名收口。"""
    try:
        ensure_table()
        c = _conn()
        rows = c.execute('SELECT name FROM img_albums').fetchall()
        c.close()
        return [r['name'] for r in rows if r['name'] and r['name'] != '未分类']
    except Exception:
        return []


def set_album_deep(name, on=True):
    """★ #1307 相册级「外脑深析」开关：开着 → 该相册新图后台空闲时外脑补一句描述进 note
    （跟随用户看图路由，烧外脑 token；默认全关，用户逐相册拍板）。"""
    try:
        ensure_table()
        c = _conn()
        c.execute('UPDATE img_albums SET deep_analyze=? WHERE name=?',
                  (1 if on else 0, (name or '').strip()[:30]))
        c.commit()
        c.close()
        logger.info(f'[ImgStore] 相册深析开关 {name} → {"开" if on else "关"}')
        return True
    except Exception as e:
        logger.error(f'[ImgStore] 深析开关失败 {name}: {e}')
        return False


def list_by_date(date='today', limit=20):
    """★ #1306 按录入日期查图（identify_images FC 工具用）。
    date: today/yesterday/今天/昨天/YYYY-MM-DD。返回 get() 同款 dict 列表（新的在前）。"""
    try:
        ensure_table()
        d = (date or 'today').strip()
        if d in ('', 'today', '今天'):
            day = datetime.now().strftime('%Y-%m-%d')
        elif d in ('yesterday', '昨天'):
            day = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            day = d[:10]
        c = _conn()
        rows = c.execute("SELECT id FROM images_index WHERE created_at LIKE ? AND deleted_at='' "
                         'ORDER BY id DESC LIMIT ?', (day + '%', int(limit or 20))).fetchall()
        c.close()
        return [get(r['id']) for r in rows if get(r['id'])]
    except Exception as e:
        logger.error(f'[ImgStore] 按日期查图失败: {e}')
        return []


def set_note(img_id, note):
    return _update(img_id, note=str(note or '')[:200])


def set_tags(img_id, tags, merge=False):
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace('，', ',').split(',') if t.strip()]
    tags = [t[:12] for t in (tags or [])][:8]
    if merge:
        cur = get(img_id)
        if cur:
            old = cur['tags']
            tags = old + [t for t in tags if t not in old]
    return _update(img_id, tags=','.join(tags)[:200])


def link_panel(img_id, panel_id, record_id=None):
    return _update(img_id, panel_id=panel_id, record_id=record_id)


def _update(img_id, **kw):
    try:
        if not kw:
            return False
        ensure_table()
        c = _conn()
        sets = ', '.join(f'{k}=?' for k in kw)
        c.execute(f'UPDATE images_index SET {sets} WHERE id=?', (*kw.values(), img_id))
        c.commit()
        c.close()
        return True
    except Exception as e:
        logger.error(f'[ImgStore] 更新失败 id={img_id}: {e}')
        return False


# ══════════════ ★ #1407-B 照片组（2026-11-03）══════════════

def set_group(img_id, group_id):
    """设/换照片组。group_id 传 ''=退出成组。"""
    return _update(img_id, group_id=str(group_id or ''))


def group_items(group_id):
    """列组内全部图（未删）。"""
    ensure_table()
    c = _conn()
    try:
        rows = c.execute("SELECT * FROM images_index WHERE group_id=? AND deleted_at='' ORDER BY id",
                         (group_id,)).fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        c.close()


def group_list(limit=50):
    """现有组清单：组号 + 张数 + 组代表图（最早一张）+ 共同 note 摘要。"""
    ensure_table()
    c = _conn()
    try:
        rows = c.execute(
            "SELECT group_id, COUNT(*) AS n, MIN(id) AS first_id FROM images_index "
            "WHERE group_id!='' AND deleted_at='' GROUP BY group_id ORDER BY MAX(id) DESC LIMIT ?",
            (max(1, min(int(limit), 200)),)).fetchall()
        out = []
        for r in rows:
            rep = c.execute('SELECT note, path FROM images_index WHERE id=?', (r['first_id'],)).fetchone()
            out.append({'group_id': r['group_id'], 'count': r['n'],
                        'first_thumb': f'/api/images/file/{r["first_id"]}?thumb=1',
                        'note': (rep['note'] if rep else '')[:60],
                        'name': (rep['path'].replace('\\\\', '/').rsplit('/', 1)[-1] if rep and rep['path'] else '')})
        return out
    finally:
        c.close()


def remove(img_id, hard=False):
    """★ #1003（2026-10-05）删除改两段式：默认软删进回收站（只打 deleted_at 标记，
    文件原封不动可恢复）；hard=True 才真删（回收站里点「彻底删除」走这，删登记+删库仓文件）。"""
    try:
        ensure_table()
        if not hard:
            return _update(img_id, deleted_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        c = _conn()
        r = c.execute('SELECT path, thumb_path FROM images_index WHERE id=?', (img_id,)).fetchone()
        if not r:
            c.close()
            return False
        c.execute('DELETE FROM images_index WHERE id=?', (img_id,))
        c.commit()
        c.close()
        for key in ('path', 'thumb_path'):
            rp = r[key]
            if rp and rp.startswith('data/media/img_store'):
                p = BASE_DIR / rp
                try:
                    p.unlink()
                except OSError:
                    pass
        return True
    except Exception as e:
        logger.error(f'[ImgStore] 删图失败 id={img_id}: {e}')
        return False


def restore(img_id):
    """★ #1003 从回收站恢复（清掉 deleted_at 标记）"""
    return _update(img_id, deleted_at='')


def list_tags():
    """★ #1003 全库标签聚合（含计数，排除回收站），按张数倒序。标签管理弹层用。"""
    try:
        ensure_table()
        c = _conn()
        rows = c.execute("SELECT tags FROM images_index WHERE deleted_at='' AND tags!=''").fetchall()
        c.close()
        tally = {}
        for r in rows:
            for t in (r['tags'] or '').split(','):
                t = t.strip()
                if t:
                    tally[t] = tally.get(t, 0) + 1
        return [{'tag': k, 'count': v} for k, v in sorted(tally.items(), key=lambda x: -x[1])]
    except Exception as e:
        logger.error(f'[ImgStore] 标签聚合失败: {e}')
        return []


def rename_tag(old, new):
    """★ #1003 标签改名/合并：所有含 old 的图，把 old 换成 new（new 已存在则自然合并去重）。
    返回改动张数。"""
    old = (old or '').strip()[:12]
    new = (new or '').strip()[:12]
    if not old or not new:
        return -1
    try:
        ensure_table()
        c = _conn()
        rows = c.execute("SELECT id, tags FROM images_index WHERE deleted_at='' AND tags!=''").fetchall()
        changed = 0
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for r in rows:
            tags = [t.strip() for t in (r['tags'] or '').split(',') if t.strip()]
            if old not in tags:
                continue
            tags = [new if t == old else t for t in tags]
            tags = list(dict.fromkeys(tags))[:8]  # 去重（合并场景）+ 8 个上限
            c.execute('UPDATE images_index SET tags=? WHERE id=?', (','.join(tags)[:200], r['id']))
            changed += 1
        c.commit()
        c.close()
        return changed
    except Exception as e:
        logger.error(f'[ImgStore] 标签改名失败 {old}→{new}: {e}')
        return -1


def batch(action, ids, album='', tags=''):
    """★ #1003 批量操作。action：delete 软删 / restore 恢复 / purge 彻底删除 /
    album 移相册 / tag 合并打标 / untag 去掉指定标签。ids=图片 id 列表。
    返回 (成功数, 总数)。"""
    ids = [int(i) for i in (ids or []) if str(i).strip().lstrip('-').isdigit()]
    if not ids or not action:
        return 0, 0
    ok = 0
    for iid in ids:
        try:
            if action == 'delete':
                ok += 1 if remove(iid) else 0
            elif action == 'restore':
                ok += 1 if restore(iid) else 0
            elif action == 'purge':
                ok += 1 if remove(iid, hard=True) else 0
            elif action == 'album':
                ok += 1 if set_album(iid, album) else 0
            elif action == 'tag':
                set_tags(iid, tags, merge=True)
                ok += 1
            elif action == 'untag':
                cur = get(iid)
                drop = set(t.strip() for t in (tags or '').replace('，', ',').split(',') if t.strip())
                if cur and drop:
                    left = [t for t in cur['tags'] if t not in drop]
                    _update(iid, tags=','.join(left)[:200])
                ok += 1
        except Exception as e:
            logger.error(f'[ImgStore] 批量 {action} id={iid} 失败: {e}')
    return ok, len(ids)


def get_file(img_id, thumb=False):
    """取本体/缩略图绝对路径（给媒体端点 send_file 用）。找不到返回 None。"""
    try:
        ensure_table()
        c = _conn()
        r = c.execute('SELECT path, thumb_path FROM images_index WHERE id=?', (img_id,)).fetchone()
        c.close()
        if not r:
            return None
        if thumb and r['thumb_path']:
            p = BASE_DIR / r['thumb_path']
            if p.is_file():
                return p
        p = BASE_DIR / r['path']
        return p if p.is_file() else None
    except Exception as e:
        logger.error(f'[ImgStore] 取文件失败 id={img_id}: {e}')
        return None


def _deep_analyze_recent(deep_fail):
    """★ #1307（2026-10-29）相册级外脑深析：img_albums.deep_analyze=1 的相册，
    近 24h 入库且 note 为空的图（每轮最多 3 张）→ 外脑一句话描述写 note。
    跟随用户看图路由（panel_assist._camp_vision）；失败退避 30 分钟防刷 token。"""
    try:
        c = _conn()
        alb_rows = c.execute('SELECT name FROM img_albums WHERE deep_analyze=1').fetchall()
        if not alb_rows:
            c.close()
            return
        albs = [r['name'] for r in alb_rows]
        since = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        marks = ','.join('?' * len(albs))
        rows = c.execute(f'SELECT id, album FROM images_index WHERE album IN ({marks}) '
                         "AND (note IS NULL OR note='') AND deleted_at='' "
                         'AND created_at >= ? ORDER BY id DESC LIMIT 6',
                         (*albs, since)).fetchall()
        c.close()
        now_t = time.time()
        done = 0
        for r in rows:
            if done >= 3:
                break
            ft = deep_fail.get(r['id'], 0)
            if ft and now_t - ft < 1800:
                continue
            p = get_file(r['id'])
            if not p:
                continue
            b64 = base64.b64encode(p.read_bytes()).decode('ascii')
            from src.core.panel_assist import _camp_vision_bg
            text, _m, err = _camp_vision_bg(
                '看这张照片，用一句中文描述画面内容（50字内，说清主体和场景），直接输出句子，不要任何前缀。',
                b64, timeout=60)
            if err or not (text or '').strip():
                deep_fail[r['id']] = now_t
                continue
            _update(r['id'], note=text.strip()[:200])
            deep_fail.pop(r['id'], None)
            done += 1
            logger.info(f'[ImgStore] 深析相册补描述 id={r["id"]} album={r["album"]}: {text.strip()[:40]}')
    except Exception as e:
        logger.debug(f'[ImgStore] 深析相册跳过: {e}')


def _night_sweep():
    """★ #1307 夜间扫尾（config vision.night_sweep，默认关）：未分类残留图重试
    CLIP 归位（全本地 0 token）。只动 auto_placed=1 的图，用户手动锁定的绝不碰。"""
    try:
        c = _conn()
        rows = c.execute("SELECT id FROM images_index WHERE album='未分类' AND auto_placed=1 "
                         "AND deleted_at='' ORDER BY id DESC LIMIT 50").fetchall()
        c.close()
        if not rows:
            return 0
        hit = 0
        for r in rows:
            try:
                _old, new = auto_place(r['id'])
                if new:
                    hit += 1
            except Exception:
                pass
        logger.info(f'[ImgStore] 夜间扫尾：未分类 {len(rows)} 张重试归位，命中 {hit}')
        return hit
    except Exception as e:
        logger.debug(f'[ImgStore] 夜间扫尾跳过: {e}')
        return 0


def ensure_auto_tag_loop():
    """★ 后台补标守护线程（幂等，web_app 启动时调用）
    ★ #930-B5 提速（2026-09-30，已批准）：原来 30 分钟才补 5 张，拍照多时标签长期空白。
    改为每 2 分钟一轮、每轮最多 10 张、批内 3 路并发（4070 转得动；Ollama 请求内部排队不会打架）。"""
    global _tag_thread_started
    with _img_lock:
        if _tag_thread_started:
            return
        _tag_thread_started = True

    def _tag_batch(ids):
        done = 0
        failed = []
        sem = threading.Semaphore(3)

        def _one(iid):
            nonlocal done
            with sem:
                tags, _m, err = retag(iid)
                if not err:
                    done += 1
                else:
                    failed.append(iid)

        ths = [threading.Thread(target=_one, args=(i,), daemon=True) for i in ids]
        for t in ths:
            t.start()
        for t in ths:
            t.join(300)
        return done, failed

    _fail = {}   # ★ 2026-10-18 防空转：失败 5 次的图本轮跳过（实锤：timeout 参数 bug 让 1 张图
                 # 每 2 分钟空转失败一次刷屏一整夜；清零时机=进程重启或图有标签后被 SQL 自然排除）
    _deep_fail = {}   # ★ #1307 深析失败退避：失败后 30 分钟内不重试同一张（防外脑故障刷 token）
    _deep_tag_fail = {}    # ★ #1542 VLM 补标失败退避（30 分钟）
    _deep_tag_count = {}   # ★ #1542 VLM 补标连败计数（5 连败放弃）

    def _queue_give_up(img_id):
        """#1542 5 连败终态：⏳待深识 → 🏷识别不出，auto_tried=4 队列不再捞。"""
        try:
            cur = get(img_id) or {}
            raw = cur.get('tags') or []
            old = [str(t).strip() for t in (raw if isinstance(raw, list) else raw.split(','))]
            new = ['🏷识别不出' if t == _TAG_QUEUE_TAG else t for t in old]
            set_tags(img_id, new, merge=False)
            _update(img_id, auto_tried=4)
        except Exception as e:
            logger.debug(f'[ImgStore] 队列放弃标记跳过 id={img_id}: {e}')

    def _loop():
        _sweep_day = ''   # ★ #1307 夜扫每天最多一次
        while True:
            try:
                # ★ #1320 对话优先闸门：用户对话中，本轮打标/深析/夜扫全部让路
                from src.core import dialog_gate as _dg
                if _dg.is_busy():
                    logger.debug('[ImgStore] 对话活跃中，后台补标本轮让路')
                    time.sleep(120)
                    continue
                ensure_table()
                c = _conn()
                # ★ #1401（2026-11-02）：已归相册的图跳过自动打标——打标使命是归相册，
                # 已有归属（album 非空）还空转重试实锤（id=151 孩子学习图每2分钟失败一次）
                rows = c.execute(
                    "SELECT id FROM images_index WHERE (tags IS NULL OR tags='') "
                    "AND (album IS NULL OR album='') "
                    "AND deleted_at='' ORDER BY id DESC LIMIT 10").fetchall()
                c.close()
                rows = [r for r in rows if _fail.get(r['id'], 0) < 5]
                if rows:
                    done, failed = _tag_batch([r['id'] for r in rows])
                    for i in failed:
                        _fail[i] = _fail.get(i, 0) + 1
                    for r in rows:
                        if r['id'] not in failed:
                            _fail.pop(r['id'], None)
                    logger.info(f'[ImgStore] 后台补标 {done}/{len(rows)} 张'
                                + (f'（{len(failed)} 张失败）' if failed else ''))
                # ★ #1542（2026-11-25）VLM 补标队列：auto_tried=2（⏳待深识）的照片，
                #   每轮最多 2 张串行走 retag_deep（L3 理解环跟用户所选）；退避 30 分钟，
                #   连败 5 次放弃（如实留标记不再空转）。存量子捞回 + 新失败照片同通道。
                c = _conn()
                qrows = c.execute(
                    "SELECT id FROM images_index WHERE auto_tried=2 AND deleted_at='' "
                    "ORDER BY id ASC LIMIT 2").fetchall()
                c.close()
                now_t = time.time()
                for qr in qrows:
                    qid = qr['id']
                    ft = _deep_tag_fail.get(qid, 0)
                    if ft and now_t - ft < 1800:
                        continue
                    dtags, _dm, derr = retag_deep(qid)
                    if derr or not dtags:
                        _deep_tag_fail[qid] = now_t
                        _deep_tag_count[qid] = _deep_tag_count.get(qid, 0) + 1
                        if _deep_tag_count.get(qid, 0) >= 5:
                            # 放弃：换回 🏷标签失败 终态 + auto_tried=4（队列不再捞）
                            _queue_give_up(qid)
                            _deep_tag_count.pop(qid, None)
                            logger.info(f'[ImgStore] VLM 补标 5 连败放弃 id={qid}')
                        else:
                            logger.debug(f'[ImgStore] VLM 补标失败退避 id={qid}: {derr}')
                    else:
                        _deep_tag_fail.pop(qid, None)
                        _deep_tag_count.pop(qid, None)
                # ★ #1307（2026-10-29）深析相册：开开关的相册近 24h 无说明的图外脑补描述
                _deep_analyze_recent(_deep_fail)
                # ★ #1307 夜间扫尾（config vision.night_sweep，默认关）：02:00-05:00 每天一次，
                #   未分类残留图重试 CLIP 归位（全本地 0 token，不越权不动用户手动锁定的图）
                _now = datetime.now()
                if 2 <= _now.hour < 5 and _sweep_day != _now.strftime('%Y-%m-%d'):
                    try:
                        from src.utils.config import get_config as _gc
                        if (_gc() or {}).get('vision.night_sweep', False):
                            _sweep_day = _now.strftime('%Y-%m-%d')
                            _night_sweep()
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f'[ImgStore] 后台补标异常: {e}')
            time.sleep(120)

    t = threading.Thread(target=_loop, name='imgstore-tag-loop', daemon=True)
    t.start()
    logger.info('[ImgStore] 图库自动打标签线程已启动（跟随用户看图路由偏好，每2分钟一批10张，批内3并发）')
    logger.info('[ImgStore] VLM 补标队列已启用（#1542：快速档认不出的照片 auto_tried=2 每轮2张串行深识，跟用户所选看图模型）')


def count():
    try:
        ensure_table()
        c = _conn()
        n = c.execute("SELECT COUNT(*) FROM images_index WHERE deleted_at=''").fetchone()[0]
        c.close()
        return n
    except Exception as e:
        logger.error(f'[ImgStore] count 失败（瞬时库锁?）: {e}')
        return 0


def count_all():
    """★ 2026-10-25 #1191 图库权威统计（真 COUNT，一次连接出总数+分相册）。
    背景：管家问答曾用「跨面板各取5张累加」封顶出 13/50 假数，本函数是唯一权威口径。"""
    try:
        ensure_table()
        c = _conn()
        total = c.execute("SELECT COUNT(*) FROM images_index WHERE deleted_at=''").fetchone()[0]
        albums = [{'name': r['album'], 'count': r['n']} for r in c.execute(
            "SELECT album, COUNT(*) AS n FROM images_index WHERE deleted_at='' GROUP BY album ORDER BY n DESC")]
        c.close()
        return {'total': total, 'albums': albums}
    except Exception as e:
        logger.error(f'[ImgStore] count_all 失败（瞬时库锁?）: {e}')
        return {'total': 0, 'albums': []}
