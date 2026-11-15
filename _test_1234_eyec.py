# -*- coding: utf-8 -*-
"""★ #1234 M4-2 测试：哈文拍照链 → HarvenEye 视觉索引自动互认。

四段：
  A 离线静默 —— Eye 未启动时 register 照常成功（互认静默跳过不炸不慢）
  B 在线互认 —— 真 7001：register 新图 → Eye 库出现该图（pointer 回指哈文真家）
                → eye_search 语义找回 → pointer 反查回哈文图库 id
  C 开关关闭 —— harveneye.auto_index=False 时完全不送
  D 回归     —— 重跑 #1233 全量（29 用例）

跑法: H:\\HarvenAI\\.venv\\Scripts\\python.exe _test_1234_eyec.py
（测试图自动清理：图库条目 + 库仓文件 + Eye 侧测试条目）
"""
import io
import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ''))


from src.core import eye_client, img_store  # noqa: E402

HARVENEYE_URL = 'http://127.0.0.1:7001'
os.environ['HARVENEYE_BASE_URL'] = HARVENEYE_URL


_EYE_PATHS = []   # 本轮注册进哈文库的 abs_path（A 段 + B 段）——收尾按路径清 Eye 条目


def _eye_items():
    with urllib.request.urlopen(HARVENEYE_URL + '/items?limit=200', timeout=5) as r:
        return json.loads(r.read())['items']


def _eye_del(item_id):
    """Eye 侧没有删除 HTTP 接口——直连真家库清理（vec_rowid 正道）。

    正道（10-26 检测实锤）：items.vec_rowid → vec_items.rowid 关联删向量行，
    再删 items 行 + 缩略图文件。Eye venv 的 sqlite_vec 哈文 venv 没有，
    但 CatalogStore.conn 里 ImportError 已被静默降级，直连不崩。
    """
    try:
        import sys as _sys
        _sys.path.insert(0, r'H:\HarvenEye')
        from eye.store import CatalogStore
        s = CatalogStore()
        conn = s.conn
        row = conn.execute(
            'SELECT vec_rowid, thumb_path FROM items WHERE id=?', (item_id,)).fetchone()
        if not row:
            print(f'  ℹ️ Eye 库条目 #{item_id} 不存在（可能已被近重复去重），跳过清理')
            return
        vrow, thumb = row
        vec_orphan = False
        if vrow:
            try:
                conn.execute('DELETE FROM vec_items WHERE rowid=?', (vrow,))
            except sqlite3.OperationalError:
                # 哈文 venv 无 sqlite_vec → 连接无 vec0 模块，碰不了虚拟表。
                # 向量行留孤儿：search JOIN items 自然过滤，无害（10-26 实锤）。
                vec_orphan = True
        conn.execute('DELETE FROM items WHERE id=?', (item_id,))
        conn.commit()
        if thumb and os.path.exists(thumb):
            try:
                os.remove(thumb)
            except OSError:
                pass
        extra = '（向量行孤儿留库，JOIN 自然过滤无害）' if vec_orphan else ''
        print(f'  🧹 Eye 库测试条目 #{item_id} 已清（vec_rowid 正道）{extra}')
    except Exception as e:
        print(f'  ⚠️ Eye 库条目 #{item_id} 清理失败（降级留待手工）: {str(e)[:80]}')


def _cleanup_eye():
    """收尾按路径清 Eye 条目（A 段 + B 段全覆盖；B 段已清的幂等跳过）。
    A 段图的后台落库可能滞后（chinese-clip 嵌入耗时），轮询最多 60s。"""
    if not _EYE_PATHS:
        return
    _norm = lambda s: str(s).replace('\\', '/').lower()
    targets = {_norm(p) for p in _EYE_PATHS}

    def _matches(ptr_norm):
        return any(t in ptr_norm or ptr_norm in t for t in targets)

    for _ in range(30):
        try:
            items = _eye_items()
        except Exception:
            return   # Eye 已停/不可达，清不了就退出
        left = [it for it in items if _matches(_norm(it.get('pointer', '')))]
        if not left:
            return   # 全清干净
        for it in left:
            _eye_del(it['id'])
        time.sleep(2)


def _tiny_jpg_bytes(color=(90, 140, 200)):
    """带随机纹理的测试图——纯色图 dhash 无区分度，会被 Eye 近重复去重（实测踩坑）。
    种子掺时间戳：Eye 服务进程有 dhash 内存缓存，固定种子二次跑会被判近重复拒入库（实测踩坑2）。"""
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(f'{color}-{time.time()}-{os.getpid()}')
    img = Image.new('RGB', (320, 240), color)
    d = ImageDraw.Draw(img)
    for _ in range(120):
        x, y = rnd.randint(0, 310), rnd.randint(0, 230)
        c = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
        d.rectangle([x, y, x + rnd.randint(6, 40), y + rnd.randint(6, 40)], fill=c)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


_cleanup_ids = []


def _register_test_img(note, color=(90, 140, 200)):
    r = img_store.register(image_data=_tiny_jpg_bytes(color), source='测试', note=note,
                           tags=['测试M4'], auto_tag=False)
    assert r and r.get('id'), f'register 失败: {r}'
    _cleanup_ids.append(r['id'])
    return r


