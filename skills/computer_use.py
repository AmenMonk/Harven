"""
computer_use.py - 电脑操作 Agent
脚本型Agent，操作本地电脑：截图、打开应用、文件管理、窗口控制

触发词：操作电脑、截图、打开D盘、整理桌面、输入文字、窗口最大化等
"""

import os
import subprocess
import platform
import shutil
import string
from pathlib import Path

def generate(user_message: str, params: dict = None) -> str:
    """处理用户消息，执行电脑操作"""
    params = params or {}
    msg = user_message.lower().strip()

    # 截图
    if any(k in msg for k in ['截图', '截屏', 'screenshot']):
        return _take_screenshot()

    # 打开磁盘/目录
    if any(k in msg for k in ['打开', 'open']) and any(k in msg for k in ['盘', '目录', '文件夹', 'directory', 'folder']):
        return _open_path(user_message)

    # 列出磁盘
    if any(k in msg for k in ['列出', '查看', 'list']) and any(k in msg for k in ['盘', '磁盘', 'drive']):
        return _list_drives()

    # 查看内存
    if any(k in msg for k in ['内存', 'memory', '查看内存']):
        return _get_memory_info()

    # 查看进程
    if any(k in msg for k in ['进程', 'process', '查看进程']):
        return _get_processes()

    # 整理桌面
    if any(k in msg for k in ['整理桌面', '整理文件', 'clean desktop']):
        return _clean_desktop()

    # 输入文字
    if any(k in msg for k in ['输入文字', 'type text', '打字']):
        return _type_text(user_message)

    # 窗口控制
    if any(k in msg for k in ['最大化', '最小化', 'maximize', 'minimize']):
        return _window_control(msg)

    # 打开应用（组合匹配，不漏词）
    if '打开' in msg and any(k in msg for k in ['设置', '浏览器', '记事本', '计算器', '画图', '资源管理器', '任务管理器', '终端', 'cmd', '控制面板', '应用']):
        return _open_app(user_message)

    return f"我是电脑操作Agent，可以帮你：截图、打开磁盘/文件夹、查看内存/进程、整理桌面、输入文字、控制窗口、打开应用。你想做什么？"


def _take_screenshot() -> str:
    """截屏"""
    try:
        # ★ 2026-08-17: 跨平台 — 优先用 platform.py
        try:
            from src.utils.platform import screenshot as _platform_screenshot, IS_WINDOWS
            save_path = _platform_screenshot()
            if save_path and os.path.exists(save_path):
                return f"已截图并保存到：{save_path}"
        except ImportError:
            pass
        # 回退：pyautogui（跨平台）
        import pyautogui
        screenshot = pyautogui.screenshot()
        save_path = Path.home() / 'Desktop' / 'harven_screenshot.png'
        screenshot.save(str(save_path))
        return f"已截图并保存到桌面：{save_path}"
    except Exception as e:
        return f"截图失败：{e}"


def _open_path(message: str) -> str:
    """打开磁盘或目录"""
    try:
        # ★ 2026-08-17: 跨平台 — 用 platform.open_path
        try:
            from src.utils.platform import IS_WINDOWS, open_path as _open_path_platform
        except ImportError:
            IS_WINDOWS = True
            _open_path_platform = None

        msg_upper = message.upper()
        if IS_WINDOWS:
            # Windows: 提取盘符
            for drive in string.ascii_uppercase:
                if f'{drive}盘' in msg_upper or f'{drive}:' in msg_upper:
                    path = f"{drive}:\\"
                    if os.path.exists(path):
                        if _open_path_platform:
                            _open_path_platform(path)
                        else:
                            os.startfile(path)
                        return f"已打开 {path}"
                    return f"磁盘 {path} 不存在"
            # 提取目录路径
            import re
            match = re.search(r'([A-Za-z]:\\[^\s]+)', message)
            if match:
                path = match.group(1)
                if os.path.exists(path):
                    if _open_path_platform:
                        _open_path_platform(path)
                    else:
                        os.startfile(path)
                    return f"已打开 {path}"
                return f"路径不存在：{path}"
        else:
            # Linux/macOS: 提取 Unix 路径
            import re
            match = re.search(r'(/(?:home|Users|tmp|var|opt|etc|mnt|media)/[^\s]+)', message)
            if match:
                path = match.group(1)
                if os.path.exists(path):
                    if _open_path_platform:
                        _open_path_platform(path)
                    return f"已打开 {path}"
                return f"路径不存在：{path}"
            # 也支持 ~ 路径
            if '~' in message:
                match = re.search(r'(~[^\s]*)', message)
                if match:
                    path = os.path.expanduser(match.group(1))
                    if os.path.exists(path):
                        if _open_path_platform:
                            _open_path_platform(path)
                        return f"已打开 {path}"

        return '请告诉我要打开哪个目录，比如"打开D盘"或"打开 /home"'
    except Exception as e:
        return f"打开失败：{e}"


