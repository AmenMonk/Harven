# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
哈文内置工具系统 v1.0
跟WorkBuddy的Bash/Read/Write一样，提供通用工具
AI调用工具名+参数 → 工具执行 → 返回结果 → 100%可靠

工具列表：
  download_file(url, save_path)     — 下载文件（支持重试/长timeout）
  execute_command(command)          — 执行系统命令（如 ollama pull）
  read_file(path)                   — 读文件
  write_file(path, content)         — 写文件
  list_dir(path)                    — 列目录
  delete_path(path)                 — 删除文件/目录
  scan_system()                     — 扫描系统（模型/库/硬件）
  install_model(name, source)       — 安装模型（通用）
  uninstall_model(name)             — 卸载模型
"""

import os
import sys
import json
import shutil
import logging
import subprocess
import time

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ★ #1233 M4-1（2026-10-26）HarvenEye 客户端（隔离红线：只 HTTP 不 import 引擎代码；
#   eye_client 自身零重依赖，导入失败也不拖垮工具系统——三工具函数引用改走兜底桩）
try:
    from src.core import eye_client as _eye
except Exception as _e:  # pragma: no cover
    logger.warning(f"[BuiltinTools] eye_client 导入失败，HarvenEye 三工具降级为不可用: {_e}")

    class _EyeStub:
        def __getattr__(self, name):
            def _dead(*a, **kw):
                return {'success': False, 'error': '视觉引擎客户端未加载（eye_client 导入失败）'}
            return _dead
    _eye = _EyeStub()

# ★ #1292（2026-10-29）自装编排工具（方案A②）：懒加载防拖垮工具系统
def _vision_install_tool(action='recommend', model_id='', confirmed=False):
    try:
        from src.vision.self_install import vision_install_tool
        return vision_install_tool(action=action, model_id=model_id, confirmed=confirmed)
    except Exception as e:
        return {'success': False, 'error': f'视觉自装编排器不可用: {e}'}



def download_file(url, save_path, timeout=600, max_retries=3):
    """下载文件（支持重试/长timeout）

    Args:
        url: 下载URL
        save_path: 保存路径
        timeout: 超时秒数（默认10分钟）
        max_retries: 最大重试次数

    Returns:
        dict: {success: bool, path: str, size_mb: float, error: str}
    """
    import requests

    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    for attempt in range(max_retries):
        try:
            logger.info(f"[BuiltinTools] 下载 {url} (尝试 {attempt+1}/{max_retries})")

            r = requests.get(url, stream=True, timeout=timeout, allow_redirects=True,
                           headers={'User-Agent': 'Mozilla/5.0'})
            r.raise_for_status()

            total = int(r.headers.get('content-length', 0))
            downloaded = 0

            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(1024 * 256):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

            size_mb = downloaded / 1024 / 1024
            logger.info(f"[BuiltinTools] 下载完成: {save_path} ({size_mb:.1f}MB)")

            return {
                'success': True,
                'path': save_path,
                'size_mb': round(size_mb, 1),
                'error': ''
            }

        except Exception as e:
            logger.warning(f"[BuiltinTools] 下载失败 (尝试 {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                return {
                    'success': False,
                    'path': save_path,
                    'size_mb': 0,
                    'error': str(e)
                }

    return {'success': False, 'path': save_path, 'size_mb': 0, 'error': '重试次数用完'}


def download_from_modelscope(model_id, cache_dir=None):
    """从ModelScope下载模型（国内满速，不重定向到被墙CDN）

    Args:
        model_id: ModelScope模型ID（如 'suxinchun/Self-Play'）
        cache_dir: 自定义缓存目录

    Returns:
        dict: {success: bool, path: str, error: str}
    """
    try:
        from modelscope import snapshot_download
        model_dir = snapshot_download(model_id, cache_dir=cache_dir)
        return {'success': True, 'path': model_dir, 'error': ''}
    except Exception as e:
        return {'success': False, 'path': '', 'error': str(e)}


def download_gguf_from_mirror(repo_id, filename, cache_dir=None):
    """从HuggingFace镜像下载GGUF文件

    Args:
        repo_id: HuggingFace仓库ID（如 'QuantFactory/HuatuoGPT-o1-7B-GGUF'）
        filename: 文件名（如 'HuatuoGPT-o1-7B.Q8_0.gguf'）
        cache_dir: 自定义缓存目录

    Returns:
        dict: {success: bool, path: str, size_mb: float, error: str}
    """
    if cache_dir is None:
        cache_dir = os.path.expanduser('~/.cache/huggingface/hub')

    repo_dir = repo_id.replace('/', '--')
    save_dir = os.path.join(cache_dir, f'models--{repo_dir}', 'snapshots', 'main')
    save_path = os.path.join(save_dir, filename)

    # 先用hf-mirror.com
    url = f'https://hf-mirror.com/{repo_id}/resolve/main/{filename}'
    result = download_file(url, save_path, timeout=600, max_retries=3)

    if result['success']:
        return result

    # 如果镜像失败，尝试用huggingface_hub（设置镜像环境变量）
    logger.info("[BuiltinTools] 镜像下载失败，尝试用huggingface_hub")
    try:
        os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
        os.environ.pop('HF_HUB_OFFLINE', None)  # 清除离线模式
        from huggingface_hub import hf_hub_download
        model_path = hf_hub_download(repo_id=repo_id, filename=filename)
        size_mb = os.path.getsize(model_path) / 1024 / 1024
        return {'success': True, 'path': model_path, 'size_mb': round(size_mb, 1), 'error': ''}
    except Exception as e:
        return {'success': False, 'path': save_path, 'size_mb': 0, 'error': str(e)}


def execute_command(command, timeout=300):
    """执行系统命令

    Args:
        command: 命令字符串（如 'ollama pull qwen2.5:14b'）
        timeout: 超时秒数

    Returns:
        dict: {success: bool, stdout: str, stderr: str, returncode: int}
    """
    # ★ 安全检查
    try:
        from src.core.security_guard import check_command, log_operation
        ok, reason = check_command(command)
        if not ok:
            return {'success': False, 'stdout': '', 'stderr': reason, 'returncode': -1}
    except ImportError:
        logger.warning('[ToolExec] security_guard 未安装，跳过命令安全检查')

    try:
        # ★ 2026-08-15: 危险命令检测（扩展黑名单）
        # ★ 2026-08-17: 加 Linux/macOS 危险命令
        # ★ 2026-10-20 FC慢治理#2: 正则词边界 — 'format' 子串误伤 Format-List 实锤；
        #   拦截 stderr 必须写明原因+建议，让外脑第一轮就改对不再瞎猜
        import re as _re
        _danger_patterns = [
            (r'\brm\s+(-[a-z]*r[a-z]*f|-rf|--recursive)', '递归强制删除'),
            (r'\bformat(\.com)?\s+[a-z]:', '磁盘格式化'),
            (r'\bdel\s+/f\s+/s\s+/q', '递归强制删除'),
            (r'\b(shutdown|reg\s+delete|reg\s+add|reg\s+import|diskpart|mkfs|bcdedit)\b', '系统级变更'),
            (r'\b(rmdir|rd)\s+/(s|q)', '递归删除'),
            (r'\btaskkill\s+/f\b', '强制杀进程'),
            (r'\bnet\s+(user|localgroup)\b', '账户管理'),
            (r'\bpowershell\s+(-enc|-e\s|command\s+.*invoke-expression)', '编码/动态执行'),
            (r'\binvoke-expression\b|\biex\b', '动态执行'),
            (r'\bcurl\b.*(-o|--output|-T|--upload)\b', 'curl 下载/外发文件'),
            (r'\bwget\b', 'wget 下载'),
            (r'\bdd\s+if=', '磁盘写入'),
            (r'\bchmod\s+777\b', '权限全开'),
            (r'\bchown\s+-r\b', '递归改属主'),
            (r'\b(kill\s+-9|killall)\b', '强制杀进程'),
            (r'\bsystemctl\s+(stop|disable)\b', '服务停用'),
            (r'\b(shutdown\s+-h|init\s+[06])\b', '关机重启'),
            (r'\b(fdisk|parted|wipefs)\b', '磁盘分区'),
            (r'\bumount\s+-f\b', '强制卸载'),
            (r'\bsc\s+(stop|delete)\b', '服务停用'),
        ]
        _cmd_lower = command.lower() if isinstance(command, str) else ' '.join(command).lower()
        for _pat, _cat in _danger_patterns:
            if _re.search(_pat, _cmd_lower):
                return {'success': False, 'stdout': '', 'stderr': (
                    f'危险命令已拦截: {_cat}（匹配规则 {_pat}）。'
                    f'请改用安全的替代写法（如只读查询命令、cmd 简单命令、去掉危险参数）后重试'
                ), 'returncode': -1}

        # ★ 2026-08-15: 命令注入防护——检测特殊字符
        if isinstance(command, str):
            _danger_chars = ['&', '|', ';', '`', '$(', '${']
            # 允许管道和重定向，但不允许命令链接符
            _cmd_no_pipes = command.replace('||', '').replace('&&', '')
            # ★ 2026-10-25 方案A（#1186）: PowerShell 计算属性 @{n='x';e={...}} 与单引号
            #   字符串是合法语法，';' 一刀切误杀外脑自检命令（10-25 14:23 一轮 4 条挂 3 条实锤）。
            #   ';' 检测前剥离 @{...} 块（括号匹配支持嵌套）与 '...' 字面量；
            #   其余危险字符（` $( ${）仍全串检测不豁免；语句位置的 ';' 照拦。
            _semi_probe = _cmd_no_pipes
            _i = _semi_probe.find('@{')
            while _i != -1:
                _depth = 0
                _j = _i + 1
                while _j < len(_semi_probe):
                    if _semi_probe[_j] == '{':
                        _depth += 1
                    elif _semi_probe[_j] == '}':
                        _depth -= 1
                        if _depth == 0:
                            break
                    _j += 1
                _semi_probe = _semi_probe[:_i] + _semi_probe[_j + 1:]
                _i = _semi_probe.find('@{')
            _semi_probe = _re.sub(r"'[^']*'", "''", _semi_probe)
            for _dc in _danger_chars:
                if _dc in _cmd_no_pipes and _dc not in ['|', '&']:
                    if _dc == ';' and ';' not in _semi_probe:
                        continue  # 分号只在 @{...} 哈希表/单引号字面量内 → 合法语法放行
                    _hint = ''
                    if _dc == ';':
                        _hint = '（此分号在语句位置属命令链接；如需 PowerShell 计算属性请确认写在 @{...} 内，或拆成多次工具调用）'
                    return {'success': False, 'stdout': '', 'stderr': f'命令含危险字符: {_dc}{_hint}', 'returncode': -1}

        # ★ 2026-08-14: 用shell=True支持复杂命令（管道、重定向等）
        if isinstance(command, str):
            result = subprocess.run(
                command,
                capture_output=True, text=True, timeout=timeout,
                cwd=BASE_DIR, shell=True,
                encoding='utf-8', errors='replace'  # ★ 修复GBK编码问题
            )
        else:
            result = subprocess.run(
                command,
                capture_output=True, text=True, timeout=timeout,
                cwd=BASE_DIR,
                encoding='utf-8', errors='replace'
            )

        # ★ 2026-10-20: 假失败命令宽容 — explorer.exe/start 打开窗口后返回码常为 1
        #   （实锤：'打开下载'执行 0.41s 窗口已开却返回 1 → 判失败 → 外脑重试多烧一轮 8 秒）
        _fire_and_forget = _cmd_lower.strip().startswith(('explorer', 'start ', 'cmd /c start', 'cmd /c explorer'))
        success = result.returncode == 0 or (_fire_and_forget and not (result.stderr or '').strip())
        return {
            'success': success,
            'stdout': result.stdout or '',
            'stderr': result.stderr or '',
            'returncode': result.returncode
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'stdout': '', 'stderr': '命令执行超时', 'returncode': -1}
    except Exception as e:
        return {'success': False, 'stdout': '', 'stderr': str(e), 'returncode': -1}


def read_file(path, max_size=1024*1024):
    """读文件

    Args:
        path: 文件路径
        max_size: 最大读取大小（默认1MB）

    Returns:
        dict: {success: bool, content: str, error: str}
    """
    try:
        if not os.path.exists(path):
            return {'success': False, 'content': '', 'error': '文件不存在'}
        size = os.path.getsize(path)
        if size > max_size:
            return {'success': False, 'content': '', 'error': f'文件太大({size/1024/1024:.1f}MB)，超过限制'}
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return {'success': True, 'content': content, 'error': ''}
    except Exception as e:
        return {'success': False, 'content': '', 'error': str(e)}


def write_file(path, content):
    """写文件

    Args:
        path: 文件路径
        content: 文件内容

    Returns:
        dict: {success: bool, path: str, error: str}
    """
    # ★ 安全检查
    try:
        from src.core.security_guard import check_path, is_destructive, log_operation
        ok, reason = check_path(path, 'write')
        if not ok:
            return {'success': False, 'path': path, 'error': reason}
        destructive, dreason = is_destructive('write_file', {'path': path})
        if destructive:
            log_operation('write_file', f'覆盖: {path}', '需确认')
    except ImportError:
        logger.warning('[ToolExec] security_guard 未安装，跳过文件写入安全检查')

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {'success': True, 'path': path, 'error': ''}
    except Exception as e:
        return {'success': False, 'path': path, 'error': str(e)}


def list_dir(path):
    """列目录

    Args:
        path: 目录路径

    Returns:
        dict: {success: bool, items: list, error: str}
    """
    try:
        if not os.path.exists(path):
            return {'success': False, 'items': [], 'error': '目录不存在'}
        items = []
        for item in os.listdir(path):
            full_path = os.path.join(path, item)
            is_dir = os.path.isdir(full_path)
            size = 0
            if not is_dir:
                size = os.path.getsize(full_path)
            items.append({
                'name': item,
                'is_dir': is_dir,
                'size': size,
                'path': full_path
            })
        return {'success': True, 'items': items, 'error': ''}
    except Exception as e:
        return {'success': False, 'items': [], 'error': str(e)}


def delete_path(path):
    """删除文件/目录

    Args:
        path: 文件或目录路径

    Returns:
        dict: {success: bool, freed_mb: float, error: str}
    """
    # ★ 安全检查
    try:
        from src.core.security_guard import check_path, log_operation
        ok, reason = check_path(path, 'write')
        if not ok:
            return {'success': False, 'freed_mb': 0, 'error': reason}
        log_operation('delete_path', path[:100], '执行')
    except ImportError:
        logger.warning('[ToolExec] security_guard 未安装，跳过路径删除安全检查')

    try:
        if not os.path.exists(path):
            return {'success': False, 'freed_mb': 0, 'error': '路径不存在'}

        # 计算大小
        if os.path.isfile(path):
            size = os.path.getsize(path)
        else:
            size = sum(os.path.getsize(os.path.join(dp, fn))
                      for dp, _, fns in os.walk(path) for fn in fns)

        # 删除
        if os.path.isfile(path):
            try:
                os.remove(path)
            except PermissionError:
                # 强制删除：先改权限再删
                import stat
                os.chmod(path, stat.S_IWRITE)
                try:
                    os.remove(path)
                except PermissionError:
                    # ★ 2026-08-17: 跨平台 — Windows 用 takeown，Linux/macOS 用 chmod
                    try:
                        from src.utils.platform import IS_WINDOWS
                    except ImportError:
                        IS_WINDOWS = True
                    if IS_WINDOWS:
                        subprocess.run(f'takeown /f "{path}"', shell=True, capture_output=True, timeout=10)
                        subprocess.run(f'icacls "{path}" /grant Everyone:F', shell=True, capture_output=True, timeout=10)
                    else:
                        subprocess.run(['chmod', 'u+w', path], capture_output=True, timeout=10)
                    os.remove(path)
        else:
            # 强制删除目录：用onerror回调改权限
            import stat
            try:
                from src.utils.platform import IS_WINDOWS as _IS_WIN
            except ImportError:
                _IS_WIN = True
            def _force_delete(func, fpath, exc_info):
                try:
                    os.chmod(fpath, stat.S_IWRITE)
                    func(fpath)
                except Exception:
                    # ★ 2026-08-17: 跨平台兜底
                    if _IS_WIN:
                        subprocess.run(f'takeown /f "{fpath}"', shell=True, capture_output=True, timeout=10)
                        subprocess.run(f'icacls "{fpath}" /grant Everyone:F', shell=True, capture_output=True, timeout=10)
                    else:
                        subprocess.run(['chmod', '-R', 'u+w', fpath], capture_output=True, timeout=10)
                    try:
                        func(fpath)
                    except:
                        pass  # 跳过删不掉的文件
            shutil.rmtree(path, onerror=_force_delete)

        freed_mb = size / 1024 / 1024
        logger.info(f"[BuiltinTools] 已删除 {path}，释放 {freed_mb:.1f}MB")

        return {'success': True, 'freed_mb': round(freed_mb, 1), 'error': ''}
    except Exception as e:
        return {'success': False, 'freed_mb': 0, 'error': str(e)}


def scan_system():
    """扫描系统（模型/库/硬件）

    Returns:
        dict: 扫描结果
    """
    try:
        from src.core.system_knowledge import scan_all
        knowledge = scan_all()

        # 重新分类
        try:
            from src.core.auto_classifier import auto_classify_and_register
            auto_classify_and_register()
        except Exception as _e: logger.debug(f"[builtin_tools] 忽略: {_e}")

        return {'success': True, 'knowledge': knowledge, 'error': ''}
    except Exception as e:
        return {'success': False, 'knowledge': {}, 'error': str(e)}


def install_model(name, source='auto', quantization=None):
    """安装模型（通用）

    Args:
        name: 模型名称（如 '华佗'/'qwen2.5'/'deepseek'）
        source: 来源（auto/modelscope/huggingface/ollama）
        quantization: 量化版本（如 'Q8_0'/'Q4_K_M'）

    Returns:
        dict: {success: bool, path: str, error: str}
    """
    # ★ 2026-08-25: FC 参数校验——外脑模型可能传 list/句子，先清洗
    if isinstance(name, list):
        # list → 取首个字符串元素
        name = next((x for x in name if isinstance(x, str)), '')
    if not isinstance(name, str) or not name.strip():
        return {'success': False, 'path': '', 'error': '参数错误：模型名必须是非空字符串',
                'message': '请传入正确的模型名，如 qwen2.5 / deepseek / 华佗'}
    name = name.strip()
    if len(name) > 60 or ' ' in name or '？' in name or '吗' in name:
        return {'success': False, 'path': '',
                'error': f'参数错误：「{name[:40]}...」不是有效的模型名',
                'message': '模型名应简短无空格（如 qwen2.5 / deepseek / 华佗）。请确认用户是否真的想安装模型'}
    msg = name.lower()

    # ── 华佗模型 ──
    if '华佗' in name or 'huatuo' in msg:
        # 先检查是否已有
        knowledge_file = os.path.join(BASE_DIR, 'data', 'system_knowledge.json')
        if os.path.exists(knowledge_file):
            with open(knowledge_file, 'r', encoding='utf-8') as f:
                k = json.load(f)
            for m in k.get('modelscope_models', []) + k.get('huggingface_models', []):
                if 'self-play' in m.get('id', '').lower() or 'huatuo' in m.get('id', '').lower():
                    return {'success': True, 'path': m.get('path', ''),
                           'error': '', 'message': '模型已存在'}

        # 下载GGUF版
        q = quantization or 'Q8_0'
        if 'q4' in q.lower() or '轻量' in name:
            filename = 'HuatuoGPT-o1-7B.Q4_K_M.gguf'
            version = "Q4_K_M (4.7GB)"
        else:
            filename = 'HuatuoGPT-o1-7B.Q8_0.gguf'
            version = "Q8_0 (8.1GB)"

        result = download_gguf_from_mirror(
            repo_id='QuantFactory/HuatuoGPT-o1-7B-GGUF',
            filename=filename
        )

        if result['success']:
            # 重新扫描
            scan_system()
            return {
                'success': True,
                'path': result['path'],
                'size_mb': result.get('size_mb', 0),
                'error': '',
                'message': f'华佗模型 {version} 下载成功'
            }
        return {'success': False, 'path': '', 'error': result['error'], 'message': ''}

    # ── Ollama模型 ──
    _ollama_map = {
        'qwen2.5': 'qwen2.5:14b', 'qwen': 'qwen2.5:14b', '通义千问': 'qwen2.5:14b',
        'deepseek': 'deepseek-r1:14b', 'deepseek-r1': 'deepseek-r1:14b',
        'llama3': 'llama3.1:8b', 'llama': 'llama3.1:8b',
        'gemma2': 'gemma2:9b', 'gemma': 'gemma2:9b',
        'phi3': 'phi3', 'minicpm': 'minicpm-v:8b',
        'codellama': 'codellama:7b', 'nomic': 'nomic-embed-text:latest',
    }

    for keyword, model_name in _ollama_map.items():
        if keyword in msg:
            result = execute_command(['ollama', 'pull', model_name], timeout=600)
            if result['success']:
                return {'success': True, 'path': '', 'error': '',
                       'message': f'{model_name} 安装成功'}
            return {'success': False, 'path': '', 'error': result['stderr'],
                   'message': ''}

    return {'success': False, 'path': '', 'error': f'未知模型: {name}', 'message': ''}


def uninstall_model(name):
    """卸载模型

    Args:
        name: 模型名称

    Returns:
        dict: {success: bool, freed_mb: float, error: str}
    """
    msg = name.lower()

    # 查找模型
    knowledge_file = os.path.join(BASE_DIR, 'data', 'system_knowledge.json')
    if not os.path.exists(knowledge_file):
        return {'success': False, 'freed_mb': 0, 'error': '系统知识文件不存在'}

    with open(knowledge_file, 'r', encoding='utf-8') as f:
        k = json.load(f)

    # 检查ModelScope/HuggingFace模型
    for m in k.get('modelscope_models', []) + k.get('huggingface_models', []):
        model_id = m.get('id', '').lower()
        model_name = m.get('name', '').lower()
        if ('华佗' in name or 'huatuo' in msg or 'self-play' in msg or
            model_id in msg or model_name in msg):
            path = m.get('path', '')
            if path and os.path.exists(path):
                result = delete_path(path)
                if result['success']:
                    scan_system()  # 重新扫描
                    return {
                        'success': True,
                        'freed_mb': result['freed_mb'],
                        'error': '',
                        'message': f'已删除 {m.get("id", "模型")}'
                    }
                return {'success': False, 'freed_mb': 0, 'error': result['error']}

    # 检查Ollama模型
    for m in k.get('ollama_models', []):
        ollama_name = m.get('name', '')
        if ollama_name.lower() in msg or msg in ollama_name.lower():
            result = execute_command(['ollama', 'rm', ollama_name])
            if result['success']:
                scan_system()
                return {'success': True, 'freed_mb': 0, 'error': '',
                       'message': f'已删除 {ollama_name}'}
            return {'success': False, 'freed_mb': 0, 'error': result['stderr']}

    return {'success': False, 'freed_mb': 0, 'error': f'未找到模型: {name}'}


# ═══════════════════════════════════════════════════════════════
# ★ 2026-07-11: 补充工具（参照WorkBuddy）
# ═══════════════════════════════════════════════════════════════

def edit_file(path, old_string, new_string):
    """编辑文件（精确替换，对应WorkBuddy的Edit）

    Args:
        path: 文件路径
        old_string: 要替换的文本
        new_string: 替换后的文本

    Returns:
        dict: {success: bool, error: str}
    """
    try:
        if not os.path.exists(path):
            return {'success': False, 'error': '文件不存在'}
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        if old_string not in content:
            return {'success': False, 'error': '未找到要替换的文本'}
        new_content = content.replace(old_string, new_string, 1)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return {'success': True, 'error': ''}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def search_content(pattern, path=None, file_type=None):
    """搜索文件内容（对应WorkBuddy的Grep）

    Args:
        pattern: 正则表达式
        path: 搜索路径（默认项目根目录）
        file_type: 文件类型（如 'py', 'js'）

    Returns:
        dict: {success: bool, results: list, error: str}
    """
    try:
        import re
        search_path = path or BASE_DIR
        results = []
        regex = re.compile(pattern, re.IGNORECASE)

        for root, dirs, files in os.walk(search_path):
            # 跳过不相关目录
            dirs[:] = [d for d in dirs if d not in ('.venv', '__pycache__', '.git', 'node_modules')]
            for filename in files:
                if file_type and not filename.endswith(f'.{file_type}'):
                    continue
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append({
                                    'file': filepath,
                                    'line': i,
                                    'content': line.strip()[:200]
                                })
                                if len(results) >= 50:
                                    return {'success': True, 'results': results, 'error': ''}
                except Exception:
                    continue

        return {'success': True, 'results': results, 'error': ''}
    except Exception as e:
        return {'success': False, 'results': [], 'error': str(e)}


def search_files(pattern, path=None):
    """搜索文件名（对应WorkBuddy的Glob）

    Args:
        pattern: 文件名模式（如 '*.py', '**/*.js'）
        path: 搜索路径

    Returns:
        dict: {success: bool, files: list, error: str}
    """
    try:
        import fnmatch
        search_path = path or BASE_DIR
        results = []

        # 简化pattern
        if pattern.startswith('**/'):
            pattern = pattern[3:]

        for root, dirs, files in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in ('.venv', '__pycache__', '.git', 'node_modules')]
            for filename in files:
                if fnmatch.fnmatch(filename, pattern):
                    results.append(os.path.join(root, filename))
                    if len(results) >= 100:
                        break

        return {'success': True, 'files': results, 'error': ''}
    except Exception as e:
        return {'success': False, 'files': [], 'error': str(e)}


def web_search(query):
    """联网搜索（对应WorkBuddy的WebSearch）

    Args:
        query: 搜索关键词

    Returns:
        dict: {success: bool, results: list, error: str}
    """
    try:
        # ★ 内置 Bing HTML 搜索（国内直连，无需 API Key / Docker）
        import requests
        from urllib.parse import quote_plus
        from bs4 import BeautifulSoup

        url = f"https://cn.bing.com/search?q={quote_plus(query)}&setlang=zh-CN"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return {'success': False, 'results': [], 'error': f'Bing HTTP {resp.status_code}'}

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for item in soup.select(".b_algo"):
            if len(results) >= 5:
                break
            title_elem = item.select_one("h2 a")
            snippet_elem = item.select_one(".b_caption p, .b_caption .b_paractl, p")
            if title_elem:
                results.append({
                    "title": title_elem.get_text(strip=True)[:200],
                    "url": title_elem.get("href", ""),
                    "snippet": snippet_elem.get_text(strip=True)[:500] if snippet_elem else ""
                })
        if results:
            return {'success': True, 'results': results, 'error': ''}
        return {'success': False, 'results': [], 'error': '搜索未返回结果'}
    except Exception as e:
        return {'success': False, 'results': [], 'error': str(e)}


def search_knowledge(query):
    """搜索知识库

    Args:
        query: 搜索关键词

    Returns:
        dict: {success: bool, results: list, error: str}
    """
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        results = kb.search(query, top_k=5)
        return {'success': True, 'results': results, 'error': ''}
    except Exception as e:
        return {'success': False, 'results': [], 'error': str(e)}


def list_knowledge():
    """列出知识库内容

    Returns:
        dict: {success: bool, items: list, total: int, error: str}
    """
    try:
        from src.core.knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        items = []

        # ★ 2026-07-13: 用kb.documents + kb.get_stats()
        # 统计每个来源的文档数
        source_counts = {}
        source_texts = {}
        for doc in kb.documents:
            src = doc.get('source', 'unknown') if isinstance(doc, dict) else 'unknown'
            text = doc.get('text', '') if isinstance(doc, dict) else str(doc)
            source_counts[src] = source_counts.get(src, 0) + 1
            if src not in source_texts:
                source_texts[src] = text[:100]

        for src, count in sorted(source_counts.items()):
            items.append({
                'filename': src,
                'format': src.split('.')[-1] if '.' in src else 'text',
                'chunks': count,
                'size_mb': 0,
                'imported_at': '',
                'preview': source_texts.get(src, '')[:100],
            })

        return {'success': True, 'items': items, 'total': len(items), 'error': ''}
    except Exception as e:
        return {'success': False, 'items': [], 'total': 0, 'error': str(e)}


def execute_vertical(model_id, params=None):
    """执行垂直模型

    Args:
        model_id: 垂直模型ID（如 'stable-diffusion', 'musicgen-small'）
        params: 参数字典（如 {'prompt': '画一只猫'}）

    Returns:
        dict: {success: bool, output: str, error: str}
    """
    try:
        params = params or {}
        from src.core.vertical_api import _BUILTIN_MODELS, _dynamic_models
        # 查找模型
        model = next((m for m in _BUILTIN_MODELS if m['id'] == model_id), None)
        if not model:
            model = _dynamic_models.get(model_id)
        if not model:
            return {'success': False, 'output': '', 'error': f'未知模型: {model_id}'}

        engine_type = model.get('engine', 'tool_executor')
        if engine_type == 'tool_executor':
            from src.core.vertical_api import _execute_via_tool_executor
            result = _execute_via_tool_executor(model, params)
        elif engine_type == 'model':
            from src.core.vertical_api import _execute_via_model
            result = _execute_via_model(model, params)
        elif engine_type == 'ollama':
            from src.core.vertical_api import _execute_via_ollama
            result = _execute_via_ollama(model, params)
        elif engine_type == 'llama_cpp':
            from src.core.vertical_api import _execute_via_llama_cpp
            result = _execute_via_llama_cpp(model, params)
        elif engine_type == 'dynamic':
            from src.core.vertical_api import _execute_via_dynamic_adapter
            result = _execute_via_dynamic_adapter(model, params)
        else:
            return {'success': False, 'output': '', 'error': f'未知引擎类型: {engine_type}'}

        return {'success': True, 'output': result, 'error': ''}
    except Exception as e:
        return {'success': False, 'output': '', 'error': str(e)}


def get_time():
    """获取当前时间

    Returns:
        dict: {success: bool, time: str, timestamp: int}
    """
    from datetime import datetime
    now = datetime.now()
    return {
        'success': True,
        'time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'timestamp': int(now.timestamp())
    }


def check_software(name):
    """★ 2026-07-24: 检查软件安装状态

    检查指定软件是否已安装、版本、运行状态。
    模型问"docker装了吗"/"python版本"时调用此工具，不写代码。

    Args:
        name: 软件名（docker/python/git/node/ollama/npm/pip/blender/gimp 等）

    Returns:
        dict: {success: bool, name: str, installed: bool, version: str, running: bool, path: str, error: str}
    """
    name = name.strip().lower()
    logger.info(f"[BuiltinTools] check_software: {name}")

    # 特殊检查映射：软件名 → 检查命令
    CHECK_MAP = {
        'docker':      {'cmd': 'docker --version',         'run_cmd': 'docker info'},
        'python':      {'cmd': 'python --version',          'run_cmd': None},
        'python3':     {'cmd': 'python3 --version',         'run_cmd': None},
        'git':         {'cmd': 'git --version',             'run_cmd': None},
        'node':        {'cmd': 'node --version',            'run_cmd': None},
        'npm':         {'cmd': 'npm --version',             'run_cmd': None},
        'ollama':      {'cmd': 'ollama --version',          'run_cmd': 'ollama list'},
        'pip':         {'cmd': 'pip --version',             'run_cmd': None},
        'java':        {'cmd': 'java -version',             'run_cmd': None},
        'go':          {'cmd': 'go version',                 'run_cmd': None},
        'rust':        {'cmd': 'rustc --version',           'run_cmd': None},
        'cargo':       {'cmd': 'cargo --version',           'run_cmd': None},
        'blender':     {'cmd': 'blender --version',         'run_cmd': None},
        'ffmpeg':      {'cmd': 'ffmpeg -version',           'run_cmd': None},
        'redis':       {'cmd': 'redis-cli --version',       'run_cmd': 'redis-cli ping'},
        'sqlite':      {'cmd': 'sqlite3 --version',         'run_cmd': None},
    }

    check = CHECK_MAP.get(name)
    if not check:
        # 未知软件，尝试通用检查
        check = {'cmd': f'{name} --version', 'run_cmd': None}

    try:
        # 检查是否安装（Windows用GBK防编码错误）
        _encoding = 'gbk' if sys.platform == 'win32' else 'utf-8'
        result = subprocess.run(
            check['cmd'], shell=True, capture_output=True, text=True, timeout=10,
            encoding=_encoding, errors='replace'
        )

        if result.returncode != 0 and not result.stdout:
            # 尝试 where/which
            where_cmd = 'where' if sys.platform == 'win32' else 'which'
            where_result = subprocess.run(
                f'{where_cmd} {name}', shell=True, capture_output=True, text=True, timeout=5
            )
            if where_result.returncode != 0:
                return {
                    'success': True,
                    'name': name,
                    'installed': False,
                    'version': '',
                    'running': False,
                    'path': '',
                    'error': f'{name} 未安装',
                }

        version = (result.stdout or result.stderr or '').strip().split('\n')[0]

        # 检查是否在运行
        running = False
        if check.get('run_cmd'):
            try:
                run_result = subprocess.run(
                    check['run_cmd'], shell=True, capture_output=True, text=True, timeout=10
                )
                running = run_result.returncode == 0
            except Exception:
                running = False

        # 获取路径
        path = ''
        try:
            where_cmd = 'where' if sys.platform == 'win32' else 'which'
            where_result = subprocess.run(
                f'{where_cmd} {name}', shell=True, capture_output=True, text=True, timeout=5
            )
            if where_result.returncode == 0:
                path = where_result.stdout.strip().split('\n')[0]
        except Exception:
            pass

        return {
            'success': True,
            'name': name,
            'installed': True,
            'version': version[:200],
            'running': running,
            'path': path,
            'error': '',
        }

    except subprocess.TimeoutExpired:
        return {
            'success': True,
            'name': name,
            'installed': True,
            'version': '检测超时',
            'running': False,
            'path': '',
            'error': '命令执行超时',
        }
    except Exception as e:
        logger.error(f"[BuiltinTools] check_software异常: {e}")
        return {
            'success': False,
            'name': name,
            'installed': False,
            'version': '',
            'running': False,
            'path': '',
            'error': str(e),
        }


def install_package(name):
    """安装Python包

    Args:
        name: 包名（如 'requests', 'transformers'）

    Returns:
        dict: {success: bool, stdout: str, error: str}
    """
    venv_python = os.path.join(BASE_DIR, '.venv', 'Scripts', 'python.exe')
    if not os.path.exists(venv_python):
        venv_python = 'python'
    result = execute_command([venv_python, '-m', 'pip', 'install', name], timeout=120)
    return {
        'success': result['success'],
        'stdout': result['stdout'],
        'error': result['stderr'] if not result['success'] else ''
    }


def ask_user(question, options=None):
    """问用户问题（对应WorkBuddy的AskUserQuestion）

    Args:
        question: 问题
        options: 选项列表

    Returns:
        dict: {success: bool, question: str, options: list}
    """
    return {
        'success': True,
        'question': question,
        'options': options or [],
        'message': '需要用户确认: ' + question
    }


def present_file(path):
    """展示文件给用户（对应WorkBuddy的present_files）

    Args:
        path: 文件路径

    Returns:
        dict: {success: bool, path: str, error: str}
    """
    if os.path.exists(path):
        return {'success': True, 'path': path, 'error': ''}
    return {'success': False, 'path': path, 'error': '文件不存在'}


# ★ #1212（2026-10-25）HarvenHub 主动搜索+安装：装前必须弹确认卡征求用户同意（定版拍板）
def hub_search_tool(query, item_type=None, limit=10):
    """搜索 HarvenHub 官方市场内容

    Args:
        query: 搜索词
        item_type: 可选类型过滤（skill/template/bundle/model/tool等）
        limit: 返回条数上限

    Returns:
        dict: {success: bool, message: str, items: list, error: str}
    """
    try:
        from src.core.hub_install import search_hub
        return search_hub(query, item_type=item_type, limit=limit)
    except Exception as e:
        logger.error(f"[BuiltinTools] hub_search异常: {e}")
        return {'success': False, 'message': '', 'items': [], 'error': str(e)}


def hub_install_tool(item_id):
    """发起 Hub 内容安装（只生成确认卡，用户点头才真下载安装）

    Args:
        item_id: hub_search 结果里的内容 ID

    Returns:
        dict: {success: bool, message: str, confirm_card: dict, error: str}
    """
    try:
        from src.core.hub_install import request_install
        return request_install(item_id)
    except Exception as e:
        logger.error(f"[BuiltinTools] hub_install异常: {e}")
        return {'success': False, 'message': '', 'error': str(e)}


def task_create(subject, description=''):
    """创建任务（对应WorkBuddy的TaskCreate）

    Args:
        subject: 任务标题
        description: 任务描述

    Returns:
        dict: {success: bool, task_id: str}
    """
    import uuid
    task_id = str(uuid.uuid4())[:8]
    # 存储到本地
    tasks_file = os.path.join(BASE_DIR, 'data', 'tasks.json')
    tasks = {}
    if os.path.exists(tasks_file):
        with open(tasks_file, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
    tasks[task_id] = {
        'subject': subject,
        'description': description,
        'status': 'pending',
        'created': get_time()['time']
    }
    os.makedirs(os.path.dirname(tasks_file), exist_ok=True)
    with open(tasks_file, 'w', encoding='utf-8') as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    return {'success': True, 'task_id': task_id}


def task_list():
    """列出所有任务

    Returns:
        dict: {success: bool, tasks: list}
    """
    tasks_file = os.path.join(BASE_DIR, 'data', 'tasks.json')
    if not os.path.exists(tasks_file):
        return {'success': True, 'tasks': []}
    with open(tasks_file, 'r', encoding='utf-8') as f:
        tasks = json.load(f)
    return {'success': True, 'tasks': list(tasks.values())}


def search_conversation(query):
    """搜索历史对话（对应WorkBuddy的conversation_search）

    Args:
        query: 搜索关键词

    Returns:
        dict: {success: bool, results: list, error: str}
    """
    try:
        from src.core.memory import search_history
        results = search_history(query, limit=5)
        return {'success': True, 'results': results, 'error': ''}
    except Exception as e:
        return {'success': False, 'results': [], 'error': str(e)}


def execute_skill(skill_id, user_input=''):
    """执行Skill（对应WorkBuddy的Skill工具）"""
    try:
        from src.core.skills_manager import get_skills_manager
        sm = get_skills_manager()
        skill = sm.get_skill(skill_id)
        if not skill:
            return {'success': False, 'output': '', 'error': f'未找到Skill: {skill_id}'}
        if not skill.enabled:
            return {'success': False, 'output': '', 'error': f'Skill已禁用: {skill_id}'}
        return {'success': True, 'output': skill.prompt, 'skill_name': skill.name, 'error': ''}
    except Exception as e:
        return {'success': False, 'output': '', 'error': str(e)}


def execute_skill_script(skill_name, action='run', params=None):
    """★ 执行中级Skill的可执行脚本（Python沙箱）

    当Skill有scripts/目录时，优先使用此工具而不是execute_skill。
    脚本在安全沙箱中运行（AST预扫描 + 路径限制）。

    Args:
        skill_name: 技能名称（data/skills/ 下的目录名）
        action: 要执行的操作（如 add/query/delete/summarize 等）
        params: 传给脚本的参数（dict）

    Returns:
        {success, output, error, skill_name, action}
    """
    try:
        from src.core.scripts_runner import run_skill_script
        return run_skill_script(skill_name, action=action, params=params or {})
    except Exception as e:
        return {'success': False, 'output': '', 'error': str(e), 'skill_name': skill_name, 'action': action}


def list_skill_scripts_tool():
    """列出所有拥有可执行脚本的技能"""
    try:
        from src.core.scripts_runner import list_skill_scripts
        scripts = list_skill_scripts()
        return {'success': True, 'scripts': scripts, 'error': '', 'count': len(scripts)}
    except Exception as e:
        return {'success': False, 'scripts': [], 'error': str(e)}


# ===== CLI-Anything 集成 =====

_CLI_CHECK_CACHE = {'ok': None, 'ts': 0.0}   # ★ 2026-10-21 速度包：探测结果缓存（1h TTL），免每条消息重复 pip show


def _check_cli_anything_installed():
    """检测 cli-anything-hub 是否已安装（★ 缓存 1 小时：未装时每条消息都跑 pip show 白耗 1~2s）"""
    import time as _t
    if _CLI_CHECK_CACHE['ok'] is not None and _t.time() - _CLI_CHECK_CACHE['ts'] < 3600:
        return _CLI_CHECK_CACHE['ok']
    ok = False
    try:
        import cli_anything_hub
        ok = True
    except ImportError:
        pass
    if not ok:
        # pip show 检测
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'show', 'cli-anything-hub'],
                capture_output=True, text=True, timeout=10
            )
            ok = result.returncode == 0 and 'Name:' in result.stdout
        except Exception:
            ok = False
    _CLI_CHECK_CACHE['ok'] = ok
    _CLI_CHECK_CACHE['ts'] = _t.time()
    return ok


def _get_available_cli_modules():
    """获取已安装的CLI-Anything子模块列表（如blender/gimp等）"""
    if not _check_cli_anything_installed():
        return []
    try:
        import cli_anything_hub
        if hasattr(cli_anything_hub, 'list_modules'):
            return cli_anything_hub.list_modules()
        # 回退：检查已知子模块
        known_modules = [
            'blender', 'gimp', 'inkscape', 'krita', 'audacity',
            'obs', 'ffmpeg', 'imagemagick', 'darktable', 'scribus',
            'natron', 'opentoonz', 'synfig', 'pencil2d', 'rawtherapee',
        ]
        installed = []
        for mod in known_modules:
            try:
                __import__(f'cli_anything_hub.{mod}', fromlist=['_'])
                installed.append(mod)
            except ImportError:
                pass
        return installed
    except Exception as e:
        # ★ 2026-10-21 降噪：未安装是常态（用户按需装），每条消息 WARNING 刷屏改 DEBUG
        logger.debug(f"[CLI-Anything] 获取子模块列表失败: {e}")
        return []


def _check_software_installed(software_name):
    """检测本机是否安装了目标软件"""
    # 常见软件的可执行文件名映射
    exe_map = {
        'blender': ['blender', 'blender.exe'],
        'gimp': ['gimp', 'gimp-2.10', 'gimp-2.99'],
        'inkscape': ['inkscape', 'inkscape.exe'],
        'krita': ['krita', 'krita.exe'],
        'audacity': ['audacity', 'audacity.exe'],
        'obs': ['obs', 'obs64'],
        'ffmpeg': ['ffmpeg', 'ffmpeg.exe'],
        'imagemagick': ['convert', 'magick'],
        'darktable': ['darktable'],
        'scribus': ['scribus'],
        'natron': ['natron', 'Natron'],
        'opentoonz': ['opentoonz', 'OpenToonz'],
        'synfig': ['synfig', 'synfigstudio'],
        'pencil2d': ['pencil2d', 'Pencil2D'],
        'rawtherapee': ['rawtherapee', 'rawtherapee-cli'],
    }
    exe_names = exe_map.get(software_name.lower(), [software_name])
    for exe in exe_names:
        if shutil.which(exe):
            return True
    return False


def cli_anything_list():
    """列出所有可用的CLI软件工具，含安装状态"""
    result = {
        'success': True,
        'installed': _check_cli_anything_installed(),
        'modules': [],
        'error': '',
    }

    if not result['installed']:
        result['message'] = 'CLI-Anything Hub 未安装。可使用 cli_anything_install 安装基础包和需要的子模块。'
        result['available_modules'] = [
            {'name': 'blender', 'desc': 'Blender 3D建模/渲染/动画', 'local_installed': _check_software_installed('blender')},
            {'name': 'gimp', 'desc': 'GIMP 图像处理', 'local_installed': _check_software_installed('gimp')},
            {'name': 'inkscape', 'desc': 'Inkscape 矢量绘图', 'local_installed': _check_software_installed('inkscape')},
            {'name': 'krita', 'desc': 'Krita 数字绘画', 'local_installed': _check_software_installed('krita')},
            {'name': 'audacity', 'desc': 'Audacity 音频编辑', 'local_installed': _check_software_installed('audacity')},
            {'name': 'ffmpeg', 'desc': 'FFmpeg 音视频处理', 'local_installed': _check_software_installed('ffmpeg')},
            {'name': 'imagemagick', 'desc': 'ImageMagick 图像批量处理', 'local_installed': _check_software_installed('imagemagick')},
        ]
        return result

    try:
        modules = _get_available_cli_modules()
        for mod in modules:
            result['modules'].append({
                'name': mod,
                'cli_installed': True,
                'local_installed': _check_software_installed(mod),
            })
    except Exception as e:
        result['error'] = str(e)

    return result


def cli_anything_execute(software, command, args=None):
    """通过CLI-Anything执行软件命令

    Args:
        software: 软件名（如 blender/gimp/inkscape）
        command: CLI-Anything命令（如 render/export/convert）
        args: 参数字典

    Returns:
        dict: {success: bool, output: str, error: str}
    """
    if not _check_cli_anything_installed():
        return {
            'success': False,
            'output': '',
            'error': 'CLI-Anything Hub 未安装。请先使用 cli_anything_install 安装基础包。'
        }

    modules = _get_available_cli_modules()
    if software not in modules:
        return {
            'success': False,
            'output': '',
            'error': f'子模块 "{software}" 未安装。可用模块: {", ".join(modules) if modules else "无"}。请使用 cli_anything_install 安装。'
        }

    try:
        args = args or {}
        import cli_anything_hub
        if hasattr(cli_anything_hub, 'execute'):
            result = cli_anything_hub.execute(software, command, **args)
            return {
                'success': True,
                'output': str(result),
                'error': '',
            }
        else:
            # 回退：通过subprocess调用
            full_cmd = [sys.executable, '-m', f'cli_anything_hub.{software}', command]
            for k, v in args.items():
                full_cmd.append(f'--{k}')
                if v is not True:
                    full_cmd.append(str(v))
            proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=300)
            return {
                'success': proc.returncode == 0,
                'output': proc.stdout.strip(),
                'error': proc.stderr.strip() if proc.returncode != 0 else '',
            }
    except Exception as e:
        return {'success': False, 'output': '', 'error': str(e)}


def cli_anything_install(software):
    """安装CLI-Anything或某个软件的子模块

    Args:
        software: 软件名（如 'hub' 安装基础包, 'blender' 安装blender子模块）

    Returns:
        dict: {success: bool, message: str, error: str}
    """
    # ★ 2026-07-22: 软件本体下载地址（CLI模块装完后提示用户去下载软件本身）
    _SOFTWARE_URLS = {
        'blender':    'https://www.blender.org/download/',
        'gimp':       'https://www.gimp.org/downloads/',
        'inkscape':   'https://inkscape.org/release/',
        'krita':      'https://krita.org/en/download/',
        'audacity':   'https://www.audacityteam.org/download/',
        'obs':        'https://obsproject.com/download',
        'ffmpeg':     'https://ffmpeg.org/download.html',
        'imagemagick':'https://imagemagick.org/script/download.php',
        'darktable':  'https://www.darktable.org/install/',
        'scribus':    'https://www.scribus.net/downloads/',
    }

    def _build_install_msg(software, msg):
        """构建安装成功消息（含软件本体下载提示）"""
        base_msg = f'{msg} 安装成功！现在可以通过 cli_anything_execute 调用 {software} 相关命令。'
        if software in _SOFTWARE_URLS:
            base_msg += (
                f'\n\n⚠️ 重要提示：CLI控制模块已装好，但 {software} 软件本体需要您自行下载安装。\n'
                f'下载地址：{_SOFTWARE_URLS[software]}\n'
                f'安装软件后，我就能通过命令行控制 {software} 了。'
            )
        return base_msg

    try:
        # ★ 2026-07-22: 如果hub已安装，安装hub时返回成功（"已安装"不算失败）
        if software == 'hub' or software == 'all':
            if _check_cli_anything_installed():
                return {
                    'success': True,
                    'message': 'CLI-Anything Hub 已安装，无需重复安装。请用 cli_anything_install("blender") 安装子模块。',
                    'error': '',
                }
            pkg = 'cli-anything-hub'
            msg = 'CLI-Anything Hub 基础包'
        else:
            # 检查子模块是否已安装
            existing_modules = _get_available_cli_modules()
            if software in existing_modules:
                return {
                    'success': True,
                    'message': f'{software} 子模块已安装，无需重复安装。可用 cli_anything_execute 直接使用。',
                    'error': '',
                }
            pkg = f'cli-anything-hub[{software}]'
            msg = f'CLI-Anything {software} 子模块'

        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', pkg],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode == 0:
            return {
                'success': True,
                'message': _build_install_msg(software, msg),
                'error': '',
            }
        else:
            # 检查是否"已安装"导致的非零返回码
            combined = result.stdout + result.stderr
            if 'already installed' in combined or 'Requirement already' in combined:
                return {
                    'success': True,
                    'message': f'{msg} 已安装（无需重复安装）。',
                    'error': '',
                }
            error_lines = result.stderr.strip().split('\n')[-5:]
            return {
                'success': False,
                'message': '',
                'error': '\n'.join(error_lines) if error_lines else result.stderr.strip(),
            }
    except subprocess.TimeoutExpired:
        return {'success': False, 'message': '', 'error': '安装超时（120秒），请检查网络后重试'}
    except Exception as e:
        return {'success': False, 'message': '', 'error': str(e)}


def list_skills():
    """列出所有可用Skills"""
    try:
        from src.core.skills_manager import get_skills_manager
        sm = get_skills_manager()
        return {'success': True, 'skills': sm.list_skills(), 'error': ''}
    except Exception as e:
        return {'success': False, 'skills': [], 'error': str(e)}


def spawn_agent(task, agent_type='general'):
    """启动子代理（multi_agent.py 模块不存在，暂不可用）"""
    return {'success': False, 'result': '', 'error': '子代理模块未安装（multi_agent.py 不存在）'}


def price_compare_tool(query, kind=''):
    """★ 2026-10-20 比价 FC 工具（受权限中心 net_browse_compare 分闸控制）
    抓到的是各平台公开价对比表+深链，永不代下单（参谋不做代购）。
    """
    try:
        from src.core.permission_center import check_web
        ok, msg = check_web('browse_compare')
        if not ok:
            return {'success': False, 'error': msg, 'hint': '让用户到 设置→权限中心→联网 打开「网页浏览/比价」'}
        from src.core.price_compare import parse_query, compare
        parsed = parse_query(query)
        result = compare(parsed['kw'], kind or parsed['kind'])
        return {'success': True, **result,
                'disclaimer': '以上为公开页价格区间参考；下单请点链接自行完成，哈文不代付。'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def web_fetch(url, max_length=10000):
    """抓取网页内容并转为文本（对应WorkBuddy的WebFetch）

    Args:
        url: 网页URL
        max_length: 最大返回字符数（默认10000）

    Returns:
        dict: {success: bool, content: str, title: str, error: str}
    """
    try:
        import requests
        from src.core.independence import bypass_independence

        with bypass_independence():
            r = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            r.raise_for_status()
            r.encoding = r.apparent_encoding or 'utf-8'
            html = r.text

        # 提取标题
        title = ''
        import re
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = title_match.group(1).strip()

        # HTML转文本（简单版：去标签）
        # 去script和style
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.IGNORECASE | re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.IGNORECASE | re.DOTALL)
        # 去标签
        text = re.sub(r'<[^>]+>', ' ', html)
        # 去多余空白
        text = re.sub(r'\s+', ' ', text).strip()
        # HTML实体
        import html as html_module
        text = html_module.unescape(text)

        # 截断
        if len(text) > max_length:
            text = text[:max_length] + '\n...(内容已截断)'

        return {
            'success': True,
            'content': text,
            'title': title,
            'error': ''
        }
    except Exception as e:
        return {'success': False, 'content': '', 'title': '', 'error': str(e)}


# ═══════════════════════════════════════════════════════════════════════
# ★ 2026-07-21: 邮箱工具（email_fetch / email_send / email_search）
# 设计原则：网络由内置工具控制（安全），业务逻辑由外部Skill实现（灵活）
# ═══════════════════════════════════════════════════════════════════════

def _get_email_config():
    """读取邮箱配置（从 config.yaml）"""
    import yaml
    cfg_path = os.path.join(BASE_DIR, 'config.yaml')
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        email_cfg = cfg.get('email', {})
        if not email_cfg.get('address'):
            return None
        return email_cfg
    except Exception:
        return None


def _save_email_config(email_config):
    """保存邮箱配置到 config.yaml"""
    import yaml
    cfg_path = os.path.join(BASE_DIR, 'config.yaml')
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        cfg['email'] = email_config
        with open(cfg_path, 'w', encoding='utf-8') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        return True
    except Exception as e:
        logger.error(f"[Email] 保存配置失败: {e}")
        return False


# ═══ ★ #965 邮箱连接三件（2026-10-04）═══
#   ① 163/126 登录后必须发 IMAP ID 命令自报身份，否则网易报「Unsafe Login」拒绝（开发者 10-04 实锤连不上 163 的根因）
#   ② 配置保存前实测登录——填错当场报人话，不再「假成功、用时才炸」
#   ③ 报错按服务商翻译成人话（授权码≠登录密码、网络不通、服务没开）
_NETEASE_DOMAINS = ('163.com', '126.com')


def _imap_send_id(mail, address):
    """★ #965-① 网易系邮箱登录后发 ID 命令（RFC 297），不然后续命令被拒"""
    try:
        domain = address.split('@')[-1].lower()
        if any(domain == d or domain.endswith('.' + d) for d in _NETEASE_DOMAINS):
            typ, _ = mail.xatom('ID', '("name" "HarvenAI" "version" "1.0" "vendor" "HarvenAI" "contact" "harven@local")')
            if typ != 'OK':
                logger.warning(f"[Email] 网易 ID 命令未确认: {typ}")
    except Exception as e:
        logger.debug(f"[Email] ID 命令发送失败（非网易邮箱无此要求，不影响）: {e}")


