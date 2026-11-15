# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""★ P0-A 2026-09-27 通用图片附件库（已批准）

痛点（09-27 实锤）：用户在主聊天发京东截图说"保存并记账"——
识别成功、账也记了，但截图本体哪里都没存，事后问"图片呢"必然落空。

本模块提供统一附件体系：
- 表 panel_images：panel_id + record_id 关联 + 落盘文件名 + 备注
- 图片落盘 data/media/panel_images/YYYYMM/（PIL 压缩存档 + 缩略图）
- 主聊天默录链发图自动存图并关联新插入的记录
- 查询链给 AI 报"该面板/记录有图"，AI 不再绕文件系统瞎找
"""
import base64
import io
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / 'data' / 'sqlite' / 'lingzhi.db'
IMG_ROOT = BASE_DIR / 'data' / 'media' / 'panel_images'


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def ensure_table(conn=None):
    """建附件表（幂等）；★ r30 补 tag/quality 列 + 旧库自动迁移"""
    own = conn is None
    if own:
        conn = _conn()
    conn.execute('''CREATE TABLE IF NOT EXISTS panel_images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        panel_id TEXT NOT NULL,
        record_id INTEGER,
        filename TEXT NOT NULL UNIQUE,
        note TEXT DEFAULT '',
        member TEXT DEFAULT '',
        tag TEXT DEFAULT '',
        quality TEXT DEFAULT '',
        created_at TEXT)''')
    # ★ 旧库迁移：已存在的表缺 tag/quality 列则补上（ALTER 幂等，先查再加）
    cols = {r[1] for r in conn.execute('PRAGMA table_info(panel_images)').fetchall()}
    if 'tag' not in cols:
        conn.execute("ALTER TABLE panel_images ADD COLUMN tag TEXT DEFAULT ''")
        logger.info('[PanelImages] 旧库迁移：补 tag 列')
    if 'quality' not in cols:
        conn.execute("ALTER TABLE panel_images ADD COLUMN quality TEXT DEFAULT ''")
        logger.info('[PanelImages] 旧库迁移：补 quality 列')
    # 索引必须在迁移之后建（旧库先补列才索引得了 tag）
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_pimg_panel
        ON panel_images(panel_id, record_id)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_pimg_tag
        ON panel_images(tag, created_at)''')
    if own:
        conn.commit()
        conn.close()


def save_image(panel_id, image_data, record_id=None, note='', member='', tag='', quality='', original=False):
    """保存一张图片并登记。image_data 支持 base64 字符串（可带 data:image 前缀）或 bytes。
    ★ r30 新增：tag=用途标签（fridge/account/album/learn/…），quality=hd(高清原图)/compressed(已压缩)。
    ★ A4（2026-10-11 照片链）：original=True 存真原图（主图不缩放、高质量），
      原图补传替换链用——手机回家 WiFi 后用原图升级压缩版，相册存档不再受损。
    返回 {'id','filename','url'}，失败返回 None。"""
    try:
        ensure_table()
        if isinstance(image_data, str):
            if ',' in image_data and image_data.strip().startswith('data:'):
                image_data = image_data.split(',', 1)[1]
            raw = base64.b64decode(image_data)
        else:
            raw = bytes(image_data)
        if not raw:
            return None

        # PIL 压缩存档（最长边 1600，JPEG q82）+ 缩略图（320）
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        base_name = uuid.uuid4().hex
        sub = IMG_ROOT / datetime.now().strftime('%Y%m')
        sub.mkdir(parents=True, exist_ok=True)
        # ★ A4：original=True 主图不缩放高质量存档；普通存档维持 1600/q82 + 320 缩略图
        variants = ([(None, 95, '.jpg')] if original else [(1600, 82, '.jpg')]) + [(320, 70, '_thumb.jpg')]
        for max_side, jpg_q, suffix in variants:
            im = img.copy()
            if max_side:
                im.thumbnail((max_side, max_side))
            im.save(sub / (base_name + suffix), 'JPEG', quality=jpg_q)

        main_name = base_name + '.jpg'
        main_path = sub / main_name
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c = _conn()
        try:
            cur = c.execute(
                'INSERT INTO panel_images(panel_id, record_id, filename, note, member, tag, quality, created_at)'
                ' VALUES (?,?,?,?,?,?,?,?)',
                (panel_id, record_id, main_name, str(note or '')[:200], member or '',
                 str(tag or '')[:40], str(quality or '')[:20], now))
            c.commit()
            new_id = cur.lastrowid
        finally:
            c.close()
        logger.info(f'[PanelImages] 存图 {panel_id}#{record_id} -> {main_name} ({len(raw)//1024}KB)')
        # ★ #916 单点接入统一图库（2026-09-27）：任何存图路径（表单/默录链/确认卡/看图弹层）
        #   都自动进 images_index 总账；sha 按原始字节算（与聊天入口同图同 sha → 去重合并），
        #   本体原地登记不复制；失败绝不影响存图主流程
        try:
            from src.core import img_store
            img_store.register(image_data=raw, src_file=main_path, source='panel',
                               panel_id=panel_id, record_id=record_id,
                               note=str(note or '')[:200])
        except Exception as _ie:
            logger.debug(f'[PanelImages] 图库登记跳过: {_ie}')
        return {'id': new_id, 'filename': main_name,
                'url': f'/api/panels/image/{main_name}'}
    except Exception as e:
        logger.error(f'[PanelImages] 存图失败: {e}')
        return None


def replace_with_original(record_id=None, image_data='', panel_id='', image_id=0):
    """★ A4 原图补传替换（2026-10-11 照片链）：手机回家 WiFi 后用原图升级已存的压缩版。
    查找锚点二选一：record_id（表单/默录链带记录的图）或 image_id（手机拍照入库的图行 id，
    ★ C2 补：手机确认卡照片 record_id 为 NULL，只能按图行 id 找）；panel_id 可选防误替。
    → 删旧登记与落盘文件 → 用原图重新落盘（original=True 真原图存档）
      并继承旧图的 note/member/tag/record_id。
    返回新图 dict（save_image 结构），找不到旧图/失败返回 None。"""
    try:
        ensure_table()
        c = _conn()
        if image_id:
            sql = ('SELECT id, panel_id, record_id, filename, note, member, tag'
                   ' FROM panel_images WHERE id=?')
            params = [int(image_id)]
        else:
            sql = ('SELECT id, panel_id, record_id, filename, note, member, tag'
                   ' FROM panel_images WHERE record_id=?')
            params = [record_id]
        if panel_id:
            sql += ' AND panel_id=?'
            params.append(panel_id)
        rows = c.execute(sql, params).fetchall()
        c.close()
        if not rows:
            return None
        old = rows[0]
        for r in rows:
            delete_image(r['filename'])
        new = save_image(old['panel_id'], image_data, record_id=old['record_id'],
                         note=(old['note'] or '') + '（原图补传）', member=old['member'],
                         tag=old['tag'], quality='hd', original=True)
        if new:
            logger.info(f'[PanelImages] 原图补传替换成功 anchor={"img" if image_id else "rec"}'
                        f'={image_id or record_id} -> {new["filename"]}')
        return new
    except Exception as e:
        logger.error(f'[PanelImages] 原图补传失败: {e}')
        return None


def _find(fname):
    """按文件名定位图片路径（原图或缩略图同名规则，含月份子目录扫描）"""
    if not fname or '/' in fname or '\\' in fname or '..' in fname:
        return None
    if IMG_ROOT.is_dir():
        for m in sorted(IMG_ROOT.iterdir(), reverse=True):
            p = m / fname
            if p.is_file():
                return p
    return None


def get_image_path(fname, thumb=False):
    """取图片/缩略图路径；找不到返回 None"""
    if thumb and not fname.endswith('_thumb.jpg'):
        fname = fname.replace('.jpg', '_thumb.jpg')
    return _find(fname)


def search_images(tag=None, quality=None, panel_id=None, limit=100):
    """★ r30 按用途/画质搜图（相册查找分类用）。
    tag: 'fridge'/'account'/'album'/'learn' 等；quality: 'hd'/'compressed'。
    条件之间 AND，都为 None 时返回全库最新图。返回列表（同 list_images 结构）。"""
    try:
        ensure_table()
        c = _conn()
        sql = ('SELECT id, panel_id, record_id, filename, note, member, tag, quality, created_at'
               ' FROM panel_images WHERE 1=1')
        params = []
        if tag:
            sql += ' AND tag=?'
            params.append(tag)
        if quality:
            sql += ' AND quality=?'
            params.append(quality)
        if panel_id:
            sql += ' AND panel_id=?'
            params.append(panel_id)
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(limit)
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
        c.close()
        for r in rows:
            r['url'] = f'/api/panels/image/{r["filename"]}'
            r['thumb_url'] = f'/api/panels/image/{r["filename"]}?thumb=1'
        return rows
    except Exception as e:
        logger.debug(f'[PanelImages] 搜图失败: {e}')
        return []


def tag_counts(limit_days=180):
    """★ r30 各用途标签的图片数（照片库分类角标/汇总用，默认只看近 180 天）"""
    try:
        ensure_table()
        c = _conn()
        rows = c.execute(
            "SELECT COALESCE(tag,'') AS t, COUNT(*) AS n FROM panel_images"
            " WHERE created_at >= datetime('now', 'localtime', ?)"
            " GROUP BY t ORDER BY n DESC", (f'-{limit_days} days',)).fetchall()
        c.close()
        return {r['t'] or '未分类': r['n'] for r in rows}
    except Exception as e:
        logger.debug(f'[PanelImages] 标签统计失败: {e}')
        return {}


def list_images(panel_id, record_id=None, limit=50):
    """列某面板/记录的图片"""
    try:
        ensure_table()
        c = _conn()
        sql = 'SELECT id, record_id, filename, note, member, tag, quality, created_at FROM panel_images WHERE panel_id=?'
        params = [panel_id]
        if record_id is not None:
            sql += ' AND record_id=?'
            params.append(record_id)
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(limit)
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
        c.close()
        for r in rows:
            r['url'] = f'/api/panels/image/{r["filename"]}'
            r['thumb_url'] = f'/api/panels/image/{r["filename"]}?thumb=1'
        return rows
    except Exception as e:
        logger.debug(f'[PanelImages] 列图失败: {e}')
        return []


def list_unlinked(panel_id, limit=50):
    """★ #914 未关联图池：列某面板下还没挂到任何记录的图（record_id IS NULL）。
    来源：聊天发图但没记成、待确认场景先挂面板等。看图弹层里可一键关联。"""
    try:
        ensure_table()
        c = _conn()
        rows = [dict(r) for r in c.execute(
            'SELECT id, filename, note, member, tag, quality, created_at FROM panel_images'
            ' WHERE panel_id=? AND record_id IS NULL ORDER BY id DESC LIMIT ?',
            (panel_id, limit)).fetchall()]
        c.close()
        for r in rows:
            r['url'] = f'/api/panels/image/{r["filename"]}'
            r['thumb_url'] = f'/api/panels/image/{r["filename"]}?thumb=1'
        return rows
    except Exception as e:
        logger.debug(f'[PanelImages] 列未关联图失败: {e}')
        return []


def delete_image(filename):
    """删一张附件图（登记行 + 原图/缩略图落盘文件）。看图弹层的 ✕ 用。"""
    try:
        if not filename or '/' in filename or '\\' in filename or '..' in filename:
            return False
        ensure_table()
        c = _conn()
        c.execute('DELETE FROM panel_images WHERE filename=?', (filename,))
        c.commit()
        c.close()
        # ★ #916 同步删统一图库登记行（本体文件仍由下方逻辑删除）
        try:
            from src.core import img_store
            c2 = img_store._conn()
            row = c2.execute("SELECT id FROM images_index WHERE path LIKE ?", (
                f'%data/media/panel_images/%{filename}',)).fetchone()
            c2.close()
            if row:
                img_store.remove(row['id'])
        except Exception:
            pass
        for suffix in ('.jpg', '_thumb.jpg'):
            p = _find(filename.replace('.jpg', '') + suffix)
            if p:
                try:
                    p.unlink()
                except OSError:
                    pass
        return True
    except Exception as e:
        logger.debug(f'[PanelImages] 删图失败: {e}')
        return False


def attach_to_record(filename, record_id):
    """把已存的图关联到记录（确认卡确认后补挂用）"""
    try:
        ensure_table()
        c = _conn()
        c.execute('UPDATE panel_images SET record_id=? WHERE filename=?', (record_id, filename))
        c.commit()
        c.close()
        # ★ #916 同步统一图库的 record_id（按登记 path 尾段匹配）
        try:
            from src.core import img_store
            c2 = img_store._conn()
            row = c2.execute("SELECT id FROM images_index WHERE path LIKE ?", (
                f'%data/media/panel_images/%{filename}',)).fetchone()
            c2.close()
            if row:
                img_store._update(row['id'], record_id=record_id)  # 只补 record_id，panel_id 不动
        except Exception:
            pass
        return True
    except Exception as e:
        logger.debug(f'[PanelImages] 关联失败: {e}')
        return False


def image_summary(panel_id, record_id=None):
    """给 AI 看的一句话图片概况（无图返回 ''）"""
    imgs = list_images(panel_id, record_id, limit=20)
    if not imgs:
        return ''
    if record_id is not None:
        return f'（本条记录有 {len(imgs)} 张图片：{"; ".join(i["note"] or i["created_at"] for i in imgs[:3])}）'
    return f'（该面板共有 {len(imgs)} 张关联图片）'
