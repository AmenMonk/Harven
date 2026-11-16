"""FC封装: moviepy (视频剪辑)"""
import os
TOOL_DEFINITIONS = [
    {
        "name": "trim_video",
        "description": "截取视频片段。从指定时间开始截取指定长度。",
        "params": {"input_path": "输入视频路径", "output_path": "输出视频路径", "start_sec": "开始时间（秒）", "end_sec": "结束时间（秒）"}
    },
    {
        "name": "get_video_info",
        "description": "获取视频信息（时长/分辨率/帧率）。",
        "params": {"input_path": "视频文件路径"}
    }
]

def trim_video(input_path, output_path, start_sec, end_sec):
    try:
        from moviepy.editor import VideoFileClip
        clip = VideoFileClip(input_path)
        segment = clip.subclip(float(start_sec), float(end_sec))
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        segment.write_videofile(output_path, codec='libx264', audio_codec='aac', verbose=False, logger=None)
        clip.close()
        segment.close()
        return {"success": True, "path": os.path.abspath(output_path)}
    except ImportError:
        return {"success": False, "error": "moviepy未安装或FFmpeg未安装"}
    except Exception as e:
        return {"success": False, "error": f"视频截取失败: {e}"}

def get_video_info(input_path):
    try:
        from moviepy.editor import VideoFileClip
        clip = VideoFileClip(input_path)
        info = {
            "success": True,
            "duration": f"{clip.duration:.1f}秒",
            "size": f"{clip.size[0]}x{clip.size[1]}",
            "fps": f"{clip.fps:.0f}fps",
            "has_audio": clip.audio is not None
        }
        clip.close()
        return info
    except ImportError:
        return {"success": False, "error": "moviepy未安装"}
    except Exception as e:
        return {"success": False, "error": f"获取视频信息失败: {e}"}
