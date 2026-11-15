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

# ★ 分组映射（data/panels/_groups.json 唯一数据源，电脑/手机两端共用）
_groups_cache = {'mtime': None, 'map': {}}

def _groups_map():
    """读 _groups.json → {面板id: 组名}；文件缺失/改动自动刷新。无组面板前端归「常用」。"""
    try:
        f = PANELS_DIR / '_groups.json'
        if not f.exists():
            return {}
        mt = f.stat().st_mtime
        if _groups_cache['mtime'] != mt:
            data = json.loads(f.read_text(encoding='utf-8'))
            m = {}
            for gname, ids in data.items():
                for pid in ids:
                    m[pid] = gname
            _groups_cache['mtime'] = mt
            _groups_cache['map'] = m
        return _groups_cache['map']
    except Exception as e:
        logger.warning(f'[_groups] 读分组失败: {e}')
        return {}


def _get_manager():
    """延迟导入，避免循环依赖"""
    from src.core.panel_manager import get_panel_manager
    return get_panel_manager()


# ══════════════════════════ 面板管理API ══════════════════════════

@bp.route('', methods=['GET'])
def list_panels():
    """获取已安装面板列表
    ★ 2026-09-19 hidden 面板（中枢页子模块）不进常规列表，避免卡墙重复露出；
    面板中心 /manage 仍返回全部，方便单独管理数据。"""
    mgr = _get_manager()
    panels = mgr.get_all_panels()
    panels = [p for p in panels if not p.get('hidden')]
    gm = _groups_map()
    for p in panels:
        p['group'] = gm.get(p.get('id'), '')
    return jsonify({'success': True, 'panels': panels})


@bp.route('/manage', methods=['GET'])
def manage_panels():
    """★ 2026-09-16 管理页专用：面板列表 + 数据条数 + 保护级别"""
    mgr = _get_manager()
    panels = mgr.get_all_panels()
    counts = mgr.get_record_counts()
    for p in panels:
        p['record_count'] = counts.get(p.get('id', ''), 0)
        p['protection'] = mgr.get_protection(p.get('id', ''))
    return jsonify({'success': True, 'panels': panels})


@bp.route('/reorder', methods=['POST'])
def reorder_panels():
    """★ 2026-09-18 拖拽排序持久化：{order:[面板id,...]}
    全局一个顺序源——面板列表页/分类空间页/右栏停靠迷你窗共用。"""
    data = request.get_json(silent=True) or {}
    mgr = _get_manager()
    result = mgr.set_panel_order(data.get('order') or [])
    return jsonify(result), (200 if result.get('success') else 400)


# ★ 2026-10-20 模块小组（分组）顺序拖拽：_group_order.json 持久化
_GROUP_ORDER_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'panels', '_group_order.json')


def _read_group_order():
    try:
        with open(_GROUP_ORDER_FILE, 'r', encoding='utf-8') as f:
            return json.load(f) or []
    except Exception:
        return []


@bp.route('/groups/order', methods=['GET'])
def get_group_order():
    """返用户自定义组顺序（空数组=用前端默认顺序）"""
    return jsonify({'success': True, 'order': _read_group_order()})


@bp.route('/groups/reorder', methods=['POST'])
def reorder_groups():
    """{order:['组名',...]} — 模块小组顺序拖拽持久化"""
    data = request.get_json(silent=True) or {}
    order = [str(x).strip() for x in (data.get('order') or []) if str(x).strip()]
    if not order:
        return jsonify({'success': False, 'message': '空顺序'}), 400
    try:
        os.makedirs(os.path.dirname(_GROUP_ORDER_FILE), exist_ok=True)
        with open(_GROUP_ORDER_FILE, 'w', encoding='utf-8') as f:
            json.dump(order, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True, 'order': order})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ★ 九大正式组（与 SPA SpacePage/PanelsPage 的 GROUP_ORDER 保持一致）
VALID_GROUPS = ['日程待办', '家庭账本', '食物存放', '健康守护', '还在成长', '老人关怀', '车辆出行', '家务管家', '生活记录', '公司事务', '创作空间']


