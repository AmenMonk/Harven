# -*- coding: utf-8 -*-
"""
HarvenAI 权限中心（2026-10-20 第二批 2.1）
四类权限三选（🟢只读/🟡写入/🟠花钱对外/🔴危险）+ 联网浏览三层开关（总闸-分闸联动）
配置家：data/permissions.json（独立文件，不动 config.yaml）

语义（一期落地说明）：
- allow = 放行
- log   = 放行+记日志
- ask   = 一期=拒绝并附「需在设置中允许」提示（FC 同步循环无法等待用户；
          确认卡交互二期接 C1/收纳盒管线），用户在设置页改为 allow 后即放行
- deny  = 拒绝+引导
"""
import os, json, logging, threading
from datetime import datetime

logger = logging.getLogger(__name__)

_PERM_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'permissions.json')
_lock = threading.Lock()

# 四类权限默认值
DEFAULT_SETTINGS = {
    # 工具权限：allow / log / ask / deny
    'perm_readonly': 'allow',
    'perm_write': 'log',
    'perm_spend': 'ask',
    'perm_danger': 'deny',
    # 联网三层开关（总闸-分闸）
    'net_search': True,        # 🌐 联网搜索（总闸，与 engine 联网开关同源显示）
    'net_browse_compare': False,  # 📄 网页浏览/比价（分闸，需总闸开）
    'net_online_order': False,    # 🛒 网购下单（分闸，需比价开+每次确认）
}

# 工具名 → 权限类别（未列出的默认 readonly）
TOOL_CATEGORY = {
    # 🔴 危险（真删除类——execute_command 10-20 22:52 移到 write：
    #   它内部已有词边界黑名单把关，整体拦 danger 会把清空回收站等普通命令全废，实锤反馈）
    'delete_path': 'danger',
    'install_package': 'danger',
    'install_model': 'danger',
    'uninstall_model': 'danger',
    # 🟠 花钱/对外
    'email_send': 'spend',
    # 🟡 写入（execute_command 安全靠内部危险命令黑名单+safe 模式 security_guard 兜底）
    'execute_command': 'write',
    'write_file': 'write',
    'edit_file': 'write',
    'create_xlsx': 'write',
    'create_docx': 'write',
    'create_pptx': 'write',
    'create_qrcode': 'write',
    'save_memory': 'write',
    'save_note': 'write',
    # （任务/面板/照片类 FC 工具按名前缀在 categorize() 里兜底）
    # 🟢 只读（不列即默认）
}

PERM_LABEL = {
    'readonly': ('🟢 只读', '查天气、读文件、搜索等无副作用操作'),
    'write': ('🟡 写入', '建记录、设提醒、存照片、写文件'),
    'spend': ('🟠 花钱/对外', '发邮件、下单等花钱或对外的动作'),
    'danger': ('🔴 危险', '删除文件、系统命令、安装卸载'),
}
PERM_CHOICE_LABEL = {
    'allow': '总是允许', 'log': '允许并记日志', 'ask': '每次问我', 'deny': '拒绝',
}


