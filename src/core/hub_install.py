# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Hub 安装引擎（#1212，2026-10-25）
哈文像 WorkBuddy 一样主动搜索 HarvenHub 内容并下载安装，
但装前必须弹确认卡征求用户同意 + 风险告知（定版拍板：用户自由原则）。

链路：
  FC 工具 hub_search → 搜云端
  FC 工具 hub_install → request_install（查详情+写 pending 文件，返回确认卡数据）
    → engine yield confirm_card 事件 → 前端渲染卡片
    → 用户点同意/取消 → POST /api/hub/install/confirm
    → confirm_install（approved=True 才真下载+安装）

安全边界：
  ① 只从 HarvenHub 官方源拉取（harvenhub_client 内置白名单）
  ② pending 令牌机制：确认卡 10 分钟过期，token 不匹配拒绝
  ③ 非 skill 类型只下载不安装（落 data/downloads/ 并报告路径）
"""

import os
import json
import time
import shutil
import logging
import secrets
import threading

logger = logging.getLogger(__name__)

# ── 路径 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PENDING_DIR = os.path.join(_PROJECT_ROOT, 'data', 'hub_pending')
PENDING_TTL = 600  # 确认卡有效期：10 分钟

# ── 风险告知四条（确认卡固定内容，定版拍板要求）──
RISK_NOTICES = [
    "Skill / 工具包含外部代码，安装后可能在本地执行",
    "内容来自社区上传，哈文未逐字安全审核",
    "哈文仅从官方 HarvenHub（harvenhub.com）拉取，不访问任何第三方源",
    "安装后可随时在「技能库」页面卸载",
]

# 可安装类型（其余类型只下载报告，不落技能目录）
INSTALLABLE_TYPES = {'skill'}


def _human_size(n):
    """字节数 → 人话大小"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return '?'
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _cleanup_expired():
    """清理过期 pending 文件（顺手做，不阻塞主流程）"""
    try:
        os.makedirs(PENDING_DIR, exist_ok=True)
        now = time.time()
        for fn in os.listdir(PENDING_DIR):
            if not fn.endswith('.json'):
                continue
            fp = os.path.join(PENDING_DIR, fn)
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if now - data.get('created_at', 0) > PENDING_TTL:
                    os.remove(fp)
                    logger.info(f"[HubInstall] 过期确认卡已清理: {fn}")
            except Exception:
                pass  # 损坏文件跳过，下次再清
    except Exception as e:
        logger.debug(f"[HubInstall] 清理过期卡失败(不影响主流程): {e}")


# ═══════════════════════════════════════════════════════
# 1. 搜索
# ═══════════════════════════════════════════════════════

def search_hub(query, item_type=None, limit=10):
    """搜索 HarvenHub 内容（包装 harvenhub_client.hub_search）

    Returns:
        dict: {success, message, items: [{item_id,name,type,version,size,size_human,author,description}], error}
    """
    from src.core.harvenhub_client import hub_search

    query = (query or '').strip()
    if not query:
        return {'success': False, 'message': '', 'items': [], 'error': '搜索词不能为空'}

    result = hub_search(query, limit=limit)
    if not result.get('success'):
        return {'success': False, 'message': '', 'items': [],
                'error': result.get('error') or '搜索失败'}

    items = []
    for it in (result.get('items') or []):
        if not isinstance(it, dict):
            continue
        # 按类型过滤（可选）
        if item_type and it.get('type') != item_type:
            continue
        items.append({
            'item_id': it.get('id', ''),
            'name': it.get('name', ''),
            'type': it.get('type', ''),
            'version': it.get('version', ''),
            'size': it.get('file_size', 0),
            'size_human': _human_size(it.get('file_size', 0)),
            'author': it.get('author_id', ''),
            'description': (it.get('description') or '')[:120],
        })

    msg = f"找到 {len(items)} 个结果"
    if item_type:
        msg += f"（已按类型 {item_type} 过滤）"
    return {'success': True, 'message': msg, 'items': items, 'error': ''}


