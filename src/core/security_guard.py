# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI - 安全防护模块
2级权限模式：安全（默认） / 全权（用户主动开启）
"""

import logging
logger = logging.getLogger(__name__)
import os
import json
import time
import threading

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

# 危险命令关键词
DANGEROUS_COMMANDS = [
    'rm -rf', 'rm -f ', 'rm -r ', 'rm -fr',
    'rmdir /s', 'rmdir /q',
    'del /s', 'del /f', 'del /q', 'del /a',
    'format ', 'mkfs',
    'shutdown', 'reboot', 'halt',
    'dd if=',
    'reg delete',
    'taskkill /f /im explorer',
    'taskkill /f /im svchost',
    'chmod 777 /',
    'kill -9',
    'systemctl stop',
    'net stop',
    'sc delete',
]

# 允许的路径前缀（安全模式下只能操作这些目录）
ALLOWED_PATHS = [
    'H:/HarvenAI/',
    'H:/HarvenHub/',
    'c:/users/usera/workbuddy/',
    os.path.expanduser('~/.cache/huggingface/'),  # HF缓存（读模型用）
    os.path.expanduser('~/.ollama/'),  # Ollama目录
    '/tmp/', '/c/tmp/',  # 临时目录
]

# 禁止的路径前缀（即使全权模式也不建议）
FORBIDDEN_PATHS = [
    'C:/Windows/',
    'C:/Program Files/',
    'C:/ProgramData/',
    'C:/System Volume Information/',
]

# 破坏性工具（需要确认）
DESTRUCTIVE_TOOLS = ['delete_path', 'uninstall_model']

# 操作日志文件
LOG_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'security_log.json')

# ═══════════════════════════════════════════════════════════════
# 权限模式管理
# ═══════════════════════════════════════════════════════════════

# ★ 2026-07-24: 模式持久化到文件（重启不丢）
MODE_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'security_mode.json')

_permission_mode = 'safe'  # 'safe' or 'full'
_full_access_until = 0  # 全权模式到期时间（0=不限时）
_lock = threading.Lock()


def _save_mode():
    """保存模式到文件"""
    try:
        with open(MODE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'mode': _permission_mode,
                'full_access_until': _full_access_until,
                'timestamp': time.time()
            }, f)
    except Exception as e:
        logger.warning(f"[Security] 保存模式失败: {e}")


def _load_mode():
    """★ 2026-11-15 定版（老谭拍板）：重启一律回归 safe（安全默认）——
    旧版「持久化 full 重启不丢」作废；限时 full 未到期也在重启时回落
    （重启=人工动作，需要全权时重新输密码，成本极低）。"""
    global _permission_mode, _full_access_until
    try:
        if os.path.exists(MODE_FILE):
            with open(MODE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('mode') == 'full':
                logger.info('[Security] 上次为全权模式，按安全默认回归 safe（重启即回归）')
        _permission_mode = 'safe'
        _full_access_until = 0
        _save_mode()
        logger.info('[Security] 从文件加载模式: safe（重启回归安全默认）')
    except Exception as e:
        logger.warning(f"[Security] 加载模式失败: {e}")


# 启动时加载模式
_load_mode()


def get_permission_mode():
    """获取当前权限模式"""
    with _lock:
        if _permission_mode == 'full' and _full_access_until > 0:
            if time.time() > _full_access_until:
                # 超时自动回到安全模式
                _set_mode('safe')
        return _permission_mode


def _set_mode(mode):
    """设置权限模式（内部）"""
    global _permission_mode, _full_access_until
    _permission_mode = mode
    if mode == 'safe':
        _full_access_until = 0
    _save_mode()  # ★ 持久化


def verify_admin_password(password):
    """★ 2026-11-15: 管理员密码校验（受保护动作密码门用，同源 get_admin_password）
    空密码一律 False"""
    return bool(password) and password == get_admin_password()


# 受保护动作默认清单：进化审批/回滚/应用类操作需管理员密码
DEFAULT_PROTECTED_ACTIONS = ['evolution']


def get_protected_actions():
    """★ 2026-11-15: 读取受保护动作清单（security.password_protected_actions）。
    未配置/类型异常 → 默认 ['evolution']（安全默认）；显式 [] = 用户主动关闭保护"""
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.yaml')
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        acts = (cfg.get('security') or {}).get('password_protected_actions')
        if not isinstance(acts, list):
            return list(DEFAULT_PROTECTED_ACTIONS)
        return acts
    except Exception:
        return list(DEFAULT_PROTECTED_ACTIONS)


def is_protected_action(name):
    """★ 2026-11-15: 判断某类动作是否受密码保护（password_protected_actions 死配置接线）"""
    return name in get_protected_actions()


def get_admin_password():
    """★ 2026-09-17 密码收口：读取管理员密码（security.admin_password → admin_password → 'harven2026'）
    保险箱锁/解锁、面板卸载确认、全权模式验证都走这里，禁止各自另写读取逻辑"""
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.yaml')
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        sec = cfg.get('security') or {}
        pwd = sec.get('admin_password') or cfg.get('admin_password') or 'harven2026'
        return pwd
    except Exception:
        return 'harven2026'


def change_admin_password(old_password, new_password):
    """★ 2026-09-17 密码收口：修改管理员密码（同步写 security.admin_password + 顶层 admin_password）
    改完保险箱/面板/全权模式全部同步生效，无需重启

    Returns:
        dict: {success: bool, error: str}
    """
    if get_admin_password() != (old_password or ''):
        return {'success': False, 'error': '旧密码不对'}
    if not new_password or len(new_password) < 6:
        return {'success': False, 'error': '新密码至少 6 位'}
    if new_password == old_password:
        return {'success': False, 'error': '新密码不能和旧密码一样'}
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config.yaml')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    # 铁律 4：yaml.dump 必须传 dict()
    sec = cfg.get('security') if isinstance(cfg.get('security'), dict) else {}
    sec['admin_password'] = new_password
    sec['remote_password'] = new_password  # ★ R1 2026-09-23 密码统一：远程访问锁与管理员锁同步，全系统一把锁
    cfg['security'] = sec
    cfg['admin_password'] = new_password  # 兼容旧读取路径（enable_full_access 等）
    with open(cfg_path, 'w', encoding='utf-8') as f:
        yaml.dump(dict(cfg), f, allow_unicode=True, default_flow_style=False)
    log_operation('security', 'change_admin_password', 'OK')
    return {'success': True, 'error': ''}


def enable_full_access(password, duration=0):
    """开启全权模式

    Args:
        password: admin密码
        duration: 持续时间（秒），0=不限时

    Returns:
        dict: {success: bool, error: str}
    """
    # ★ 2026-09-17 密码收口：改用统一读取（原来只读顶层 admin_password，与保险箱不同源）
    if password != get_admin_password():
        return {'success': False, 'error': '密码错误'}

    with _lock:
        _set_mode('full')
        if duration > 0:
            global _full_access_until
            _full_access_until = time.time() + duration
        _save_mode()  # ★ 持久化

    log_operation('security', 'enable_full_access', 'OK')
    return {'success': True, 'error': '', 'mode': 'full'}


def disable_full_access():
    """关闭全权模式"""
    with _lock:
        _set_mode('safe')
    log_operation('security', 'disable_full_access', 'OK')
    return {'success': True, 'error': '', 'mode': 'safe'}


# ═══════════════════════════════════════════════════════════════
# 安全检查
# ═══════════════════════════════════════════════════════════════

def check_command(command):
    """检查命令是否危险

    Returns:
        tuple: (allowed: bool, reason: str)
    """
    if get_permission_mode() == 'full':
        return True, '全权模式'

    cmd_lower = (command or '').lower().strip()
    for dangerous in DANGEROUS_COMMANDS:
        if dangerous.lower() in cmd_lower:
            log_operation('execute_command', command[:100], f'拦截: {dangerous}')
            return False, f'⚠️ 危险命令被拦截: {dangerous}（需要全权模式）'

    return True, ''


def check_path(path, mode='write'):
    """检查路径是否允许操作

    Args:
        path: 文件路径
        mode: 'read' or 'write'

    Returns:
        tuple: (allowed: bool, reason: str)
    """
    if get_permission_mode() == 'full':
        return True, '全权模式'

    if not path:
        return True, ''

    # 标准化路径
    norm_path = path.replace('\\', '/').replace('//', '/').lower()

    # 检查禁止路径
    for forbidden in FORBIDDEN_PATHS:
        if norm_path.startswith(forbidden.lower()):
            log_operation('path_check', path[:100], f'拦截: 系统路径')
            return False, f'⚠️ 系统路径被保护: {path}'

    # 读模式 → 允许读任何非禁止路径
    if mode == 'read':
        return True, ''

    # 写模式 → 检查是否在允许路径里
    for allowed in ALLOWED_PATHS:
        if norm_path.startswith(allowed.lower()):
            return True, ''

    # 不在允许列表 → 检查是否是相对路径（在项目目录内）
    if not os.path.isabs(path):
        return True, ''

    log_operation('path_check', path[:100], f'拦截: 路径越界')
    return False, f'⚠️ 路径不在允许范围内: {path}（需要全权模式）'


def is_destructive(tool_name, params=None):
    """判断是否是破坏性操作

    Returns:
        tuple: (destructive: bool, reason: str)
    """
    if get_permission_mode() == 'full':
        return False, '全权模式'

    if tool_name in DESTRUCTIVE_TOOLS:
        return True, f'{tool_name} 是破坏性操作'

    # write_file覆盖已有文件
    if tool_name == 'write_file':
        path = (params or {}).get('path', '')
        if path and os.path.exists(path):
            return True, f'覆盖已有文件: {path}'

    # edit_file修改配置文件
    if tool_name == 'edit_file':
        path = (params or {}).get('file_path', '') or (params or {}).get('path', '')
        if path and ('config.yaml' in path.lower() or '.env' in path.lower()):
            return True, f'修改配置文件: {path}'

    return False, ''


# ═══════════════════════════════════════════════════════════════
# 操作日志
# ═══════════════════════════════════════════════════════════════

def log_operation(tool, params, result):
    """记录操作日志"""
    try:
        log_dir = os.path.dirname(LOG_FILE)
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        logs = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                try:
                    logs = json.load(f)
                except Exception:
                    logs = []

        logs.append({
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'tool': tool,
            'params': str(params)[:200],
            'result': str(result)[:200],
            'mode': get_permission_mode(),
        })

        # 最多保留200条
        logs = logs[-200:]

        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as _e: logger.debug(f"[security_guard] 忽略: {_e}")


def get_logs(limit=50):
    """获取操作日志"""
    try:
        if not os.path.exists(LOG_FILE):
            return []
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            logs = json.load(f)
        return logs[-limit:]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# 导入GGUF到Ollama
# ═══════════════════════════════════════════════════════════════

def import_gguf_to_ollama(gguf_path, model_name=None):
    """把GGUF文件导入到Ollama

    Args:
        gguf_path: GGUF文件路径
        model_name: Ollama模型名（可选）

    Returns:
        dict: {success: bool, error: str}
    """
    import subprocess
    import tempfile

    gguf_path = os.path.expanduser(gguf_path)
    if not os.path.exists(gguf_path):
        return {'success': False, 'error': f'GGUF文件不存在: {gguf_path}'}

    if not model_name:
        # 从文件名推断模型名
        basename = os.path.basename(gguf_path)
        model_name = basename.replace('.gguf', '').replace('.GGUF', '').lower()
        # 简化名字
        model_name = model_name.replace('huatuogpt-o1-7b.', 'huatuo-').replace('_', '-')

    # 创建临时Modelfile
    modelfile_content = f'FROM {gguf_path}\n'

    try:
        # 写Modelfile
        modelfile_path = os.path.join(tempfile.gettempdir(), 'harven_modelfile.txt')
        with open(modelfile_path, 'w', encoding='utf-8') as f:
            f.write(modelfile_content)

        # 执行ollama create
        result = subprocess.run(
            ['ollama', 'create', model_name, '-f', modelfile_path],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode == 0:
            log_operation('import_ollama', f'{model_name} from {gguf_path[:100]}', 'OK')
            return {'success': True, 'error': '', 'model_name': model_name}
        else:
            return {'success': False, 'error': result.stderr[:200]}
    except Exception as e:
        return {'success': False, 'error': str(e)[:200]}