def _imap_friendly_error(e, imap_server=''):
    """★ #965-③ IMAP/网络报错翻译成人话"""
    s = str(e)
    if 'Unsafe Login' in s:
        return ('网易邮箱拒绝登录：请先在邮箱网页端「设置 → POP3/SMTP/IMAP」开启 IMAP/SMTP 服务'
                '（要手机发短信验证），再用 16 位授权码重试')
    if any(k in s.upper() for k in ('LOGIN', 'AUTH', 'PASSWORD', 'AUTHENTICATE')) or '登录' in s or '密码' in s:
        return ('授权码不对或已过期。注意：授权码不是邮箱登录密码——要去邮箱网页端开启「IMAP/SMTP 服务」'
                '后生成一串 16 位字母授权码（QQ 邮箱在「设置→账户」，163 在「设置→POP3/SMTP/IMAP」）')
    if 'getaddrinfo failed' in s or 'Name or service not known' in s or 'temporary failure in name resolution' in s.lower():
        return f'连不上邮件服务器 {imap_server}：检查这台电脑能不能上网、服务器地址有没有写错'
    if 'timed out' in s.lower() or 'timeout' in s.lower() or 'unreachable' in s.lower():
        return f'连 {imap_server} 超时：网络不通、被安全软件拦截，或该邮箱的 IMAP 服务没开启'
    return f'邮箱服务器说：{s}'


