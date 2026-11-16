"""FC封装: pytesseract (OCR文字识别)"""
import os, subprocess, sys
TOOL_DEFINITIONS = [
    {
        "name": "ocr_image",
        "description": "从图片中识别文字（OCR）。支持中英文等多语言。",
        "params": {"image_path": "图片文件路径", "lang": "语言（chi_sim=中文, eng=英文, chi_sim+eng=中英混合）"}
    }
]

def ocr_image(image_path, lang='chi_sim+eng'):
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang=lang)
        return {"success": True, "text": text.strip()[:5000], "chars": len(text.strip())}
    except ImportError:
        return {"success": False, "error": "pytesseract未安装，请在设置→插件→推荐工具中安装"}
    except Exception as e:
        return {"success": False, "error": f"OCR失败: {e}"}
