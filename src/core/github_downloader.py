# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
GitHub 仓库下载器
从对话中识别 GitHub URL → 下载 → 安全扫描 → 导入知识库
"""
import logging
logger = logging.getLogger(__name__)
import os
import io
import re
import json
import hashlib
import zipfile
import tempfile
import shutil
import threading
from pathlib import Path
from datetime import datetime

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 支持导入知识库的文件扩展名
KB_EXTENSIONS = {
    '.md', '.txt', '.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css',
    '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
    '.rst', '.csv', '.tsv', '.xml', '.svg',
    '.pdf', '.docx', '.pptx', '.xlsx',
    '.c', '.cpp', '.h', '.hpp', '.java', '.go', '.rs', '.rb', '.php',
    '.sh', '.bat', '.ps1', '.sql', '.r', '.m', '.swift', '.kt',
}

# 跳过目录
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.vscode', 'dist', 'build', '.next'}

# 危险文件扩展名
DANGEROUS_EXTENSIONS = {
    '.exe', '.dll', '.so', '.dylib', '.bin', '.sys', '.msi', '.apk', '.app', '.jar', '.war',
    # ★ 2026-08-15: 扩展危险扩展名（脚本类）
    '.bat', '.ps1', '.vbs', '.vbe', '.wsf', '.wsh', '.scr', '.com', '.pif',
    '.reg', '.inf', '.lnk', '.url', '.hta', '.cpl', '.msc',
}

# 安全扫描敏感模式
SUSPICIOUS_PATTERNS = [
    rb'eval\s*\(', rb'exec\s*\(', rb'__import__\s*\(', rb'os\.system\s*\(',
    rb'subprocess\.', rb'popen\s*\(', rb'rm\s+-rf', rb'del\s+/[FS]',
    rb'base64\.b64decode', rb'pickle\.loads', rb'marshal\.loads',
    rb'requests\.get.*password', rb'curl.*\.exe', rb'wget.*\.exe',
    # ★ 2026-08-15: 扩展检测模式
    rb'powershell\s+-enc', rb'invoke-expression', rb'iex\s+',
    rb'net\s+user', rb'net\s+localgroup', rb'reg\s+add', rb'reg\s+delete',
    rb'schtasks\s+/create', rb'certutil\s+-decode', rb'bitsadmin\s+/transfer',
    rb'New-Object\s+System\.Net\.WebClient', rb'Invoke-WebRequest', rb'Invoke-RestMethod',
    rb'Start-Process', rb'WScript\.Shell', rb'CreateObject',
]


def detect_github_urls(text: str) -> list[dict]:
    """
    从文本中检测 GitHub URL（只匹配仓库级别，不匹配具体文件路径）
    Returns: [{"url": str, "owner": str, "repo": str, "branch": str, "full_name": str, "raw_url": str}]
    """
    # 在末尾追加空格，确保 URL 在字符串末尾时也能被正确匹配
    text = text + ' '
    patterns = [
        # 完整 URL: https://github.com/owner/repo
        r'https?://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)',
        # 简短形式: github.com/owner/repo
        r'(?<![\w.])github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)',
    ]

    results = []
    seen = set()

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            owner = match.group(1)
            raw_repo = match.group(2)
            # 注意：rstrip 按字符集删除，用 removesuffix 按子串删除
            repo = raw_repo.removesuffix('.git') if raw_repo.endswith('.git') else raw_repo
            full_url = f"https://github.com/{owner}/{repo}"

            # 过滤掉无效的匹配（owner/repo 必须像真的 GitHub 仓库名）
            if len(owner) < 2 or len(repo) < 2:
                continue
            if repo in ('tree', 'blob', 'issues', 'pulls', 'actions', 'settings', 'wiki', 'releases', 'tags'):
                continue

            # 检查 URL 后面是否有 /tree/xxx 或 /blob/xxx 指定分支
            end_pos = match.end()
            remaining = text[end_pos:]
            branch = 'main'
            tree_match = re.match(r'^/tree/([a-zA-Z0-9_.-]+)', remaining)
            blob_match = re.match(r'^/blob/([a-zA-Z0-9_.-]+)', remaining)
            if tree_match:
                branch = tree_match.group(1)
            elif blob_match:
                branch = blob_match.group(1)

            key = f"{owner}/{repo}"
            if key not in seen:
                seen.add(key)
                results.append({
                    'url': full_url,
                    'owner': owner,
                    'repo': repo,
                    'branch': branch,
                    'full_name': key,
                    'raw_url': match.group(0).strip(),
                })

    return results


def download_repo_zip(full_name: str, branch: str = "main") -> tuple[bool, bytes | str]:
    """
    下载 GitHub 仓库 ZIP
    Returns: (success, data_or_error)
    """
    try:
        import urllib.request
        zip_url = f"https://github.com/{full_name}/archive/refs/heads/{branch}.zip"
        req = urllib.request.Request(zip_url, headers={
            "User-Agent": "HarvenAI-GitHubDownloader/1.0",
            "Accept": "application/zip, application/octet-stream",
        })
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
            content_type = resp.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                return False, f"分支 '{branch}' 不存在或仓库为空"
        return True, data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # 尝试 main 分支（可能是 master）
            if branch != "master":
                return download_repo_zip(full_name, "master")
            return False, f"仓库 {full_name} 不存在（404）"
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)


def compute_hash(data: bytes) -> str:
    """SHA-256"""
    return hashlib.sha256(data).hexdigest()


def security_scan_bytes(data: bytes, filename: str) -> tuple[bool, list[str]]:
    """
    二进制安全扫描
    Returns: (is_safe, warnings)
    """
    warnings = []
    ext = Path(filename).suffix.lower()

    # 危险扩展名
    if ext in DANGEROUS_EXTENSIONS:
        warnings.append(f"⚠️ {filename}: 可执行文件（{ext}）已跳过导入")

    # 可疑模式扫描（仅对文本文件）
    if ext in {'.py', '.js', '.ts', '.sh', '.bat', '.ps1', '.rb', '.pl', '.php'}:
        try:
            text = data.decode('utf-8', errors='replace')
            for pattern in SUSPICIOUS_PATTERNS:
                if re.search(pattern, text.encode('utf-8', errors='replace')):
                    warnings.append(f"⚠️ {filename}: 检测到可疑代码模式 `{pattern.decode()}`")
        except Exception as _e: logger.debug(f"[github_downloader] 忽略: {_e}")

    return len(warnings) == 0 or all('可执行文件' in w for w in warnings), warnings


def import_files_to_knowledgebase(extract_dir: str, repo_name: str) -> dict:
    """
    扫描解压目录，将支持的文件导入知识库
    Returns: {"imported": int, "skipped": int, "warnings": list, "files": list}
    """
    from src.core.knowledge_base import get_knowledge_base

    kb = get_knowledge_base()
    imported = 0
    skipped = 0
    all_warnings = []
    imported_files = []
    max_file_size = 5 * 1024 * 1024  # 5MB 上限

    for root, dirs, files in os.walk(extract_dir):
        # 跳过不必要目录
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]

        for fname in files:
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, extract_dir)

            # 检查文件大小
            try:
                size = os.path.getsize(fpath)
                if size > max_file_size:
                    skipped += 1
                    continue
            except OSError:
                continue

            ext = Path(fname).suffix.lower()

            # 安全扫描
            try:
                with open(fpath, 'rb') as f:
                    data = f.read()
                is_safe, warnings = security_scan_bytes(data, rel_path)
                all_warnings.extend(warnings)
            except Exception:
                skipped += 1
                continue

            # 检查是否支持导入
            if ext not in KB_EXTENSIONS:
                skipped += 1
                continue

            # 导入知识库
            try:
                source_tag = f"github:{repo_name}/{rel_path}"
                # 读取文本内容
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()

                if len(content.strip()) < 50:
                    skipped += 1
                    continue

                # 添加文件头标注
                full_content = f"# {rel_path}\n# 来源: https://github.com/{repo_name}\n\n{content}"

                kwargs = {}
                import importlib
                sig = importlib.import_module('inspect')
                try:
                    params = sig.signature(kb.add_document).parameters
                    if 'source' not in params and 'metadata' in params:
                        kwargs['metadata'] = {'source': source_tag}
                    elif 'source' in params:
                        kwargs['source'] = source_tag
                except Exception as _e: logger.debug(f"[github_downloader] 忽略: {_e}")

                if 'source' in kwargs:
                    doc_id = kb.add_document(full_content, source=source_tag)
                else:
                    doc_id = kb.add_document(full_content)

                if doc_id:
                    imported += 1
                    imported_files.append({'file': rel_path, 'size': len(full_content)})
                else:
                    skipped += 1
            except Exception as e:
                skipped += 1
                all_warnings.append(f"⚠️ {rel_path}: 导入失败 - {e}")

    return {
        'imported': imported,
        'skipped': skipped,
        'warnings': all_warnings,
        'files': imported_files,
    }


def download_and_import_github(repo_url: str, branch: str = "main") -> dict:
    """
    完整流程：检测 → 下载 → 扫描 → 导入知识库
    Returns: {"success": bool, "repo": str, "branch": str, "hash": str,
              "imported": int, "skipped": int, "warnings": list, "message": str}
    """
    # 1. 解析 URL
    urls = detect_github_urls(repo_url)
    if not urls:
        return {"success": False, "message": "未检测到有效的 GitHub 仓库 URL"}

    info = urls[0]
    full_name = info['full_name']
    branch_name = info.get('branch', branch)

    # 2. 下载
    success, data = download_repo_zip(full_name, branch_name)
    if not success:
        return {"success": False, "repo": full_name, "message": f"下载失败: {data}"}

    file_hash = compute_hash(data)

    # 3. 解压到临时目录
    tmp_dir = tempfile.mkdtemp(prefix='harven_github_')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(tmp_dir)

        # 找到解压后根目录（GitHub ZIP 外层有 repo-branch 目录）
        items = os.listdir(tmp_dir)
        if len(items) == 1 and os.path.isdir(os.path.join(tmp_dir, items[0])):
            extract_root = os.path.join(tmp_dir, items[0])
        else:
            extract_root = tmp_dir

        # 4. 导入知识库
        result = import_files_to_knowledgebase(extract_root, full_name)

        return {
            "success": True,
            "repo": full_name,
            "branch": branch_name,
            "hash": file_hash,
            "imported": result['imported'],
            "skipped": result['skipped'],
            "warnings": result['warnings'],
            "files": result['files'],
            "message": f"✅ 已下载 {full_name}，安全扫描通过，{result['imported']} 个文件已导入知识库",
        }
    except zipfile.BadZipFile:
        return {"success": False, "repo": full_name, "message": "下载的文件不是有效 ZIP"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "repo": full_name, "message": f"处理失败: {e}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ===== 后台下载队列 =====

_download_queue = []
_download_lock = threading.Lock()
_download_results = {}


def queue_github_download(repo_url: str, conversation_id: str = None) -> str:
    """将 GitHub 下载任务加入后台队列"""
    task_id = f"github_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(_download_queue)}"

    with _download_lock:
        _download_queue.append({
            'task_id': task_id,
            'repo_url': repo_url,
            'conversation_id': conversation_id,
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
        })

    def _run():
        with _download_lock:
            for item in _download_queue:
                if item['task_id'] == task_id:
                    item['status'] = 'running'
                    break

        result = download_and_import_github(repo_url)

        with _download_lock:
            _download_results[task_id] = result
            for item in _download_queue:
                if item['task_id'] == task_id:
                    item['status'] = 'completed' if result['success'] else 'failed'
                    break

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return task_id


def get_download_status(task_id: str) -> dict:
    """查询下载任务状态"""
    with _download_lock:
        for item in _download_queue:
            if item['task_id'] == task_id:
                return item
        if task_id in _download_results:
            return {'task_id': task_id, 'status': 'completed', 'result': _download_results[task_id]}
    return {'task_id': task_id, 'status': 'not_found'}