def _list_drives() -> str:
    """列出所有磁盘"""
    try:
        drives = []
        for letter in string.ascii_uppercase:
            path = f"{letter}:\\"
            if os.path.exists(path):
                usage = shutil.disk_usage(path)
                free_gb = usage.free / (1024**3)
                total_gb = usage.total / (1024**3)
                drives.append(f"  {letter}: 总计{total_gb:.0f}GB 可用{free_gb:.0f}GB")
        return "磁盘列表：\n" + "\n".join(drives)
    except Exception as e:
        return f"获取磁盘信息失败：{e}"


def _get_memory_info() -> str:
    """查看内存"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        total_gb = mem.total / (1024**3)
        used_gb = mem.used / (1024**3)
        free_gb = mem.available / (1024**3)
        return f"内存：总计{total_gb:.1f}GB 已用{used_gb:.1f}GB 可用{free_gb:.1f}GB ({mem.percent}%)"
    except Exception as e:
        return f"获取内存信息失败：{e}"


def _get_processes() -> str:
    """查看进程"""
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'memory_percent']):
            try:
                info = p.info
                if info['memory_percent'] and info['memory_percent'] > 1:
                    procs.append(f"  PID:{info['pid']} {info['name']} 内存:{info['memory_percent']:.1f}%")
            except:
                continue
        procs.sort(key=lambda x: float(x.split('内存:')[1].rstrip('%')), reverse=True)
        return "占用内存最多的进程：\n" + "\n".join(procs[:10])
    except Exception as e:
        return f"获取进程信息失败：{e}"


def _clean_desktop() -> str:
    """整理桌面（跳过快捷方式.lnk）"""
    try:
        desktop = Path.home() / 'Desktop'
        # 跳过快捷方式和文件夹
        files = [f for f in desktop.iterdir() if f.is_file() and f.suffix.lower() != '.lnk']
        if not files:
            return "桌面上没有需要整理的文件"

        # 按扩展名分类
        folders = {}
        for f in files:
            ext = f.suffix.lstrip('.').lower() or 'other'
            if ext not in folders:
                folders[ext] = []
            folders[ext].append(f)

        moved = 0
        for ext, file_list in folders.items():
            folder = desktop / f"_{ext.upper()}"
            folder.mkdir(exist_ok=True)
            for f in file_list:
                target = folder / f.name
                if not target.exists():
                    shutil.move(str(f), str(target))
                    moved += 1

        return f"已整理桌面：移动了{moved}个文件到{len(folders)}个分类文件夹（快捷方式已保留）"
    except Exception as e:
        return f"整理失败：{e}"


def _type_text(message: str) -> str:
    """输入文字（中文用剪贴板粘贴）"""
    try:
        import pyautogui
        import re
        match = re.search(r'["""\'](.+?)["""\']', message)
        if match:
            text = match.group(1)
        else:
            return '请用引号包裹要输入的文字，比如 输入文字"你好"'

        if text.isascii():
            # 英文直接输入
            pyautogui.typewrite(text, interval=0.02)
        else:
            # 中文先写剪贴板再粘贴
            try:
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey('ctrl', 'v')
            except ImportError:
                return "中文输入需要pyperclip包，请运行：pip install pyperclip"
        return f"已输入：{text}"
    except Exception as e:
        return f"输入失败：{e}"


