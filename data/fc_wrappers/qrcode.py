"""FC封装: qrcode (二维码生成)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "create_qrcode",
        "description": "生成二维码图片。将文字/URL转为二维码。",
        "params": {"data": "二维码内容（URL或文字）", "save_path": "保存路径（如output/qr.png）"}
    }
]

def create_qrcode(data, save_path='output/qrcode.png'):
    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        os.makedirs(os.path.dirname(save_path) if os.path.dirname(save_path) else '.', exist_ok=True)
        img.save(save_path)
        return {"success": True, "path": os.path.abspath(save_path), "data": data[:50]}
    except ImportError:
        return {"success": False, "error": "qrcode未安装，请运行 pip install qrcode[pil]"}
    except Exception as e:
        return {"success": False, "error": f"二维码生成失败: {e}"}