@bp.route('/<panel_id>/context', methods=['GET'])
def api_panel_context(panel_id):
    """★ 2026-10-20 面板上下文（「和哈文聊目标」用）：面板名+字段+近7天记录摘要+挂接目标。
    让 AI 拿到数据再给建议——这是「AI 出饮食/运动建议」的前提。"""
    try:
        mgr = _get_manager()
        panel = next((p for p in mgr.get_all_panels() if p.get('id') == panel_id), None)
        if not panel:
            return jsonify({'success': False, 'message': '面板不存在'}), 404
        fields = [f.get('name') for f in (panel.get('fields') or []) if isinstance(f, dict)]
        rows = mgr.query_panel_data(panel_id, None, 20, 0) or []
        # 近7天记录数（按 created_at）
        from datetime import datetime, timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        recent7 = [r for r in rows if str(r.get('created_at') or '')[:10] >= week_ago]
        today = datetime.now().strftime('%Y-%m-%d')
        today_rows = [r for r in rows if str(r.get('created_at') or '')[:10] == today]
        # 摘要：取每条记录前两个有值字段
        brief = []
        for r in recent7[:8]:
            parts = []
            for k, v in r.items():
                if k in ('id', 'created_at', 'updated_at', 'kind', 'member', 'source') or v in ('', None):
                    continue
                parts.append(f'{k}={v}')
                if len(parts) >= 2:
                    break
            if parts:
                brief.append('，'.join(parts))
        goals = []
        try:
            from src.core.goal_manager import list_goals, summary_card
            goals = [summary_card(g) for g in list_goals(status='active', panel_id=panel_id)]
        except Exception:
            pass
        return jsonify({'success': True, 'panel_id': panel_id,
                        'name': panel.get('name', panel_id), 'fields': fields[:12],
                        'record_total': len(rows), 'recent7': len(recent7), 'today': len(today_rows),
                        'brief': brief, 'goals': goals})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/<panel_id>/group', methods=['POST'])
