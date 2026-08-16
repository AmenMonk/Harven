# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
图片识别模块
通过 Ollama 的多模态模型识别图片内容
"""
import logging
logger = logging.getLogger(__name__)
import base64
import mimetypes
from pathlib import Path


# 图片格式白名单
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".ico"}
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB


def is_image_file(file_path):
    """判断文件是否为图片"""
    path = Path(file_path)
    return path.suffix.lower() in IMAGE_EXTENSIONS


def image_to_base64(file_path):
    """
    将图片文件转为 base64 编码
    
    Args:
        file_path: 图片文件路径
    
    Returns:
        (str, str): (base64编码, mime类型)
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {file_path}")
    
    size = path.stat().st_size
    if size > MAX_IMAGE_SIZE:
        raise ValueError(f"图片太大({size/1024/1024:.1f}MB)，最大支持{MAX_IMAGE_SIZE/1024/1024:.0f}MB")
    
    mime_type = mimetypes.guess_type(str(path))[0] or "image/png"
    
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    
    return b64, mime_type


def build_image_message(text, image_paths):
    """
    构建包含图片的多模态消息
    
    Args:
        text: 用户的文字描述（可选）
        image_paths: 图片路径列表
    
    Returns:
        dict: 符合 OpenAI API 格式的消息
    """
    content = []
    
    # 文字部分
    if text and text.strip():
        content.append({
            "type": "text",
            "text": text.strip()
        })
    else:
        content.append({
            "type": "text",
            "text": "请描述这张图片的内容。"
        })
    
    # 图片部分
    for img_path in image_paths:
        b64, mime = image_to_base64(img_path)
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{b64}"
            }
        })
    
    return {"role": "user", "content": content}


def analyze_image(model_manager, text, image_paths, system_prompt=None, stream=True):
    """
    使用多模态模型分析图片
    
    Args:
        model_manager: ModelManager 实例
        text: 用户的问题/描述
        image_paths: 图片路径列表
        system_prompt: 系统提示词
        stream: 是否流式输出
    
    Returns:
        stream=True: 生成器 (type, content)
        stream=False: 完整回复文本
    """
    message = build_image_message(text, image_paths)
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append(message)
    
    # 使用视觉模型
    available = get_available_models(model_manager.base_url)
    vision_model = None
    for name in available:
        if any(v in name.lower() for v in ["llava", "minicpm-v", "qwen2-vl", "bakllava", "moondream"]):
            vision_model = name
            break
    
    if not vision_model:
        # ★ 2026-07-06: 没有视觉模型 → 返回提示，不要用默认模型（会报错）
        yield ("text", "⚠️ 当前没有可用的视觉模型。\n\n**看图需要视觉模型支持**：\n· 本地：请安装 `minicpm-v:8b`（推荐，`ollama pull minicpm-v`）\n· 外脑：切换到🤖外脑模式（如GPT-4o/Gemini支持看图）\n\n安装后重新上传图片即可分析。")
        yield ("done", "")
        return

    logger.info(f"[ImageProcessor] 使用视觉模型: {vision_model}")
    
    response = model_manager.client.chat.completions.create(
        model=vision_model,
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


def get_available_models(base_url="http://localhost:11434"):
    """获取已安装的模型列表"""
    import requests
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        if resp.status_code == 200:
            return [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as _e: logger.debug(f"[image_processor] 忽略: {_e}")
    return []
