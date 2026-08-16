# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
插件安装器：GitHub 搜索、下载、安全扫描、哈希校验
"""
import os
import io
import sys
import json
import hashlib
import zipfile
import tempfile
import re
import ast
from pathlib import Path
import logging
logger = logging.getLogger(__name__)

# 危险操作关键字（静态扫描用）
DANGEROUS_IMPORTS = [
    "os.system", "subprocess", "popen", "eval", "exec", "compile",
    "pty", "webbrowser", "smtplib", "ftplib", "telnetlib",
    "__import__", "importlib.import_module",
    "open",  # 写文件需谨慎
]

DANGEROUS_PATTERNS = [
    r"\.system\s*\(",
    r"subprocess\.",
    r"eval\s*\(",
    r"exec\s*\(",
    r"popen\s*\(",
    r"__import__\s*\(",
    r"\.write\s*\(",
    r"\.remove\s*\(",
    r"os\.remove",
    r"os\.unlink",
    r"os\.rmdir",
    r"os\.system",
    r"shutil\.rmtree",
]


def compute_hash(data: bytes) -> str:
    """计算 SHA-256 哈希"""
    return hashlib.sha256(data).hexdigest()


def static_scan_plugin(plugin_code: str, filename: str) -> dict:
    """
    静态扫描插件代码，检测危险操作
    Returns:
        { "safe": bool, "warnings": [str], "errors": [str] }
    """
    warnings = []
    errors = []

    # 1. 正则表达式扫描
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, plugin_code, re.IGNORECASE):
            warnings.append(f"⚠️ 检测到可疑模式：`{pattern}` in {filename}")

    # 2. AST 语法树扫描（更精确）
    try:
        tree = ast.parse(plugin_code)
        for node in ast.walk(tree):
            # 检查 import 语句
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ["os", "subprocess", "sys", "pty", "shutil"]:
                        warnings.append(f"⚠️ 导入了潜在危险模块：`{alias.name}` in {filename}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module in ["os", "subprocess", "sys", "shutil"]:
                    warnings.append(f"⚠️ 从危险模块导入：`{node.module}` in {filename}")
            # 检查函数调用
            elif isinstance(node, (ast.Call,)):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ["eval", "exec", "compile", "__import__"]:
                        errors.append(f"🚨 检测到危险函数调用：`{node.func.id}()` in {filename}")
                elif isinstance(node.func, ast.Attribute):
                    attr = node.func.attr
                    if attr in ["system", "popen", "write", "remove", "rmtree"]:
                        warnings.append(f"⚠️ 检测到危险方法调用：`.{attr}()` in {filename}")
    except SyntaxError as e:
        errors.append(f"❌ 语法错误 in {filename}: {e}")

    return {
        "safe": len(errors) == 0,
        "warnings": warnings,
        "errors": errors,
    }


def scan_plugin_package(plugin_dir: str) -> dict:
    """
    扫描整个插件包（目录）
    Returns: 汇总扫描结果
    """
    all_warnings = []
    all_errors = []
    scanned_files = []

    for root, dirs, files in os.walk(plugin_dir):
        for fname in files:
            if fname.endswith(".py"):
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, plugin_dir)
                scanned_files.append(relpath)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        code = f.read()
                    result = static_scan_plugin(code, relpath)
                    all_warnings.extend(result["warnings"])
                    all_errors.extend(result["errors"])
                except Exception as e:
                    all_warnings.append(f"无法扫描 {relpath}: {e}")

    return {
        "safe": len(all_errors) == 0,
        "warnings": all_warnings,
        "errors": all_errors,
        "scanned_files": scanned_files,
    }


def search_github_plugins(query: str, limit: int = 10) -> list:
    """
    搜索 GitHub 上的 Harven AI 插件
    通过 GitHub API 搜索，无需 token（有速率限制）
    Returns: [ { "name", "full_name", "description", "html_url", "stargazers_count", "updated_at" } ]
    """
    try:
        import urllib.request
        import urllib.parse
        q = urllib.parse.quote(f"{query} harven-ai-plugin OR lingzhi-plugin")
        url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "HarvenAI-PluginInstaller"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data.get("items", []):
                results.append({
                    "name": item["name"],
                    "full_name": item["full_name"],
                    "description": item.get("description", ""),
                    "html_url": item["html_url"],
                    "stargazers_count": item["stargazers_count"],
                    "updated_at": item["updated_at"],
                    "clone_url": item["clone_url"],
                    "default_branch": item.get("default_branch", "main"),
                })
            return results
    except Exception as e:
        logger.error(f"[PluginInstaller] GitHub 搜索失败: {e}")
        return []


def download_github_plugin(repo_full_name: str, branch: str = "main") -> tuple:
    """
    从 GitHub 下载插件 ZIP 包
    Returns: (success: bool, data: bytes or error_msg: str)
    """
    try:
        import urllib.request
        zip_url = f"https://github.com/{repo_full_name}/archive/refs/heads/{branch}.zip"
        req = urllib.request.Request(zip_url, headers={"User-Agent": "HarvenAI-PluginInstaller"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        return True, data
    except Exception as e:
        return False, str(e)


def extract_and_validate_plugin(zip_data: bytes, plugin_dir: str) -> tuple:
    """
    解压插件 ZIP 包并验证结构
    Returns: (success: bool, plugin_name: str or error: str)
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # 查找主插件文件（*_plugin.py）
            plugin_files = [f for f in zf.namelist() if f.endswith("_plugin.py") and not f.startswith("__MACOSX")]

            if not plugin_files:
                return False, "ZIP 中未找到 *_plugin.py 文件，不是有效的 Harven AI 插件"

            # 解压到临时目录
            with tempfile.TemporaryDirectory() as tmpdir:
                zf.extractall(tmpdir)

                # 找到解压后的根目录
                items = os.listdir(tmpdir)
                if len(items) == 1 and os.path.isdir(os.path.join(tmpdir, items[0])):
                    extract_dir = os.path.join(tmpdir, items[0])
                else:
                    extract_dir = tmpdir

                # 验证插件结构
                plugin_file = None
                for f in plugin_files:
                    # f 是 ZIP 内相对路径
                    full_path = os.path.join(tmpdir, f)
                    if os.path.exists(full_path):
                        plugin_file = full_path
                        break

                if not plugin_file:
                    return False, "解压后未找到插件文件"

                # 读取插件代码做静态扫描
                scan_dir = extract_dir
                scan_result = scan_plugin_package(scan_dir)

                # 复制到插件目录
                plugin_name = os.path.basename(plugin_file).replace("_plugin.py", "")
                dest_dir = os.path.join(plugin_dir, plugin_name)
                os.makedirs(dest_dir, exist_ok=True)

                # 复制所有文件
                for root, dirs, files in os.walk(extract_dir):
                    for fname in files:
                        src = os.path.join(root, fname)
                        rel = os.path.relpath(src, extract_dir)
                        dst = os.path.join(dest_dir, rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with open(src, "rb") as sf:
                            with open(dst, "wb") as df:
                                df.write(sf.read())

                # 计算插件文件的哈希
                hashes = {}
                for root, dirs, files in os.walk(dest_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        with open(fpath, "rb") as f:
                            hashes[os.path.relpath(fpath, dest_dir)] = compute_hash(f.read())

                return True, {
                    "plugin_name": plugin_name,
                    "plugin_dir": dest_dir,
                    "scan_result": scan_result,
                    "hashes": hashes,
                    "plugin_file": os.path.basename(plugin_file),
                }
    except zipfile.BadZipFile:
        return False, "无效的 ZIP 文件"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, str(e)


def install_plugin_from_github(repo_url: str, plugin_dir: str) -> dict:
    """
    完整安装流程：从 GitHub URL 下载并安装插件
    Args:
        repo_url: GitHub 仓库 URL（如 https://github.com/user/repo）
        plugin_dir: 本地插件目录
    Returns:
        { "success": bool, "message": str, "scan_result": dict, "hashes": dict }
    """
    # 解析 repo full name
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)", repo_url)
    if not match:
        return {"success": False, "message": "无效的 GitHub URL"}
    owner, repo = match.group(1), match.group(2).rstrip(".git")
    full_name = f"{owner}/{repo}"

    # 下载
    success, data = download_github_plugin(full_name)
    if not success:
        return {"success": False, "message": f"下载失败: {data}"}

    file_hash = compute_hash(data)

    # 解压 + 验证 + 扫描
    success, result = extract_and_validate_plugin(data, plugin_dir)
    if not success:
        return {"success": False, "message": f"安装失败: {result}"}

    result["file_hash"] = file_hash
    result["repo_url"] = f"https://github.com/{full_name}"
    return {"success": True, "message": "安装成功", **result}


def list_available_plugins(plugin_dir: str) -> list:
    """列出已安装的插件"""
    if not os.path.exists(plugin_dir):
        return []
    plugins = []
    for d in os.listdir(plugin_dir):
        dpath = os.path.join(plugin_dir, d)
        if os.path.isdir(dpath):
            # 查找插件文件
            for f in os.listdir(dpath):
                if f.endswith("_plugin.py"):
                    plugins.append({
                        "name": d,
                        "path": os.path.join(dpath, f),
                        "dir": dpath,
                    })
                    break
    return plugins