def _window_control(msg: str) -> str:
    """窗口控制"""
    try:
        import pyautogui
        if '最大化' in msg or 'maximize' in msg:
            pyautogui.hotkey('win', 'up')
            return "已最大化当前窗口"
        elif '最小化' in msg or 'minimize' in msg:
            pyautogui.hotkey('win', 'down')
            return "已最小化当前窗口"
        return "支持：最大化、最小化"
    except Exception as e:
        return f"窗口控制失败：{e}"


def _open_app(message: str) -> str:
    """打开应用"""
    try:
        import re
        # ★ 2026-08-17: 跨平台应用映射
        try:
            from src.utils.platform import IS_WINDOWS, IS_MACOS, IS_LINUX
        except ImportError:
            IS_WINDOWS, IS_MACOS, IS_LINUX = True, False, False

        if IS_WINDOWS:
            # Windows 应用白名单
            apps = {
                '浏览器': ['cmd', '/c', 'start', 'msedge'],
                '记事本': ['cmd', '/c', 'start', 'notepad'],
                '计算器': ['cmd', '/c', 'start', 'calc:'],
                '画图': ['cmd', '/c', 'start', 'mspaint'],
                '资源管理器': ['cmd', '/c', 'start', 'explorer'],
                '任务管理器': ['cmd', '/c', 'start', 'taskmgr'],
                '设置': ['cmd', '/c', 'start', 'ms-settings:'],
                '控制面板': ['cmd', '/c', 'start', 'control.exe'],
                'cmd': ['cmd', '/c', 'start', 'cmd'],
                '终端': ['cmd', '/c', 'start', 'wt'],
            }
        elif IS_MACOS:
            # macOS 应用（用 open -a）
            apps = {
                '浏览器': ['open', '-a', 'Safari'],
                '记事本': ['open', '-a', 'TextEdit'],
                '计算器': ['open', '-a', 'Calculator'],
                '画图': ['open', '-a', 'Preview'],
                '资源管理器': ['open', '-a', 'Finder'],
                '终端': ['open', '-a', 'Terminal'],
                '设置': ['open', 'x-apple.systempreferences:com.apple.preference'],
            }
        else:
            # Linux 应用
            apps = {
                '浏览器': ['xdg-open', 'https://'],
                '记事本': ['gedit'],
                '计算器': ['gnome-calculator'],
                '资源管理器': ['xdg-open', '.'],
                '终端': ['x-terminal-emulator'],
                '设置': ['gnome-control-center'],
            }

        for name, cmd_list in apps.items():
            if name in message:
                result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    return f"已打开{name}"
                else:
                    return f"打开{name}失败，可能未安装该应用"

        # ★ 2026-08-15: 不再接受用户输入直接作为命令（防止命令注入）
        app_name = re.search(r'(?:打开|启动|open|start)\s+(.+)', message)
        if app_name:
            name = app_name.group(1).strip()
            # 只允许白名单中的应用名
            safe_apps = ['msedge', 'notepad', 'calc', 'mspaint', 'explorer', 'taskmgr', 'cmd', 'wt']
            if name.lower() in safe_apps and IS_WINDOWS:
                try:
                    subprocess.Popen(['cmd', '/c', 'start', name])
                    return f"尝试打开：{name}"
                except Exception:
                    return f"找不到应用：{name}"
            else:
                return f"不支持的 应用：{name}，支持：{', '.join(apps.keys())}"

        return "支持打开：浏览器、记事本、计算器、画图、资源管理器、任务管理器、设置、控制面板、终端、cmd"
    except Exception as e:
        return f"打开应用失败：{e}"

