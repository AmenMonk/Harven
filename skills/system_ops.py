"""
system_ops.py - 系统管家 Agent
脚本型Agent，系统操作：装/卸软件、磁盘清理、网络修复、查配置、文件整理、截屏、查进程、改壁纸、OCR

触发词：装软件、卸软件、清理磁盘、网络修复、查配置、整理桌面、截图、查进程、换壁纸
11项操作：winget/pip/netsh
"""

import os
import subprocess
import shutil
import platform
from pathlib import Path

# ★ 2026-08-17: 跨平台支持
try:
    from src.utils.platform import IS_WINDOWS, install_package_cmd, uninstall_package_cmd, clear_trash as _clear_trash, flush_dns as _flush_dns, PLATFORM_NAME
except ImportError:
    IS_WINDOWS = True
    PLATFORM_NAME = 'windows'
    def install_package_cmd(n): return ['winget', 'install', n]
    def uninstall_package_cmd(n): return ['winget', 'uninstall', n]
    def _clear_trash(): return False
    def _flush_dns(): return False

def generate(user_message: str, params: dict = None) -> str:
    """处理用户消息，执行系统操作"""
    params = params or {}
    msg = user_message.lower().strip()

    # 装软件
    if any(k in msg for k in ['装软件', '安装软件', 'install', '下载软件']):
        return _install_software(user_message)

    # 卸软件
    if any(k in msg for k in ['卸软件', '卸载', 'uninstall']):
        return _uninstall_software(user_message)

    # 清理磁盘
    if any(k in msg for k in ['清理磁盘', '清理垃圾', 'clean', 'disk cleanup']):
        return _clean_disk()

    # 网络修复
    if any(k in msg for k in ['网络修复', '修网络', 'network fix', 'dns', 'flushdns']):
        return _fix_network()

    # 查配置
    if any(k in msg for k in ['查配置', '系统配置', 'system info', '电脑配置']):
        return _get_system_info()

    # 查进程
    if any(k in msg for k in ['查进程', '进程', 'process', 'tasklist']):
        return _get_processes()

    # 截屏
    if any(k in msg for k in ['截图', '截屏', 'screenshot']):
        return _take_screenshot()

    # 改壁纸
    if any(k in msg for k in ['换壁纸', '改壁纸', 'wallpaper']):
        return _change_wallpaper(user_message)

    # pip操作
    if any(k in msg for k in ['pip', '装包', 'install package']):
        return _pip_operation(user_message)

    # 网络配置
    if any(k in msg for k in ['ip地址', 'ip配置', 'ipconfig', '查ip']):
        return _get_network_config()

    return "我是系统管家Agent，可以帮你：装/卸软件、清理磁盘、修复网络、查配置、查进程、截图、换壁纸、pip装包、查IP。你想做什么？"


