# -*- coding: utf-8 -*-
"""
Harven AI 跨平台工具模块
★ 2026-08-17: 支持 Windows / Linux / macOS
提供平台检测和跨平台命令映射
"""
import os
import sys
import platform
import shutil
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 平台检测 ──
IS_WINDOWS = sys.platform.startswith('win')
IS_LINUX = sys.platform.startswith('linux')
IS_MACOS = sys.platform == 'darwin'
PLATFORM_NAME = 'windows' if IS_WINDOWS else ('linux' if IS_LINUX else ('macos' if IS_MACOS else 'unknown'))


def get_platform() -> str:
    """返回当前平台名称: windows / linux / macos"""
    return PLATFORM_NAME


def get_home_dir() -> Path:
    """返回用户主目录"""
    return Path.home()


def get_trash_dir() -> Path:
    """返回回收站/废纸篓路径"""
    if IS_WINDOWS:
        return Path('C:/$Recycle.Bin')
    elif IS_MACOS:
        return Path.home() / '.Trash'
    else:  # Linux
        return Path.home() / '.local' / 'share' / 'Trash'


def get_separator() -> str:
    """路径分隔符"""
    return '\\' if IS_WINDOWS else '/'


# ── 包管理器 ──
def get_package_manager() -> str:
    """检测系统的包管理器"""
    if IS_WINDOWS:
        return 'winget'
    elif IS_MACOS:
        return 'brew' if shutil.which('brew') else 'none'
    else:  # Linux
        for pm in ['apt', 'yum', 'dnf', 'pacman', 'zypper']:
            if shutil.which(pm):
                return pm
        return 'none'


def install_package_cmd(pkg_name: str) -> list:
    """返回安装软件的命令（列表形式，安全传参）"""
    pm = get_package_manager()
    if pm == 'winget':
        return ['winget', 'install', pkg_name, '--accept-source-agreements', '--accept-package-agreements']
    elif pm == 'brew':
        return ['brew', 'install', pkg_name]
    elif pm == 'apt':
        return ['sudo', 'apt', 'install', '-y', pkg_name]
    elif pm in ('yum', 'dnf'):
        return ['sudo', pm, 'install', '-y', pkg_name]
    elif pm == 'pacman':
        return ['sudo', 'pacman', '-S', '--noconfirm', pkg_name]
    elif pm == 'zypper':
        return ['sudo', 'zypper', 'install', '-y', pkg_name]
    else:
        return ['echo', f'No package manager found for {pkg_name}']


def uninstall_package_cmd(pkg_name: str) -> list:
    """返回卸载软件的命令"""
    pm = get_package_manager()
    if pm == 'winget':
        return ['winget', 'uninstall', pkg_name]
    elif pm == 'brew':
        return ['brew', 'uninstall', pkg_name]
    elif pm == 'apt':
        return ['sudo', 'apt', 'remove', '-y', pkg_name]
    elif pm in ('yum', 'dnf'):
        return ['sudo', pm, 'remove', '-y', pkg_name]
    elif pm == 'pacman':
        return ['sudo', 'pacman', '-R', '--noconfirm', pkg_name]
    elif pm == 'zypper':
        return ['sudo', 'zypper', 'remove', '-y', pkg_name]
    else:
        return ['echo', f'No package manager found for {pkg_name}']


# ── 系统操作 ──
def clear_trash() -> bool:
    """清空回收站/废纸篓"""
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ['powershell', '-Command', 'Clear-RecycleBin -Force'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30
            )
            return result.returncode == 0
        else:
            trash = get_trash_dir()
            if trash.exists():
                for item in trash.iterdir():
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
            return True
    except Exception as e:
        logger.error(f'[Platform] 清空回收站失败: {e}')
        return False


def screenshot(output_path: str = None) -> str:
    """截图，返回文件路径"""
    import tempfile
    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), 'harven_screenshot.png')
    if IS_WINDOWS:
        # PowerShell 截图
        script = f'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
$bitmap.Save("{output_path}")
'''
        subprocess.run(['powershell', '-Command', script], capture_output=True, timeout=10)
    elif IS_MACOS:
        subprocess.run(['screencapture', '-x', output_path], capture_output=True, timeout=10)
    else:  # Linux
        if shutil.which('scrot'):
            subprocess.run(['scrot', output_path], capture_output=True, timeout=10)
        elif shutil.which('gnome-screenshot'):
            subprocess.run(['gnome-screenshot', '-f', output_path], capture_output=True, timeout=10)
        elif shutil.which('import'):
            subprocess.run(['import', '-window', 'root', output_path], capture_output=True, timeout=10)
    return output_path


def open_path(path: str):
    """用系统文件管理器打开路径"""
    try:
        if IS_WINDOWS:
            subprocess.run(['explorer', path], capture_output=True)
        elif IS_MACOS:
            subprocess.run(['open', path], capture_output=True)
        else:  # Linux
            subprocess.run(['xdg-open', path], capture_output=True)
    except Exception as e:
        logger.debug(f'[Platform] open_path 失败: {e}')


def open_app(app_name: str):
    """打开应用程序"""
    try:
        if IS_WINDOWS:
            subprocess.run(['cmd', '/c', 'start', '', app_name], capture_output=True)
        elif IS_MACOS:
            subprocess.run(['open', '-a', app_name], capture_output=True)
        else:  # Linux
            subprocess.run([app_name], capture_output=True)
    except Exception as e:
        logger.debug(f'[Platform] open_app 失败: {e}')


def get_system_info() -> dict:
    """获取系统信息"""
    return {
        'platform': PLATFORM_NAME,
        'system': platform.system(),
        'release': platform.release(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'python_version': sys.version,
        'package_manager': get_package_manager(),
        'home_dir': str(get_home_dir()),
    }


def delete_path_force(path: str) -> bool:
    """强制删除路径（跨平台）"""
    try:
        p = Path(path)
        if not p.exists():
            return True
        if p.is_dir():
            if IS_WINDOWS:
                # Windows: 先改属性再删
                subprocess.run(['attrib', '-R', '-H', '-S', path, '/S', '/D'], capture_output=True)
                shutil.rmtree(p, ignore_errors=True)
                if p.exists():
                    subprocess.run(['cmd', '/c', 'rd', '/s', '/q', path], capture_output=True)
            else:
                # Linux/macOS: chmod + rm
                subprocess.run(['chmod', '-R', 'u+w', path], capture_output=True)
                shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        return not p.exists()
    except Exception as e:
        logger.error(f'[Platform] 删除失败 {path}: {e}')
        return False


def flush_dns():
    """刷新 DNS 缓存"""
    try:
        if IS_WINDOWS:
            subprocess.run(['ipconfig', '/flushdns'], capture_output=True, encoding='utf-8', errors='replace')
        elif IS_MACOS:
            subprocess.run(['sudo', 'dscacheutil', '-flushcache'], capture_output=True)
            subprocess.run(['sudo', 'killall', '-HUP', 'mDNSResponder'], capture_output=True)
        else:  # Linux
            if shutil.which('systemd-resolve'):
                subprocess.run(['sudo', 'systemd-resolve', '--flush-caches'], capture_output=True)
            elif shutil.which('resolvectl'):
                subprocess.run(['sudo', 'resolvectl', 'flush-caches'], capture_output=True)
        return True
    except Exception as e:
        logger.debug(f'[Platform] flush_dns 失败: {e}')
        return False
