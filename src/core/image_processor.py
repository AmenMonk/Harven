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

# ★ 2026-09-26 外脑看图报错转译关键词（翻译官不做裁判：报错说人话+给出路）
_VISION_ERROR_KEYS = ('image', 'vision', 'multimodal', 'multi-modal', 'content type',
                      'unsupported', 'invalid content', 'media', 'not supported')


def translate_vision_error(raw: str, ext_label: str, ext_name: str) -> str:
    """外脑看图报错转译：技术报错→大白话+两条出路（电脑端）"""
    _raw = (raw or '').strip()
    low = _raw.lower()
    if any(k in low for k in _VISION_ERROR_KEYS):
        return (f"⚠️ {ext_label}（{ext_name}）没能看这张图——这家外脑大概率不收图片。\n\n"
                "**两个选择**：\n"
                "1. 到 设置→模型管理 换一个能看图的外脑（Gemini / GLM-5.3-Flash / 豆包视觉 等）\n"
                "2. 点对话区 🏠本地 按钮切回本地模式（装了看图模型即可离线识别）\n\n"
                f"（技术详情：{_raw[:150]}）")
    return f"❌ 外脑看图失败：{_raw[:200]}"


def translate_mobile_vision_error(raw: str, ext_name: str) -> str:
    """外脑看图报错转译：手机端版（返回给手机的人话）"""
    _raw = (raw or '').strip()
    low = _raw.lower()
    if any(k in low for k in _VISION_ERROR_KEYS):
        return (f'⚠️ 当前外脑（{ext_name}）没能看这张照片——它大概率不收图片。\n'
                '两个办法：① 电脑端设置里换一个能看图的外脑（Gemini / GLM-5.3-Flash 等）；'
                '② 切回 🏠本地模式 用本地看图。')
    return f'❌ 外脑看图失败：{_raw[:200]}'


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


def normalize_image_for_vision(path, max_side=1280, max_bytes=1024 * 1024):
    """★ 2026-09-23 实测实锤：相机直出大图（约4MB+）→ Ollama 静默丢图，
    minicpm-v 表现为「抱歉，我无法提供图片中的单词」（图没到模型）。
    实测阈值：1.5MB 正常 / 6.7MB 必挂。看图前统一归一化：
    超过 max_bytes 或最长边超过 max_side → 缩边 + JPEG 重编码。
    返回可用路径（新图由调用方负责删除；不超限原样返回）。"""
    out_path = None
    try:
        from PIL import Image
        p = Path(path)
        need = False
        if p.stat().st_size > max_bytes:
            need = True
        else:
            with Image.open(p) as im:
                if max(im.size) > max_side * 2:
                    need = True
        if not need:
            return path
        with Image.open(p) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            scale = max(w, h) / float(max_side)
            if scale > 1:
                img = img.resize((max(1, int(w / scale)), max(1, int(h / scale))), Image.LANCZOS)
            out_path = str(p.with_name(p.stem + "_vision.jpg"))
            img.save(out_path, "JPEG", quality=85)
        logger.info(f"[ImageProcessor] 大图归一化 {path} -> {out_path} "
                    f"({p.stat().st_size//1024}KB -> {Path(out_path).stat().st_size//1024}KB)")
        return out_path
    except Exception as e:
        logger.warning(f"[ImageProcessor] 图片归一化失败，用原图: {e}")
        return path


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

    # ★ #1267（2026-10-29）：主模型=HarvenEye → Eye 视觉通道（底座 qwen3-vl 通用视觉模型，选谁给谁）
    from src.core import eye_client as _eyecli
    if _eyecli.is_selected():
        _imgs = []
        for _p in (image_paths or []):
            _b64, _mime = image_to_base64(_p)
            _imgs.append(_b64)
        _sys = system_prompt or "你是哈文，本地AI助手。用户发来了图片，请用中文、口语化地回答关于图片的问题。"
        _ok, _ans = _eyecli.vision_chat(text, _imgs, timeout=150.0, system=_sys)
        if stream:
            if _ok:
                yield ("text", _ans)
                yield ("done", "")
            else:
                yield ("text", f"⚠️ {_ans}")
                yield ("done", "")
            return
        return (_ans if _ok else f"⚠️ {_ans}")

    # ★ #1204（2026-10-25 开发者死命令）：只用用户选的主模型，严禁扫描代选
    vision_model = (getattr(model_manager, 'model', '') or '').strip()
    _base = getattr(model_manager, 'base_url', 'http://localhost:11434')
    try:
        if not vision_model or not model_supports_vision(vision_model, base_url=_base):
            vision_model = None
    except Exception:
        vision_model = None

    if not vision_model:
        # ★ #1204: 用户选的模型不能看图 → 如实报错；★ #1356: 升级为硬件分流引导卡
        _cur = (getattr(model_manager, 'model', '') or '').strip() or '（未选）'
        try:
            _card = vision_guide_card(_base)
        except Exception:
            _card = ''
        if _card:
            yield ("text", f"⚠️ 当前本地模型「{_cur}」不支持看图。\n\n{_card}")
        else:
            try:
                _hint = ' / '.join(installed_vision_models(_base)[:4])
            except Exception:
                _hint = ''
            yield ("text", f"⚠️ 当前本地模型不支持看图。\n\n到 **设置 → 模型中心** 自己换一个能看图的本地模型"
                           f"（已装可选：{_hint or '还没有，需要先安装一个'}），或切到🤖外脑模式（选支持看图的外脑）。\n\n换好后重新上传图片即可。")
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