def _install_software(message: str) -> str:
    """用winget/brew/apt安装软件"""
    try:
        import re
        match = re.search(r'(?:装|安装|install)\s*(.+)', message)
        if not match:
            return '请告诉我要装什么软件，比如"安装微信"'
        name = match.group(1).strip()

        # ★ 2026-08-17: 跨平台 — 非 Windows 用 platform.py
        if not IS_WINDOWS:
            cmd = install_package_cmd(name)
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
            if result.returncode == 0:
                return f"已成功安装 {name}"
            return f"安装失败：{result.stderr[:200]}"

        # Windows: winget 搜索 + 安装
        result = subprocess.run(
            ['winget', 'search', name, '--source', 'msstore'],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
        if result.returncode != 0:
            result = subprocess.run(
                ['winget', 'search', name],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
            )

        if result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            if len(lines) > 2:
                # 取第一个匹配
                parts = lines[2].split()
                if parts:
                    pkg_id = parts[0] if len(parts) > 0 else name
                    install_result = subprocess.run(
                        ['winget', 'install', pkg_id, '--accept-package-agreements', '--accept-source-agreements'],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
                    )
                    if install_result.returncode == 0:
                        return f"已成功安装 {name}"
                    return f"安装失败：{install_result.stderr[:200]}"

        return f"没找到名为 {name} 的软件，试试用全名搜索"
    except Exception as e:
        return f"安装失败：{e}"


def _uninstall_software(message: str) -> str:
    """用winget/brew/apt卸载软件"""
    try:
        import re
        match = re.search(r'(?:卸|卸载|uninstall)\s*(.+)', message)
        if not match:
            return "请告诉我要卸载什么软件"
        name = match.group(1).strip()

        # ★ 2026-08-17: 跨平台
        if not IS_WINDOWS:
            cmd = uninstall_package_cmd(name)
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if result.returncode == 0:
                return f"已成功卸载 {name}"
            return f"卸载失败：{result.stderr[:200]}"

        result = subprocess.run(
            ['winget', 'uninstall', name, '--silent'],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
        if result.returncode == 0:
            return f"已成功卸载 {name}"
        return f"卸载失败：{result.stderr[:200]}"
    except Exception as e:
        return f"卸载失败：{e}"


def _clean_disk() -> str:
    """清理磁盘临时文件"""
    try:
        freed = 0
        temp_dirs = [
            Path(os.environ.get('TEMP', '')),
            Path(os.environ.get('TMP', '')),
            Path('C:/Windows/Temp'),
        ]
        for temp_dir in temp_dirs:
            if temp_dir.exists():
                for f in temp_dir.rglob('*'):
                    if f.is_file():
                        try:
                            size = f.stat().st_size
                            f.unlink()
                            freed += size
                        except:
                            continue

        # 清理回收站（★ 2026-08-15: 用Clear-RecycleBin代替rd /s /q，更安全）
        # ★ 2026-08-17: 跨平台 — 非 Windows 用 platform.py
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ['powershell', '-Command', 'Clear-RecycleBin -Force -ErrorAction SilentlyContinue'],
                    capture_output=True, timeout=30
                )
            else:
                _clear_trash()
        except:
            pass

        freed_mb = freed / (1024 * 1024)
        return f"已清理临时文件，释放 {freed_mb:.0f}MB 空间"
    except Exception as e:
        return f"清理失败：{e}"


def _fix_network() -> str:
    """修复网络"""
    try:
        results = []
        # 刷新DNS
        r1 = subprocess.run(['ipconfig', '/flushdns'], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        results.append("DNS缓存已刷新" if r1.returncode == 0 else "DNS刷新失败")

        # 重置Winsock
        r2 = subprocess.run(['netsh', 'winsock', 'reset'], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        results.append("Winsock已重置" if r2.returncode == 0 else "Winsock重置失败")

        # 重置TCP/IP
        r3 = subprocess.run(['netsh', 'int', 'ip', 'reset'], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        results.append("TCP/IP已重置" if r3.returncode == 0 else "TCP/IP重置失败")

        return "网络修复完成：\n" + "\n".join(results) + "\n\n建议重启电脑使修复生效"
    except Exception as e:
        return f"网络修复失败：{e}"


def _get_system_info() -> str:
    """查系统配置"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=1)
        disk = shutil.disk_usage('C:\\')

        return (f"系统：{platform.system()} {platform.version()}\n"
                f"CPU使用率：{cpu}%\n"
                f"内存：{mem.used/(1024**3):.1f}GB / {mem.total/(1024**3):.1f}GB ({mem.percent}%)\n"
                f"C盘：{disk.used/(1024**3):.0f}GB / {disk.total/(1024**3):.0f}GB")
    except Exception as e:
        return f"获取系统信息失败：{e}"


def _get_processes() -> str:
    """查进程"""
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                info = p.info
                if info['memory_percent'] and info['memory_percent'] > 0.5:
                    procs.append(f"  PID:{info['pid']} {info['name']} CPU:{info.get('cpu_percent',0):.0f}% 内存:{info['memory_percent']:.1f}%")
            except:
                continue
        procs.sort(key=lambda x: float(x.split('内存:')[1].rstrip('%')) if '内存:' in x else 0, reverse=True)
        return "主要进程：\n" + "\n".join(procs[:15])
    except Exception as e:
        return f"获取进程失败：{e}"


def _take_screenshot() -> str:
    """截屏"""
    try:
        import pyautogui
        screenshot = pyautogui.screenshot()
        save_path = Path.home() / 'Desktop' / 'harven_screenshot.png'
        screenshot.save(str(save_path))
        return f"已截图并保存到桌面：{save_path}"
    except Exception as e:
        return f"截图失败：{e}"


def _change_wallpaper(message: str) -> str:
    """换壁纸"""
    try:
        import re
        import ctypes
        match = re.search(r'["""\'](.+?)["""\']', message)
        if not match:
            return "请告诉我要设为壁纸的图片路径，比如 换壁纸\"C:/图片/1.jpg\""

        path = match.group(1)
        if not os.path.exists(path):
            return f"图片不存在：{path}"

        ctypes.windll.user32.SystemParametersInfoW(20, 0, os.path.abspath(path), 3)
        return f"已更换壁纸为：{path}"
    except Exception as e:
        return f"换壁纸失败：{e}"


def _pip_operation(message: str) -> str:
    """pip安装包"""
    try:
        import re
        match = re.search(r'(?:pip|装包|install)\s+(.+)', message)
        if not match:
            return "请告诉我要装什么包，比如 pip install requests"
        pkg = match.group(1).strip().replace('install ', '').replace('装', '').strip()

        result = subprocess.run(
            ['pip', 'install', pkg],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )
        if result.returncode == 0:
            return f"已成功安装 {pkg}"
        return f"安装失败：{result.stderr[:200]}"
    except Exception as e:
        return f"pip安装失败：{e}"


def _get_network_config() -> str:
    """查IP配置"""
    try:
        result = subprocess.run(
            ['ipconfig', '/all'],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
        # 只返回关键信息
        lines = result.stdout.split('\n')
        important = []
        for line in lines:
            if any(k in line for k in ['IPv4', 'IPv6', 'DNS', '网关', 'Gateway', 'IP Address', '子网', 'Subnet']):
                important.append(line.strip())
        return "网络配置：\n" + "\n".join(important[:10]) if important else result.stdout[:500]
    except Exception as e:
        return f"获取网络配置失败：{e}"
