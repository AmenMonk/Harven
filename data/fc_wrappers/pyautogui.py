"""FC封装: pyautogui (鼠标键盘自动化)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "gui_screenshot",
        "description": "截取屏幕截图。保存当前屏幕画面为图片。",
        "params": {"save_path": "保存路径（如output/screenshot.png）"}
    },
    {
        "name": "gui_click",
        "description": "在屏幕指定位置点击鼠标。坐标原点在左上角。",
        "params": {"x": "X坐标（像素）", "y": "Y坐标（像素）", "button": "鼠标按钮（left/right/double，默认left）"}
    },
    {
        "name": "gui_type",
        "description": "模拟键盘输入文字。",
        "params": {"text": "要输入的文字"}
    }
]

def gui_screenshot(save_path='output/screenshot.png'):
    try:
        import pyautogui
        os.makedirs(os.path.dirname(save_path) if os.path.dirname(save_path) else '.', exist_ok=True)
        img = pyautogui.screenshot()
        img.save(save_path)
        return {"success": True, "path": os.path.abspath(save_path), "size": f"{img.size[0]}x{img.size[1]}"}
    except ImportError:
        return {"success": False, "error": "pyautogui未安装"}
    except Exception as e:
        return {"success": False, "error": f"截图失败: {e}"}

def gui_click(x, y, button='left'):
    try:
        import pyautogui
        pyautogui.click(int(float(x)), int(float(y)))
        return {"success": True, "x": int(float(x)), "y": int(float(y)), "button": button}
    except Exception as e:
        return {"success": False, "error": f"点击失败: {e}"}

def gui_type(text):
    try:
        import pyautogui
        pyautogui.typewrite(text, interval=0.05)
        return {"success": True, "text": text, "chars": len(text)}
    except Exception as e:
        return {"success": False, "error": f"输入失败: {e}"}