# ═══════════════════════════════════════════════════════
# 2. 请求安装（生成确认卡，不真下载）
# ═══════════════════════════════════════════════════════

def request_install(item_id):
    """查详情 → 写 pending 文件 → 返回确认卡数据

    Returns:
        dict: {success, message, confirm_card: {...}, error}
              confirm_card 含 name/type/risks/token 等，engine 检测此键 yield confirm_card 事件
    """
    from src.core.harvenhub_client import hub_detail

    item_id = (item_id or '').strip()
    if not item_id:
        return {'success': False, 'message': '', 'error': '缺少内容 ID'}

    detail = hub_detail(item_id)
    if not detail.get('success') or not detail.get('item'):
        return {'success': False, 'message': '',
                'error': detail.get('error') or '未找到该内容，可能已下架'}

    # ★ #1215 修复：hub_detail 返回嵌套结构 {item: {item: {...}}}，真实字段在内层
    item = (detail['item'] or {}).get('item') or detail['item']
    token = secrets.token_hex(16)
    now = time.time()

    card = {
        'item_id': item_id,
        'name': item.get('name', item_id),
        'type': item.get('type', ''),
        'version': item.get('version', ''),
        'size_human': _human_size(item.get('file_size', 0)),
        'author': item.get('author_id', ''),
        'description': (item.get('description') or '')[:300],
        'installable': item.get('type', '') in INSTALLABLE_TYPES,
        'risks': RISK_NOTICES,
        'token': token,
        'expires_in': PENDING_TTL,
    }

    # 写 pending 文件（用户点头后 confirm_install 凭 token 真下载）
    os.makedirs(PENDING_DIR, exist_ok=True)
    pending_path = os.path.join(PENDING_DIR, f"{item_id}.json")
    with open(pending_path, 'w', encoding='utf-8') as f:
        json.dump({
            'item_id': item_id,
            'token': token,
            'created_at': now,
            'name': card['name'],
            'type': card['type'],
        }, f, ensure_ascii=False, indent=2)

    _cleanup_expired()

    action = '安装' if card['installable'] else '下载'
    logger.info(f"[HubInstall] 已生成{action}确认卡: {card['name']} (item_id={item_id}, TTL={PENDING_TTL}s)")
    return {
        'success': True,
        'message': f"已生成「{card['name']}」的{action}确认卡，等待用户在界面上确认（10 分钟内有效）",
        'confirm_card': card,
        'error': '',
    }


# ═══════════════════════════════════════════════════════
# 3. 确认安装（用户点头后才真下载）
# ═══════════════════════════════════════════════════════

