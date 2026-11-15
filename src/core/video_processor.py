# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
视频识别模块
通过 FFmpeg 抽帧 + 本地多模态模型逐帧分析视频内容
纯本地运行，不依赖外部API
"""
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import timedelta
import logging
logger = logging.getLogger(__name__)

# 视频格式白名单
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg"}
MAX_VIDEO_SIZE = 500 * 1024 * 1024  # 500MB
MAX_FRAMES = 30  # 最多抽取帧数


def is_video_file(file_path):
    """判断文件是否为视频"""
    path = Path(file_path)
    return path.suffix.lower() in VIDEO_EXTENSIONS


def get_video_info(file_path):
    """
    获取视频基本信息（时长、分辨率、帧率）

    Returns:
        dict: {"duration_sec": float, "width": int, "height": int, "fps": float, "size_mb": float}
        None: 文件不存在或无法解析
    """
    path = Path(file_path)
    if not path.exists():
        return None

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_VIDEO_SIZE:
        return None

    # 优先使用系统安装的 ffmpeg（Apache/GPL-compatible 外部程序，不引入 GPL 库依赖）
    # 不再依赖 imageio-ffmpeg（GPL-2.0），避免许可证传染
    ffmpeg_exe = shutil.which("ffmpeg") or "ffmpeg"

    try:
        result = subprocess.run(
            [ffmpeg_exe, "-i", str(path), "-hide_banner"],
            capture_output=True, text=True, timeout=15, encoding="utf-8",
            errors="replace"
        )

        # 从 stderr 中解析视频信息
        output = result.stderr

        duration = None
        width = None
        height = None
        fps = None

        # 解析时长
        import re
        dur_match = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", output)
        if dur_match:
            h, m, s, ms = dur_match.groups()
            duration = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 100

        # 解析视频流
        video_match = re.search(
            r"Stream.*Video:.*?(\d+)x(\d+).*?(\d+(?:\.\d+)?)\s*fps",
            output, re.DOTALL
        )
        if video_match:
            width = int(video_match.group(1))
            height = int(video_match.group(2))
            fps = float(video_match.group(3))

        # 如果正则没匹配到帧率，尝试另一种格式
        if fps is None:
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
            if fps_match:
                fps = float(fps_match.group(1))

        if duration is None or width is None:
            return None

        return {
            "duration_sec": duration,
            "width": width,
            "height": height,
            "fps": fps or 30.0,
            "size_mb": round(size_mb, 1),
        }
    except Exception as e:
        logger.error(f"[VideoProcessor] 获取视频信息失败: {e}")
        return None


def extract_frames(
    file_path,
    max_frames=MAX_FRAMES,
    output_dir=None,
    interval_sec=None,
):
    """
    从视频中均匀抽取关键帧

    Args:
        file_path: 视频文件路径
        max_frames: 最大抽取帧数（默认30）
        output_dir: 输出目录（默认临时目录）
        interval_sec: 固定间隔秒数（如果指定，忽略max_frames）

    Returns:
        list: [frame_path, ...] 抽取的帧图片路径列表
        dict: 视频信息
    """
    info = get_video_info(file_path)
    if info is None:
        return [], None

    # 计算抽帧参数
    if interval_sec is None:
        if info["duration_sec"] <= 0:
            return [], info
        interval = max(1, info["duration_sec"] / max_frames)
    else:
        interval = interval_sec

    # 创建输出目录
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="lingzhi_frames_")

    os.makedirs(output_dir, exist_ok=True)

    # 系统 ffmpeg（与 get_video_info 保持一致，不引入 imageio-ffmpeg GPL 依赖）
    ffmpeg_exe = shutil.which("ffmpeg") or "ffmpeg"

    try:
        # 使用 FFmpeg 均匀抽帧，每 interval 秒一帧
        output_pattern = os.path.join(output_dir, "frame_%04d.jpg")
        cmd = [
            ffmpeg_exe,
            "-i", str(file_path),
            "-vf", f"fps=1/{interval}",
            "-q:v", "2",  # 高质量JPEG
            "-y",
            output_pattern,
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace"
        )

        # 收集生成的帧
        frames = sorted(
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.endswith(".jpg")
        )

        return frames, info

    except subprocess.TimeoutExpired:
        logger.debug("[VideoProcessor] 抽帧超时")
        return [], info
    except Exception as e:
        logger.error(f"[VideoProcessor] 抽帧失败: {e}")
        return [], info


def analyze_video(model_manager, text, video_path, system_prompt=None, stream=True):
    """
    分析视频内容

    流程：
    1. 获取视频信息
    2. FFmpeg 均匀抽帧（最多30帧）
    3. 逐帧（或批量）送多模态模型分析
    4. 整合所有帧的描述，生成最终回复

    Args:
        model_manager: ModelManager 实例
        text: 用户的问题/描述
        video_path: 视频文件路径
        system_prompt: 系统提示词
        stream: 是否流式输出

    Yields:
        ("thinking", str): 处理状态
        ("text", str): 文本片段
        ("done", str): 完整回复
        ("error", str): 错误信息
    """
    # 1. 获取视频信息
    info = get_video_info(video_path)
    if info is None:
        yield ("error", "无法解析视频文件，请确认文件格式是否支持。")
        return

    yield ("thinking",
           f"视频信息：{info['width']}x{info['height']}，时长 {info['duration_sec']:.1f} 秒，"
           f"大小 {info['size_mb']}MB")

    # 2. 抽帧
    yield ("thinking", "正在抽取视频关键帧...")
    frames, _ = extract_frames(video_path, max_frames=MAX_FRAMES)

    if not frames:
        yield ("error", "视频抽帧失败，无法分析。")
        return

    yield ("thinking", f"已抽取 {len(frames)} 个关键帧，正在逐帧分析...")

    # 3. 逐帧分析
    # ★ #1204（2026-10-25 开发者死命令）：只用用户选的主模型，严禁扫描代选；不能看图如实报错
    from src.core.image_processor import analyze_image, model_supports_vision, installed_vision_models

    vision_model = (getattr(model_manager, 'model', '') or '').strip()
    _cur_name = vision_model or '（未选）'
    # ★ #1267：主模型=HarvenEye → 视频逐帧走 Eye 视觉通道（帧循环内分支），跳过字面名检查
    from src.core import eye_client as _eyecli
    _eye_video = _eyecli.is_selected()
    if not _eye_video:
        try:
            if not vision_model or not model_supports_vision(vision_model, base_url=model_manager.base_url):
                vision_model = None
        except Exception:
            vision_model = None
    if not vision_model and not _eye_video:
        try:
            _hint = ' / '.join(installed_vision_models(base_url=model_manager.base_url)[:4])
        except Exception:
            _hint = ''
        yield ("error", f"当前本地模型「{_cur_name}」不支持看图（视频逐帧分析需要）。"
                        f"请到 设置 → 模型中心 自己换一个能看图的本地模型（已装可选：{_hint or '还没有'}），或切到外脑模式。")
        return

    frame_descriptions = []

    for i, frame_path in enumerate(frames):
        try:
            yield ("thinking", f"正在分析第 {i + 1}/{len(frames)} 帧...")

            # 构建帧分析请求
            frame_prompt = "请简洁描述这一帧画面中的内容（人物、场景、动作、文字等），50字以内。"

            # ★ #1267（2026-10-29）：主模型=HarvenEye → Eye 视觉通道逐帧分析
            from src.core import eye_client as _eyecli
            if _eyecli.is_selected():
                import base64 as _b64m
                with open(frame_path, 'rb') as _f:
                    _fb64 = _b64m.b64encode(_f.read()).decode()
                _ok, _ans = _eyecli.vision_chat(frame_prompt, [_fb64], timeout=120.0,
                                                system=system_prompt or '')
                if not _ok:
                    raise RuntimeError(_ans)
                description = str(_ans).strip()
            else:
                message = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": frame_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"file://{frame_path}"}
                        }
                    ]
                }

                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append(message)

                response = model_manager.client.chat.completions.create(
                    model=vision_model,
                    messages=messages,
                    stream=False,
                    max_tokens=200,
                )

                description = response.choices[0].message.content.strip()
            frame_descriptions.append({
                "frame": i + 1,
                "time_sec": round(i * max(1, info["duration_sec"] / len(frames)), 1),
                "description": description,
            })

        except Exception as e:
            logger.error(f"[VideoProcessor] 帧 {i + 1} 分析失败: {e}")
            frame_descriptions.append({
                "frame": i + 1,
                "time_sec": round(i * max(1, info["duration_sec"] / len(frames)), 1),
                "description": f"[分析失败]",
            })

    # 清理临时帧文件
    if frames:
        try:
            frame_dir = os.path.dirname(frames[0])
            shutil.rmtree(frame_dir, ignore_errors=True)
        except Exception as _e: logger.debug(f"[video_processor] 忽略: {_e}")

    # 4. 整合分析结果
    yield ("thinking", "正在整合分析结果...")

    frames_text = "\n".join(
        f"[{fd['time_sec']:.1f}秒 第{fd['frame']}帧]: {fd['description']}"
        for fd in frame_descriptions
    )

    integration_prompt = f"""以下是一个视频的关键帧描述（共{len(frame_descriptions)}帧，视频总时长{info['duration_sec']:.1f}秒）：

{frames_text}

请基于以上帧描述，回答用户的问题：{text or '请总结这个视频的内容'}

要求：
1. 综合所有帧的信息，给出连贯的回答
2. 描述视频的主要场景、人物动作和情节变化
3. 如果用户问了具体问题，针对问题回答
4. 回答控制在200字以内"""

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": integration_prompt})

    try:
        response = model_manager.client.chat.completions.create(
            model=model_manager.model,  # 用主模型做最终整合
            messages=messages,
            stream=stream,
            max_tokens=2048,
        )

        if stream:
            full_text = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    piece = chunk.choices[0].delta.content
                    full_text += piece
                    yield ("text", piece)
            yield ("done", full_text)
        else:
            result = response.choices[0].message.content
            yield ("done", result)
    except Exception as e:
        yield ("error", f"视频分析失败: {e}")
