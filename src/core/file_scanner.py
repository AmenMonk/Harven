# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
文件安全扫描模块 — 上传文件安全检测

安全措施：
1. 文件类型验证（Magic Bytes 检测，防止伪装）
2. 可疑模式扫描（脚本注入、Webshell 特征）
3. ClamAV 集成（可选，系统级病毒扫描）
4. 文件大小限制
5. 危险扩展名屏蔽
"""

import logging
logger = logging.getLogger(__name__)
import os
import re
import subprocess
from pathlib import Path
from typing import Tuple, Optional, List

# 禁止上传的危险扩展名
_BLOCKED_EXTENSIONS = {
    ".exe", ".dll", ".sys", ".bat", ".cmd", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".scr", ".pif",
    ".reg", ".hta", ".cpl", ".msc", ".jar", ".app",
    ".sh", ".bash", ".zsh", ".csh", ".run", ".bin",
}

# 可疑内容特征（脚本注入/Webshell 常见模式）
_SUSPICIOUS_PATTERNS = [
    (r"<\?php\s+system\s*\(.+\)", "PHP Webshell"),
    (r"eval\s*\(\s*(?:base64_decode|gzinflate|str_rot13)\s*\(", "PHP 混淆代码"),
    (r"Runtime\.getRuntime\(\)\.exec\(", "Java 命令执行"),
    (r"Process\.Start\s*\(.+cmd", ".NET 命令执行"),
    (r"os\.popen|subprocess\.Popen|shell_exec|passthru", "命令注入"),
    (r'cmd\.exe\s*/c|powershell\.exe\s+-[^ ]*c', "Windows 命令执行"),
    (r"eval\s*\(\s*unescape\s*\(", "JavaScript 混淆执行"),
    (r"document\.write\s*\(\s*unescape\s*\(", "JavaScript XSS"),
    (r"<script[^>]*>.*?document\.cookie.*?</script>", "Cookie 窃取脚本"),
]

# 文件大小限制（100MB）
MAX_FILE_SIZE = 100 * 1024 * 1024

# Magic Bytes 签名（前 4 字节）
# 注：RIFF 容器（.wav/.webp/.avi）需在 check_magic_bytes 中二次判断
_MAGIC_SIGNATURES = {
    b"\x89PNG": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF8": ".gif",
    b"RIFF": None,  # 容器格式 — 需进一步检测子类型（WAVE=wav / WEBP=webp）
    b"%PDF": ".pdf",
    b"PK\x03\x04": ".zip",
    b"\xd0\xcf\x11\xe0": ".doc",
    b"<?xml": ".xml",
    b"<!DO": ".html",
    b"{\"": ".json",
    b"{\n": ".json",
    b"MZ": ".exe",
    b"\x7fELF": ".elf",
    b"\x1a\x45\xdf\xa3": ".webm",  # EBML 头部（WebM/Matroska）
    b"OggS": ".ogg",              # OGG 容器
    b"fLaC": ".flac",             # FLAC 无损音频
    b"ID3": ".mp3",               # MP3 (ID3v2 tag)
    b"\xff\xfb": ".mp3",          # MP3 (MPEG frame sync)
    b"\xff\xf3": ".mp3",          # MP3 (MPEG v2.5)
}

_CLAMAV_AVAILABLE = None


def _check_clamav() -> bool:
    """检测 ClamAV 是否可用"""
    global _CLAMAV_AVAILABLE
    if _CLAMAV_AVAILABLE is not None:
        return _CLAMAV_AVAILABLE
    try:
        result = subprocess.run(
            ["clamscan", "--version"],
            capture_output=True, text=True, timeout=5
        )
        _CLAMAV_AVAILABLE = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _CLAMAV_AVAILABLE = False
    return _CLAMAV_AVAILABLE


def check_extension(filename: str) -> Tuple[bool, str]:
    """
    检查文件扩展名是否在禁止列表中。

    Returns:
        (is_safe, reason)
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in _BLOCKED_EXTENSIONS:
        return False, f"禁止上传的文件类型: {ext}"
    return True, ""


