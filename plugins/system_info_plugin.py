# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
系统信息插件 — 查询系统资源状态
"""
import logging
logger = logging.getLogger(__name__)
import re
import os
from src.core.plugin_system import PluginBase, PluginResult, PluginContext


class SystemInfoPlugin(PluginBase):
    name = "system_info"
    version = "1.0.0"
    description = "系统信息：CPU/内存/磁盘/进程 查询"
    author = "Harven AI"

    _SYS_PATTERNS = [
        (r"(?:系统|电脑|机器).*(?:状态|信息|资源|性能)", 0.9),
        (r"(?:CPU|内存|磁盘|硬盘|显卡|GPU).*(?:多少|多大|使用|占用|状态)", 0.9),
        (r"(?:查看|查|显示).*(?:系统|资源|进程)", 0.85),
    ]

    def should_handle(self, message: str) -> float:
        for pattern, score in self._SYS_PATTERNS:
            if re.search(pattern, message):
                return score
        return 0.0

    def execute(self, context: PluginContext) -> PluginResult:
        msg = context.user_message
        lines = ["🖥️ 系统状态："]
        has_data = False

        # CPU 信息
        if any(kw in msg for kw in ["CPU", "cpu", "处理器", "整体", "系统", "电脑", "机器"]):
            try:
                import psutil
                cpu_percent = psutil.cpu_percent(interval=1)
                cpu_count = psutil.cpu_count()
                lines.append(f"  🔲 CPU: {cpu_percent}% (核心数: {cpu_count})")
                has_data = True
            except ImportError:
                try:
                    import subprocess, platform
                    if platform.system() == "Windows":
                        result = subprocess.run(["wmic", "cpu", "get", "loadpercentage"],
                                                capture_output=True, text=True, timeout=5)
                        for line in result.stdout.strip().split("\n")[1:]:
                            if line.strip().isdigit():
                                lines.append(f"  🔲 CPU: {line.strip()}%")
                                has_data = True
                except Exception as _e: logger.debug(f"[system_info_plugin] 忽略: {_e}")

        # 内存信息
        if any(kw in msg for kw in ["内存", "memory", "RAM", "整体", "系统", "电脑", "机器"]):
            try:
                import psutil
                mem = psutil.virtual_memory()
                lines.append(f"  📊 内存: {mem.percent}% (已用 {mem.used // (1024**3)}GB / 总 {mem.total // (1024**3)}GB)")
                has_data = True
            except ImportError as _e:
                logger.debug(f"[system_info_plugin] 忽略: {_e}")

        # 磁盘信息
        if any(kw in msg for kw in ["磁盘", "硬盘", "disk", "存储", "整体", "系统", "电脑", "机器"]):
            try:
                import psutil
                disk = psutil.disk_usage(os.path.expanduser("~"))
                lines.append(f"  💾 磁盘: {disk.percent}% (已用 {disk.used // (1024**3)}GB / 总 {disk.total // (1024**3)}GB)")
                has_data = True
            except ImportError as _e:
                logger.debug(f"[system_info_plugin] 忽略: {_e}")

        if not has_data:
            return PluginResult(success=False, message="无法获取系统信息（psutil 未安装）")

        return PluginResult(success=True, message="\n".join(lines))


plugin_class = SystemInfoPlugin
