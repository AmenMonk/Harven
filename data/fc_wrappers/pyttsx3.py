"""FC封装: pyttsx3 (文字转语音)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "speak_text",
        "description": "文字转语音。将文字朗读出来或保存为音频文件。",
        "params": {"text": "要朗读的文字", "save_path": "保存路径（可选，如output/voice.mp3。留空则直接朗读）"}
    }
]

def speak_text(text, save_path=''):
    try:
        import pyttsx3
        engine = pyttsx3.init()
        # 设置中文
        voices = engine.getProperty('voices')
        for v in voices:
            if 'chinese' in v.name.lower() or 'zh' in v.name.lower():
                engine.setProperty('voice', v.id)
                break
        engine.setProperty('rate', 150)
        if save_path:
            os.makedirs(os.path.dirname(save_path) if os.path.dirname(save_path) else '.', exist_ok=True)
            engine.save_to_file(text, save_path)
            engine.runAndWait()
            return {"success": True, "path": os.path.abspath(save_path), "message": "语音文件已保存"}
        else:
            engine.say(text)
            engine.runAndWait()
            return {"success": True, "message": "已朗读"}
    except ImportError:
        return {"success": False, "error": "pyttsx3未安装"}
    except Exception as e:
        return {"success": False, "error": f"语音合成失败: {e}"}
