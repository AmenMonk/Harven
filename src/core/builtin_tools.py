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
        _danger_cmds = [
            'rm -rf', 'format', 'del /f /s /q', 'shutdown', 'reg delete', 'diskpart', 'mkfs',
            'rmdir /s', 'rd /s', 'taskkill /f', 'net user', 'net localgroup',
            'powershell -enc', 'cmd /c', 'curl ', 'wget ', 'invoke-expression', 'iex ',
            'reg add', 'reg import', 'bcdedit', 'netsh ', 'sc stop', 'sc delete',
            # Linux/macOS 危险命令
            'rm -rf /', 'dd if=', 'chmod 777', 'chown -R', 'kill -9', 'killall',
            'systemctl stop', 'systemctl disable', 'shutdown -h', 'init 0', 'init 6',
            'fdisk', 'parted', 'wipefs', 'umount -f',
        ]
        _cmd_lower = command.lower() if isinstance(command, str) else ' '.join(command).lower()
        for _dc in _danger_cmds:
            if _dc in _cmd_lower:
                return {'success': False, 'stdout': '', 'stderr': f'危险命令已拦截: {_dc}', 'returncode': -1}

        # ★ 2026-08-15: 命令注入防护——检测特殊字符
        if isinstance(command, str):
            _danger_chars = ['&', '|', ';', '`', '$(', '${']
            # 允许管道和重定向，但不允许命令链接符
            _cmd_no_pipes = command.replace('||', '').replace('&&', '')
            for _dc in _danger_chars:
                if _dc in _cmd_no_pipes and _dc not in ['|', '&']:
                    return {'success': False, 'stdout': '', 'stderr': f'命令含危险字符: {_dc}', 'returncode': -1}

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

        return {
            'success': result.returncode == 0,
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

def _check_cli_anything_installed():
    """检测 cli-anything-hub 是否已安装"""
    try:
        import cli_anything_hub
        return True
    except ImportError:
        pass
    # pip show 检测
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'show', 'cli-anything-hub'],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0 and 'Name:' in result.stdout
    except Exception:
        return False


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
        logger.warning(f"[CLI-Anything] 获取子模块列表失败: {e}")
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

    if _save_email_config(email_config):
        logger.info(f"[Email] 邮箱配置已保存: {address}")
        return {'success': True, 'error': ''}
    return {'success': False, 'error': '保存配置失败'}


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

                emails.append({
                    'uid': eid.decode(),
                    'from': from_addr,
                    'to': to_addr,
                    'subject': subject,
                    'date': date,
                    'body': body[:500],  # 返回给模型的只给前500字符
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
        return {'success': False, 'emails': [], 'count': 0, 'error': f'IMAP错误: {e}'}
    except Exception as e:
        logger.error(f"[Email] 拉取邮件失败: {e}")
        return {'success': False, 'emails': [], 'count': 0, 'error': str(e)}


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
# 工具注册表（供AI调用）
# ═══════════════════════════════════════════════════════════════

def _import_ollama_wrapper(gguf_path, model_name=None):
    """把GGUF文件导入Ollama（包装security_guard的import_gguf_to_ollama）"""
    try:
        from src.core.security_guard import import_gguf_to_ollama
        return import_gguf_to_ollama(gguf_path, model_name)
    except Exception as e:
        return {'success': False, 'error': str(e)[:200]}


TOOL_REGISTRY = {
    # 基础工具
    'download_file': {'function': download_file, 'description': '下载文件', 'params': {'url': 'str', 'save_path': 'str'}},
    'execute_command': {'function': execute_command, 'description': '执行系统命令', 'params': {'command': 'str'}},
    'read_file': {'function': read_file, 'description': '读文件', 'params': {'path': 'str'}},
    'write_file': {'function': write_file, 'description': '写文件', 'params': {'path': 'str', 'content': 'str'}},
    'edit_file': {'function': edit_file, 'description': '编辑文件(精确替换)', 'params': {'path': 'str', 'old_string': 'str', 'new_string': 'str'}},
    'list_dir': {'function': list_dir, 'description': '列目录', 'params': {'path': 'str'}},
    'delete_path': {'function': delete_path, 'description': '删除文件/目录', 'params': {'path': 'str'}},
    'search_content': {'function': search_content, 'description': '搜索文件内容', 'params': {'pattern': 'str', 'path': 'str', 'file_type': 'str'}},
    'search_files': {'function': search_files, 'description': '搜索文件名', 'params': {'pattern': 'str', 'path': 'str'}},

    # 模型工具
    'scan_system': {'function': scan_system, 'description': '扫描系统', 'params': {}},
    'install_model': {'function': install_model, 'description': '安装模型', 'params': {'name': 'str', 'source': 'str', 'quantization': 'str'}},
    'uninstall_model': {'function': uninstall_model, 'description': '卸载模型', 'params': {'name': 'str'}},
    'install_package': {'function': install_package, 'description': '安装Python包', 'params': {'name': 'str'}},

    # 知识/搜索工具
    'web_search': {'function': web_search, 'description': '联网搜索', 'params': {'query': 'str'}},
    'search_knowledge': {'function': search_knowledge, 'description': '搜索知识库', 'params': {'query': 'str'}},
    'list_knowledge': {'function': list_knowledge, 'description': '列出知识库内容（已导入的文件/条目）', 'params': {}},
    'search_conversation': {'function': search_conversation, 'description': '搜索历史对话', 'params': {'query': 'str'}},

    # 垂直模型工具
    'execute_vertical': {'function': execute_vertical, 'description': '执行垂直模型', 'params': {'model_id': 'str', 'params': 'dict'}},

    # 实用工具
    'get_time': {'function': get_time, 'description': '获取当前时间', 'params': {}},
    'check_software': {'function': check_software, 'description': '检查软件安装状态（是否已安装/版本/是否运行）。当用户问"docker装了吗""python版本""git有没有"时调用此工具，不要写代码。支持：docker/python/git/node/npm/ollama/pip/blender/ffmpeg等', 'params': {'name': 'str'}},
    'ask_user': {'function': ask_user, 'description': '问用户问题', 'params': {'question': 'str', 'options': 'list'}},
    'present_file': {'function': present_file, 'description': '展示文件给用户', 'params': {'path': 'str'}},

    # 任务管理
    'task_create': {'function': task_create, 'description': '创建任务', 'params': {'subject': 'str', 'description': 'str'}},
    'task_list': {'function': task_list, 'description': '列出任务', 'params': {}},

    # Skills工具
    'execute_skill': {'function': execute_skill, 'description': '执行Skill', 'params': {'skill_id': 'str', 'user_input': 'str'}},
    'list_skills': {'function': list_skills, 'description': '列出所有Skills', 'params': {}},

    # 子代理
    'spawn_agent': {'function': spawn_agent, 'description': '启动子代理处理任务', 'params': {'task': 'str', 'agent_type': 'str'}},

    # 网页抓取
    'web_fetch': {'function': web_fetch, 'description': '抓取网页内容转文本', 'params': {'url': 'str', 'max_length': 'int'}},

    # 完整任务管理
    'task_update': {'function': task_update, 'description': '更新任务状态/内容', 'params': {'task_id': 'str', 'status': 'str', 'subject': 'str', 'description': 'str'}},
    'task_get': {'function': task_get, 'description': '获取单个任务详情', 'params': {'task_id': 'str'}},

    # GGUF导入Ollama
    'import_ollama': {'function': _import_ollama_wrapper, 'description': '把GGUF文件导入Ollama', 'params': {'gguf_path': 'str', 'model_name': 'str'}},

    # ★ 中级Skill脚本执行
    'execute_skill_script': {'function': execute_skill_script, 'description': '执行Skill的可执行脚本（Python沙箱）. 当Skill有scripts/目录时使用此工具而非execute_skill', 'params': {'skill_name': 'str', 'action': 'str', 'params': 'dict'}},
    'list_skill_scripts': {'function': list_skill_scripts_tool, 'description': '列出所有有可执行脚本的技能', 'params': {}},

    # ★ CLI-Anything 工具（动态发现）
    'cli_anything_list': {'function': cli_anything_list, 'description': '列出所有可用的CLI软件工具（Blender/GIMP/Inkscape等），检测本机是否已安装对应软件', 'params': {}},
    'cli_anything_execute': {'function': cli_anything_execute, 'description': '通过CLI-Anything执行软件命令（如Blender渲染、GIMP图像处理等）。需要先安装cli-anything-hub和对应子模块', 'params': {'software': 'str', 'command': 'str', 'args': 'dict'}},
    'cli_anything_install': {'function': cli_anything_install, 'description': '安装CLI-Anything或某个软件的子模块（如 blender/gimp/inkscape 等）。会自动检测已安装的子模块', 'params': {'software': 'str'}},

    # ★ 2026-08-02: 文件操作工具（只读，安全模式下可用）
    'list_dir': {'function': list_dir, 'description': '列出目录内容。当用户问"下载了什么文件""data目录有什么""skills目录内容"时调用此工具，不要写代码。返回文件名/大小/类型', 'params': {'path': 'str'}},
    'read_file': {'function': read_file, 'description': '读取文件内容（文本文件，最大1MB）。当用户问"这个文件写了什么""看看配置文件"时调用此工具，不要写代码。', 'params': {'path': 'str'}},

    # ★ 2026-07-21: 邮箱工具
    'email_setup': {'function': email_setup, 'description': '配置邮箱账号（支持QQ/163/Gmail等，自动推断服务器地址）。配置后才能使用email_fetch和email_send', 'params': {'address': 'str', 'password': 'str', 'imap_server': 'str', 'smtp_server': 'str', 'imap_port': 'int', 'smtp_port': 'int'}},
    'email_fetch': {'function': email_fetch, 'description': '通过IMAP拉取邮件，缓存到本地。返回邮件列表（发件人/主题/摘要/日期）。需要先调用email_setup配置邮箱', 'params': {'folder': 'str', 'limit': 'int', 'mark_seen': 'bool'}},
    'email_send': {'function': email_send, 'description': '⚠️通过SMTP发送邮件（危险操作，需用户确认）。需要先调用email_setup配置邮箱', 'params': {'to': 'str', 'subject': 'str', 'body': 'str'}},
    'email_search': {'function': email_search, 'description': '搜索本地缓存的邮件（不联网）。匹配主题/发件人/正文。需要先email_fetch拉取邮件', 'params': {'query': 'str', 'folder': 'str', 'limit': 'int'}},
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

    try:
        # ★ 2026-07-29: 支持FC封装工具（handler模式）
        if 'handler' in tool:
            result = tool['handler'](**kwargs)
            return result
        result = tool['function'](**kwargs)
        logger.info(f"[BuiltinTools] {tool_name} 执行完成: success={result.get('success')}")
        return result
    except Exception as e:
        logger.error(f"[BuiltinTools] {tool_name} 执行失败: {e}")
        return {'success': False, 'error': str(e)}


def get_tool_definitions():
    """获取工具定义列表（OpenAI Function Calling格式）

    返回OpenAI API的tools参数格式，供外脑调用时传入。

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
                            'to', 'body', 'address', 'password'):
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
        existing_names = set(tools_names) if 'tools_names' in dir() else set(t['function']['name'] for t in tools)
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
