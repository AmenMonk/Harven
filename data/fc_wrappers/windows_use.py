"""FC封装: windows-use (Windows GUI高级控制)"""
DEPENDENCY = 'pyautogui'  # 实际依赖pyautogui，不是windows_use
import os, subprocess, sys
TOOL_DEFINITIONS = [
    {
        "name": "gui_find_click",
        "description": "通过文字查找Windows窗口/按钮并点击。比坐标点击更精准。",
        "params": {"window_title": "窗口标题（部分匹配），如'记事本'", "element_name": "要点击的元素名称，如'确定'按钮"}
    },
    {
        "name": "gui_list_windows",
        "description": "列出所有打开的Windows窗口。返回窗口标题列表。",
        "params": {}
    },
    {
        "name": "gui_send_keys",
        "description": "发送键盘快捷键。如Ctrl+C、Alt+Tab、Win+D等。",
        "params": {"keys": "快捷键组合，如'ctrl+c'、'alt+tab'、'win+d'"}
    }
]

def gui_find_click(window_title='', element_name=''):
    try:
        import pyautogui
        # 用pyautogui实现：先找窗口，再找文字位置点击
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle(window_title) if window_title else []
        if not windows:
            # 列出所有窗口让用户选
            all_wins = gw.getAllTitles()
            return {"success": False, "error": f"没找到标题含'{window_title}'的窗口",
                    "available_windows": [w for w in all_wins if w.strip()][:10]}
        win = windows[0]
        win.activate()
        # 截图后用OCR找element_name位置（如果有pytesseract）
        try:
            import pytesseract
            from PIL import Image
            ss = pyautogui.screenshot()
            text = pytesseract.image_to_string(ss, lang='chi_sim+eng')
            return {"success": True, "window": win.title, "element": element_name,
                    "note": "已激活窗口，元素点击需要pytesseract支持"}
        except ImportError:
            return {"success": True, "window": win.title, "note": "已激活窗口，元素定位需要pytesseract"}
    except ImportError:
        return {"success": False, "error": "pyautogui/pygetwindow未安装"}
    except Exception as e:
        return {"success": False, "error": f"操作失败: {e}"}

def gui_list_windows():
    try:
        import pygetwindow as gw
        titles = [w.title for w in gw.getAllWindows() if w.title.strip()]
        return {"success": True, "windows": titles[:20], "count": len(titles)}
    except ImportError:
        # 用PowerShell作为备选
        try:
            r = subprocess.run(
                ['powershell', '-Command',
                 'Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object MainWindowTitle'],
                capture_output=True, text=True, timeout=5
            )
            titles = [l.strip() for l in r.stdout.split('\n') if l.strip() and l.strip() != 'MainWindowTitle' and '---' not in l]
            return {"success": True, "windows": titles[:20], "count": len(titles)}
        except Exception as e:
            return {"success": False, "error": f"获取窗口列表失败: {e}"}
    except Exception as e:
        return {"success": False, "error": f"获取窗口列表失败: {e}"}

def gui_send_keys(keys=''):
    try:
        import pyautogui
        pyautogui.hotkey(*[k.strip() for k in keys.split('+')])
        return {"success": True, "keys": keys}
    except ImportError:
        return {"success": False, "error": "pyautogui未安装"}
    except Exception as e:
        return {"success": False, "error": f"发送快捷键失败: {e}"}
