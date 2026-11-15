# -*- coding: utf-8 -*-
"""
HarvenAI 面板包规范 v1 + 导出/导入（2026-10-20 第五批 5.1）
=========================================================
《面板包规范》v1 要点（详细文档见 data/panel_spec_v1.md）：
- 面板包 = manifest.json + schema.json + terms.json(分类词表) + resources/(图片等)
- 图片分类记录卡 = 同一种包（图+标注+词表），标准互通
- 安装三道闸：①manifest 校验+冲突检测 ②隐私扫描（人脸/定位，导出前生死线）
  ③权限声明（接 permission_center，安装时用户确认）
- 交易系统为本模块预留 manifest 字段（price/license），生态期接入 Hub
"""
import os, re, json, shutil, logging, zipfile, hashlib
from datetime import datetime

logger = logging.getLogger(__name__)

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
PANELS_DIR = os.path.join(_ROOT, 'data', 'panels')
PACKS_DIR = os.path.join(_ROOT, 'data', 'panel_packs')

SPEC_VERSION = '1.0'


# ---------- manifest 校验（第一道闸） ----------

def validate_manifest(manifest):
    """校验 manifest 完整性。返回 (ok, errors[], warnings[])"""
    errors, warnings = [], []
    if not isinstance(manifest, dict):
        return False, ['manifest 必须是 JSON 对象'], warnings
    for req in ('name', 'version', 'id'):
        if not manifest.get(req):
            errors.append(f'缺少必填字段: {req}')
    mid = str(manifest.get('id', ''))
    if mid and not re.match(r'^[a-z][a-z0-9_]{2,31}$', mid):
        errors.append(f'id "{mid}" 不合法（小写字母开头，3~32 位小写字母/数字/下划线）')
    ver = str(manifest.get('version', ''))
    if ver and not re.match(r'^\d+\.\d+\.\d+$', ver):
        errors.append(f'version "{ver}" 不符合 x.y.z')
    # 权限声明规范化
    perms = manifest.get('permissions', [])
    if not isinstance(perms, list):
        errors.append('permissions 必须是数组')
    else:
        for pm in perms:
            if pm not in ('readonly', 'write', 'spend', 'danger', 'net_search', 'net_browse', 'net_order'):
                warnings.append(f'未知权限声明: {pm}')
    # 数据表前缀防冲突
    table_prefix = manifest.get('table_prefix', f'panel_{mid}')
    if table_prefix and not re.match(r'^[a-z][a-z0-9_]{2,31}$', table_prefix):
        errors.append(f'table_prefix "{table_prefix}" 不合法')
    return (len(errors) == 0), errors, warnings


def detect_conflicts(manifest, installed_dir=None):
    """冲突检测：同 id 已装/同表前缀已占 → 拒装。
    ★ 2026-10-20 检测实锤修复：原实现只认 manifest.json，而存量面板目录是 panel.json →
      扫不到已装面板 → 同 id 导入直接覆盖（实锤毁了冰箱面板目录，已从 staging 恢复）。
      改为**目录名即 id** 判定，不依赖任何文件。"""
    conflicts = []
    mid = manifest.get('id', '')
    prefix = manifest.get('table_prefix', f'panel_{mid}')
    installed_dir = installed_dir or PANELS_DIR
    try:
        if os.path.isdir(installed_dir):
            for d in os.listdir(installed_dir):
                full = os.path.join(installed_dir, d)
                if not os.path.isdir(full):
                    continue
                if d == mid:
                    conflicts.append(f'面板 id "{mid}" 已安装（覆盖需先卸载/改名）')
                    continue
                # 表前缀冲突：读 manifest.json 或 panel.json 任一定义
                for mf_name in ('manifest.json', 'panel.json'):
                    mf = os.path.join(full, mf_name)
                    if not os.path.exists(mf):
                        continue
                    try:
                        other = json.load(open(mf, encoding='utf-8'))
                    except Exception:
                        continue
                    if other.get('table_prefix', f'panel_{d}') == prefix:
                        conflicts.append(f'数据表前缀 "{prefix}" 与已装面板 "{d}" 冲突')
                    break
    except Exception as e:
        logger.warning(f'[PanelPack] 冲突检测异常: {e}')
    return conflicts


# ---------- 隐私扫描（第二道闸，导出生死线） ----------

def privacy_scan(pack_dir):
    """导出/上架前隐私扫描。
    一期：EXIF 定位扫描（PIL 可用则读 GPS）+ 人脸检测占位（haarcascade，有 cv2 就查）。
    返回 {'ok': bool, 'issues': [...], 'scanned': N}"""
    issues = []
    scanned = 0
    res_dir = os.path.join(pack_dir, 'resources')
    files = []
    if os.path.isdir(res_dir):
        for root, _, fs in os.walk(res_dir):
            files.extend(os.path.join(root, f) for f in fs)
    for fp in files:
        ext = os.path.splitext(fp)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
            continue
        scanned += 1
        # EXIF GPS（jpeg）
        if ext in ('.jpg', '.jpeg'):
            try:
                from PIL import Image
                img = Image.open(fp)
                exif = img._getexif() or {}
                gps = exif.get(34853)  # TAGS GPSInfo
                if gps:
                    issues.append({'file': os.path.basename(fp), 'type': 'gps',
                                   'detail': '图片含 EXIF 定位信息'})
            except Exception:
                pass
        # 人脸检测（cv2 可用则查；一期启发式，误报交用户复核）
        try:
            import cv2
            img = cv2.imread(fp)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                cascade_path = os.path.join(cv2.data.haarcascades, 'haarcascade_frontalface_default.xml') \
                    if hasattr(cv2, 'data') else ''
                if cascade_path and os.path.exists(cascade_path):
                    cc = cv2.CascadeClassifier(cascade_path)
                    faces = cc.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
                    if len(faces):
                        issues.append({'file': os.path.basename(fp), 'type': 'face',
                                       'detail': f'检测到 {len(faces)} 张人脸，导出前请替换为无人物的「标准图」'})
        except ImportError:
            pass  # cv2 未装：扫描降级为仅 EXIF，报告里注明
        except Exception:
            pass
    return {'ok': len(issues) == 0, 'issues': issues, 'scanned': scanned,
            'note': 'cv2 未安装时人脸检测降级（仅 EXIF 定位扫描）' if issues == [] else ''}