def check_file_size(filepath: str) -> Tuple[bool, str]:
    """检查文件大小"""
    try:
        size = os.path.getsize(filepath)
        if size > MAX_FILE_SIZE:
            return False, f"文件过大: {size / (1024*1024):.1f}MB (限制 {MAX_FILE_SIZE / (1024*1024):.0f}MB)"
        if size == 0:
            return False, "文件为空"
        return True, ""
    except OSError as e:
        return False, f"无法读取文件: {e}"


def check_magic_bytes(filepath: str, expected_ext: str = None) -> Tuple[bool, str]:
    """
    Magic Bytes 校验 — 防止扩展名伪装攻击。

    例如：virus.exe 改名为 report.pdf 上传，Magic Bytes 检测会暴露。

    Args:
        filepath: 文件路径
        expected_ext: 期望的扩展名（如 .pdf）

    Returns:
        (is_safe, reason)
    """
    # ★ 2026-07-05 G1: 文本类文件无标准Magic Bytes签名，跳过检查直接通过
    TEXT_EXTENSIONS = {'.txt', '.md', '.csv', '.log', '.ini', '.conf', '.yaml', '.yml', '.py', '.js', '.json', '.xml', '.html', '.htm', '.sql', '.java', '.c', '.cpp', '.h', '.css', '.sh', '.bat', '.ts', '.go', '.rs', '.rb', '.php'}
    if expected_ext and expected_ext in TEXT_EXTENSIONS:
        return True, f"文本文件 {expected_ext}（跳过Magic Bytes检查）"
    try:
        with open(filepath, "rb") as f:
            header = f.read(12)  # 需要 12 字节以检测 RIFF 子类型
    except OSError as e:
        return False, f"无法读取文件头: {e}"

    detected_type = None
    for magic, ext in _MAGIC_SIGNATURES.items():
        if header.startswith(magic):
            detected_type = ext
            # RIFF 容器需二次判断子类型（WAVE=wav, WEBP=webp, AVI=avi）
            if detected_type is None and magic == b"RIFF" and len(header) >= 12:
                riff_type = header[8:12]
                if riff_type == b"WAVE":
                    detected_type = ".wav"
                elif riff_type == b"WEBP":
                    detected_type = ".webp"
                elif riff_type == b"AVI ":
                    detected_type = ".avi"
                else:
                    detected_type = f".riff_{riff_type.decode('ascii','replace')}"
            break

    if detected_type is None:
        # 无法识别 Magic Bytes，标记为可疑
        return False, f"无法识别的文件类型（Magic Bytes: {header[:4].hex()}）"

    if detected_type == ".exe" or detected_type == ".elf":
        return False, f"检测到可执行文件（Magic Bytes 匹配 {detected_type}）"

    if expected_ext and detected_type != expected_ext:
        # ★ 2026-07-05: Office文件（docx/pptx/xlsx）本质是ZIP容器，PK开头
        # detected_type会是.zip，expected_ext是.docx/.pptx/.xlsx，这是正常的不是伪装
        OFFICE_ZIP_EXTENSIONS = {'.docx', '.pptx', '.xlsx', '.xlsm', '.odt', '.odp', '.ods', '.epub', '.apk', '.jar', '.war'}
        if detected_type == '.zip' and expected_ext in OFFICE_ZIP_EXTENSIONS:
            return True, f"Office文件 {expected_ext}（ZIP容器格式）"
        return False, f"文件扩展名不匹配: 声称为 {expected_ext}，实际为 {detected_type}（Magic Bytes）"

    return True, f"文件类型: {detected_type}"