def _cleanup_gallery():
    """清理哈文图库测试条目 + 库仓文件（真家不留测试垃圾）。"""
    from src.utils.config import get_config
    root = get_config().root_dir
    for iid in _cleanup_ids:
        c = img_store._conn()
        row = c.execute('SELECT path FROM images_index WHERE id=?', (iid,)).fetchone()
        c.execute('DELETE FROM images_index WHERE id=?', (iid,))
        c.commit()
        c.close()
        if row:
            p = os.path.join(root, row['path'])
            if os.path.isfile(p):
                os.remove(p)
    print(f'  🧹 已清理哈文图库测试条目: {_cleanup_ids}')


# ═══════════ A 离线静默 ═══════════
print("\n══ A 段 Eye 离线静默 ══════════")
alive, _ = eye_client.is_eye_alive()
if alive:
    print('  （7001 当前在线——A 段仍会跑，连接成功也不影响断言）')
t0 = time.time()
r = _register_test_img('M4-2 离线静默测试', color=(90, 140, 200))
_EYE_PATHS.append(eye_client.resolve_image_path(str(r['id'])))
dt = time.time() - t0
check('A1 Eye 离线时 register 正常成功', r.get('duplicate') is False)
check('A2 register 不因互认变慢（<2s）', dt < 2.0, f'{dt:.2f}s')
time.sleep(1.5)  # 给后台线程机会炸（若实现错误）

# ═══════════ B 在线互认 ═══════════
print("\n══ B 段 在线互认（真 7001）══════════")
alive, ver = eye_client.is_eye_alive()
if not alive:
    print(f'  ⏭️ 7001 未启动，跳过 B 段——start_eye.bat 可起后重跑')
else:
    print(f'  {ver}')
    r = _register_test_img('M4-2 在线互认测试', color=(200, 90, 140))   # 换色防 sha 去重
    abs_path = eye_client.resolve_image_path(str(r['id']))
    _EYE_PATHS.append(abs_path)
    check('B1 新图登记成功', bool(abs_path), abs_path)
    _norm = lambda s: str(s).replace('\\', '/').lower()
    # 等 fire-and-forget 落库（chinese-clip 嵌入有耗时）；按路径比对（库仓文件名是 sha256 非图库 id）
    hit, item = False, None
    for _ in range(30):
        time.sleep(2)
        try:
            items = _eye_items()
        except Exception:
            continue
        for it in items:
            if _norm(abs_path) in _norm(it.get('pointer', '')) or _norm(it.get('pointer', '')) in _norm(abs_path):
                hit, item = True, it
                break
        if hit:
            break
    check('B2 Eye 库出现该图（pointer 回指哈文真家）', hit,
          json.dumps(item, ensure_ascii=False)[:120] if item else '60s 内未出现')
    if hit:
        check('B3 pointer 即哈文库仓绝对路径',
              'data' in str(item.get('pointer', '')) and str(item.get('pointer')).lower().replace('\\', '/').endswith('.jpg'))
        rs = eye_client.eye_search_tool(query='纯色测试图', top_k=5)
        found = [x for x in (rs.get('keyword', []) + rs.get('semantic', []))
                 if x.get('url') == f"/api/images/file/{r['id']}"]
        check('B4 eye_search 语义找回 + url 反查回哈文图库', bool(found),
              f"kw={len(rs.get('keyword', []))} se={len(rs.get('semantic', []))}")
        _eye_del(item['id'])

# ═══════════ C 开关关闭 ═══════════
print("\n══ C 段 auto_index 开关 ══════════")
eye_client.auto_index_enabled.__globals__['_CFG_OFF'] = True
# 直接用 monkeypatch 方式验证开关路径
_orig = eye_client.auto_index_enabled
eye_client.auto_index_enabled = lambda: False
_sent = {'n': 0}
_orig_job = eye_client._session.post
def _count_post(*a, **kw):
    _sent['n'] += 1
    return _orig_job(*a, **kw)
eye_client._session.post = _count_post
eye_client.notify_index(__file__)
time.sleep(0.5)
check('C1 开关关时完全不发请求', _sent['n'] == 0)
eye_client.auto_index_enabled = _orig
eye_client._session.post = _orig_job
check('C2 开关开时正常发送路径', eye_client.auto_index_enabled() is True)

# ═══════════ D 回归 #1233 ═══════════
print("\n══ D 段 回归 #1233 全量 ══════════")
import subprocess
proc = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), '_test_1233_eyec.py')],
                      capture_output=True, text=True, timeout=300)
tail = (proc.stdout or '').strip().splitlines()[-2:] if proc.stdout else [str(proc.stderr)[-300:]]
check('D1 #1233 回归全绿', proc.returncode == 0, ' | '.join(tail))

# ═══════════ 收尾 ═══════════
_cleanup_gallery()
_cleanup_eye()
total = len(PASS) + len(FAIL)
print(f"\n════════ #1234 测试收口: {total - len(FAIL)}/{total} 通过 ════════")
if FAIL:
    print('失败项: ' + ', '.join(FAIL))
    sys.exit(1)
print('ALL GREEN ✅')