# ── ★ 2026-09-24 定版拍板：视觉能力判断升级为 Ollama 官方 capabilities 标记 ──
# 旧方案靠模型名字关键词（minicpm-v/qwen2-vl/llava…），用户新装的视觉模型认不出。
# 现在优先读 /api/show 的官方 capabilities（含 "vision"），关键词只作查询失败时的兜底。

_CAPS_CACHE = {}  # {base_url::model_name: set|None}，进程级缓存


def model_capabilities(model_name, base_url="http://localhost:11434"):
    """读 Ollama 官方能力标记（completion/vision/tools/thinking…）。
    返回 set；查询失败返回 None（调用方自行兜底）。"""
    if not model_name:
        return None
    key = f"{base_url}::{model_name}"
    if key in _CAPS_CACHE:
        return _CAPS_CACHE[key]
    import requests
    try:
        resp = requests.post(f"{base_url}/api/show", json={"model": model_name}, timeout=6)
        if resp.status_code == 200:
            caps = set(resp.json().get("capabilities", []) or [])
            _CAPS_CACHE[key] = caps
            return caps
    except Exception as _e:
        logger.debug(f"[image_processor] capabilities 查询失败 {model_name}: {_e}")
    _CAPS_CACHE[key] = None
    return None


# 关键词兜底表（仅当 Ollama 旧版无 capabilities / 查询失败时使用）
_VISION_KEYWORDS = ("minicpm-v", "qwen2-vl", "qwen2.5-vl", "qwen2.5vl", "qwen3-vl",
                    "llava", "bakllava", "moondream", "internvl", "vl", "-v-")


def model_supports_vision(model_name, base_url="http://localhost:11434"):
    """官方 vision 标记优先；查不到退关键词兜底。"""
    if not model_name:
        return False
    caps = model_capabilities(model_name, base_url)
    if caps is not None:
        return "vision" in caps
    low = model_name.lower()
    return any(v in low for v in _VISION_KEYWORDS)


def _same_model(a, b):
    """宽松比较两个模型名：qwen2.5vl / qwen2.5vl:7b 视为同一个"""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a.split(":")[0] == b.split(":")[0]


def find_vision_model(base_url="http://localhost:11434", prefer_current=None):
    """★ #1204（2026-10-25 开发者死命令）：本地看图只准用用户选的模型，严禁代选。
    只校验 prefer_current（当前主模型）是否支持看图：支持 → 返回它；
    不支持/没传 → None（调用方如实报错，提醒用户自己换模型，主动权在用户）。
    ★ 旧版会扫描已装模型列表自动挑视觉模型 = 代选，违反「选谁给谁」铁律，已废除。"""
    cur = prefer_current.strip() if isinstance(prefer_current, str) else ""
    if not cur:
        return None
    available = get_available_models(base_url)
    hit = next((n for n in available if _same_model(n, cur)), None)
    if hit and model_supports_vision(hit, base_url):
        return hit
    return None