def set_panel_group(panel_id):
    """★ 2026-10-13 (#1120) 拖拽换组持久化：{group:'组名'} 或 {group:''}（移回常用）。
    数据源仍是 data/panels/_groups.json（唯一账本），写入后失效缓存立即生效，电脑/手机两端共用。"""
    data = request.get_json(silent=True) or {}
    group = (data.get('group') or '').strip()
    if group and group not in VALID_GROUPS:
        return jsonify({'success': False, 'message': f'未知分组：{group}'}), 400
    mgr = _get_manager()
    if not any(p.get('id') == panel_id for p in mgr.get_all_panels()):
        return jsonify({'success': False, 'message': '面板不存在'}), 404
    f = PANELS_DIR / '_groups.json'
    try:
        gdata = json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}
    except Exception as e:
        logger.warning(f'[_groups] 换组读取失败，重建: {e}')
        gdata = {}
    # 先从所有组摘除（防历史脏数据一卡多组）
    for gname in list(gdata.keys()):
        if isinstance(gdata[gname], list) and panel_id in gdata[gname]:
            gdata[gname].remove(panel_id)
    if group:
        gdata.setdefault(group, [])
        if panel_id not in gdata[group]:
            gdata[group].append(panel_id)
    try:
        f.write_text(json.dumps(gdata, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        logger.error(f'[_groups] 换组写入失败: {e}')
        return jsonify({'success': False, 'message': '写入分组账本失败'}), 500
    _groups_cache['mtime'] = None  # 失效缓存，下次读取自动刷新
    return jsonify({'success': True, 'group': group})


@bp.route('/<panel_id>/protection', methods=['POST'])
def set_protection(panel_id):
    """★ 2026-09-16 设置面板保护级别：normal/protected/locked"""
    data = request.get_json(silent=True) or {}
    mgr = _get_manager()
    result = mgr.set_protection(panel_id, data.get('level', 'normal'))
    if result['success']:
        return jsonify(result)
    return jsonify(result), 400


@bp.route('/<panel_id>', methods=['DELETE'])
def uninstall_panel(panel_id):
    """卸载面板（locked 级需 body 带管理员密码 password）"""
    data = request.get_json(silent=True) or {}
    mgr = _get_manager()
    result = mgr.uninstall_panel(panel_id, password=data.get('password', ''))
    if result['success']:
        return jsonify(result)
    return jsonify(result), 400


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


# ═══ ★ #1130（2026-10-14）面板生态四件套：导出打包 + Hub 一键安装 ═══

@bp.route('/<panel_id>/export', methods=['GET'])
def export_panel(panel_id):
    """导出面板为标准 zip（仅生成器台账面板；定制模块拒绝）"""
    from flask import send_file
    mgr = _get_manager()
    result = mgr.export_panel(panel_id)
    if not result.get('success'):
        return jsonify(result), 400
    return send_file(result['zip_path'], as_attachment=True, download_name=result['name'])


@bp.route('/install_hub', methods=['POST'])
def install_panel_hub():
    """从 HarvenHub 一键安装面板模块：body {item_id} → 下载 zip → 复用安装管线（含兼容校验/冲突拒绝/Zip Slip 防护）"""
    import zipfile as _zipfile
    data = request.get_json(silent=True) or {}
    item_id = str(data.get('item_id') or '').strip()
    if not item_id:
        return jsonify({'success': False, 'message': '缺少 Hub 内容 id（Hub 网页条目详情里可查）'}), 400
    from src.core.harvenhub_client import hub_download
    dl = hub_download(item_id)
    if not dl.get('success'):
        return jsonify({'success': False, 'message': f"Hub 下载失败: {dl.get('error') or '未知错误'}"}), 502
    fp = dl['file_path']
    if not _zipfile.is_zipfile(fp):
        return jsonify({'success': False,
                        'message': '该 Hub 条目不是面板压缩包（可能是文档/Skill 等其他类型），请确认类型后用对应入口安装'}), 400
    mgr = _get_manager()
    result = mgr.install_panel(fp)
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
    features = data.get('features') or {}  # ★ 2026-09-19 模板自带能力配置（remind/stats/ai）
    ai_role = (data.get('ai_role') or '').strip()  # ★ 2026-09-19 AI 角色卡（P0 全量铺）
    universal = bool(data.get('universal', False))  # ★ 2026-09-22 (#843) 通用模块标记（所有分类页可见）

    if not name:
        return jsonify({'success': False, 'message': '面板名称不能为空'}), 400
    if not panel_id:
        return jsonify({'success': False, 'message': '面板ID不能为空'}), 400
    if not fields:
        return jsonify({'success': False, 'message': '至少需要定义一个字段'}), 400

    mgr = _get_manager()
    result = mgr.create_panel_from_template(name, panel_id, fields, description, icon,
                                             with_chart=with_chart, with_search=with_search,
                                             features=features, ai_role=ai_role,
                                             universal=universal)
    if result['success']:
        return jsonify(result)
    return jsonify(result), 400


@bp.route('/<panel_id>/ensure_photo_field', methods=['POST'])
def ensure_photo_field(panel_id):
    """★ #1459（2026-11-05）存图快捷键候选池补缺：模块没有照片字段时现场补一个「照片」。
    已有 → success/added=false 直接返回字段名；加了 → ALTER TABLE+panel.json+页面再生成
    （全走 edit_panel_fields 现成链路）；定制模块 → 透传「不能对话改」的友好提示。"""
    try:
        mgr = _get_manager()
        panel = mgr.get_panel(panel_id)
        if not panel:
            return jsonify({'success': False, 'error': '模块不存在'}), 404
        for f in (panel.fields or []):
            if f.get('type') == 'image':
                return jsonify({'success': True, 'added': False,
                                'photo_field': f.get('name', '照片')})
        r = mgr.edit_panel_fields(panel_id, 'add', field_name='照片', ftype='image')
        if not r.get('success'):
            return jsonify({'success': False, 'error': r.get('message', '加照片字段失败')}), 200
        return jsonify({'success': True, 'added': True, 'photo_field': '照片',
                        'message': r.get('message', '')})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200


@bp.route('/<panel_id>/vision-fill', methods=['POST'])
def vision_fill(panel_id):
    """★ 2026-09-20 #801 AI 识图填表：照片 → 本地视觉模型(minicpm-v) → 按面板字段自动填值
    入参 {image: dataURL或纯base64}；返回 {values: {字段名: 值}}，前端填进表单再由用户确认保存。"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        image = (d.get('image') or '').strip()
        if not image:
            return jsonify({'success': False, 'error': '没有拿到照片，先在表单里选一张图'}), 400
        mgr = _get_manager()
        panel = mgr.get_panel(panel_id)
        if not panel:
            return jsonify({'success': False, 'error': '面板不存在'}), 404
        # 纯 base64（dataURL 前缀剥掉）
        b64 = image.split(',', 2)[-1] if image.startswith('data:image') else image
        if not b64:
            return jsonify({'success': False, 'error': '照片格式读不出来，换一张试试'}), 400
        # 可填字段：文本/数字/单选/多行（跳过图片、日期——日期让用户自己挑）
        fillable = [f for f in (panel.fields or [])
                    if f.get('type') in ('text', 'number', 'select', 'textarea') and not f.get('adv')]
        if not fillable:
            return jsonify({'success': False, 'error': '这个面板没有能自动填的文本字段'}), 400
        type_label = {'text': '文字', 'number': '数字', 'select': '单选', 'textarea': '一段话'}
        # ★ #1059 物品存放专用预填约束（实测实锤：AI 猜品名不可靠）——
        #   只预填分类大类+适用季节，物品名称不猜留空；其它面板维持原样
        is_storage = panel_id.startswith(('home_items', 'item_storage'))
        # ★ 2026-10-10 #1062 B3 热量诚实化（餐饮面板）：AI 猜热量不可靠（实锤：韩式炸酱面
        #   被 AI 填 47.6 千卡，真实约 600）——「估算热量」不让 AI 填，入库链按词典粗估兜底
        is_meal = panel_id == 'meal_diary'
        if is_storage:
            fillable = [f for f in fillable if f['name'] not in ('物品名称',)]
        elif is_meal:
            fillable = [f for f in fillable if f['name'] != '估算热量']
            desc_lines = []
            for f in fillable:
                line = f"- 「{f['name']}」（{type_label.get(f.get('type'), '文字')}"
                if f.get('type') == 'select' and f.get('options'):
                    line += '，只能选：' + '、'.join(f['options'])
                desc_lines.append(line + '）')
            prompt = (
                "这是一张家里物品的照片。只填你能确定的，不确定就填空字符串，绝不通猜。\n"
                f"请看图填写：\n{chr(10).join(desc_lines)}\n"
                "要求：只输出一个 JSON 对象，键就是上面《》里的字段名，值是字符串；"
                "看不清或照片里没有的字段填空字符串；不要输出 JSON 以外的任何字。")
        else:
            desc_lines = []
            for f in fillable:
                line = f"- 「{f['name']}」（{type_label.get(f.get('type'), '文字')}"
                if f.get('type') == 'select' and f.get('options'):
                    line += '，只能选：' + '、'.join(f['options'])
                desc_lines.append(line + '）')
            prompt = (
                f"这是「{panel.name}」面板的一条记录照片。面板说明：{(panel.description or '')[:80]}\n"
                f"请看图填写下面这些字段的值：\n{chr(10).join(desc_lines)}\n"
                "要求：只输出一个 JSON 对象，键就是上面《》里的字段名，值是字符串；"
                "看不清或照片里没有的字段填空字符串；数字字段只填数字；不要输出 JSON 以外的任何字。")
        # ★ #1155 看图路由统一入口：跟随用户当前外脑/本地偏好（选谁给谁，不代选不兜底）
        from src.core.panel_assist import _camp_vision_bg
        text, model, err = _camp_vision_bg(prompt, b64)
        if err or not text:
            return jsonify({'success': False,
                            'error': (err or '视觉模型没有返回') + '（识别模型没就绪时，可在聊天框让我处理）'}), 200
        # 宽松解析：优先取第一个 {...} 块
        values = {}
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start >= 0 and end > start:
                values = json.loads(text[start:end + 1])
        except Exception:
            values = {}
        if not isinstance(values, dict):
            values = {}
        valid_names = {f['name'] for f in fillable}
        clean = {}
        for k, v in values.items():
            k = str(k).strip().strip('「》<>')
            if k not in valid_names or v in (None, ''):
                continue
            s = str(v).strip()
            if not s:
                continue
            clean[k] = s[:300]
        return jsonify({'success': True, 'values': clean, 'model': model})
    except Exception as e:
        logger.error(f"[panel_routes] vision-fill 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/photos/batch-extract', methods=['POST'])
def photos_batch_extract():
    """★ 照片链 A2（2026-10-11 已批准）批量照片录入：多张照片 → 每张自动分类建议面板 + 抽取记录。
    入参 {images: [dataURL/纯b64, ...]}（上限 4 张）
    出参 {success, items: [{desc, groups: [{panel_id, domain, label, fields, records}], model}]}
    每张图独立走家庭空间跨面板抽取（_extract_home），fields 带给确认卡可改可换面板。"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        images = d.get('images') or []
        if not isinstance(images, list) or not images:
            return jsonify({'success': False, 'error': '没有拿到照片，先选好图再点「批量照片录入」'}), 400
        from src.core.panel_assist import _norm_images, _extract_home
        imgs = _norm_images(images)
        if not imgs:
            return jsonify({'success': False, 'error': '照片格式读不出来，换几张试试'}), 400
        items = []
        first_err = ''
        from datetime import datetime as _dt
        today = _dt.now().strftime('%Y-%m-%d')
        for _i, im in enumerate(imgs, 1):
            # 通用短语引导：让模型看图判断该记进哪个面板（空文本会绕过词典快车道直接走大模型主链）
            groups, _dom, model, err = _extract_home('（看照片帮我记下来）', None, im, today)
            if err and not groups:
                first_err = first_err or err
                items.append({'desc': f'第{_i}张没认出来', 'groups': [], 'model': ''})
                continue
            items.append({'desc': '', 'groups': groups or [], 'model': model})
        if all(not it['groups'] for it in items) and first_err:
            return jsonify({'success': False, 'error': first_err}), 200
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        logger.error(f"[panel_routes] photos/batch-extract 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/photos/replace-original', methods=['POST'])
def photos_replace_original():
    """★ 照片链 A4（2026-10-11）原图补传替换：手机回家 WiFi 后用原图升级已存压缩版。
    入参 {record_id: int 或 image_id: int 二选一, image: dataURL/纯b64, panel_id: 可选防误替}
    （★ C2 补：手机拍照入库的图没有 record_id，按 image_id=图行 id 找）
    出参 {success, image: {id, filename, url}}"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        try:
            rid = int(d.get('record_id') or 0)
        except (TypeError, ValueError):
            rid = 0
        try:
            iid = int(d.get('image_id') or 0)
        except (TypeError, ValueError):
            iid = 0
        image = (d.get('image') or '').strip()
        if (not rid and not iid) or not image:
            return jsonify({'success': False, 'error': '补传缺记录编号或原图'}), 400
        from src.core import panel_images
        new = panel_images.replace_with_original(rid or None, image,
                                                 panel_id=d.get('panel_id') or '', image_id=iid)
        if not new:
            return jsonify({'success': False, 'error': '没找到这条记录的旧图，或原图读不出来'}), 200
        return jsonify({'success': True, 'image': new})
    except Exception as e:
        logger.error(f"[panel_routes] photos/replace-original 失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════ ★ D批 家庭共享：成员API ══════════════════════════

@bp.route('/members', methods=['GET'])
def api_get_members():
    """获取家庭成员列表"""
    return jsonify({'success': True, 'members': _get_manager().get_members()})


@bp.route('/members', methods=['POST'])
def api_add_member():
    """新增家庭成员"""
    data = request.get_json() or {}
    try:
        _get_manager().add_member(data.get('name', ''), data.get('color', ''), data.get('role', '员工'))
        return jsonify({'success': True, 'members': _get_manager().get_members()})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/members/<int:member_id>', methods=['DELETE'])
def api_delete_member(member_id):
    """删除家庭成员（历史记录里的名字保留不动）"""
    ok = _get_manager().delete_member(member_id)
    if ok:
        return jsonify({'success': True, 'members': _get_manager().get_members()})
    return jsonify({'success': False, 'message': '成员不存在'}), 404


# ══════ ★ 2026-10-20 家人档案（panel_members 扩列升级） ══════

@bp.route('/members/<int:member_id>', methods=['PUT'])
def api_update_member(member_id):
    """编辑家人档案（生日/手机/口味/忌口过敏/健康备注/关系）"""
    data = request.get_json() or {}
    try:
        ok = _get_manager().update_member(member_id, **data)
        if ok:
            return jsonify({'success': True, 'members': _get_manager().get_members()})
        return jsonify({'success': False, 'message': '没有可更新的字段'}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route('/members/birthdays', methods=['GET'])
def api_member_birthdays():
    """未来 N 天过生日的家人（SPA 横幅/手机推送共用）"""
    days = int(request.args.get('days', 7) or 7)
    return jsonify({'success': True, 'birthdays': _get_manager().upcoming_birthdays(days)})


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


@bp.route('/<panel_id>/cross_context', methods=['GET'])
def panel_cross_context(panel_id):
    """★ #1105（2026-10-11）关联动态横幅取数：link_rules 联动规则表驱动，
    纯 SQL 组装零 token；无规则/无数据返回空 banners（前端横幅自动隐藏）"""
    from src.core.link_rules import get_cross_context
    member = request.args.get('member') or None
    r = get_cross_context(panel_id, member)
    return jsonify({'success': True, **r})


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
        # ★ P1-A 修正（2026-09-27 实测抓虫）：原版先 insert 再转存——面板表大多没有照片列，
        #   data:image 字段留在 data 里直接把 insert 炸掉（实锤：记账表报 no column named 凭证截图），
        #   结果"带图反而录不进"。正确顺序：先剥照片字段 → 入库纯文本 → 照片转存附件库。
        #   面板表恰好有该列时（罕见）照片字段照旧落列 + 转存，两种面板都通。
        # ★ #908 B方案（2026-09-27）：面板声明了 image 字段的，本体进附件库、列里只落文件名。
        # ★ #917 多图（2026-09-27）：字段值支持 JSON 数组串 '["data:image..","data:image.."]'
        #   （前端多选图打包）→ 拆成多张分别入库，声明列落逗号分隔文件名。
        photos = []
        for k in list(data.keys()):
            v = data.get(k)
            if not (isinstance(v, str) and len(v) > 60):
                continue
            if v.startswith('data:image'):
                photos.append((k, v))
                data.pop(k)
            elif v.startswith('[') and '"data:image' in v[:40]:
                try:
                    arr = json.loads(v)
                    if isinstance(arr, list):
                        data.pop(k)
                        photos += [(k, item) for item in arr
                                   if isinstance(item, str) and item.startswith('data:image')]
                except Exception:
                    pass
        # ★ #913 入库查重（比去图后的纯文本）：今天已有同金额+近似名 → 拒绝重复入
        dup = mgr.find_duplicate(panel_id, data)
        if dup:
            return jsonify({'success': False, 'duplicate': True,
                            'message': '这条像是刚记过：今天已有一条同金额同名的记录，没有重复入账。真要重记，改一下金额或备注再存。'})
        panel = mgr.get_panel(panel_id)
        img_fields = {f.get('name') for f in (getattr(panel, 'fields', None) or [])
                      if isinstance(f, dict) and f.get('type') == 'image'} if panel else set()
        _cols = set()
        if photos:
            try:
                from src.core.panel_manager import DB_PATH as _PM_DB
                import sqlite3 as _sq
                _c = _sq.connect(str(_PM_DB))
                _cols = {r[1] for r in _c.execute(f'PRAGMA table_info("panel_{panel_id}")').fetchall()}
                _c.close()
            except Exception as _ce:
                logger.debug(f'[PanelRoutes] 照片列检查跳过: {_ce}')
        pre_saved = []   # 声明式字段：先存图拿文件名，入库后挂 record_id
        deferred = []    # 其余照片：入库后转存附件库挂 record_id
        field_files = {}  # ★ #917 多图：字段 → [文件名,...]（声明列落逗号分隔）
        from src.core import panel_images as _pi
        for k, v in photos:
            if k in img_fields:
                r = _pi.save_image(panel_id, v, note=f'表单字段「{k}」')
                if r:
                    pre_saved.append(r['filename'])
                    field_files.setdefault(k, []).append(r['filename'])
            elif k in _cols:
                data[k] = v            # 老面板表真有此列 → 字段照旧落库（兼容）
                deferred.append((k, v))
            else:
                deferred.append((k, v))
        for k, fns in field_files.items():
            if k in _cols:
                data[k] = ','.join(fns)   # ★ #917 列只落文件名（多图逗号分隔），绝不背 base64
        if not data:
            return jsonify({'success': False, 'message': '只有照片没有文字信息，补一句说明（买了什么/多少钱）再记'}), 400
        # ★ #1378（2026-11-02）冰箱录入兜底：确认卡提交也过公共函数（分区校验门+到期日词典+状态默认）
        #   ——花生油 id=28 全 NULL 进未分区的实锤补丁
        if panel_id.startswith('fridge_stock'):
            try:
                from src.core.fridge_hub import fridge_intake_guard as _fig, _append_note as _apn
                _gn = _fig(data)
                _apn(data, _gn)
            except Exception as _fg:
                logger.debug(f'[PanelRoutes] 冰箱兜底跳过: {_fg}')
        result = mgr.insert_panel_data(panel_id, data)
        rid = (result or {}).get('id')
        for fname in pre_saved:
            try:
                _pi.attach_to_record(fname, rid)
            except Exception as _ae:
                logger.debug(f'[PanelRoutes] 图片挂记录跳过: {_ae}')
        # 遗留照片全部转存附件库（AI 可见、可缩略图列表、FTS 不背长文本）
        for k, v in deferred:
            try:
                _pi.save_image(panel_id, v, record_id=rid,
                               note=f'面板表单照片 · 字段「{k}」')
            except Exception as _pe:
                logger.debug(f'[PanelRoutes] 照片转存附件库跳过: {_pe}')
        # ★ P1-C：页面直接入库也挂提醒联动（此前只有默录链有——"页面记的保修到期"从不上历）
        try:
            panel = mgr.get_panel(panel_id)
            if panel is not None and getattr(panel, 'features', None):
                from src.core.panel_assist import _sync_record_reminder
                _sync_record_reminder(panel_id, panel.features, data, rid)
        except Exception as _re:
            logger.debug(f'[PanelRoutes] 提醒联动跳过: {_re}')
        # ★ 2026-10-10 #1061 外卖自动记账联动（餐饮面板手填/识图入库 → 记账待确认卡）
        _link_note = ''
        try:
            from src.core.meal_insights import maybe_link_accounting
            _link_note = maybe_link_accounting(panel_id, data)
        except Exception as _le:
            logger.debug(f'[PanelRoutes] 外卖记账联动跳过: {_le}')
        out = {'success': True, 'record': result}
        if _link_note:
            out['note'] = _link_note
        return jsonify(out)
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
        # ★ #917（2026-09-27）编辑路径也剥照片：原版 PUT 原样透传，编辑带图会把整个
        #   dataURL 灌进文本列（库膨胀 + FTS 背长文本）。与新增同款：剥→入库→转存挂记录。
        photos = []
        for k in list(data.keys()):
            v = data.get(k)
            if not (isinstance(v, str) and len(v) > 60):
                continue
            if v.startswith('data:image'):
                photos.append((k, v))
                data.pop(k)
            elif v.startswith('[') and '"data:image' in v[:40]:
                try:
                    arr = json.loads(v)
                    if isinstance(arr, list):
                        data.pop(k)
                        photos += [(k, item) for item in arr
                                   if isinstance(item, str) and item.startswith('data:image')]
                except Exception:
                    pass
        if photos:
            from src.core import panel_images as _pi
            panel = mgr.get_panel(panel_id)
            img_fields = {f.get('name') for f in (getattr(panel, 'fields', None) or [])
                          if isinstance(f, dict) and f.get('type') == 'image'} if panel else set()
            field_files = {}
            for k, v in photos:
                r = _pi.save_image(panel_id, v, record_id=row_id, note=f'编辑补图 · 字段「{k}」')
                if r and k in img_fields:
                    field_files.setdefault(k, []).append(r['filename'])
            for k, fns in field_files.items():
                data[k] = ','.join(fns)   # 多图逗号分隔；新图覆盖列里旧文件名（旧附件行保留）
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
    # ★ 2026-09-16 面板文件禁缓存（与 web_app /panel/* 同步）：升级后面板必须立刻换新页
    resp = send_from_directory(str(panel_dir), filename)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


# ══════════════════════════ ★ P0-A 2026-09-27 图片附件 API ══════════════════════════

@bp.route('/image', methods=['POST'])
def upload_panel_image():
    """上传图片附件。JSON: {panel_id, image_b64, record_id?, note?}
    返回 {success, id, filename, url}"""
    try:
        d = request.get_json(force=True, silent=True) or {}
        panel_id = str(d.get('panel_id', '')).strip()
        image_b64 = d.get('image_b64') or ''
        if not panel_id or not image_b64:
            return jsonify({'success': False, 'message': '缺 panel_id 或 image_b64'}), 400
        from src.core import panel_images
        r = panel_images.save_image(panel_id, image_b64,
                                    record_id=d.get('record_id'),
                                    note=d.get('note', ''),
                                    member=d.get('member', ''))
        if not r:
            return jsonify({'success': False, 'message': '存图失败'}), 500
        return jsonify({'success': True, **r})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/image/<filename>', methods=['GET'])
def serve_panel_image(filename):
    """读取图片文件（?thumb=1 取缩略图）"""
    from src.core import panel_images
    p = panel_images.get_image_path(filename, thumb=request.args.get('thumb') == '1')
    if not p:
        return jsonify({'success': False, 'message': '图片不存在'}), 404
    resp = send_from_directory(str(p.parent), p.name)
    # 图片可长缓存（文件名是 uuid，内容不可变）
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


@bp.route('/image/<filename>', methods=['DELETE'])
def delete_panel_image(filename):
    """★ #909 看图弹层的 ✕：删附件图（登记行+落盘文件）"""
    from src.core import panel_images
    return jsonify({'success': bool(panel_images.delete_image(filename))})


@bp.route('/<panel_id>/images/attach', methods=['POST'])
def attach_panel_image(panel_id):
    """★ #914 未关联图池一键挂记录。JSON: {filename, record_id}"""
    d = request.get_json(force=True, silent=True) or {}
    filename = (d.get('filename') or '').strip()
    rid = d.get('record_id')
    if not filename or rid is None:
        return jsonify({'success': False, 'message': '缺 filename 或 record_id'}), 400
    from src.core import panel_images
    return jsonify({'success': bool(panel_images.attach_to_record(filename, rid))})


@bp.route('/<panel_id>/images', methods=['GET'])
def list_panel_images(panel_id):
    """列面板图片（?record_id= 过滤某条记录的图；?unlinked=1 只列未关联图池）"""
    from src.core import panel_images
    if request.args.get('unlinked') == '1':
        return jsonify({'success': True, 'images': panel_images.list_unlinked(panel_id)})
    record_id = request.args.get('record_id', type=int)
    return jsonify({'success': True, 'images': panel_images.list_images(panel_id, record_id)})


# ══════════════════ ★ #1056 物品存放位置档案 API ══════════════════
# 四级模型：房产 → 房间 → 容器 → 格位。端点均为两段以上路径，不会被 /<panel_id> 单段通配吞掉。

@bp.route('/storage-places/tree', methods=['GET'])
def storage_places_tree():
    """全量位置树（房产→房间→容器→格位），前端三级联动/管理 Tab 直接吃。"""
    from src.core import storage_places as sp
    return jsonify({'success': True, 'tree': sp.get_tree(),
                    'properties': sp.list_properties()})


@bp.route('/storage-places/templates', methods=['GET'])
def storage_places_templates():
    """格位模板清单（五类 + 默认层数），前端模板选择卡用。"""
    from src.core import storage_places as sp
    return jsonify({'success': True, 'templates': [
        {'type': t, 'label': v['label'], 'default_levels': v['default_levels']}
        for t, v in sp.CONTAINER_TEMPLATES.items()]})


@bp.route('/storage-places', methods=['POST'])
def storage_places_add():
    """新建位置节点。JSON: {property_name, room, container?, slot?, aliases?, photo?}"""
    d = request.get_json(silent=True) or {}
    from src.core import storage_places as sp
    return jsonify(sp.add_place(d.get('property_name') or '家', d.get('room') or '',
                                d.get('container') or '', d.get('slot') or '',
                                d.get('aliases'), d.get('photo') or ''))


@bp.route('/storage-places/template', methods=['POST'])
def storage_places_apply_template():
    """一键应用格位模板。JSON: {property_name, room, container_name, container_type, levels?}"""
    d = request.get_json(silent=True) or {}
    from src.core import storage_places as sp
    r = sp.apply_template(d.get('property_name') or '家', d.get('room') or '',
                          d.get('container_name') or '', d.get('container_type') or '',
                          d.get('levels'), d.get('aliases'))
    return jsonify(r), (200 if r.get('success') else 400)


@bp.route('/storage-places/<int:place_id>', methods=['PUT'])
def storage_places_update(place_id):
    """改节点（改名/别名/照片/排序）。JSON 字段任选。"""
    d = request.get_json(silent=True) or {}
    from src.core import storage_places as sp
    return jsonify(sp.update_place(place_id, **d))


@bp.route('/storage-places/<int:place_id>', methods=['DELETE'])
def storage_places_delete(place_id):
    """删节点。返回 children_affected 供前端确认卡提示。"""
    from src.core import storage_places as sp
    return jsonify(sp.delete_place(place_id))


@bp.route('/storage-places/resolve', methods=['POST'])
def storage_places_resolve():
    """别名归一：{room, container?, slot?} → 规范名。录入链/前端表单即时校验用。"""
    d = request.get_json(silent=True) or {}
    from src.core import storage_places as sp
    room, cont, slot = sp.resolve_place_input(d.get('room') or '', d.get('container') or '', d.get('slot') or '')
    return jsonify({'success': True, 'room': room, 'container': cont, 'slot': slot})