def confirm_install(item_id, token, approved):
    """校验 pending 令牌 → approved=True 才真下载 + 按类型安装

    Returns:
        dict: {success, message, installed: bool|None, error}
    """
    from src.core.harvenhub_client import hub_download

    item_id = (item_id or '').strip()
    _cleanup_expired()

    pending_path = os.path.join(PENDING_DIR, f"{item_id}.json")
    if not os.path.exists(pending_path):
        return {'success': False, 'message': '', 'installed': None,
                'error': '确认卡不存在或已过期（10 分钟有效），请让哈文重新发起'}

    try:
        with open(pending_path, 'r', encoding='utf-8') as f:
            pending = json.load(f)
    except Exception as e:
        return {'success': False, 'message': '', 'installed': None,
                'error': f'确认卡损坏: {e}'}

    # 令牌校验：不匹配一律拒绝（防伪造/防串卡）
    if (token or '').strip() != pending.get('token', ''):
        return {'success': False, 'message': '', 'installed': None,
                'error': '确认令牌不匹配，已拒绝操作'}

    # 用完即删（无论同意还是取消，卡都作废，防重复确认）
    try:
        os.remove(pending_path)
    except Exception:
        pass

    name = pending.get('name', item_id)
    item_type = pending.get('type', '')

    if not approved:
        logger.info(f"[HubInstall] 用户取消: {name} (item_id={item_id})")
        return {'success': True, 'message': f'已取消「{name}」的安装', 'installed': False, 'error': ''}

    # ── 真下载 ──
    logger.info(f"[HubInstall] 用户已同意，开始下载: {name} (item_id={item_id}, type={item_type})")
    dl = hub_download(item_id)
    if not dl.get('success'):
        return {'success': False, 'message': '', 'installed': False,
                'error': dl.get('error') or '下载失败'}

    file_path = dl['file_path']

    # ── 按类型处理 ──
    if item_type not in INSTALLABLE_TYPES:
        # 非 skill 类型：只下载报告，不自动安装
        msg = (f"「{name}」已下载到 {file_path}。"
               f"该类型（{item_type or '未知'}）暂不支持自动安装，请按说明手动处理")
        logger.info(f"[HubInstall] 仅下载不安装: {name} → {file_path}")
        return {'success': True, 'message': msg, 'installed': False, 'error': ''}

    # skill 类型：按扩展名安装
    lower = file_path.lower()
    try:
        from src.core.skills_manager import get_skills_manager, SKILLS_DIR
        sm = get_skills_manager()

        if lower.endswith('.zip'):
            with open(file_path, 'rb') as f:
                zip_data = f.read()
            skill_name = os.path.splitext(os.path.basename(file_path))[0]
            r = sm._install_from_zip_bytes(zip_data, skill_name)
            if not r.get('success'):
                return {'success': False, 'message': '', 'installed': False,
                        'error': r.get('error') or 'ZIP 安装失败'}
            msg = f"技能「{r.get('skill_name', skill_name)}」安装成功，已在技能库生效"
            logger.info(f"[HubInstall] {msg}")
            return {'success': True, 'message': msg, 'installed': True, 'error': ''}

        if lower.endswith('.md'):
            skill_name = os.path.splitext(os.path.basename(file_path))[0]
            target = SKILLS_DIR / f"{skill_name}.md"
            shutil.copy2(file_path, str(target))
            sm.reload()
            msg = f"技能「{skill_name}」安装成功，已在技能库生效"
            logger.info(f"[HubInstall] {msg}")
            return {'success': True, 'message': msg, 'installed': True, 'error': ''}

        return {'success': False, 'message': '', 'installed': False,
                'error': f'不支持的技能文件格式: {os.path.basename(file_path)}，仅支持 .md / .zip'}

    except Exception as e:
        logger.error(f"[HubInstall] 安装失败: {e}")
        return {'success': False, 'message': '', 'installed': False,
                'error': f'安装失败: {e}'}


# ═══════════════════════════════════════════════════════
# 4. Flask Blueprint（前端确认卡回调路由）
# ═══════════════════════════════════════════════════════

from flask import Blueprint, request, jsonify

bp = Blueprint('hub_install', __name__)
_confirm_lock = threading.Lock()  # 防并发双击确认


@bp.route('/api/hub/search', methods=['GET'])
def api_hub_search():
    """搜索 HarvenHub（前端也可直接用）"""
    q = request.args.get('q', '').strip()
    item_type = request.args.get('type', '').strip() or None
    limit = min(int(request.args.get('limit', 10) or 10), 30)
    r = search_hub(q, item_type=item_type, limit=limit)
    return jsonify(r)


@bp.route('/api/hub/install/request', methods=['POST'])
def api_hub_install_request():
    """生成安装确认卡（前端/哈文均可调）"""
    data = request.get_json(silent=True) or {}
    item_id = (data.get('item_id') or '').strip()
    r = request_install(item_id)
    return jsonify(r)


@bp.route('/api/hub/install/confirm', methods=['POST'])
def api_hub_install_confirm():
    """用户确认/取消安装（前端确认卡按钮回调）"""
    data = request.get_json(silent=True) or {}
    item_id = (data.get('item_id') or '').strip()
    token = (data.get('token') or '').strip()
    approved = bool(data.get('approved'))
    with _confirm_lock:
        r = confirm_install(item_id, token, approved)
    return jsonify(r)