def scan_content_patterns(filepath: str) -> Tuple[bool, List[str]]:
    """
    扫描文件内容中的可疑模式。

    Returns:
        (is_clean, warnings)
    """
    # 仅扫描文本文件（<10MB 以避免过大文件）
    try:
        size = os.path.getsize(filepath)
        if size > 10 * 1024 * 1024:
            return True, []  # 大文件跳过文本扫描
    except OSError:
        return True, []

    # 检测是否为文本文件
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(4096)
        # 简单二进制检测：包含大量 null 字节
        null_count = chunk.count(b"\x00")
        if null_count > len(chunk) * 0.1:
            return True, []  # 二进制文件跳过
    except OSError:
        return True, []

    # 扫描可疑模式
    warnings = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(50000)  # 限制扫描范围

        for pattern, desc in _SUSPICIOUS_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE | re.DOTALL):
                warnings.append(f"可疑: {desc}")
    except Exception as _e: logger.debug(f"[file_scanner] 忽略: {_e}")

    return len(warnings) == 0, warnings


def clamav_scan(filepath: str) -> Tuple[bool, str]:
    """
    ClamAV 病毒扫描（如果可用）。

    Returns:
        (is_clean, result_message)
    """
    if not _check_clamav():
        return True, "ClamAV 未安装，跳过系统级病毒扫描"

    try:
        result = subprocess.run(
            ["clamscan", "--no-summary", "--quiet", filepath],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return True, "ClamAV: 未检测到威胁"
        elif result.returncode == 1:
            threat_info = result.stdout.strip()
            return False, f"ClamAV 检测到威胁: {threat_info}"
        else:
            return True, f"ClamAV 扫描出错 (code {result.returncode})"
    except subprocess.TimeoutExpired:
        return True, "ClamAV 扫描超时"
    except Exception as e:
        return True, f"ClamAV 扫描异常: {e}"


def full_scan(filepath: str, filename: str = None) -> dict:
    """
    完整安全扫描流程。

    Args:
        filepath: 文件完整路径
        filename: 原始文件名（用于扩展名检查）

    Returns:
        {
            "safe": bool,
            "warnings": [str],
            "details": {stage: result}
        }
    """
    if filename is None:
        filename = os.path.basename(filepath)

    results = {"safe": True, "warnings": [], "details": {}}

    # 1. 扩展名检查
    ext_safe, ext_msg = check_extension(filename)
    results["details"]["extension"] = ext_msg
    if not ext_safe:
        results["safe"] = False
        results["warnings"].append(f"扩展名: {ext_msg}")

    # 2. 文件大小检查
    size_safe, size_msg = check_file_size(filepath)
    results["details"]["size"] = size_msg
    if not size_safe:
        results["safe"] = False
        results["warnings"].append(f"大小: {size_msg}")
        return results  # 空/过大文件无需继续

    # 3. Magic Bytes 检查
    expected_ext = os.path.splitext(filename)[1].lower()
    magic_safe, magic_msg = check_magic_bytes(filepath, expected_ext if expected_ext else None)
    results["details"]["magic_bytes"] = magic_msg
    if not magic_safe:
        results["safe"] = False
        results["warnings"].append(f"文件类型: {magic_msg}")

    # 4. 内容模式扫描
    content_safe, content_warnings = scan_content_patterns(filepath)
    results["details"]["patterns"] = f"检测到 {len(content_warnings)} 项可疑" if content_warnings else "未发现可疑模式"
    if not content_safe:
        results["safe"] = False
        results["warnings"].extend(content_warnings)

    # 5. ClamAV 扫描（系统级）
    clam_safe, clam_msg = clamav_scan(filepath)
    results["details"]["clamav"] = clam_msg
    if not clam_safe:
        results["safe"] = False
        results["warnings"].append(clam_msg)

    return results


def quick_check(filepath: str, filename: str = None) -> bool:
    """
    快速安全检查（上传前调用）。

    Returns:
        True = 安全，False = 不安全（拒绝上传）
    """
    result = full_scan(filepath, filename)
    return result["safe"]