def _imap_test_login(cfg):
    """★ #965-② 真登录一次验证配置。返回 (ok, 人话错误)——不落任何数据，只验连通"""
    import imaplib
    from src.core.independence import bypass_independence
    mail = None
    try:
        with bypass_independence():
            mail = imaplib.IMAP4_SSL(cfg['imap_server'], cfg['imap_port'])
            mail.login(cfg['address'], cfg['password'])
            _imap_send_id(mail, cfg['address'])
            mail.select('INBOX', readonly=True)   # 能选中收件箱才算真通
            try: mail.logout()
            except Exception: pass
            mail = None
        return True, ''
    except Exception as e:
        return False, _imap_friendly_error(e, cfg.get('imap_server', ''))
    finally:
        if mail is not None:
            try: mail.shutdown()
            except Exception: pass


def email_setup(address, password, imap_server=None, smtp_server=None,
                imap_port=993, smtp_port=465):
    """配置邮箱账号

    Args:
        address: 邮箱地址（如 xxx@qq.com）
        password: 邮箱密码或授权码
        imap_server: IMAP服务器地址（不填则根据邮箱域名自动推断）
        smtp_server: SMTP服务器地址（不填则根据邮箱域名自动推断）
        imap_port: IMAP端口（默认993 SSL）
        smtp_port: SMTP端口（默认465 SSL）

    Returns:
        dict: {success: bool, error: str}
    """
    # 常见邮箱服务器自动推断
    domain = address.split('@')[-1].lower() if '@' in address else ''
    _PRESETS = {
        'qq.com':      {'imap': 'imap.qq.com',  'smtp': 'smtp.qq.com'},
        '163.com':     {'imap': 'imap.163.com', 'smtp': 'smtp.163.com'},
        '126.com':     {'imap': 'imap.126.com', 'smtp': 'smtp.126.com'},
        'sina.com':    {'imap': 'imap.sina.com','smtp': 'smtp.sina.com'},
        'gmail.com':   {'imap': 'imap.gmail.com','smtp': 'smtp.gmail.com'},
        'outlook.com': {'imap': 'outlook.office365.com', 'smtp': 'smtp.office365.com'},
        'hotmail.com': {'imap': 'outlook.office365.com', 'smtp': 'smtp.office365.com'},
    }
    preset = _PRESETS.get(domain, {})
    imap_server = imap_server or preset.get('imap', f'imap.{domain}')
    smtp_server = smtp_server or preset.get('smtp', f'smtp.{domain}')

    email_config = {
        'address': address,
        'password': password,  # ★ 存config.yaml（本地文件，不外传）
        'imap_server': imap_server,
        'imap_port': imap_port,
        'smtp_server': smtp_server,
        'smtp_port': smtp_port,
    }

    # ★ #965-② 保存前实测登录——连不上就不保存，当场给人话报错（根除「假成功、用时才炸」）
    ok, err = _imap_test_login(email_config)
    if not ok:
        logger.warning(f"[Email] 实测登录失败 {address}: {err}")
        return {'success': False, 'error': err}

    if _save_email_config(email_config):
        logger.info(f"[Email] 邮箱配置已保存（实测连通）: {address}")
        return {'success': True, 'error': '', 'tested': True}
    return {'success': False, 'error': '保存配置失败（配置文件写入失败）'}


# ★ #943 邮件附件五道闸（2026-10-03）：
#   闸1 类型白名单：只放行常见安全类型，可执行/脚本/安装包一律拒收（白名单制=天然拒exe/bat/vbs/js等）
#   闸2 隔离区落盘：data/mail_inbox_quarantine/年月/，文件名清洗+防覆盖，绝不落原文件名到工作目录
#   闸3 不自动联动：附件只隔离保存，绝不进知识库/面板/Skill——要处理必须用户明确开口
#   闸4 杀毒扫描：Windows Defender 命令行（MpCmdRun）逐个扫；报毒文件留在隔离区并标记，不删不打开
#   闸5 大小上限：单附件超 20MB 不落盘，只在附件列表里记一笔「超大不收」
MAIL_QUARANTINE_DIR = os.path.join(BASE_DIR, 'data', 'mail_inbox_quarantine')
MAIL_ATTACHMENT_MAX = 20 * 1024 * 1024   # 闸5：20MB
MAIL_ATTACHMENT_TYPES = {                # 闸1：白名单（小写含点）
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.md', '.rtf',
    '.mp3', '.wav', '.m4a', '.aac', '.flac',
    '.mp4', '.mov', '.avi', '.mkv',
    '.zip', '.rar', '.7z',
}


def _mail_scan_file(path):
    """★ #943 闸4：Windows Defender 命令行扫描。

    Returns: 'clean' / 'infected' / 'unknown'（扫描器不在或失败——文件仍隔离着，闸3兜底不联动）
    """
    try:
        if sys.platform != 'win32':
            return 'unknown'
        pf = os.environ.get('ProgramFiles', r'C:\Program Files')
        mp = os.path.join(pf, 'Windows Defender', 'MpCmdRun.exe')
        if not os.path.isfile(mp):
            pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
            mp = os.path.join(pf86, 'Windows Defender', 'MpCmdRun.exe')
        if not os.path.isfile(mp):
            return 'unknown'
        r = subprocess.run([mp, '-Scan', '-ScanType', '3', '-File', path],
                           capture_output=True, timeout=120, text=True,
                           creationflags=0x08000000)   # CREATE_NO_WINDOW
        out = ((r.stdout or '') + (r.stderr or '')).lower()
        # MpCmdRun 退出码：0=没发现威胁，2=发现威胁——但 Defender 被动模式（装了火绒/360 等）扫描失败
        # 时退出码同样是 2（实测 hr=0x80004005），必须看输出区分，否则干净文件会被冤枉
        if r.returncode == 0:
            return 'clean'
        if r.returncode == 2 and 'failed with hr' not in out and 'hr = 0x' not in out:
            return 'infected'
        return 'unknown'
    except Exception:
        return 'unknown'


