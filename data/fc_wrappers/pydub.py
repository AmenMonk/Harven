"""FC封装: pydub (音频处理)"""
import os, subprocess
TOOL_DEFINITIONS = [
    {
        "name": "convert_audio",
        "description": "音频格式转换。将音频文件转换为其他格式。",
        "params": {"input_path": "输入文件路径", "output_path": "输出文件路径（如output/audio.mp3）", "format": "输出格式（mp3/wav/ogg等）"}
    },
    {
        "name": "trim_audio",
        "description": "截取音频片段。从指定时间开始截取指定长度。",
        "params": {"input_path": "输入文件路径", "output_path": "输出文件路径", "start_sec": "开始时间（秒）", "duration_sec": "截取长度（秒）"}
    }
]

def convert_audio(input_path, output_path, format='mp3'):
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(input_path)
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        audio.export(output_path, format=format)
        return {"success": True, "path": os.path.abspath(output_path), "format": format}
    except ImportError:
        return {"success": False, "error": "pydub未安装或FFmpeg未安装"}
    except Exception as e:
        return {"success": False, "error": f"转换失败: {e}"}

def trim_audio(input_path, output_path, start_sec, duration_sec):
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(input_path)
        start = int(float(start_sec) * 1000)
        duration = int(float(duration_sec) * 1000)
        segment = audio[start:start+duration]
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        segment.export(output_path, format=output_path.split('.')[-1])
        return {"success": True, "path": os.path.abspath(output_path), "start": start_sec, "duration": duration_sec}
    except ImportError:
        return {"success": False, "error": "pydub未安装或FFmpeg未安装"}
    except Exception as e:
        return {"success": False, "error": f"截取失败: {e}"}