def installed_vision_models(base_url="http://localhost:11434"):
    """★ #1204 信息告知用：列出已装的视觉模型（仅用于报错文案提醒用户可换谁，绝不代选）"""
    return [n for n in get_available_models(base_url) if model_supports_vision(n, base_url)]


def vision_readiness(base_url="http://localhost:11434"):
    """★ #1356（2026-11-01 定版拍板）：看图三路就绪度体检。
    发图预检引导卡（B）+ 管家首启检测（A）共用，一次体检处处用。
    三条路：① Eye/理解槽视觉通道 ② 本地 Ollama 视觉模型 ③ 外脑（配了 key 即可能看图）。
    铁律 0：只报状态+给选项，绝不代选代装。任何一环体检失败都不炸（降级为未知）。"""
    out = {'local_models': [], 'understand_channel': False, 'eye_base_ready': False,
           'external_vision': False, 'vram_gb': 0.0, 'fit': []}
    try:
        out['local_models'] = installed_vision_models(base_url)
    except Exception:
        pass
    try:
        from src.core import vision_link as _vl
        out['understand_channel'] = bool(_vl.resolve_understand_channel())
    except Exception:
        pass
    try:
        from src.vision import installer as _vi
        out['eye_base_ready'] = bool(_vi.engine_status().get('ready'))
    except Exception:
        pass
    try:
        from src.core.multi_model_manager import get_multi_model_manager as _gmm
        _cfg = _gmm().config.get('models', {})
        out['external_vision'] = any(
            (e or {}).get('enabled') and (e or {}).get('api_key')
            for pid, e in _cfg.items() if pid != 'ollama')
    except Exception:
        pass
    try:
        from src.utils.hardware import detect_hardware
        out['vram_gb'] = float(detect_hardware().get('vram_gb') or 0)
    except Exception:
        pass
    if out['vram_gb'] >= 4:
        try:
            from src.vision.catalog import fit_list
            out['fit'] = fit_list(out['vram_gb'])[:2]
        except Exception:
            pass
    return out


def vision_guide_card(base_url="http://localhost:11434"):
    """★ #1356 发图预检引导卡：按硬件体检结果分流的话术（替换干巴巴报错）。
    显存够（≥4GB）→ 推本地通用视觉模型档位让用户自选（图片不出门）；
    显存不够 → 只推外脑，绝不忽悠用户装跑不动的模型。选择权永远在用户。"""
    r = vision_readiness(base_url)
    vram = r['vram_gb']
    lines = []
    if r['fit']:
        lines.append(f"按你的显卡（显存 {vram:g}GB），可以装本地视觉模型（图片不出门）：")
        lines.append("1️⃣ 装 HarvenEye 本地视觉模型 → 设置 → 视觉引擎 页选档位安装")
        for e in r['fit']:
            _d = (e.get('desc') or '').split('，')[0].split('；')[0]
            lines.append(f"   · {e['label']} · 约{e['size_gb']:g}GB · {_d[:40]}")
        if r['external_vision']:
            lines.append("2️⃣ 零下载方案：切 🤖 外脑模式看图（你已配好外脑，切过去就能用，图片发云端识别）")
        else:
            lines.append("2️⃣ 零下载方案：到 设置 → 模型中心 配一个支持看图的外脑（如智谱GLM），切外脑模式看图")
    else:
        lines.append(f"检测到你的电脑显存有限（{vram:g}GB），装本地视觉模型会吃力，推荐走云端：")
        if r['external_vision']:
            lines.append("✅ 你已配置外脑：切到 🤖 外脑模式，选一个支持看图的外脑，图片发云端识别")
        else:
            lines.append("1️⃣ 到 设置 → 模型中心 配置一个外脑（如智谱GLM），选支持看图的模型——零下载就能看图")
            lines.append("2️⃣ 或换一台显存 ≥4GB 的电脑，在 设置 → 视觉引擎 页装本地视觉模型")
    if r['local_models']:
        lines.append(f"\n📦 已装的本地视觉模型：{' / '.join(r['local_models'][:3])}（到 设置 → 模型中心 选用）")
    lines.append("\n选好后把图片重新发一次即可。")
    return '\n'.join(lines)
