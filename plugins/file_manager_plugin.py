# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
文件管理插件 — 处理本地文件操作请求
"""
import os
import re
from pathlib import Path
from src.core.plugin_system import PluginBase, PluginResult, PluginContext


class FileManagerPlugin(PluginBase):
    name = "file_manager"
    version = "1.0.0"
    description = "文件管理：列出文件、查看大小、搜索文件"
    author = "Harven AI"

    _FILE_PATTERNS = [
        (r"(?:列出|查看|显示).*(?:文件|目录|文件夹)", 0.85),
        (r"(?:搜索|查找|找).*(?:文件|文档)", 0.85),
        (r"(?:文件|目录|文件夹).*(?:多大|大小|占用)", 0.8),
    ]

    # 安全路径白名单
    _SAFE_ROOTS = [
        str(Path.home() / "Downloads"),
        str(Path.home() / "Documents"),
        str(Path.home() / "Desktop"),
    ]

    def should_handle(self, message: str) -> float:
        for pattern, score in self._FILE_PATTERNS:
            if re.search(pattern, message):
                return score
        return 0.0

    def execute(self, context: PluginContext) -> PluginResult:
        msg = context.user_message

        # 列出文件
        if any(kw in msg for kw in ["列出", "查看", "显示"]):
            # 尝试提取路径
            path_match = re.search(r"(?:在|路径|目录|文件夹)\s*[:：]?\s*([^\s，,。]+)", msg)
            if path_match:
                dir_path = path_match.group(1)
            else:
                dir_path = str(Path.home())

            dir_path = os.path.expanduser(dir_path)
            if not os.path.isdir(dir_path):
                return PluginResult(success=False, message=f"目录不存在：{dir_path}")

            try:
                files = os.listdir(dir_path)
                files.sort()
                lines = [f"📁 {dir_path} ({len(files)} 项):"]
                for f in files[:15]:
                    full = os.path.join(dir_path, f)
                    prefix = "📂" if os.path.isdir(full) else "📄"
                    size = ""
                    if os.path.isfile(full):
                        size = f" ({self._fmt_size(os.path.getsize(full))})"
                    lines.append(f"  {prefix} {f}{size}")
                if len(files) > 15:
                    lines.append(f"  ... 还有 {len(files) - 15} 项")
                return PluginResult(success=True, message="\n".join(lines))
            except PermissionError:
                return PluginResult(success=False, message="没有权限访问该目录")

        return PluginResult(success=False, message="未识别文件操作")

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f}MB"
        return f"{size / (1024 * 1024 * 1024):.1f}GB"


plugin_class = FileManagerPlugin