def email_fetch(folder="INBOX", limit=10, mark_seen=False):
    """通过IMAP拉取邮件，缓存到本地SQLite

    Args:
        folder: 邮箱文件夹（默认 INBOX）
        limit: 拉取数量（默认10封）
        mark_seen: 是否标记为已读（默认False）

    Returns:
        dict: {success: bool, emails: list, count: int, error: str}
    """
    import imaplib
    import email as email_module
    from email.header import decode_header
    from src.core.independence import bypass_independence
    from src.models.database import DEFAULT_DB_PATH
    import sqlite3

    cfg = _get_email_config()
    if not cfg:
        return {'success': False, 'emails': [], 'count': 0,
                'error': '邮箱未配置。请先调用 email_setup 配置邮箱。'}

    try:
        # ★ 使用 bypass_independence 绕过出站白名单（受信任的内置邮件工具）
        with bypass_independence():
            mail = imaplib.IMAP4_SSL(cfg['imap_server'], cfg['imap_port'])
            mail.login(cfg['address'], cfg['password'])
            _imap_send_id(mail, cfg['address'])   # ★ #965-① 网易系必需
            mail.select(folder)

            # 搜索邮件
            status, data = mail.search(None, 'ALL')
            if status != 'OK':
                return {'success': False, 'emails': [], 'count': 0,
                        'error': f'IMAP搜索失败: {status}'}

            ids = data[0].split()
            if not ids:
                return {'success': True, 'emails': [], 'count': 0, 'error': ''}

            # 取最新的 limit 封
            ids = ids[-limit:]

            emails = []
            for eid in reversed(ids):
                status, msg_data = mail.fetch(eid, '(RFC822)')
                if status != 'OK':
                    continue

                msg = email_module.message_from_bytes(msg_data[0][1])

                # 解析邮件
                def _decode_str(s):
                    if not s:
                        return ''
                    parts = decode_header(s)
                    result = []
                    for part, enc in parts:
                        if isinstance(part, bytes):
                            result.append(part.decode(enc or 'utf-8', errors='replace'))
                        else:
                            result.append(part)
                    return ''.join(result)

                subject = _decode_str(msg.get('Subject', ''))
                from_addr = _decode_str(msg.get('From', ''))
                to_addr = _decode_str(msg.get('To', ''))
                date = msg.get('Date', '')

                # 提取纯文本正文
                body = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct == 'text/plain':
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or 'utf-8'
                                body = payload.decode(charset, errors='replace')
                                break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        charset = msg.get_content_charset() or 'utf-8'
                        body = payload.decode(charset, errors='replace')

                # 截断正文
                body = body[:5000] if body else '(无正文)'

                # ★ #943 附件五道闸：白名单→20MB上限→隔离落盘→杀毒扫描（不联动，只存）
                import re as _re
                atts = []
                for part in msg.walk():
                    raw_name = part.get_filename()
                    if not raw_name:
                        continue
                    fname = _decode_str(raw_name)
                    try:
                        payload = part.get_payload(decode=True)
                    except Exception:
                        payload = None
                    size = len(payload) if payload else 0
                    ext = os.path.splitext(fname)[1].lower()
                    att = {'filename': fname, 'size': size, 'saved': False, 'reason': ''}
                    if size > MAIL_ATTACHMENT_MAX:                      # 闸5
                        att['reason'] = '超过20MB，不收'
                    elif ext not in MAIL_ATTACHMENT_TYPES:              # 闸1
                        att['reason'] = f'类型 {ext or "(无扩展名)"} 不在白名单，拒收'
                    else:                                               # 闸2 隔离落盘
                        try:
                            qdir = os.path.join(MAIL_QUARANTINE_DIR, time.strftime('%Y%m'))
                            os.makedirs(qdir, exist_ok=True)
                            safe = _re.sub(r'[\\/:*?"<>|\r\n]', '_', fname).strip('._ ') or 'attachment'
                            dest = os.path.join(qdir, safe)
                            _b, _e = os.path.splitext(dest)
                            _i = 1
                            while os.path.exists(dest):
                                dest = f'{_b}_{_i}{_e}'
                                _i += 1
                            with open(dest, 'wb') as fh:
                                fh.write(payload)
                            att['saved'] = True
                            att['path'] = dest
                            att['scan'] = _mail_scan_file(dest)         # 闸4
                            if att['scan'] == 'infected':
                                att['reason'] = '⚠️ 杀毒扫描报毒——文件留在隔离区别打开，确认是垃圾邮件建议整封删除'
                        except Exception as _ae:
                            att['reason'] = f'落盘失败: {_ae}'
                    atts.append(att)                                    # 闸3：只登记，绝不自动联动

                emails.append({
                    'uid': eid.decode(),
                    'from': from_addr,
                    'to': to_addr,
                    'subject': subject,
                    'date': date,
                    'body': body[:500],  # 返回给模型的只给前500字符
                    'attachments': atts,
                    'is_read': not (mail.store(eid, '+FLAGS', '\\Seen')[0] == 'OK' and False) if mark_seen else True,
                })

                # 缓存到本地SQLite
                try:
                    conn = sqlite3.connect(DEFAULT_DB_PATH)
                    conn.execute("""
                        INSERT OR REPLACE INTO emails (id, account, folder, from_addr, to_addr, subject, body_text, date, is_read)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (f"{cfg['address']}_{eid.decode()}", cfg['address'], folder,
                          from_addr, to_addr, subject, body, date, 0))
                    conn.commit()
                    conn.close()
                except Exception as _e:
                    logger.debug(f"[Email] 缓存邮件失败: {_e}")

            mail.logout()

        logger.info(f"[Email] 拉取 {len(emails)} 封邮件 from {cfg['address']}/{folder}")
        return {'success': True, 'emails': emails, 'count': len(emails), 'error': ''}

    except imaplib.IMAP4.error as e:
        return {'success': False, 'emails': [], 'count': 0,
                'error': _imap_friendly_error(e, cfg.get('imap_server', ''))}
    except Exception as e:
        logger.error(f"[Email] 拉取邮件失败: {e}")
        return {'success': False, 'emails': [], 'count': 0,
                'error': _imap_friendly_error(e, cfg.get('imap_server', ''))}


def email_send(to, subject, body):
    """通过SMTP发送邮件（⚠️ 危险操作，必须用户确认）

    Args:
        to: 收件人邮箱地址
        subject: 邮件主题
        body: 邮件正文

    Returns:
        dict: {success: bool, error: str}
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from src.core.independence import bypass_independence

    cfg = _get_email_config()
    if not cfg:
        return {'success': False, 'error': '邮箱未配置。请先调用 email_setup 配置邮箱。'}

    try:
        msg = MIMEMultipart()
        msg['From'] = cfg['address']
        msg['To'] = to
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # ★ 使用 bypass_independence 绕过出站白名单
        with bypass_independence():
            server = smtplib.SMTP_SSL(cfg['smtp_server'], cfg['smtp_port'])
            server.login(cfg['address'], cfg['password'])
            server.sendmail(cfg['address'], [to], msg.as_string())
            server.quit()

        logger.info(f"[Email] 邮件已发送: {cfg['address']} → {to} | 主题: {subject}")
        return {'success': True, 'error': ''}
    except Exception as e:
        logger.error(f"[Email] 发送邮件失败: {e}")
        return {'success': False, 'error': str(e)}


def email_search(query, folder=None, limit=20):
    """搜索本地缓存的邮件（纯本地操作，不联网）

    Args:
        query: 搜索关键词（匹配主题/发件人/正文）
        folder: 限定文件夹（可选）
        limit: 最多返回数量

    Returns:
        dict: {success: bool, emails: list, count: int, error: str}
    """
    from src.models.database import DEFAULT_DB_PATH
    import sqlite3

    try:
        conn = sqlite3.connect(DEFAULT_DB_PATH)
        cursor = conn.cursor()

        sql = """SELECT id, account, folder, from_addr, subject, body_text, date, is_read
                 FROM emails
                 WHERE (subject LIKE ? OR from_addr LIKE ? OR body_text LIKE ?)"""
        params = [f'%{query}%', f'%{query}%', f'%{query}%']

        if folder:
            sql += " AND folder = ?"
            params.append(folder)

        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        conn.close()

        emails = []
        for row in rows:
            emails.append({
                'id': row[0],
                'account': row[1],
                'folder': row[2],
                'from': row[3],
                'subject': row[4],
                'body': row[5][:300] if row[5] else '',  # 搜索结果只给前300字符
                'date': row[6],
                'is_read': row[7],
            })

        logger.info(f"[Email] 搜索 '{query}' → {len(emails)} 条结果")
        return {'success': True, 'emails': emails, 'count': len(emails), 'error': ''}
    except Exception as e:
        logger.error(f"[Email] 搜索邮件失败: {e}")
        return {'success': False, 'emails': [], 'count': 0, 'error': str(e)}


def task_update(task_id, status=None, subject=None, description=None):
    """更新任务（对应WorkBuddy的TaskUpdate）

    Args:
        task_id: 任务ID
        status: 新状态（pending/in_progress/completed/deleted）
        subject: 新标题（可选）
        description: 新描述（可选）

    Returns:
        dict: {success: bool, error: str}
    """
    try:
        tasks_file = os.path.join(BASE_DIR, 'data', 'tasks.json')
        if not os.path.exists(tasks_file):
            return {'success': False, 'error': '任务文件不存在'}

        with open(tasks_file, 'r', encoding='utf-8') as f:
            tasks = json.load(f)

        if task_id not in tasks:
            return {'success': False, 'error': f'任务不存在: {task_id}'}

        if status:
            tasks[task_id]['status'] = status
        if subject:
            tasks[task_id]['subject'] = subject
        if description:
            tasks[task_id]['description'] = description
        tasks[task_id]['updated'] = get_time()['time']

        with open(tasks_file, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)

        return {'success': True, 'error': '', 'task': tasks[task_id]}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def task_get(task_id):
    """获取单个任务详情（对应WorkBuddy的TaskGet）

    Args:
        task_id: 任务ID

    Returns:
        dict: {success: bool, task: dict, error: str}
    """
    try:
        tasks_file = os.path.join(BASE_DIR, 'data', 'tasks.json')
        if not os.path.exists(tasks_file):
            return {'success': False, 'task': None, 'error': '任务文件不存在'}

        with open(tasks_file, 'r', encoding='utf-8') as f:
            tasks = json.load(f)

        if task_id not in tasks:
            return {'success': False, 'task': None, 'error': f'任务不存在: {task_id}'}

        return {'success': True, 'task': tasks[task_id], 'error': ''}
    except Exception as e:
        return {'success': False, 'task': None, 'error': str(e)}


# ═══════════════════════════════════════════════════════════════
# ★ A批A5(2026-09-13): 同族工具合并（省token：61→约40，旧名保留别名一版）
# ═══════════════════════════════════════════════════════════════

def tool_schema(names=''):
    """A批A3: 目录式FC元工具 — 按名查工具的完整参数定义

    Args:
        names: 逗号分隔的工具名（如 "task,email_fetch"）
    """
    requested = [n.strip() for n in str(names).replace('，', ',').split(',') if n.strip()]
    all_defs = get_tool_definitions()
    by_name = {d['function']['name']: d for d in all_defs}
    found = [by_name[n] for n in requested if n in by_name]
    missing = [n for n in requested if n not in by_name]
    return {'success': True, 'tools': found, 'not_found': missing}


def search_tool(type='content', pattern='', query='', path='', file_type='', limit=10):
    """统一搜索工具（合并 search_content/search_files/search_knowledge/search_conversation）

    Args:
        type: content=文件内容 | filename=文件名 | knowledge=知识库 | conversation=历史对话
        pattern/query: 搜索词（两名字等价，任填一个）
        path: 目录（仅 content/filename 用）
        file_type: 扩展名过滤（仅 content 用）
        limit: 结果上限（仅 knowledge/conversation 用）
    """
    q = query or pattern
    if type == 'filename':
        return search_files(pattern=q, path=path or '.')
    if type == 'knowledge':
        return search_knowledge(query=q)
    if type == 'conversation':
        return search_conversation(query=q)
    return search_content(pattern=q, path=path or '.', file_type=file_type)


def task_tool(action='list', task_id='', subject='', description='', status=''):
    """统一任务工具（合并 task_create/task_list/task_update/task_get）

    Args:
        action: create | list | update | get
    """
    if action == 'create':
        return task_create(subject=subject, description=description)
    if action == 'update':
        return task_update(task_id=task_id, status=status or None, subject=subject or None, description=description or None)
    if action == 'get':
        return task_get(task_id=task_id)
    return task_list()


def skill_tool(action='list', skill_id='', user_input=''):
    """统一Skill工具（合并 list_skills/execute_skill）

    Args:
        action: list | execute
    """
    if action == 'execute':
        return execute_skill(skill_id=skill_id, user_input=user_input)
    return list_skills()


def skill_script_tool(action='list', skill_name='', script_action='run', params=None):
    """统一Skill脚本工具（合并 list_skill_scripts/execute_skill_script）

    Args:
        action: list | execute
        script_action: 脚本内动作名（如 add/query/summary）
    """
    if action == 'execute':
        return execute_skill_script(skill_name=skill_name, action=script_action, params=params or {})
    return list_skill_scripts_tool()


# ═══════════════════════════════════════════════════════════════
# 工具注册表（供AI调用）
# ═══════════════════════════════════════════════════════════════

def _import_ollama_wrapper(gguf_path, model_name=None):
    """把GGUF文件导入Ollama（包装security_guard的import_gguf_to_ollama）"""
    try:
        from src.core.security_guard import import_gguf_to_ollama
        return import_gguf_to_ollama(gguf_path, model_name)
    except Exception as e:
        return {'success': False, 'error': str(e)[:200]}


def get_current_model_status():
    # FC 工具：查询哈文当前实际使用的模型/模式（本地还是外脑、模型名、引擎）
    try:
        from src.core.multi_model_manager import get_multi_model_manager
        mm = get_multi_model_manager()
        cur = mm.current_model
        if cur == 'ollama':
            import yaml as _y, pathlib as _p
            cfg = _y.safe_load((_p.Path(__file__).parent.parent.parent / 'config.yaml').read_text(encoding='utf-8')) or {}
            model = cfg.get('ollama', {}).get('default_model', '') or ''  # ★ 2026-09-26 审计：不内定，空=未选择
            return {'success': True, 'mode': '本地模式', 'engine': 'Ollama/llama.cpp',
                    'model': model, 'external': False}
        elif cur is None:
            return {'success': True, 'mode': '未选择模型', 'engine': '-', 'model': '-', 'external': None}
        else:
            info = mm._get_all_providers().get(cur, {})
            entry = mm.config.get('models', {}).get(cur, {})
            return {'success': True, 'mode': '外脑模式', 'engine': info.get('name', cur),
                    'model': entry.get('model_name', ''), 'external': True}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def query_panel_tool(keyword='', panel_id='', time_range=''):
    """★ 2026-09-26 乙（已批准）：外脑查面板档案工具 — 找自己存过的信息一轮查对地方，不再爬目录。
    面板数据/照片都存在 sqlite 里（照片是 base64 文本列），文件系统搜索注定落空。
    ★ 2026-09-27 #883：新增 time_range 参数（今天/昨天/本周/本月/上月/近N天/具体日期），
    记账类查询走档案中枢毫秒级直查，不再绕 skill 空库。
    ★ #973：keyword 改默认值——AI 调用漏传参数时不再 TypeError 崩栈，返回人话引导补参。"""
    try:
        if not str(keyword or '').strip():
            return {'success': False,
                    'error': '请提供查询关键词（keyword 参数）。请根据用户想找的内容重新调用本工具，'
                             '例如「执照」「保险单」「血压」「雨伞」，查账目可配合 time_range（今天/本月等）。'}

        # ★ #883 时间过滤：记账域走中枢直查（一期只做记账，二期扩全域）
        if time_range:
            try:
                from src.core.archive_hub import parse_time_range, hub_query_accounting
                tr = parse_time_range(str(time_range))
                _is_acc = (str(panel_id or '') in ('accounting_daily_1', 'accounting_daily') or
                           any(w in str(keyword) for w in ('账', '花', '支出', '收入', '开销', '金额')))
                if tr and _is_acc:
                    start, end, label = tr
                    r = hub_query_accounting(start, end)
                    if r.get('success'):
                        r['note'] = f'已按「{label}」直查记账面板（档案中枢），请用人话转述，禁止原样输出 JSON'
                    return r
            except Exception:
                pass  # 中枢异常退回原 LIKE 检索

        from src.core.panel_manager import get_panel_manager
        # ★ P0-B FTS5 全文索引快路径：先索引命中（毫秒级、省 token），空了再退原检索
        try:
            pm = get_panel_manager()
            fts_hits = pm.fts_search(str(keyword)[:40], panel_id=str(panel_id or ''), limit=10)
        except Exception:
            fts_hits = []
        results = []
        if fts_hits:
            panel_names = {}
            try:
                for p in get_panel_manager().get_all_panels():
                    panel_names[p['id']] = p.get('name', p['id'])
            except Exception:
                pass
            for h in fts_hits[:10]:
                results.append({'panel': panel_names.get(h['panel_id'], h['panel_id']),
                                'panel_id': h['panel_id'], 'record_id': h['record_id'],
                                'matched': h['text'][:180]})
        else:
            from src.core.panel_assist import archive_search_records
            hits = archive_search_records([str(keyword)[:20]], panel_id=str(panel_id or ''))
            for label, pid, slim in hits[:10]:
                results.append({'panel': label, 'panel_id': pid, **slim})
        out = {'success': True, 'count': len(results), 'results': results}
        out['note'] = ('记录的图片存在哈文图片附件库（可到对应面板记录点开查看），文件系统里搜不到'
                       if results else '本机面板档案中没有找到相关记录')
        return out
    except Exception as e:
        return {'success': False, 'error': str(e)}


def search_images_tool(q='', album='', panel_id='', limit=20):
    """★ #920 2026-09-27 统一图库检索工具（已批准「通用开发」）——找图片/照片类请求的正式通道。
    图片存在哈文图库（data/media/img_store + 知识库/面板附件原地登记），全盘扫描注定又慢又漏。
    返回带可直接展示的 URL（Markdown 图片语法可直接用）。"""
    try:
        from src.core import img_store
        items = img_store.search(q=str(q or ''), album=str(album or ''),
                                 panel_id=str(panel_id or ''), limit=max(1, min(int(limit or 20), 50)))
        for it in items:
            it.pop('path', None)   # 不给 AI 本机路径，防它去文件系统瞎找
        # ★ 2026-10-25 #1191 反幻觉：count 只是「本页条数」，必须把图库真实总数一起给，
        #   否则模型会把 13 当成全库总数说「图库共 13 张」（开发者截图实锤的假数根因之二）
        try:
            _lib_total = img_store.count()
        except Exception:
            _lib_total = None
        _head = (f'本页返回 {len(items)} 张' + (f'（图库共 {_lib_total} 张，这只是其中匹配的一页）' if _lib_total else '')
                 + '。回答「家里有多少照片」必须用图库总数，绝不能把本页条数当总数。'
                   '图库总数已在上方 total 字段给出，直接引用即可——禁止再用 read_file、execute_command 等任何其它工具去统计图片条数。'
                   '展示方法：在回答里用 Markdown 图片语法 ![](URL)（URL 用返回的 url 字段）。'
                   '用户也能在「图片库」页面浏览全部相册'
                  if items else
                  '图库里没有找到相关图片。可提示用户：手机拍照/聊天发图会自动入图库，'
                  '也可到「图片库」页面上传或换关键词搜索')
        return {
            'success': True, 'count': len(items),
            **({'total': _lib_total} if _lib_total is not None else {}),
            'images': items, 'note': _head,
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def describe_image_tool(img_id='', style='story'):
    """★ #925 T4 2026-09-29 看图说话工具：给图库里的某张图编故事/写诗/讲给孩子听（本地视觉零费用）。
    通常紧跟 search_images 使用：先找到图拿到 id，再调本工具。"""
    try:
        from src.core import img_store
        try:
            iid = int(img_id)
        except (TypeError, ValueError):
            return {'success': False, 'error': 'img_id 必须是 search_images 返回的图片 id 数字。先调 search_images 找图'}
        text, model, err = img_store.describe(iid, str(style or 'story'))
        if err or not text:
            return {'success': False, 'error': err or '视觉模型没有返回'}
        return {'success': True, 'text': text.strip(), 'model': model,
                'note': '把 text 用自己的语气讲给用户，不要原样贴 JSON'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ═══ ★ 2026-11-12 相册管理工具（A 方案：HarvenEye 两次谎报「已建相册」实锤根治）═══

def manage_album_tool(action='list', name='', img_id=0, img_ids=None, person=''):
    """建相册/移照片/按人脸归册——照片归类类请求的唯一正规通道。
    背景：qwen3-vl 两次嘴上说「已归类」实际啥也没动（11-12 老谭抓包实锤），
    本工具给它真正的手：底层全走 img_store.add_album/set_album（移入即手动锁定，归位引擎不再动）。"""
    try:
        from src.core import img_store
        action = str(action or 'list').strip()

        if action == 'list':
            albums = img_store.list_album_names()
            counts = {}
            try:
                c = img_store._conn()
                for r in c.execute("SELECT album, COUNT(*) n FROM images_index WHERE deleted_at='' AND album<>'' GROUP BY album"):
                    counts[r['album']] = r['n']
                c.close()
            except Exception:
                pass
            return {'success': True, 'albums': [{'name': a, 'count': counts.get(a, 0)} for a in albums],
                    'note': '把 albums 列表用人话报给用户（名字+张数），不要贴 JSON'}

        if action == 'create':
            nm = str(name or '').strip()
            if not nm:
                return {'success': False, 'error': 'name 不能为空：要建的相册叫什么？'}
            if not img_store.add_album(nm):
                return {'success': False, 'error': f'建相册「{nm}」失败'}
            return {'success': True, 'album': nm,
                    'note': f'相册「{nm}」已建好。如实告诉用户已创建'}

        # move / move_faces 都要先确定目标相册
        nm = str(name or '').strip() or str(person or '').strip()
        if not nm:
            return {'success': False, 'error': 'name 不能为空：照片要移进哪个相册？'}
        if not img_store.add_album(nm):
            return {'success': False, 'error': f'相册「{nm}」创建失败'}

        ids = []
        if action == 'move':
            if img_ids:
                ids = [int(i) for i in img_ids if str(i).strip().lstrip('-').isdigit()]
            elif img_id and str(img_id).strip().lstrip('-').isdigit():
                ids = [int(img_id)]
            if not ids:
                return {'success': False, 'error': '要移哪些照片？先调 search_images 找到图片 id，再传 img_ids=[...]'}
        elif action == 'move_faces':
            who = str(person or name or '').strip()
            if not who:
                return {'success': False, 'error': 'person 不能为空：要归哪个人的照片？'}
            from src.vision.engines.face_engine import FaceEngine
            eng = FaceEngine()
            eng.load()
            import os as _os
            imgs = img_store.search('', limit=2000)
            matched = []
            for im in imgs:
                p = im.get('path', '')
                if not p or not _os.path.exists(p):
                    continue
                try:
                    r = eng.infer(p)
                    if who in (r.data.get('matched_names') or []):
                        matched.append(int(im['id']))
                except Exception:
                    continue
            if not matched:
                return {'success': False,
                        'error': f'人脸库里没有识别出任何「{who}」的照片。可能 TA 还没录入人脸（图片库→👤录家人），如实告诉用户'}
            ids = matched
        else:
            return {'success': False, 'error': f'未知 action: {action}（可用 list/create/move/move_faces）'}

        moved, failed = [], []
        for i in ids:
            if img_store.set_album(i, nm):
                moved.append(i)
            else:
                failed.append(i)
        return {'success': bool(moved), 'album': nm, 'moved_ids': moved,
                **({'failed_ids': failed} if failed else {}),
                'count': len(moved),
                'note': f'已把 {len(moved)} 张照片移入「{nm}」相册。按 moved_ids 数量如实汇报，禁止夸大'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ═══ ★ #1306/#1307（2026-10-29）批量识别提议 + 确认落库（定版拍板「AI 只提议，用户拍板」）═══

_DEEP_ALBUM_PROMPT = ('看这张照片，严格按下面三行格式回答：\n'
                      '描述：一句话中文说清画面主体和场景（40字内）\n'
                      '标签：4到6个中文标签，逗号分隔（主体/场所/人物）\n'
                      '相册：从这些候选里挑最合适的一个：{albums}\n'
                      '只输出这三行，不要多余的话。')


def identify_images_tool(date='today', limit=10, depth='fast'):
    """★ #1306 批量识别提议工具：查某天录入的图 → 逐张识别 → 返回「建议标签+建议相册」清单。
    ★ 铁律：只提议不落库——用户确认后必须用 apply_photo_suggestion 才写入（用户拍板权）。"""
    try:
        from src.core import img_store
        from concurrent.futures import ThreadPoolExecutor
        depth = str(depth or 'fast').strip().lower()
        limit = max(1, min(int(limit or 10), 20))
        items = img_store.list_by_date(date=str(date or 'today'), limit=limit)
        if not items:
            return {'success': True, 'count': 0, 'proposals': [],
                    'note': '那天没有录入过照片。可提示用户确认日期，或先拍照入库'}
        albums = img_store.list_album_names()   # ★ #1409 字符串形状（原被同名覆盖实锤，改名后显式调用）
        if depth == 'deep':
            if not albums:
                albums = ['生活照']
            prompt = _DEEP_ALBUM_PROMPT.format(albums='、'.join(albums))

        def _one(it):
            iid = it['id']
            out = {'id': iid, 'album': it.get('album', ''), 'note': (it.get('note') or '')[:50],
                   'tags_now': ','.join(it.get('tags') or []) if isinstance(it.get('tags'), list)
                   else (it.get('tags') or '')}
            p = img_store.get_file(iid)
            if not p:
                out['error'] = '图片读不出来'
                return out
            b64 = __import__('base64').b64encode(p.read_bytes()).decode('ascii')
            if depth == 'deep':
                # 外脑细描（跟随用户看图路由；烧 token——只应在用户同意深度识别时走）
                # ★ A2+B（2026-11-12）：换 bg 退避入口——用户要的是深度识别结果，
                #   原路模型看不了时自动退避换可用通道，不再直接报失败
                from src.core.panel_assist import _camp_vision_bg
                text, model, err = _camp_vision_bg(prompt, b64, timeout=90)
                if err or not text:
                    out['error'] = f'深识失败: {str(err)[:60]}'
                    return out
                desc, sugg_tags, sugg_album = '', '', ''
                for line in str(text).splitlines():
                    line = line.strip()
                    if line.startswith('描述'):
                        desc = line.split('：', 1)[-1].split(':', 1)[-1].strip()
                    elif line.startswith('标签'):
                        sugg_tags = line.split('：', 1)[-1].split(':', 1)[-1].strip()
                    elif line.startswith('相册'):
                        sugg_album = line.split('：', 1)[-1].split(':', 1)[-1].strip()
                if sugg_album and sugg_album not in albums:
                    sugg_album = ''   # 外脑给了候选外的相册 → 不采纳，留给用户定
                out.update({'desc': desc[:60], 'suggest_tags': sugg_tags,
                            'suggest_album': sugg_album, 'model': model})
            else:
                # Eye 快速档（本地 0 token）：打标签落库 + CLIP 自动归位（自动引擎份内事）
                tags, model, err = img_store.retag(iid)
                if not err and tags:
                    out['suggest_tags'] = ','.join(tags)
                img_store.auto_place(iid)
                cur = img_store.get(iid) or {}
                if cur.get('album') and cur['album'] != out['album']:
                    out['album_after'] = cur['album']
                if err:
                    out['error'] = f'标签没打成: {str(err)[:60]}'
            return out

        with ThreadPoolExecutor(max_workers=3) as ex:
            proposals = list(ex.map(_one, items))
        head = (f'已识别 {len(proposals)} 张（{depth} 档）。' if depth == 'fast'
                else f'已深识 {len(proposals)} 张，下面是建议清单——逐条念给用户听，'
                     '用户点头哪条就用 apply_photo_suggestion 落库，没点头的一条都不许写。')
        return {'success': True, 'count': len(proposals), 'proposals': proposals, 'note': head}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def apply_photo_suggestion_tool(ids='', albums='', tags='', deep=''):
    """★ #1306 确认落库工具：用户点头后把建议写进图库。
    ids 与 albums 逗号对齐（id=图库 id，album=目标相册）；tags 可选逗号分隔合并进标签。
    set_album 会置 auto_placed=0 手动锁定，自动归位引擎永不再动。
    deep=on/off 可选：顺带设置 albums 里相册的「外脑深析」开关（#1307）。"""
    try:
        from src.core import img_store
        id_list = [x.strip() for x in str(ids or '').split(',') if x.strip()]
        alb_list = [x.strip() for x in str(albums or '').split(',') if x.strip()]
        if not id_list:
            return {'success': False, 'error': 'ids 必填（图库图片 id，逗号分隔）'}
        if alb_list and len(alb_list) != len(id_list):
            # 只给了一个相册 = 全部归同一相册
            if len(alb_list) == 1:
                alb_list = alb_list * len(id_list)
            else:
                return {'success': False, 'error': 'albums 与 ids 数量不齐（或只给一个相册名）'}
        ok, fails = 0, []
        for i, iid in enumerate(id_list):
            try:
                iid_i = int(iid)
            except ValueError:
                fails.append(f'{iid}: id 不合法')
                continue
            if alb_list:
                if not img_store.set_album(iid_i, alb_list[i]):
                    fails.append(f'{iid}: 相册写入失败')
                    continue
            if tags:
                img_store.set_tags(iid_i, [t.strip() for t in str(tags).split(',') if t.strip()],
                                   merge=True)
            ok += 1
        # ★ #1307 相册深析开关（可选顺带设置）
        deep_msg = ''
        if str(deep or '').strip().lower() in ('on', 'off') and alb_list:
            on = str(deep).strip().lower() == 'on'
            for a in dict.fromkeys(alb_list):
                img_store.set_album_deep(a, on)
            deep_msg = f'；相册深析已{"开" if on else "关"}：{",".join(dict.fromkeys(alb_list))}'
        note = f'已落库 {ok}/{len(id_list)} 张' + deep_msg
        if fails:
            note += '；失败：' + '；'.join(fails[:3])
        return {'success': ok > 0, 'ok': ok, 'total': len(id_list),
                'note': note + '。用自己话告诉用户结果，被锁定的图自动归位不再动' if ok else note}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ═══ ★ #929-A4（2026-09-29）语音档案库 ═══

def save_voice_note_tool(text=''):
    """保存语音档案（用户说「这段话存下来/把刚才的话存进档案」时调用）。
    text 不传时自动用最近一次语音转写的文字（哈文内存暂存 30 分钟内有效）。"""
    try:
        from src.core import voice_archive
        rec = voice_archive.save_voice_note(text=str(text or ''), source='chat')
        return {'success': True, 'id': rec['id'],
                'has_audio': bool(rec['audio_file']),
                'note': '已存入语音档案。告诉用户存好了，并带出存的前几句'}
    except ValueError as e:
        return {'success': False, 'error': str(e)}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def search_voice_notes_tool(q='', limit=20):
    """搜索语音档案（用户问「之前说的话/语音记录/我说过的XX」时调用）"""
    try:
        from src.core import voice_archive
        items = voice_archive.list_voice_notes(query=str(q or ''), limit=max(1, min(int(limit or 20), 100)))
        return {'success': True, 'count': len(items), 'items': items,
                'note': '把匹配的语音文字按时间讲给用户（created_at + text）' if items
                        else '语音档案里没有找到相关内容'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ═══════════════════════════════════════════════════════════════
# ★ #1037（2026-10-06）对话建模块：主聊天 FC 工具 — 用户说「帮我建个模块」直接建好
#   后端复用 panel_manager.create_panel_from_template（生成器一条龙，与界面「新建模块」同源）；
#   面板助手对话框的旧入口（panel_assist._create_panel_from_text，关键词只认「分类/面板」）保留不动
# ═══════════════════════════════════════════════════════════════

def create_panel(name='', fields='', description='', icon='🧩', panel_id=''):
    """对话建模块：按用户描述的模块名和字段清单创建一个台账模块

    Args:
        name: 模块名（2-6字中文，如「车辆保养」）
        fields: 字段清单 JSON 数组字符串，每项 {"name":"字段中文名","type":"text/number/date/select","options":["select 类型才给选项"]}
        description: 模块用途一句话（选填）
        icon: 一个 emoji 图标（选填）
        panel_id: 模块英文标识（选填，不传自动生成）
    """
    from src.core.panel_manager import get_panel_manager
    name = str(name or '').strip()[:12]
    if not name:
        return {'success': False, 'error': '缺少模块名 name（用 2-6 字中文，如「车辆保养」）'}
    try:
        fs_raw = json.loads(fields) if isinstance(fields, str) else (fields or [])
    except Exception:
        return {'success': False, 'error': 'fields 不是合法的 JSON 数组，应为 [{"name":"日期","type":"date"}, ...]'}
    fs = []
    for f in (fs_raw if isinstance(fs_raw, list) else [])[:6]:
        if not isinstance(f, dict):
            continue
        fn = str(f.get('name') or f.get('label') or '').strip()[:12]
        if not fn:
            continue
        ftype = str(f.get('type') or 'text').lower()
        if ftype not in ('text', 'number', 'date', 'select'):
            ftype = 'text'
        item = {'name': fn, 'label': str(f.get('label') or fn), 'type': ftype}
        if ftype == 'select':
            item['options'] = [str(o)[:20] for o in (f.get('options') or [])[:8]]
        fs.append(item)
    if not fs:
        return {'success': False, 'error': 'fields 里没有可用字段（每项要有 name），至少给 1 个字段'}
    import re as _re
    pid = str(panel_id or '').strip()
    if not pid or not _re.fullmatch(r'[A-Za-z][A-Za-z0-9_\-]{1,39}', pid):
        pid = 'user_' + time.strftime('%m%d%H%M%S')
    # ★ #1123 防重复闸：近名面板已存在时不重复建骨架（错题本×4 悲剧根源），引导直接录入
    if not str(panel_id or '').strip():
        existing = [p for p in (get_panel_manager().get_all_panels() or [])
                    if not p.get('hidden') and (name == str(p.get('name') or '') or _panel_name_match(name, p.get('name')))]
        if existing:
            return {'success': False,
                    'error': f'已有类似面板：「{existing[0].get("name")}」，不要重复建。用户是要往里记内容，直接调 add_panel_record（panel_name 填「{existing[0].get("name")}」）；确要另建新面板时传 panel_id 指定一个新 id',
                    'existing': [{'panel_id': p.get('id'), 'name': p.get('name')} for p in existing[:5]]}
    try:
        if get_panel_manager().panel_exists(pid):
            pid = 'user_' + time.strftime('%m%d%H%M%S')
        result = get_panel_manager().create_panel_from_template(
            name, pid, fs, str(description or '对话创建')[:120], str(icon or '🧩')[:8])
    except Exception as e:
        return {'success': False, 'error': f'创建失败：{e}'}
    if not (result or {}).get('success'):
        return {'success': False, 'error': (result or {}).get('message') or '创建失败'}
    return {'success': True, 'panel_id': pid, 'name': name,
            'fields': [f['name'] for f in fs],
            'note': f'已建好。告诉用户：在「面板中心 → 我的模块」里就能打开「{name}」；以后说「记一笔{name}：…」直接录入'}


# ═══ ★ #1123（2026-10-13）对话建档/录入：往面板新增一条记录 ═══
# 痛点实锤：54 工具有 create_panel（只建空骨架）+ update_panel_record（只改/删已有记录），
# 独缺「新增一条记录」——用户说「给孩子建档：小明 男 2018-05-01」时哈文只能建空表让用户手填。
# 复用 panel_manager.insert_panel_data（#938 列白名单 + 自动时间戳 + FTS5 同步），零重复造轮子。
# 特判：kid_archive（孩子成长档案）是专属子系统（数据真家=children 表），直接走 ka.create_child。

def _panel_name_match(pname, name):
    """子序列匹配：「孩子档案」能命中「孩子成长档案」（连续子串匹配不了中间插字的）"""
    pname, name = str(pname or '').strip(), str(name or '').strip()
    if len(pname) < 2:
        return False
    it = iter(name)
    return all(ch in it for ch in pname)


_KID_CHILD_FIELD_MAP = {'姓名': 'name', '名字': 'name', '小名': 'nickname', '昵称': 'nickname',
                        '性别': 'gender', '出生日期': 'birthday', '生日': 'birthday',
                        '学校': 'school', '年级': 'grade', '班级': 'class_name', '备注': 'notes'}


# ═══ ★ #1224（2026-10-26）写类工具 15 分钟相似去重（#1217 同款，统一防线） ═══
# 痛点实锤：10-25 晚同一次「练腿」主聊天+管家浮层双通道 FC 各写一遍（add_panel_record×2
# + set_reminder×3）。#1217 只给随身记加了去重，这两个口子还开着。
def _recent_dup_scan(table, new_text, minutes=15, threshold=0.5, time_field='', new_time='', pick_fields=None):
    """查 table 里最近 N 分钟内是否有与 new_text 高相似的行（lingzhi.db）。
    pick_fields：只挑这些列拼旧文本比对（避免全列拼接稀释相似度，实锤 0.36<0.5）；
    None=除 id 外全列。time_field/new_time：双方都有时间且不同 → 视为不同的事。
    命中返回 (id, 旧文本, 旧时间)；无命中返回 None。任何异常返回 None（不挡写入）。"""
    import difflib as _dl
    import sqlite3 as _sq
    try:
        from src.core.home_reminder import DB_PATH as _dbp
        c = _sq.connect(str(_dbp), timeout=10)
        c.row_factory = _sq.Row
        try:
            rows = c.execute(
                f"SELECT * FROM {table} WHERE created_at >= datetime('now','localtime','-{int(minutes)} minutes') "
                "ORDER BY id DESC LIMIT 30").fetchall()
        finally:
            c.close()
        for r in rows:
            keys = set(r.keys())
            if pick_fields:
                old = ' '.join(str(r[f]) for f in pick_fields if f in keys and r[f])
            else:
                old = ' '.join(str(v) for v in tuple(r)[1:] if v)
            if _dl.SequenceMatcher(None, str(new_text), old).ratio() < threshold:
                continue
            if time_field and new_time:
                old_t = (r[time_field] or '').strip()[:16] if time_field in keys else ''
                nt = str(new_time).strip()[:16]
                if old_t and nt and old_t != nt:
                    continue  # 提醒时间不同 = 不同的事
            return r['id'], old[:60], (r[time_field] if time_field in keys else '')
        return None
    except Exception:
        return None


# ★ #1376（2026-11-02 定版拍板）对话改状态：冰箱食物消耗（吃掉了/处理掉了）
def consume_food(name='', action='', row_id=0):
    """对话改冰箱台账状态：用户说「卤肉扔掉了」「黄瓜吃完了」时调用。

    Args:
        name: 食材名关键词（如「卤肉」，模糊匹配）
        action: 'eaten'=吃掉了/吃完了；'trashed'=扔了/坏了/过期处理掉。不明确就先问用户，禁止瞎猜
        row_id: 台账行 id（选填；多候选时从 candidates 里让用户挑了再传）

    Returns:
        dict: {success, message, candidates?, log_id?, error}
    """
    try:
        from src.core.fridge_hub import consume_food_fc
        return consume_food_fc(name=name, action=action, row_id=row_id)
    except Exception as e:
        logger.error(f"[BuiltinTools] consume_food异常: {e}")
        return {'success': False, 'message': '', 'error': str(e)}


def set_fridge_zone(name='', zone='', row_id=0):
    """对话改冰箱食物的存储分区：用户说「把花生油放调味油盐区」「鸡蛋改放冷冻」时调用。

    Args:
        name: 食材名关键词（如「花生油」，模糊匹配）
        zone: 目标分区，只能是：冷藏/冷冻/门格/零食饮料/粮食干货/调味油盐。
              注意「冰箱」不是分区！用户说「放冰箱」时默认选 冷藏 或追问
        row_id: 台账行 id（选填；多候选时从清单里让用户挑了再传）

    Returns:
        dict: {success, message, need_clarify?}
    """
    try:
        from src.core.fridge_hub import set_fridge_zone_fc
        return set_fridge_zone_fc(name=name, zone=zone, row_id=row_id)
    except Exception as e:
        logger.error(f"[BuiltinTools] set_fridge_zone异常: {e}")
        return {'success': False, 'message': '', 'error': str(e)}


def undo_last_accounting():
    """撤销最近一次「确认入账」落库的记账行：用户说「撤销刚才入账」「刚记错了，撤回来」时调用。
    只有 10 分钟内的最近一次入账可撤；已超过时限或重启过会如实告知撤不了。

    Returns:
        dict: {success, message}
    """
    try:
        from src.core.panel_assist import undo_last_posted
        ok, note = undo_last_posted()
        return {'success': ok, 'message': note}
    except Exception as e:
        logger.error(f"[BuiltinTools] undo_last_accounting异常: {e}")
        return {'success': False, 'message': '', 'error': str(e)}


def add_panel_record(panel_id='', panel_name='', fields=''):
    """往已有面板新增一条记录（对话建档/录入）：用户报出具体信息要存下来时调用，如「给孩子建档：小明，男，2018-05-01」「记一笔加油 300 元」——直接落库，不要让用户自己打开面板手动填

    Args:
        panel_id: 目标面板 id（选填；不确定 id 时用 panel_name 按名字找）
        panel_name: 目标面板中文名（如「孩子档案」「错题本」，模糊匹配）
        fields: 要录入的内容 JSON 对象字符串，键=面板字段名，如 '{"姓名":"小明","性别":"男","出生日期":"2018-05-01"}'
    """
    try:
        from src.core.panel_manager import get_panel_manager
        pm = get_panel_manager()
        # ── 定位面板：panel_id 优先，否则按名字模糊匹配（含子序列） ──
        pid = str(panel_id or '').strip()
        pname = str(panel_name or '').strip()
        if pid:
            if not pm.panel_exists(pid):
                return {'success': False, 'error': f'面板 {pid} 不存在；先用 query_panel 查现有面板，没有就先 create_panel 建骨架'}
        elif pname:
            hits = [p for p in (pm.get_all_panels() or [])
                    if not p.get('hidden') and (pname in str(p.get('name') or '') or _panel_name_match(pname, p.get('name')))]
            if not hits:
                return {'success': False, 'error': f'没找到名字带「{pname}」的面板；可先 create_panel 建骨架再录入'}
            if len(hits) > 1:
                return {'success': False,
                        'error': '名字匹配到多个面板，请向用户复述候选让其确认，或改用 panel_id 精确指定',
                        'candidates': [{'panel_id': p.get('id'), 'name': p.get('name')} for p in hits[:5]]}
            pid = hits[0].get('id')
        else:
            return {'success': False, 'error': '缺少 panel_id 或 panel_name（二选一）'}
        # ── 解析 fields：必须是 JSON 对象 ──
        try:
            data = json.loads(fields) if isinstance(fields, str) else (fields or {})
        except Exception:
            return {'success': False, 'error': 'fields 不是合法的 JSON 对象，应为 {"字段名":"值", ...}'}
        if not isinstance(data, dict) or not data:
            return {'success': False, 'error': 'fields 至少要有一个字段，如 {"姓名":"小明"}'}
        data = {str(k).strip()[:20]: (str(v)[:500] if v is not None else '') for k, v in data.items()}
        # ── 特判：孩子成长档案是专属子系统，children 主档走 ka.create_child ──
        if pid == 'kid_archive':
            from src.core import kid_archive as _ka
            cd = {}
            unknown = []
            for k, v in data.items():
                eng = _KID_CHILD_FIELD_MAP.get(k)
                if eng:
                    cd[eng] = v
                else:
                    unknown.append(k)
            if not cd.get('name'):
                return {'success': False, 'error': '孩子成长档案建档至少要有「姓名」；支持字段：姓名/小名/性别/出生日期/学校/年级/班级/备注'}
            cid = _ka.create_child(cd)
            note = f'已建档（孩子 id={cid}），在「孩子成长档案」面板就能看到；已自动记一条时间轴。'
            if unknown:
                note += f'字段 {unknown} 不在主档支持范围，没存'
            return {'success': True, 'panel_id': 'kid_archive', 'child_id': cid, 'record': cd, 'note': note}
        # ── 通用面板：先预检列白名单，全被丢弃就报错，不落空壳行 ──
        from src.models.database import get_connection
        from src.core.panel_manager import DB_PATH as _PM_DB
        conn = get_connection(str(_PM_DB))
        clean, dropped = pm._filter_columns(conn, f'panel_{pid}', dict(data))
        # ★ #1513（2026-11-14）：预检失败必须附真实字段清单——16:14 修车实锤：
        #   模型传「事件/内容」被拒后瞎猜「项目」（真列叫「保养项目」），甚至被旧文案
        #   引导去 edit_panel 改面板定义（编了个不存在的 JSON 路径）。有清单才能第二轮直接写对。
        _all_cols = [r[1] for r in conn.execute(f'PRAGMA table_info("panel_{pid}")').fetchall()]
        conn.close()
        _usable = [c for c in _all_cols if c not in ('id', 'created_at', 'updated_at')]
        if not clean:
            return {'success': False,
                    'error': f'「{pid}」没有这些字段：{list(data.keys())}。'
                             f'该面板可用字段：{_usable}。'
                             f'请把内容对号入座到可用字段重录（语义近的直接映射，如「项目」→「保养项目」；'
                             f'日期用 YYYY-MM-DD），不要新建面板、不要改面板定义',
                    'available_fields': _usable}
        # ★ #1224 双通道重复防线：15 分钟内同面板高相似记录 → 不重复插
        #   日期只做「不同=放行」的门，不参与文本相似度（日期前缀会稀释真实差异，练腿/练背实锤 0.92 误拦）
        _cand = ' '.join(str(v) for k, v in data.items() if v and k != '日期')
        _dup = _recent_dup_scan(f'panel_{pid}', _cand, threshold=0.7,
                                time_field='日期', new_time=str(data.get('日期', '')),
                                pick_fields=tuple(k for k in data.keys() if k != '日期'))
        if _dup:
            return {'success': True, 'panel_id': pid, 'dedup': True, 'id': _dup[0],
                    'note': f'这条刚记过（#{_dup[0]}：{_dup[1]}），没有重复录入'}
        # ★ #1378（2026-11-02）冰箱录入兜底统一走公共函数（分区校验门+到期日词典+状态默认）
        #   ——#40「胡姬花花生油」分区 NULL 实锤补丁（Eye 走本工具入库，旧守门只管到期日）
        if pid.startswith('fridge_stock'):
            try:
                from src.core.fridge_hub import fridge_intake_guard as _fig, _append_note as _apn
                _gn = _fig(data)
                _apn(data, _gn)
            except Exception:
                pass
        result = pm.insert_panel_data(pid, data)  # #938 白名单过滤在内部：非本表列自动丢弃
        dropped2 = result.pop('dropped_keys', None)
        note = f'已录入（第 {result.get("id")} 条）。'
        if dropped2:
            # ★ #1513：部分丢弃不再无感——note 强提示 + 返回可用字段，引导模型 update 补录。
            #   16:14 修车实锤：「项目=修车」被静默丢弃还报成功，用户以为记全了其实只剩备注。
            note += (f'⚠️ 但字段 {dropped2} 不是「{pid}」的列，没存上！可用字段：{_usable}。'
                     f'请立即用 update_panel_record 把这些内容补到正确的字段（如「项目」→「保养项目」），'
                     f'或向用户确认后重录')
            logger.warning(f'[BuiltinTools] #1513 add_panel_record {pid} 部分字段被丢弃: {dropped2}，已要求模型补录')
        return {'success': True, 'panel_id': pid, 'record': result,
                'dropped': dropped2 or [], 'available_fields': _usable, 'note': note}
    except Exception as e:
        return {'success': False, 'error': f'录入失败：{e}'}


# ═══ ★ #1134（2026-10-14）随身记收件箱：未分类碎片的暂存处 ═══
# 定位：用户说「记一下XX」但内容不像任何模块的，先进收件箱，别硬塞面板、也别当闲聊蒸发。

def add_inbox_note(content='', source='chat', remind_at=''):
    """往随身记收件箱存一条碎片（用户说「记一下XX」「帮我先记着」但内容不像记账/日程/任何模块时调用）；内容带「提醒我XX时间XX」时传 remind_at 挂提醒

    Args:
        content: 要记的内容原文（别改写，保留用户原话）
        source: 来源标记（chat=对话默认）
        remind_at: 提醒时间（选填，'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD'；用户说「提醒我明天…」时解析填入）
    """
    try:
        from src.core.inbox import add_note
        r = add_note(content, source=str(source or 'chat')[:10], remind_at=str(remind_at or '').strip())
        if not r.get('success'):
            return {'success': False, 'error': r.get('message', '存入失败')}
        note = '已放进随身记收件箱。'
        if r.get('confident') and r.get('suggest'):
            note += f"这条比较像「{r['suggest']}」的事，可以问用户要不要直接记过去。"
        return {'success': True, 'id': r['id'], 'suggest': r.get('suggest', ''),
                'note': note}
    except Exception as e:
        return {'success': False, 'error': f'随身记存入失败：{e}'}


# ═══ ★ #1219（2026-10-25）提醒归位：set_reminder 直进提醒系统（不再借道随身记）═══
# 痛点实锤（开发者 10-25 深夜）：FC 工具箱没有「直接设提醒」工具，所有提醒只能走
# add_inbox_note(remind_at) 借道随身记——健身提醒进了琐事箱，违反「能进模块的不进随身记」。
def set_reminder(title='', remind_at='', module='通用', repeat='once'):
    """直接设一条提醒（进提醒系统，带域归属标签），不经过随身记。

    Args:
        title: 提醒内容（如「练胸」「交物业费」）
        remind_at: 提醒时间，'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD'（默认当天09:00）
        module: 域归属——fitness=健身运动 / fridge=冰箱食材 / items=物品家居 /
                kid=孩子学习 / calendar=日程 / 通用=其他（面板按域显示相关提醒）
        repeat: once=一次 / daily=每天 / weekly=每周 / monthly=每月
    """
    from src.core.home_reminder import add_reminder
    module = str(module or '通用').strip()
    # ★ #1224 双通道重复防线：15 分钟内相似标题且提醒时间一致（或都没时间）→ 不重复设
    _dup = _recent_dup_scan('home_reminders', f'{module} {title}'.strip(), time_field='remind_at',
                            new_time=remind_at, pick_fields=('module', 'title'))
    if _dup:
        return {'success': True, 'id': _dup[0], 'dedup': True, 'module': module,
                'remind_at': _dup[2] or remind_at,
                'message': f'这条提醒刚设过（#{_dup[0]}「{_dup[2] or "无固定时间"}」），没有重复设置'}
    rid, err = add_reminder(module=module, title=str(title or '').strip(),
                            remind_at=str(remind_at or '').strip(),
                            repeat=str(repeat or 'once'), source='chat')
    if err:
        return {'success': False, 'error': err}
    return {'success': True, 'id': rid, 'module': module, 'remind_at': remind_at,
            'message': f'已直接设提醒「{title}」→ {remind_at}（归属:{module}），到点会主动叫你——'
                       f'没走随身记，在管家中心「提醒」区和对应面板都能看到'}


# ═══ ★ #1220-C（2026-10-25）长期语义记忆：remember_fact ═══
# 用户随口说的生活事实（孩子过敏药、WiFi 密码、丈母娘生日…），对话结束后仍要记得。
# 外脑判断「这是值得长期记住的事实/偏好」时调用；下次相关对话自动召回注入。
def remember_fact(fact='', action='remember', fact_id=0, limit=30):
    """长期记忆：把生活事实存进长期记忆 / 列出已记住的 / 忘掉某条。

    Args:
        action: remember=记住一条（默认）/ list=列出已记住的 / forget=忘掉 fact_id 那条
        fact: 要长期记住的事实（一句话，含关键细节，如「儿子对青霉素过敏」）
        fact_id: forget 时要忘掉的条目 id（list 结果里拿）
        limit: list 时的条数上限
    """
    from src.core import memory_facts
    action = str(action or 'remember').strip().lower()
    if action in ('list', '查', '列出'):
        rows = memory_facts.list_facts(limit=limit)
        if not rows:
            return {'success': True, 'facts': [], 'message': '长期记忆还是空的'}
        return {'success': True, 'facts': rows,
                'message': f'共 {len(rows)} 条长期记忆（最新的在前）'}
    if action in ('forget', '忘', '忘掉'):
        if not fact_id:
            return {'success': False, 'error': 'forget 需要 fact_id（先 list 拿到 id）'}
        return memory_facts.forget_fact(fact_id)
    r = memory_facts.add_fact(fact, source='chat')
    if r.get('success'):
        verb = '更新了' if r.get('updated') else '记住了'
        return {'success': True, 'id': r['id'],
                'message': f'{verb}：「{fact}」——以后相关的事我会自动想起来'}
    return r


# ═══ ★ #1217（2026-10-25）跨数据家查证：query_my_records ═══
# 痛点实锤：用户问「你记录到了哪里」，外脑只会调 query_panel（查面板表），
# 随身记/提醒/任务都不在面板里 → 查空 → 误答「没落库」+ 提议重记（会重复）。
def query_my_records(keyword='', days=7, limit=6):
    """跨数据家查证用户让哈文记过的东西：随身记+提醒+任务+面板记录 一次查全。

    Args:
        keyword: 关键词（如「练胸」「加油」；空=只列各数据家最近几条）
        days: 只查最近 N 天（默认 7）
        limit: 每个数据家最多返回条数
    """
    from datetime import datetime as _dt, timedelta as _td
    since = (_dt.now() - _td(days=max(1, min(int(days or 7), 90)))).strftime('%Y-%m-%d %H:%M:%S')
    limit = max(1, min(int(limit or 6), 20))
    kw = str(keyword or '').strip()
    sections = []
    try:
        from src.models.database import get_connection
        from src.core.panel_manager import DB_PATH as _PM_DB
        like = f'%{kw}%' if kw else '%'
        conn = get_connection(str(_PM_DB))
        # ① 随身记收件箱
        rows = conn.execute(
            "SELECT id, content, remind_at, status, created_at FROM inbox_notes "
            "WHERE created_at >= ? AND content LIKE ? ORDER BY id DESC LIMIT ?",
            (since, like, limit)).fetchall()
        if rows:
            sections.append({'source': '随身记收件箱', 'items': [
                {'id': r[0], 'content': r[1], 'remind_at': r[2], 'status': r[3], 'time': r[4]}
                for r in rows]})
        # ② 提醒（home_reminders，含随身记转正）
        rows = conn.execute(
            "SELECT id, title, remind_at, notified, source FROM home_reminders "
            "WHERE created_at >= ? AND title LIKE ? ORDER BY id DESC LIMIT ?",
            (since, like, limit)).fetchall()
        if rows:
            sections.append({'source': '提醒', 'items': [
                {'id': r[0], 'title': r[1], 'remind_at': r[2],
                 '已响': bool(r[3]), '来源': r[4] or 'chat'}
                for r in rows]})
        # ③ 任务（task_list / structured_tasks 表若存在）
        for tbl, cols in (('task_list', 'id, title, due, status, created_at'),
                          ('structured_tasks', 'id, title, due_date, status, created_at')):
            try:
                cur_cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()]
                if not cur_cols:
                    continue
                tcol = 'due' if 'due' in cur_cols else ('due_date' if 'due_date' in cur_cols else '')
                ccreated = 'created_at' if 'created_at' in cur_cols else ''
                where_extra = f" AND created_at >= '{since}'" if ccreated else ''
                rows = conn.execute(
                    f"SELECT {cols} FROM \"{tbl}\" WHERE title LIKE ?{where_extra} "
                    f"ORDER BY id DESC LIMIT ?", (like, limit)).fetchall()
                if rows:
                    sections.append({'source': f'任务（{tbl}）', 'items': [str(r)[:120] for r in rows]})
            except Exception:
                continue
        # ④ 面板记录（有关键词才扫，控制耗时）
        if kw:
            try:
                from src.core.panel_manager import get_panel_manager
                pm = get_panel_manager()
                hits = _panel_like_search(pm, kw, limit=limit)
                if hits:
                    sections.append({'source': '面板记录',
                                     'items': [str(h)[:150] for h in hits[:limit]]})
            except Exception:
                pass
        conn.close()
        if not sections:
            scope = f'最近{days}天内有关键词「{kw}」的' if kw else '最近的'
            return {'success': True, 'sections': [],
                    'message': f'{scope}记录里没有查到。注意：查询为空不代表当时没落库——'
                               '可能是记在更早时间或别的数据家，可如实告知用户待进一步确认'}
        total = sum(len(s['items']) for s in sections)
        return {'success': True, 'sections': sections,
                'message': f'查到 {total} 条（分布在 {len(sections)} 个数据家），如实按来源告知用户'}
    except Exception as e:
        return {'success': False, 'error': f'查证失败：{e}'}


# ═══ ★ #1036（2026-10-06）对话消库存：改/删面板记录（已批准「开发 全部都做」批③）═══
# 痛点：东西吃完了/用完了只能开面板手动改。「黄瓜吃完了」「只剩2个鸡蛋」「把那条XX删了」
# 这类话哈文应当能直接落库。
# 安全设计：两段式确认门——第一步 action=preview 只找不碰，把候选复述给用户听；
# 用户确认后才第二步 action=apply（必须带 confirm=true）。多候选让用户挑，禁止猜。

def _panel_like_search(pm, kw, panel_id='', limit=8):
    """FTS 没命中时的 LIKE 兜底：逐面板扫文本列（全部参数化查询，无注入面）"""
    out = []
    try:
        from src.models.database import get_connection
        from src.core.panel_manager import DB_PATH as _PM_DB
        panels = pm.get_all_panels()
        if panel_id:
            panels = [p for p in panels if p.get('id') == panel_id]
        conn = get_connection(str(_PM_DB))
        for p in panels[:20]:
            tid = str(p.get('id') or '')
            if not pm._valid_panel_id(tid):
                continue
            try:
                cols = [r[1] for r in conn.execute(f'PRAGMA table_info("panel_{tid}")').fetchall()]
                text_cols = [c for c in cols if c not in ('id', 'created_at', 'updated_at')]
                if not text_cols:
                    continue
                conds = ' OR '.join([f'"{c}" LIKE ?' for c in text_cols])
                sel = ', '.join([f'"{c}"' for c in text_cols])
                rows = conn.execute(
                    f'SELECT id, {sel} FROM "panel_{tid}" WHERE {conds} '
                    f'ORDER BY updated_at DESC LIMIT ?',
                    ['%' + kw + '%'] * len(text_cols) + [limit]).fetchall()
                for row in rows:
                    parts = []
                    for c, v in zip(text_cols, row[1:]):
                        s = str(v or '')
                        if s and len(s) <= 120 and 'data:image' not in s[:30]:
                            parts.append(f'{c}:{s}')
                    out.append({'row_id': row[0], 'panel': p.get('name', tid),
                                'panel_id': tid, 'summary': ' | '.join(parts)[:180]})
            except Exception:
                continue   # 单面板出错不挡其它面板
        conn.close()
    except Exception:
        pass
    return out


def _resolve_panel_for_row(pm, rid):
    """apply 没给 panel_id 时按行号全面板反查归属表（防止拿空串改错表）"""
    try:
        from src.models.database import get_connection
        from src.core.panel_manager import DB_PATH as _PM_DB
        conn = get_connection(str(_PM_DB))
        for p in pm.get_all_panels():
            tid = str(p.get('id') or '')
            if not pm._valid_panel_id(tid):
                continue
            try:
                hit = conn.execute(f'SELECT id FROM "panel_{tid}" WHERE id = ?', [rid]).fetchone()
                if hit:
                    conn.close()
                    return tid
            except Exception:
                continue
        conn.close()
    except Exception:
        pass
    return ''


def update_panel_record(keyword='', panel_id='', action='preview', row_id=0,
                        set_fields='', delete=False, confirm=False):
    """对话改/删面板记录（消库存）：用户说「黄瓜吃完了」「只剩2个鸡蛋」「把那条XX删了」时调用

    Args:
        keyword: 要找的记录关键词（如「黄瓜」「鸡蛋」）
        panel_id: 限定在某个面板里找（选填，不填扫全部面板）
        action: 'preview'=只找不改（第一步）；'apply'=真正修改/删除（第二步）
        row_id: apply 时必填，来自 preview 返回的 row_id
        set_fields: apply 时要改的字段 JSON 对象字符串，如 '{"数量": "2"}' 或 '{"状态": "已吃完"}'
        delete: apply 时为 true 表示删掉这条记录
        confirm: apply 时必须为 true——代表已向用户复述且用户确认过
    """
    try:
        from src.core.panel_manager import get_panel_manager
        pm = get_panel_manager()
        kw = str(keyword or '').strip()[:40]
        action = str(action or 'preview').strip().lower()

        # ── 第一步：preview 只找不碰 ──
        if action == 'preview':
            if not kw:
                return {'success': False,
                        'error': '请提供要找的记录关键词（如「黄瓜」「鸡蛋」）'}
            hits = []
            try:
                hits = pm.fts_search(kw, panel_id=str(panel_id or ''), limit=8)
            except Exception:
                hits = []
            if not hits:
                hits = _panel_like_search(pm, kw, panel_id=str(panel_id or ''), limit=8)
            if not hits:
                return {'success': True, 'count': 0,
                        'note': '没找到相关记录。可以告诉用户没找到，问是不是记在别的说法下'
                                '（比如「黄瓜」记成了「青瓜」），或者去面板里看一眼'}
            results = []
            for h in hits[:8]:
                if 'record_id' in h:   # fts_search 返回结构
                    results.append({'row_id': h['record_id'],
                                    'panel': h.get('panel_id', ''), 'panel_id': h.get('panel_id', ''),
                                    'summary': str(h.get('text', ''))[:180]})
                else:                  # LIKE 兜底返回结构（已带 panel 名）
                    results.append(h)
            # 给 panel 补中文名
            names = {}
            try:
                for p in pm.get_all_panels():
                    names[p['id']] = p.get('name', p['id'])
            except Exception:
                pass
            for r in results:
                r['panel'] = names.get(r.get('panel_id'), r.get('panel') or r.get('panel_id', ''))
            return {'success': True, 'count': len(results), 'results': results,
                    'note': ('找到了 ' + str(len(results)) + ' 条。下一步：把候选复述给用户听'
                             '（几号记录、内容是什么），请用户确认改哪条、改成什么；'
                             '多条相似时让用户挑。用户确认后再调本工具 action=apply 并带 confirm=true。'
                             '现在什么都还没改' if len(results) > 1 else
                             '找到 1 条：把内容复述给用户听并确认（比如「要把黄瓜那条记成吃完吗？」）。'
                             '用户确认后再调 action=apply 并带 confirm=true。现在什么都还没改')}

        # ── 第二步：apply 动手（确认门） ──
        if action == 'apply':
            if not confirm:
                return {'success': False,
                        'error': '还没拿到用户确认。请先把要改的内容复述给用户听，用户说确认后'
                                 '再调本工具并带 confirm=true（防误改铁门，不能跳）'}
            try:
                rid = int(row_id)
            except (TypeError, ValueError):
                return {'success': False, 'error': 'row_id 必须是 preview 返回的数字 id。'
                                                    '请先调 action=preview 拿候选'}
            # ★ AI 漏传 panel_id 时按行号反查归属表，不拿空串瞎改
            pid = str(panel_id or '').strip()
            if not pid:
                pid = _resolve_panel_for_row(pm, rid)
            if not pid:
                return {'success': False,
                        'error': 'panel_id 没定位到。请传 preview 返回结果里的 panel_id'}
            if delete:
                ok = pm.delete_panel_data(pid, rid)
                if not ok:
                    return {'success': False, 'error': '删除没成功（记录可能已不存在）'}
                return {'success': True, 'deleted': True, 'row_id': rid,
                        'note': '已删除。用人话告诉用户删了哪条'}
            # 改字段：set_fields JSON 对象
            try:
                data = json.loads(set_fields) if isinstance(set_fields, str) else (set_fields or {})
            except Exception:
                return {'success': False,
                        'error': 'set_fields 不是合法 JSON 对象，应为 {"字段名": "新值"}，如 {"数量": "2"}'}
            if not isinstance(data, dict) or not data:
                return {'success': False,
                        'error': 'set_fields 为空。要改成什么请放进 set_fields，如 {"数量": "2"}；'
                                 '要删掉记录请传 delete=true'}
            data = {str(k)[:20]: (str(v)[:120] if not isinstance(v, (int, float)) else v)
                    for k, v in data.items()}
            result = pm.update_panel_data(pid, rid, data)
            changed = {k: result.get(k) for k in data}
            return {'success': True, 'row_id': rid, 'panel_id': pid, 'changed': changed,
                    'note': '已改好。用人话告诉用户改了什么（比如「记好了，黄瓜已清空」）'}

        return {'success': False, 'error': 'action 只支持 preview（先找）或 apply（确认后改）'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ═══ ★ #1037（2026-10-06）对话改模块字段（已批准「开发 全部都做」批④）═══
# 用户说「给车辆保养模块加个字段」「把备注改名成情况说明」「删掉XX字段」时调用。
# 加/改名直接做（旧数据保留）；删字段必须确认门（复述「这列历史记录也会删掉」）。

def edit_panel(panel_id='', action='list', field_name='', new_name='',
               ftype='text', options='', confirm=False):
    """对话改模块字段：list 列出字段 | add 加 | rename 改名 | delete 删

    Args:
        panel_id: 模块标识（不确定就先 query 或 list 面板确认）
        action: list / add / rename / delete
        field_name: 字段名（add/rename 的原字段/delete 的目标）
        new_name: rename 的新字段名
        ftype: add 时字段类型 text/number/date/select
        options: select 类型的选项 JSON 数组字符串，如 '["已完成","进行中"]'
        confirm: delete 时必须为 true（用户确认过「数据一起删掉」）
    """
    try:
        from src.core.panel_manager import get_panel_manager
        opts = None
        if options:
            try:
                opts = json.loads(options) if isinstance(options, str) else options
            except Exception:
                return {'success': False, 'message': 'options 不是合法 JSON 数组，如 ["已完成","进行中"]'}
        r = get_panel_manager().edit_panel_fields(
            str(panel_id or ''), str(action or 'list'),
            field_name=str(field_name or ''), new_name=str(new_name or ''),
            ftype=str(ftype or 'text'), options=opts, confirm=bool(confirm))
        return r
    except Exception as e:
        return {'success': False, 'message': str(e)}


TOOL_REGISTRY = {
    # 基础工具
    'download_file': {'function': download_file, 'description': '下载文件', 'params': {'url': 'str', 'save_path': 'str'}},
    'execute_command': {'function': execute_command, 'description': '执行系统命令', 'params': {'command': 'str'}},
    # ★ A批A1(2026-09-13): 删除此处重复的 read_file/list_dir 简版定义
    #   （dict 重复键静默覆盖——详版定义见下方「2026-08-02 文件操作工具」区，行为不变）
    'write_file': {'function': write_file, 'description': '写文件', 'params': {'path': 'str', 'content': 'str'}},
    'edit_file': {'function': edit_file, 'description': '编辑文件(精确替换)', 'params': {'path': 'str', 'old_string': 'str', 'new_string': 'str'}},
    'delete_path': {'function': delete_path, 'description': '删除文件/目录', 'params': {'path': 'str'}},
    # ★ A批A5(2026-09-13): search×4→1（旧名保留为别名，不注入FC目录）
    'search': {'function': search_tool, 'description': '统一搜索：type=content搜文件内容|filename搜文件名|knowledge搜知识库|conversation搜历史对话。用户问"找文件/搜记录/知识库里有没有"时调用', 'params': {'type': 'str', 'pattern': 'str', 'path': 'str', 'file_type': 'str', 'limit': 'int'}},
    'search_content': {'function': search_content, 'description': '搜索文件内容', 'params': {'pattern': 'str', 'path': 'str', 'file_type': 'str'}, 'alias': True},
    'search_files': {'function': search_files, 'description': '搜索文件名', 'params': {'pattern': 'str', 'path': 'str'}, 'alias': True},

    # 模型工具
    'scan_system': {'function': scan_system, 'description': '扫描系统', 'params': {}},
    'get_current_model_status': {'function': get_current_model_status, 'description': '查询哈文当前实际使用的模型和模式（本地还是外脑、具体模型名、引擎）。用户询问当前模式或模型时调用本工具，不要猜测。', 'params': {}},
    'install_model': {'function': install_model, 'description': '安装模型（仅在用户明确要求安装/下载模型时调用；用户讨论模型问题、检测模型状态时禁止调用）', 'params': {'name': 'str', 'source': 'str', 'quantization': 'str'}},
    'uninstall_model': {'function': uninstall_model, 'description': '卸载模型（仅在用户明确要求删除/卸载模型时调用；用户讨论模型问题时禁止调用）', 'params': {'name': 'str'}},
    'install_package': {'function': install_package, 'description': '安装Python包', 'params': {'name': 'str'}},

    # 知识/搜索工具
    # ★ 方案A 联网授权制（2026-10-05 定版拍板）：联网类工具不再常驻 FC——
    #   仅当用户点🌐联网按钮（web_search=True）时才注入/可执行。requires 门禁三处生效：
    #   get_tool_definitions / get_tool_catalog_text / call_tool 执行兜底
    'web_search': {'function': web_search, 'description': '联网搜索', 'params': {'query': 'str'}, 'requires': 'web_search_enabled'},
    'search_knowledge': {'function': search_knowledge, 'description': '搜索知识库', 'params': {'query': 'str'}, 'alias': True},
    'list_knowledge': {'function': list_knowledge, 'description': '列出知识库内容（已导入的文件/条目）', 'params': {}},
    'search_conversation': {'function': search_conversation, 'description': '搜索历史对话', 'params': {'query': 'str'}, 'alias': True},
    # ★ #920 统一图库检索（2026-09-27）：找图片/照片唯一正式通道
    'search_images': {'function': search_images_tool, 'description': '搜索哈文图库里的图片/照片（手机拍照、聊天发图、面板附件、知识库图片都在这）。用户问"找照片/那图呢/我拍过的XX/冰箱照片"时调用本工具，禁止去文件系统扫描。找到后用 Markdown 图片语法 ![](url) 展示。★ 护栏（2026-11-15）：用户要的是关键词/建议/英文单词/知识解释等纯知识问答时（如"乡村曲风用什么乐器、给几个英文词"）禁止调用本工具，直接用自己的知识回答——搜图库只会答非所问（qwen3-vl 误触实锤）', 'params': {'q': 'str', 'album': 'str', 'panel_id': 'str', 'limit': 'int'}},
    # ★ #925 T4 记忆相册：看图说话（编故事/写诗/讲给孩子听），紧跟 search_images 用
    'describe_image': {'function': describe_image_tool, 'description': '看图说话：给图库里某张照片编故事/写诗/讲给孩子听（本地视觉免费）。用户说「给那张照片编个故事/写首诗」时用：先 search_images 拿到图片 id，再调本工具（img_id=图片id，style=story/poem/kid/desc）', 'params': {'img_id': 'int', 'style': 'str'}},
    'manage_album': {'function': manage_album_tool, 'description': '相册管理：建相册/把照片移入相册/按人脸把某位家人的照片归到一个相册。用户说「建个XX相册」「把这几张移到XX」「把老谭的照片归一起」时必须调本工具动手，严禁嘴上说已归类而不调工具。action=list 列全部相册 | action=create 建相册(name=相册名) | action=move 移照片(先 search_images 拿 id，传 img_ids=[...] 和 name=目标相册) | action=move_faces 人脸归册(person=家人名字，本地人脸识别扫全库后自动移入 name 或 person 同名相册)', 'params': {'action': 'str', 'name': 'str', 'img_id': 'int', 'img_ids': 'list', 'person': 'str'}},
    # ★ #1306/#1307（2026-10-29）批量识别提议 + 确认落库（AI 只提议，用户拍板）
    'identify_images': {'function': identify_images_tool, 'description': '批量识别照片：查某天录入的图库照片并逐张识别，产出「建议标签+建议相册」清单。用户说「帮我识别今天刚录的10张照片/重新分类昨天的照片」时调用。depth=fast 本地免费秒级（默认）；depth=deep 外脑细描更准但烧 token，必须用户明确同意深度识别才用。★ 只提议不落库，用户确认后用 apply_photo_suggestion 写入', 'params': {'date': 'str', 'limit': 'int', 'depth': 'str'}},
    'apply_photo_suggestion': {'function': apply_photo_suggestion_tool, 'description': '照片建议确认落库：用户对识别清单点头后调用。ids 与 albums 逗号对齐（如 ids=140,141 albums=冰箱,生活照），只给一个相册名=全部归它；tags 可选合并进标签。deep=on/off 可顺带设置相册「外脑深析」开关。未获用户确认禁止调用', 'params': {'ids': 'str', 'albums': 'str', 'tags': 'str', 'deep': 'str'}},

    # ★ #1233 M4-1（2026-10-26）HarvenEye 握手三工具：本地视觉引擎深度识别/搜图/问图。
    #   隔离红线：只 HTTP 调 HarvenEye（127.0.0.1:7001），零 import 引擎代码；
    #   Eye 未启动时优雅降级（返回提示不炸哈文），基础看图说话不受影响
    'eye_recognize': {'function': _eye.eye_recognize_tool, 'description': '【身份:HarvenEye 本地视觉引擎】深度识别一张图：物体检测+标签+描述+置信度，分层溯源（重复图秒回/检测/语义/大模型兜底）。适合「这张照片里有什么/帮我归类这张图/识别一下」等需要结构化视觉结果的场景。image_path 传本机图片绝对路径、图库图片 id 或 search_images 返回的 url。do_index=True 时顺带入 HarvenEye 视觉索引库（之后可用 eye_search 搜到）', 'params': {'image_path': 'str', 'preset': 'str', 'do_index': 'bool', 'force_vlm': 'bool'}},
    'eye_search': {'function': _eye.eye_search_tool, 'description': '【身份:HarvenEye 本地视觉引擎】以文搜图（关键词+语义双路）：搜的是视觉索引库，能按画面内容找到图（如「有冰箱的照片」「数学作业」「海边的照片」），比 search_images 的文字标签匹配更懂画面。用户对 search_images 结果不满意或按画面内容找图时用本工具。结果里的 url 可直接 Markdown 展示。★ 护栏（2026-11-15）：纯知识问答（要关键词/建议/解释/英文单词）禁止调用，直接用自己的知识回答', 'params': {'query': 'str', 'top_k': 'int'}},
    'eye_ask': {'function': _eye.eye_ask_tool, 'description': '【身份:HarvenEye 本地视觉引擎】问图：本地视觉大模型直读一张图并回答问题（卷子题目/说明书/单据/药品盒等需要「读懂图」的场景）。image_path 传本机图片绝对路径、图库图片 id 或 search_images 返回的 url，question 写具体问题（留空=直接描述图片）。返回模型原文，需组织成人话讲给用户', 'params': {'image_path': 'str', 'question': 'str'}},
    # ★ #1292（2026-10-29）HarvenEye 大脑自装一条龙（检测→推荐→下载/拷贝→钉选→冒烟）
    'vision_install': {'function': _vision_install_tool, 'description': '【身份:HarvenEye】给 HarvenEye 安装视觉理解大脑（本地视觉大模型）。action=recommend：查本机硬件显存+盘点已装/可装档位并给出推荐（只荐不装，把结果展示给用户等确认）；action=install：用户明确同意安装某档位后才调用，必须 confirmed=True 且 model_id=推荐清单里的 id（铁律0：绝不代替用户做决定）；action=status：查下载/拷贝进度百分比、是否完成钉选与冒烟结果，用户问「装得怎样了」或安装期间主动轮询时调用', 'params': {'action': 'str', 'model_id': 'str', 'confirmed': 'bool'}},

    # ★ #929-A4（2026-09-29）语音档案：默认不存，用户明确说存才存
    'save_voice_note': {'function': save_voice_note_tool, 'description': '把语音内容存进语音档案。用户说「这段话存下来/把刚才的语音存进档案」时调用；text 不传则自动用最近一次语音转写的文字', 'params': {'text': 'str'}},
    'search_voice_notes': {'function': search_voice_notes_tool, 'description': '搜索语音档案（用户之前明确要求保存的语音文字）。用户问「我之前说过XX/语音记录」时调用', 'params': {'q': 'str', 'limit': 'int'}},

    # 垂直模型工具
    'execute_vertical': {'function': execute_vertical, 'description': '执行垂直模型', 'params': {'model_id': 'str', 'params': 'dict'}},

    # 实用工具
    'get_time': {'function': get_time, 'description': '获取当前时间', 'params': {}},
    # ★ A批A3: 目录式FC元工具 — 需要目录中某工具的参数详情时先调它
    'tool_schema': {'function': tool_schema, 'description': '查询其他工具的完整参数定义。当你想用目录里列出的工具但不知道参数时，先调用本工具（names=逗号分隔的工具名），拿到参数定义后再调用目标工具', 'params': {'names': 'str'}},
    # ★ #1037（2026-10-06）对话建模块：用户说「帮我建个模块/台账，字段有XX」时调用。
    #   确认门（软）：字段清单明显时可直接建并复述结果；用户描述模糊（没说清要记哪些信息）时先追问，别瞎编字段
    'create_panel': {'function': create_panel, 'description': '创建记录模块/台账（如「帮我建个车辆保养模块，字段有日期、里程、项目、费用」）。把用户说的信息整理成字段：名称/项目类给 text、数字金额给 number、日期给 date、有固定选项给 select+options。建完把返回里的 note 转告用户。若用户在建模块时同时给了具体数据（如「给孩子建档：小明…」），建完骨架立即调 add_panel_record 把数据写进去，不要留空表', 'params': {'name': 'str', 'fields': 'str(JSON数组)', 'description': 'str', 'icon': 'str', 'panel_id': 'str'}},
    # ★ #1123（2026-10-13）对话建档/录入：往面板新增一条记录，补齐「建骨架→录数据→改/删」最后一环
    'add_panel_record': {'function': add_panel_record, 'description': '往已有面板/台账新增一条记录（用户报出具体信息要存时调用：如「给孩子建档：小明，男，2018-05-01」「记一笔加油300元」「帮我把这条错题记到错题本」）。直接落库，不要让用户自己打开面板手动填。panel_id 和 panel_name 二选一；fields 是 JSON 对象，键用面板的字段名（不确定字段名可先 edit_panel action=list 查）。多面板同名时会返回候选让用户挑', 'params': {'panel_id': 'str', 'panel_name': 'str', 'fields': 'str(JSON对象)'}},
    # ★ #1376（2026-11-02）对话改状态：冰箱食物消耗——补齐「说扔了/吃完了」管家真能动手
    'consume_food': {'function': consume_food, 'description': '改冰箱台账状态并记消耗流水（用户说「卤肉扔掉了/坏了/过期了扔掉」→ action=trashed；「黄瓜吃完了/吃掉了」→ action=eaten 时调用；这月扔了多少吃了多少全靠它留痕挂健康画像）。name 传食材名关键词，模糊匹配；匹配到多条会返回 candidates 清单，让用户挑一条后带 row_id 重调；action 不明确先问用户，禁止瞎猜。执行成功回执里带撤销方式，转告用户', 'params': {'name': 'str', 'action': 'str', 'row_id': 'int'}},
    'set_fridge_zone': {'function': set_fridge_zone, 'description': '改冰箱食物的存储分区（用户说「把花生油放调味油盐区」「鸡蛋改放冷冻」时调用）。zone 只能是：冷藏/冷冻/门格/零食饮料/粮食干货/调味油盐；「冰箱」不是分区，用户说「放冰箱」就选 冷藏 或追问。匹配到多条会返回清单让用户挑；改完回执以工具返回为准', 'params': {'name': 'str', 'zone': 'str', 'row_id': 'int'}},
    'undo_last_accounting': {'function': undo_last_accounting, 'description': '撤销最近一次「确认入账」落库的记账行（用户说「撤销刚才入账」「刚记错了撤回来」时调用）。仅 10 分钟内最近一次可撤，超时或重启过要如实告知撤不了，引导去记账面板手动删', 'params': {}},
    # ★ #1134（2026-10-14）随身记收件箱：记录类的兜底去处——不像任何模块的碎片先进这里，不硬塞不蒸发
    'add_inbox_note': {'function': add_inbox_note, 'description': '往随身记收件箱存一条琐事/想法（用户说「记一下XX」「先帮我记着」且不像任何模块时调用）。★注意：带明确时间的「X时间提醒我Y」一律用 set_reminder，禁止塞这里——随身记只收琐事和想法，能进模块的不进随身记（开发者 10-25 拍板）。保留用户原话存入', 'params': {'content': 'str', 'source': 'str', 'remind_at': 'str'}},
    # ★ #1217（2026-10-25）跨数据家查证：用户问「记到哪了/存了没」时用这个，别只查面板
    'query_my_records': {'function': query_my_records, 'description': '跨数据家查证哈文替用户记过的东西：随身记收件箱+提醒+任务+面板记录一次查全（用户问「你刚才记到哪了」「存了没」「我记过XX吗」时必须用这个查证，禁止只调 query_panel 查面板就断言没落库——随身记/提醒/任务都不在面板表里）。keyword 传内容关键词如「练胸」「物业费」；keyword 空=列各数据家最近几条。返回为空时如实说没查到，禁止说「没落库、再记一遍」', 'params': {'keyword': 'str', 'days': 'int', 'limit': 'int'}},
    # ★ #1219（2026-10-25）提醒归位：带时间的提醒直接进提醒系统，不再借道随身记
    'set_reminder': {'function': set_reminder, 'description': '直接设提醒（用户说「X时间提醒我Y」时调用，带时间的提醒一律用这个，禁止塞随身记）。module 按域归属：健身/运动→fitness，吃/过期/冰箱食材→fridge，物品/晾晒/换季/保养→items，孩子/作业/接送/学习→kid，日程会议→calendar，其他→通用。返回回执要告知用户提醒时间与归属', 'params': {'title': 'str', 'remind_at': 'str', 'module': 'str', 'repeat': 'str'}},
    # ★ #1220-C（2026-10-25）长期语义记忆：生活事实/偏好随口说也能长期记住
    'remember_fact': {'function': remember_fact, 'description': '长期记忆：用户随口说的长期事实/偏好/家庭常量（如「儿子对青霉素过敏」「家里WiFi密码是xxx」「孩子爷爷生日腊月初八」）→ action=remember 存下（fact 一句话含关键细节）。用户问「你还记得XX吗」「我跟你说过什么」→ action=list；用户要求忘掉某条 → action=forget（fact_id 从 list 拿）。⚠️一次性的事（明天交物业费）不存这里，那是提醒的事', 'params': {'action': 'str', 'fact': 'str', 'fact_id': 'int', 'limit': 'int'}},
    # ★ #1212（2026-10-25）HarvenHub 主动搜索+安装：装前必须弹确认卡征求用户同意
    'hub_search': {'function': hub_search_tool, 'description': '搜索哈文官方市场 HarvenHub 里的内容（技能/模板/工具/安装包等）。用户说「市场里有没有XX」「帮找个XX技能」时调用。找到后把结果列表讲给用户听；用户明确要装哪个，再调 hub_install', 'params': {'query': 'str', 'item_type': 'str', 'limit': 'int'}},
    'hub_install': {'function': hub_install_tool, 'description': '发起安装 HarvenHub 里的内容（item_id 来自 hub_search 结果）。⚠️本工具只生成安装确认卡，不直接安装——用户界面会弹出确认卡，用户点「同意」后才真下载安装（10分钟内有效）。调用后必须转告用户：确认卡已弹出，请在界面上确认', 'params': {'item_id': 'str'}},
    # ★ #1036（2026-10-06）对话消库存：改/删已有记录，两段式确认门（preview→复述→apply+confirm）
    'update_panel_record': {'function': update_panel_record, 'description': '修改或删除面板/台账里的已有记录（用户说「黄瓜吃完了」「只剩2个鸡蛋」「把那条XX删了」时调用）。必须两步走：第一步 action=preview 按关键词找候选；把候选复述给用户听并请确认；用户确认后才第二步 action=apply（带 row_id + confirm=true，改字段用 set_fields 如 {"数量":"2"}，删除用 delete=true）。禁止跳过确认直接改', 'params': {'keyword': 'str', 'panel_id': 'str', 'action': 'str', 'row_id': 'int', 'set_fields': 'str(JSON对象)', 'delete': 'bool', 'confirm': 'bool'}},
    # ★ #1037（2026-10-06）对话改模块字段：加/改名直接做；删字段过确认门；定制模块拒绝
    'edit_panel': {'function': edit_panel, 'description': '查看或修改某个台账模块的字段（用户说「给XX模块加个备注字段」「把XX字段改名叫XX」「删掉XX字段」「XX模块有哪些字段」时调用）。action=list列出字段|add加字段|rename改字段名|delete删字段。加/改名可以直接做（旧数据保留）；删字段要先复述「这列的历史记录也会一起删掉」征得同意后带 confirm=true。定制模块（记账等）会拒绝，属正常保护', 'params': {'panel_id': 'str', 'action': 'str', 'field_name': 'str', 'new_name': 'str', 'ftype': 'str', 'options': 'str(JSON数组)', 'confirm': 'bool'}},
    'check_software': {'function': check_software, 'description': '检查软件安装状态（是否已安装/版本/是否运行）。当用户问"docker装了吗""python版本""git有没有"时调用此工具，不要写代码。支持：docker/python/git/node/npm/ollama/pip/blender/ffmpeg等', 'params': {'name': 'str'}},
    'ask_user': {'function': ask_user, 'description': '问用户问题', 'params': {'question': 'str', 'options': 'list'}},
    'present_file': {'function': present_file, 'description': '展示文件给用户', 'params': {'path': 'str'}},

    # ★ A批A5: task×4→1
    'task': {'function': task_tool, 'description': '统一任务管理：action=create创建|list列出|update更新状态|get详情。用户说"帮我记个任务/看看任务"时调用', 'params': {'action': 'str', 'task_id': 'str', 'subject': 'str', 'description': 'str', 'status': 'str'}},
    'task_create': {'function': task_create, 'description': '创建任务', 'params': {'subject': 'str', 'description': 'str'}, 'alias': True},
    'task_list': {'function': task_list, 'description': '列出任务', 'params': {}, 'alias': True},

    # ★ A批A5: skill×4→2（旧名保留为别名）
    'skill': {'function': skill_tool, 'description': '统一Skill：action=list列出所有技能|execute执行技能(需skill_id+user_input)', 'params': {'action': 'str', 'skill_id': 'str', 'user_input': 'str'}},
    'skill_script': {'function': skill_script_tool, 'description': '统一Skill脚本：action=list列出有可执行脚本的技能|execute执行脚本(需skill_name+script_action+params)。技能带scripts/目录时用本工具', 'params': {'action': 'str', 'skill_name': 'str', 'script_action': 'str', 'params': 'dict'}},
    'execute_skill': {'function': execute_skill, 'description': '执行Skill', 'params': {'skill_id': 'str', 'user_input': 'str'}, 'alias': True},
    'list_skills': {'function': list_skills, 'description': '列出所有Skills', 'params': {}, 'alias': True},

    # 子代理
    'spawn_agent': {'function': spawn_agent, 'description': '启动子代理处理任务', 'params': {'task': 'str', 'agent_type': 'str'}},

    # 网页抓取
    'web_fetch': {'function': web_fetch, 'description': '抓取网页内容转文本', 'params': {'url': 'str', 'max_length': 'int'}, 'requires': 'web_search_enabled'},
    # ★ 2026-10-20 补实装：比价工具（受权限中心 net_browse_compare 分闸控制；参谋不做代购）
    'price_compare': {'function': price_compare_tool, 'description': '多平台比价：用户要买酒店/机票/商品时用。返回各平台公开价对比表+直达链接。只在权限中心「网页浏览/比价」开启后可用；不下单代付', 'params': {'query': 'str', 'kind': 'str'}},

    # 完整任务管理
    'task_update': {'function': task_update, 'description': '更新任务状态/内容', 'params': {'task_id': 'str', 'status': 'str', 'subject': 'str', 'description': 'str'}, 'alias': True},
    'task_get': {'function': task_get, 'description': '获取单个任务详情', 'params': {'task_id': 'str'}, 'alias': True},

    # GGUF导入Ollama
    'import_ollama': {'function': _import_ollama_wrapper, 'description': '把GGUF文件导入Ollama', 'params': {'gguf_path': 'str', 'model_name': 'str'}},

    # ★ 中级Skill脚本执行
    'execute_skill_script': {'function': execute_skill_script, 'description': '执行Skill的可执行脚本（Python沙箱）', 'params': {'skill_name': 'str', 'action': 'str', 'params': 'dict'}, 'alias': True},
    'list_skill_scripts': {'function': list_skill_scripts_tool, 'description': '列出所有有可执行脚本的技能', 'params': {}, 'alias': True},

    # ★ A批A6: CLI-Anything 动态化 — 未装 cli_anything_hub 时 execute/install 不注入FC目录
    'cli_anything_list': {'function': cli_anything_list, 'description': '列出所有可用的CLI软件工具（Blender/GIMP/Inkscape等），检测本机是否已安装对应软件', 'params': {}},
    'cli_anything_execute': {'function': cli_anything_execute, 'description': '通过CLI-Anything执行软件命令（如Blender渲染、GIMP图像处理等）。需要先安装cli-anything-hub和对应子模块', 'params': {'software': 'str', 'command': 'str', 'args': 'dict'}, 'optional': 'cli_anything'},
    'cli_anything_install': {'function': cli_anything_install, 'description': '安装CLI-Anything或某个软件的子模块（如 blender/gimp/inkscape 等）。会自动检测已安装的子模块', 'params': {'software': 'str'}, 'optional': 'cli_anything'},

    # ★ 2026-08-02: 文件操作工具（只读，安全模式下可用）
    'list_dir': {'function': list_dir, 'description': '列出目录内容。当用户问"下载了什么文件""data目录有什么""skills目录内容"时调用此工具，不要写代码。返回文件名/大小/类型', 'params': {'path': 'str'}},
    'read_file': {'function': read_file, 'description': '读取文件内容（文本文件，最大1MB）。当用户问"这个文件写了什么""看看配置文件"时调用此工具，不要写代码。', 'params': {'path': 'str'}},

    # ★ 2026-09-26 乙（已批准）：面板档案查询 — 找用户自己存过的信息（执照/账目/物品位置等）
    'query_panel': {'function': query_panel_tool, 'description': '查询本机面板档案数据（记账/证件保单/物品存放/孩子/车辆/健康等全部面板记录）。用户找自己存过的信息（如"公司执照在哪""XX的记录""东西放哪了"）时优先用本工具，不要用 list_dir/execute_command 爬目录搜文件。参数 keyword=关键词，panel_id 可选限定某面板；查账目可传 time_range（今天/昨天/本周/本月/上月/近7天）', 'params': {'keyword': 'str', 'panel_id': 'str', 'time_range': 'str'}},

    # ★ 2026-07-21: 邮箱工具
    'email_setup': {'function': email_setup, 'description': '配置邮箱账号（支持QQ/163/Gmail等，自动推断服务器地址）。配置后才能使用email_fetch和email_send', 'params': {'address': 'str', 'password': 'str', 'imap_server': 'str', 'smtp_server': 'str', 'imap_port': 'int', 'smtp_port': 'int'}},
    'email_fetch': {'function': email_fetch, 'description': '通过IMAP拉取邮件，缓存到本地。返回邮件列表（发件人/主题/摘要/日期）。需要先调用email_setup配置邮箱', 'params': {'folder': 'str', 'limit': 'int', 'mark_seen': 'bool'}},
    'email_send': {'function': email_send, 'description': '⚠️通过SMTP发送邮件（危险操作，需用户确认）。需要先调用email_setup配置邮箱', 'params': {'to': 'str', 'subject': 'str', 'body': 'str'}},
    'email_search': {'function': email_search, 'description': '搜索本地缓存的邮件（不联网）。匹配主题/发件人/正文。需要先email_fetch拉取邮件', 'params': {'query': 'str', 'folder': 'str', 'limit': 'int'}},
}


# ★ 方案A 联网授权制（2026-10-05 定版拍板）：联网工具执行兜底门禁。
#   engine.send() 每次按 🌐 开关状态调 set_web_search_allowed()——
#   未授权时即便模型凭记忆喊出 web_search/web_fetch，执行层也直接拒绝（fail-closed）。
_WEB_SEARCH_ALLOWED = False


def set_web_search_allowed(v):
    """由 engine.send() 按用户🌐联网开关状态调用（bool）"""
    global _WEB_SEARCH_ALLOWED
    _WEB_SEARCH_ALLOWED = bool(v)


def web_search_allowed():
    return _WEB_SEARCH_ALLOWED


# ★ #1220-A（2026-10-25）：写类工具清单——这些工具执行成功后 bump 数据版本总线，
#   手机端/SPA 轮询 /api/sync/version 发现版本变化即刷新（电脑写库 → 手机感知）。
_SYNC_WRITE_TOOLS = {
    'set_reminder', 'add_inbox_note', 'add_panel_record', 'update_panel_record',
    'task', 'task_create', 'task_update', 'save_voice_note', 'remember_fact',
    'manage_album',   # ★ 2026-11-12 相册写入也触发手机端同步刷新
}


def call_tool(tool_name, **kwargs):
    """调用工具

    Args:
        tool_name: 工具名
        **kwargs: 工具参数

    Returns:
        dict: 工具执行结果
    """
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return {'success': False, 'error': f'未知工具: {tool_name}'}

    # ★ 方案A 联网授权制: 联网类工具执行兜底（防模型绕过注入门禁直接喊工具名）
    if tool.get('requires') == 'web_search_enabled' and not _WEB_SEARCH_ALLOWED:
        logger.warning(f"[BuiltinTools] 拒绝 {tool_name}：联网未授权（用户未点🌐联网按钮）")
        return {'success': False,
                'error': '联网搜索未授权：请让用户点击聊天框的🌐联网按钮后重新提问，届时我才能帮你查资料'}

    try:
        # ★ 2026-07-29: 支持FC封装工具（handler模式）
        if 'handler' in tool:
            result = tool['handler'](**kwargs)
        else:
            result = tool['function'](**kwargs)
            logger.info(f"[BuiltinTools] {tool_name} 执行完成: success={result.get('success')}")
        # ★ #1220-A 写类工具成功 → 数据版本 +1（失败不 bump，避免手机端空刷新）
        if result and result.get('success') and tool_name in _SYNC_WRITE_TOOLS:
            try:
                from src.core.sync_bus import bump
                bump(f'fc:{tool_name}')
            except Exception:
                pass
        return result
    except Exception as e:
        logger.error(f"[BuiltinTools] {tool_name} 执行失败: {e}")
        return {'success': False, 'error': str(e)}


def get_tool_definitions(allow_web_search=False):
    """获取工具定义列表（OpenAI Function Calling格式）

    返回OpenAI API的tools参数格式，供外脑调用时传入。

    Args:
        allow_web_search: ★ 方案A 联网授权制（2026-10-05 定版拍板）——仅当用户点🌐联网
            按钮（web_search=True）时传 True，web_search/web_fetch 才会注入。
            默认 False = 联网工具不进 FC 清单，模型无从自主发起联网。

    Returns:
        list: 工具定义列表
    """
    # 参数类型映射
    type_map = {
        'str': 'string',
        'int': 'integer',
        'float': 'number',
        'bool': 'boolean',
        'list': 'array',
        'dict': 'object',
    }

    tools = []
    for name, info in TOOL_REGISTRY.items():
        # ★ A批A5: 别名不注入FC（保旧调用名兼容，但不占token）
        if info.get('alias'):
            continue
        # ★ 方案A 联网授权制: 未授权时联网类工具不注入（模型无从调用）
        if info.get('requires') == 'web_search_enabled' and not allow_web_search:
            continue
        # ★ A批A6: 可选工具按装机动梶过滤（如未装 cli_anything_hub）
        _opt = info.get('optional')
        if _opt and not _optional_available(_opt):
            continue
        params = info.get('params', {})

        properties = {}
        required = []

        for param_name, param_type in params.items():
            clean_type = param_type.replace('str', '').strip() if isinstance(param_type, str) else 'string'
            if clean_type not in type_map:
                clean_type = 'string'

            properties[param_name] = {
                'type': type_map.get(clean_type, 'string'),
                'description': f'{param_name}参数'
            }
            # 简单判断必填参数
            if param_name in ('url', 'save_path', 'command', 'path', 'content',
                            'old_string', 'new_string', 'pattern', 'query',
                            'name', 'model_id', 'question', 'subject',
                            'to', 'body', 'address', 'password', 'item_id',
                            'title', 'remind_at'):
                required.append(param_name)

        tool_def = {
            'type': 'function',
            'function': {
                'name': name,
                'description': info['description'],
                'parameters': {
                    'type': 'object',
                    'properties': properties,
                }
            }
        }

        if required:
            tool_def['function']['parameters']['required'] = required

        tools.append(tool_def)

    # ★ 2026-07-29: 加载FC封装工具（推荐工具的FC封装）
    # 只加载TOOL_REGISTRY里还没有的工具（避免重复）
    try:
        from src.core.fc_wrapper_loader import load_fc_wrappers
        wrapper_tools, wrapper_handlers = load_fc_wrappers()
        existing_names = set(t['function']['name'] for t in tools)  # ★ A批A1: 原三目表达式是永远走else的死代码，直接简化
        for wt in wrapper_tools:
            tool_name = wt.get('name', '')
            if tool_name in existing_names:
                continue  # 跳过已存在的工具名
            existing_names.add(tool_name)
            params = wt.get('params', {})
            properties = {}
            required = []
            for pname, pdesc in params.items():
                properties[pname] = {'type': 'string', 'description': pdesc if isinstance(pdesc, str) else pname}
                required.append(pname)
            tools.append({
                'type': 'function',
                'function': {
                    'name': tool_name,
                    'description': wt.get('description', ''),
                    'parameters': {'type': 'object', 'properties': properties, 'required': required}
                }
            })
        # 注册处理函数到全局（只注册一次）
        if wrapper_handlers:
            import src.core.builtin_tools as _bt
            for tname, thandler in wrapper_handlers.items():
                if tname not in _bt.TOOL_REGISTRY:
                    _bt.TOOL_REGISTRY[tname] = {
                        'description': tname,
                        'params': {},
                        'handler': thandler,
                    }
            logger.info(f"[FC] 已加载 {len(wrapper_tools)} 个FC封装工具")
    except Exception as _e:
        logger.debug(f"[FC] 加载封装工具失败: {_e}")

    return tools


# ═══════════════════════════════════════════════════════════
# ★ A批A3/A4/A6(2026-09-13): 目录式FC + 工具结果瘦身 + 可选工具探测
# ═══════════════════════════════════════════════════════════

# 常驻全量注入的高频工具（其余只进"名+一句话"目录，用时先 tool_schema 拿参数）
CORE_TOOL_NAMES = {
    'list_dir', 'read_file', 'write_file', 'edit_file',
    'execute_command', 'delete_path', 'web_search', 'tool_schema',
    'query_panel',
    'search_images',  # ★ #925（2026-09-27）：图库是核心能力，必须常驻——此前只进目录，
                      #   智谱只能调 tools 参数里带上的工具 → GLM 死循环 tool_schema 5 次
                      #   也发不出正式调用（runtime.log 实锤），「找图片」从未成功过
    'describe_image', # ★ #925 T4（2026-09-29）：看图说话与 search_images 配对使用，同样必须常驻
    'identify_images',        # ★ #1306（2026-10-29）：批量识别提议（AI 只提议）
    'apply_photo_suggestion', # ★ #1306：用户确认后落库（用户拍板才写）
    'add_inbox_note',     # ★ #1217（2026-10-25）：「记一下/提醒我」高频落库，常驻（10-25 实锤
                          #   外脑目录式绕行成功但慢；常驻后直调）
    'query_my_records',   # ★ #1217：跨数据家查证常驻——「记到哪了」必须能一步查对，
                          #   否则外脑只拿 query_panel 查空 → 反向乌龙「没落库」（10-25 实锤）
    'add_panel_record',   # ★ #1217：「今晚练腿30分钟」一句话自动进健身等面板（工具早已存在，
                          #   藏在目录里外脑想不到用 → 用户被迫手动填表单，10-25 实锤）
    # ★ #1497（2026-11-13）：邮箱四件套升常驻——此前藏在目录里，API 级 tools 参数不带
    #   email_* → 外脑/本地模型都发不出正式调用（#925 同款病根：目录工具调不动），
    #   2026-11-12 23:59「帮我看看新邮件」qwen3-vl 只能瞎编「没集成邮箱」实锤。
    #   升常驻后本地 Ollama /api/chat + tools 真调链（#1497-B）与外脑同享。
    'email_setup',
    'email_fetch',
    'email_send',         # ⚠️ 危险操作：工具描述自带「需用户确认」，且哈文侧两级权限守门
    'email_search',
    'set_reminder',       # ★ #1219：「后天提醒我练胸」直接进提醒系统（带域归属），
                          #   不再借道随身记——随身记回归琐事/想法本位（开发者 10-25 拍板）
    'remember_fact',      # ★ #1220-C：长期记忆「儿子过敏药/ WiFi 密码」这类生活常量，
                          #   用户说一次就该一直记得——藏目录里外脑想不到用，常驻
}

# 可选工具探测缓存
_OPTIONAL_CACHE = {}

def _optional_available(feature):
    """A批A6: 探测可选组件是否可用（结果缓存）"""
    if feature in _OPTIONAL_CACHE:
        return _OPTIONAL_CACHE[feature]
    ok = False
    try:
        if feature == 'cli_anything':
            import importlib.util as _iu
            ok = _iu.find_spec('cli_anything_hub') is not None
    except Exception:
        ok = False
    _OPTIONAL_CACHE[feature] = ok
    return ok


def get_tool_catalog_text(allow_web_search=False):
    """A批A3: 生成"名+一句话"目录文本（非核心、非别名、可选过滤后）

    Args:
        allow_web_search: ★ 方案A 联网授权制——未授权时联网类工具不进目录

    Returns:
        str: 多行目录文本（给FC system prompt用，约几十token/行）
    """
    lines = []
    for name, info in TOOL_REGISTRY.items():
        if name in CORE_TOOL_NAMES or info.get('alias'):
            continue
        # ★ 方案A 联网授权制: 未授权时联网类工具不进目录
        if info.get('requires') == 'web_search_enabled' and not allow_web_search:
            continue
        _opt = info.get('optional')
        if _opt and not _optional_available(_opt):
            continue
        lines.append(f"- {name}: {info.get('description', '')}")
    # FC封装工具也进目录
    try:
        from src.core.fc_wrapper_loader import load_fc_wrappers
        wrapper_tools, _ = load_fc_wrappers()
        for wt in wrapper_tools:
            n = wt.get('name', '')
            if n in TOOL_REGISTRY or n in CORE_TOOL_NAMES:
                continue
            lines.append(f"- {n}: {wt.get('description', '')}")
    except Exception:
        pass
    return '\n'.join(lines)


def get_fc_bundle(allow_web_search=False):
    """A批A3: 目录式FC打包 — 返回(常驻全量defs, 目录文本)

    engine.py 用返回值替代全量 get_tool_definitions() 注入。

    Args:
        allow_web_search: ★ 方案A 联网授权制（开发者 2026-10-05 拍板）——
            仅用户点🌐联网按钮时为 True，web_search/web_fetch 才进常驻工具与目录
    """
    all_defs = get_tool_definitions(allow_web_search=allow_web_search)
    core_defs = [d for d in all_defs if d['function']['name'] in CORE_TOOL_NAMES]
    catalog = get_tool_catalog_text(allow_web_search=allow_web_search)
    return core_defs, catalog


def slim_tool_result(content, tool_name='tool', max_chars=1000):
    """A批A4: 工具结果瘦身 — 进历史前截断，全文落盘 data/tool_results/

    Args:
        content: 工具执行结果（str或dict）
        tool_name: 工具名（落盘文件名用）
        max_chars: 截断阈值
    Returns:
        str: 截断后内容（超长时附落盘路径指针）
    """
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    if len(content) <= max_chars:
        return content
    pointer = ''
    try:
        from datetime import datetime as _dt
        d = os.path.join(BASE_DIR, 'data', 'tool_results', _dt.now().strftime('%Y%m%d'))
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, f"{_dt.now().strftime('%H%M%S_%f')}_{tool_name}.json")
        with open(fn, 'w', encoding='utf-8') as f:
            f.write(content)
        pointer = f"（结果过长已截断，全文已存: {fn}，可用 read_file 查看）"
    except Exception:
        pointer = '（结果过长已截断）'
    # 记录到token账本
    try:
        from src.core.token_ledger import record
        record('tool_result_slimmed', tool=tool_name, saved_chars=len(content) - max_chars)
    except Exception:
        pass
    return content[:max_chars] + '\n...' + pointer


def execute_tool_call(tool_name, arguments):
    """执行工具调用（从Function Calling的JSON解析后调用）

    Args:
        tool_name: 工具名
        arguments: 参数字典（可能是JSON字符串或dict）

    Returns:
        str: 工具执行结果（JSON字符串）
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}

    # ★ 2026-10-20 第二批2.1: 权限中心检查 — 四类权限三选统一闸门
    try:
        from src.core.permission_center import check_tool as _perm_check, log_operation as _perm_log
        _decision, _msg = _perm_check(tool_name)
        if _decision == 'block':
            _perm_log(tool_name, 'block', _msg)
            return json.dumps({'success': False, 'error': _msg, 'permission_blocked': True},
                              ensure_ascii=False)
        _perm_log(tool_name, 'allow')
    except ImportError:
        pass  # 权限中心未安装不阻塞主流程
    except Exception as _pe:
        logger.warning(f"[ToolExec] 权限检查异常(放行): {_pe}")

    result = call_tool(tool_name, **arguments)
    return json.dumps(result, ensure_ascii=False)


# 支持Function Calling的外脑列表
FUNCTION_CALLING_SUPPORTED_PROVIDERS = {
    'openai', 'deepseek', 'claude', 'anthropic',
    'zhipu', 'glm', 'chatglm',
    'qwen', 'dashscope', 'tongyi',
    'moonshot', 'kimi',
    'yi', 'lingyi',
    'baichuan',
    'minimax',
    'stepfun', 'minimax',
}


def is_function_calling_supported(provider):
    """检测外脑是否支持Function Calling

    Args:
        provider: 外脑提供商标识

    Returns:
        bool: 是否支持
    """
    if not provider:
        return False
    provider_lower = provider.lower()
    for p in FUNCTION_CALLING_SUPPORTED_PROVIDERS:
        if p in provider_lower:
            return True
    return False