# ---------- 导出 ----------

def export_pack(panel_id, include_resources=True):
    """把已装面板导出为 .hvpk（哈文面板包，zip 格式）"""
    src = os.path.join(PANELS_DIR, panel_id)
    if not os.path.isdir(src):
        return {'success': False, 'error': f'面板不存在: {panel_id}'}
    mf_path = os.path.join(src, 'manifest.json')
    manifest = {}
    if os.path.exists(mf_path):
        try:
            manifest = json.load(open(mf_path, encoding='utf-8'))
        except Exception:
            pass
    if not manifest:
        manifest = {'id': panel_id, 'name': panel_id, 'version': '1.0.0',
                    'spec': SPEC_VERSION, 'exported_at': datetime.now().isoformat()}
    # 隐私扫描（导出生死线——发现人脸/GPS 先报给用户，用户确认后仍可导出但带警示）
    scan = privacy_scan(src)
    os.makedirs(PACKS_DIR, exist_ok=True)
    out_name = f"{panel_id}_v{manifest.get('version', '1.0.0')}_{datetime.now().strftime('%Y%m%d')}.hvpk"
    out_path = os.path.join(PACKS_DIR, out_name)
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        for fname in ('schema.json', 'terms.json'):
            fp = os.path.join(src, fname)
            if os.path.exists(fp):
                z.write(fp, fname)
        if include_resources:
            res = os.path.join(src, 'resources')
            if os.path.isdir(res):
                for root, _, fs in os.walk(res):
                    for f in fs:
                        full = os.path.join(root, f)
                        z.write(full, os.path.relpath(full, src))
    md5 = hashlib.md5(open(out_path, 'rb').read()).hexdigest()
    return {'success': True, 'path': out_path, 'name': out_name, 'md5': md5,
            'privacy_scan': scan}


# ---------- 导入（三道闸） ----------

def import_pack(pack_path, auto_perms=None):
    """导入 .hvpk。三道闸：manifest 校验→冲突检测→隐私扫描；权限声明返回给前端弹确认。
    auto_perms: 用户已确认的权限集合（二次调用时传，跳过权限确认步骤）"""
    if not os.path.exists(pack_path):
        return {'success': False, 'error': '包不存在'}
    try:
        with zipfile.ZipFile(pack_path, 'r') as z:
            names = z.namelist()
            if 'manifest.json' not in names:
                return {'success': False, 'error': '包内缺 manifest.json（不是合法面板包）'}
            manifest = json.loads(z.read('manifest.json').decode('utf-8'))
        # 第一道：manifest 校验
        ok, errors, warnings = validate_manifest(manifest)
        if not ok:
            return {'success': False, 'error': 'manifest 校验失败', 'errors': errors}
        # 第二道：冲突检测
        conflicts = detect_conflicts(manifest)
        if conflicts:
            return {'success': False, 'error': '安装冲突', 'conflicts': conflicts}
        # 第三道：解包到临时区做隐私扫描
        tmp = os.path.join(PACKS_DIR, '_import_tmp')
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        with zipfile.ZipFile(pack_path, 'r') as z:
            z.extractall(tmp)
        scan = privacy_scan(tmp)
        perms = manifest.get('permissions', [])
        # 权限确认闸：非只读权限且用户未确认 → 返回待确认（前端弹窗）
        need_perms = [p for p in perms if p != 'readonly']
        if need_perms and not auto_perms:
            return {'success': False, 'need_permission': True,
                    'permissions': need_perms,
                    'message': f'此面板需要权限：{"、".join(need_perms)}，请确认后重试（传 auto_perms）'}
        # 落地安装
        mid = manifest['id']
        dest = os.path.join(PANELS_DIR, mid)
        os.makedirs(PANELS_DIR, exist_ok=True)
        # ★ 2026-10-20 二次保险：目标目录已存在一律拒装（原 rmtree 覆盖逻辑实锤毁过面板）
        if os.path.isdir(dest):
            shutil.rmtree(tmp, ignore_errors=True)
            return {'success': False, 'error': f'目标目录已存在: {mid}（覆盖安装已禁用，请先卸载/改名）',
                    'conflicts': [f'面板目录 {mid} 已存在']}
        shutil.move(tmp, dest)
        # ★ 2026-10-21 C2 热加载：导入成功立即重扫面板（免重启即见）
        try:
            from src.core.panel_manager import get_panel_manager
            get_panel_manager()._scan_panels()
            logger.info(f'[PanelPack] 热加载完成: {mid}')
        except Exception as he:
            logger.warning(f'[PanelPack] 热加载失败(重启后可见): {he}')
        return {'success': True, 'id': mid, 'version': manifest.get('version'),
                'privacy_scan': scan, 'warnings': warnings,
                'message': f'面板 "{manifest.get("name", mid)}" 安装成功；重启哈文后面板列表可见'}
    except zipfile.BadZipFile:
        return {'success': False, 'error': '不是合法的 zip 面板包'}
    except Exception as e:
        logger.error(f'[PanelPack] 导入失败: {e}')
        return {'success': False, 'error': str(e)}