def _load():
    settings = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(_PERM_FILE):
            with open(_PERM_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            for k in DEFAULT_SETTINGS:
                if k in saved:
                    settings[k] = saved[k]
    except Exception as e:
        logger.warning(f"[PermCenter] 配置读取失败用默认: {e}")
    return settings


def _save(settings):
    os.makedirs(os.path.dirname(_PERM_FILE), exist_ok=True)
    with open(_PERM_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def get_settings():
    """读全部设置（带三层联动修正：总闸关→分闸强制关）"""
    s = _load()
    if not s.get('net_search'):
        s['net_browse_compare'] = False
        s['net_online_order'] = False
    elif not s.get('net_browse_compare'):
        s['net_online_order'] = False
    return s


def set_setting(key, value):
    """写单项设置（联动校验），返回 (ok, 生效值, message)"""
    if key not in DEFAULT_SETTINGS:
        return False, None, f'未知设置项: {key}'
    with _lock:
        s = _load()
        # 联动：开分闸时检查上级
        if key == 'net_browse_compare' and value and not s.get('net_search'):
            return False, False, '需先开启联网搜索（总闸）'
        if key == 'net_online_order' and value and not s.get('net_browse_compare'):
            return False, False, '需先开启网页浏览/比价'
        if key in ('perm_readonly', 'perm_write', 'perm_spend', 'perm_danger'):
            if value not in ('allow', 'log', 'ask', 'deny'):
                return False, None, f'非法权限值: {value}'
        s[key] = value
        _save(s)
    return True, value, 'ok'


def categorize(tool_name):
    """工具 → 权限类别"""
    if tool_name in TOOL_CATEGORY:
        return TOOL_CATEGORY[tool_name]
    n = (tool_name or '').lower()
    # 前缀兜底
    if n.startswith(('create_', 'save_', 'add_', 'set_', 'update_', 'inbox_', 'to_task', 'remind')):
        return 'write'
    if n.startswith(('delete_', 'remove_', 'uninstall_', 'format_')):
        return 'danger'
    return 'readonly'


def check_tool(tool_name):
    """FC 工具执行前检查。返回 (decision, message)
    decision: 'allow' | 'block'
    """
    s = get_settings()
    cat = categorize(tool_name)
    mode = s.get(f'perm_{cat}', 'allow')
    label = PERM_LABEL.get(cat, (cat, ''))[0]
    if mode in ('allow', 'log'):
        if mode == 'log':
            logger.info(f"[PermCenter] 写入类执行: {tool_name} ({label})")
        return 'allow', ''
    if mode == 'ask':
        return 'block', (
            f'权限中心已拦截：{label}类操作当前为「每次问我」模式。'
            f'请让用户在 设置→权限中心 将「{label}」改为「总是允许」后重试，'
            f'或由用户手动执行该操作。')
    # deny
    return 'block', (
        f'权限中心已拒绝：{label}类操作当前为「拒绝」模式。'
        f'请勿重试；如用户确需此操作，请告知其到 设置→权限中心 调整。')


def check_web(feature):
    """联网功能检查 feature: 'search' | 'browse_compare' | 'order'
    返回 (ok, message)
    """
    s = get_settings()
    if feature == 'search':
        return bool(s.get('net_search')), '' if s.get('net_search') else '联网搜索未开启（设置→权限中心）'
    if feature == 'browse_compare':
        if not s.get('net_search'):
            return False, '需先开启联网搜索（总闸）'
        if not s.get('net_browse_compare'):
            return False, '网页浏览/比价未开启（设置→权限中心→联网）'
        return True, ''
    if feature == 'order':
        ok, msg = check_web('browse_compare')
        if not ok:
            return False, msg
        if not s.get('net_online_order'):
            return False, '网购下单未开启（设置→权限中心→联网），且每次下单都需要用户确认'
        return True, ''
    return False, f'未知联网功能: {feature}'


def export_ui():
    """给 SPA/手机端的设置页数据"""
    s = get_settings()
    perms = []
    for cat in ('readonly', 'write', 'spend', 'danger'):
        label, desc = PERM_LABEL[cat]
        perms.append({
            'key': f'perm_{cat}', 'label': label, 'desc': desc,
            'value': s.get(f'perm_{cat}', 'allow'),
            'choices': [{'value': v, 'label': PERM_CHOICE_LABEL[v]} for v in ('allow', 'log', 'ask', 'deny')],
        })
    net = [
        {'key': 'net_search', 'label': '🌐 联网搜索', 'desc': '开了哈文才能搜新闻查资料（总闸）', 'value': s.get('net_search'), 'enabled': True},
        {'key': 'net_browse_compare', 'label': '📄 网页浏览/比价', 'desc': '开了哈文才能替你翻网页、货比三家出对比表', 'value': s.get('net_browse_compare'), 'enabled': bool(s.get('net_search'))},
        {'key': 'net_online_order', 'label': '🛒 网购下单', 'desc': '比完价替你下单，每次都要你确认，永不自动付款', 'value': s.get('net_online_order'), 'enabled': bool(s.get('net_search') and s.get('net_browse_compare'))},
    ]
    return {'perms': perms, 'net': net}


def log_operation(tool_name, decision, detail=''):
    """操作审计日志（追加 data/permission_log.jsonl）"""
    try:
        logf = os.path.join(os.path.dirname(_PERM_FILE), 'permission_log.jsonl')
        with open(logf, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'tool': tool_name, 'decision': decision, 'detail': detail[:200],
            }, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.debug(f"[PermCenter] 审计日志失败: {e}")
