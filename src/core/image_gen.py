# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
图片/视频生成模块 — Harven AI
支持多种生成能力：文本生成图片、文本生成视频、提示词翻译
后端：腾讯混元 / DALL-E / Stable Diffusion 兼容 API
"""
import json
import base64
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
GEN_DIR = PROJECT_ROOT / 'data' / 'generated'
GEN_DIR.mkdir(parents=True, exist_ok=True)

# ─── 意图检测 ───
IMAGE_GEN_PATTERNS = [
    "画", "生成.*图", "画.*图", "画个", "画一张", "生成图片", "生成图像",
    "生成.*插画", "插图", "配图", "做.*海报", "做.*图", "弄.*图",
    "generate.*image", "draw", "make.*picture", "create.*image",
    "帮我画", "帮我生成", "帮画", "来张图", "做个图",
]

VIDEO_GEN_PATTERNS = [
    "生成.*视频", "生成.*动画", "做.*视频", "做.*动画",
    "generate.*video", "create.*video", "make.*video",
]


def detect_image_gen_intent(user_message: str) -> bool:
    """检测是否为图片生成意图（排除音乐/视频请求）"""
    import re
    msg = user_message.lower()
    # 先排除：音乐、视频、歌曲请求
    music_video_exclude = [
        r'音乐', r'歌曲', r'作曲', r'旋律', r'编曲', r'写.*歌', r'做.*曲', r'做.*歌',
        r'国风', r'古风', r'民乐', r'摇滚', r'爵士', r'流行', r'古典', r'乡村',
        r'rap', r'hip.?hop', r'bgm', r'背景音乐', r'电子音乐',
        r'制作.*视频', r'生成.*视频', r'视频.*生成',
    ]
    for p in music_video_exclude:
        if re.search(p, msg):
            return False
    for p in IMAGE_GEN_PATTERNS:
        if re.search(p, msg):
            return True
    return False


def detect_video_gen_intent(user_message: str) -> bool:
    """检测是否为视频生成意图"""
    import re
    msg = user_message.lower()
    for p in VIDEO_GEN_PATTERNS:
        if re.search(p, msg):
            return True
    return False


def extract_gen_prompt(user_message: str) -> str:
    """从用户消息中提取生成描述"""
    import re
    # 移除前置指令词
    cleaned = re.sub(
        r'(帮我|请|帮忙|来一张|生成一张|画一张|画个|画|做一张|做|请帮我生成)',
        '', user_message
    )
    # 移除结尾的"图""图片""图像"
    cleaned = re.sub(r'(的)?(图?片?|图像|插画|海报)$', '', cleaned.strip())
    return cleaned.strip() or user_message.strip()


# ─── 腾讯混元文生图 ───
class HunyuanImageGen:
    """腾讯混元文生图 API（需密钥）"""
    
    def __init__(self, secret_id: str = "", secret_key: str = ""):
        self.secret_id = secret_id
        self.secret_key = secret_key
    
    def generate(self, prompt: str, style: str = "201", resolution: str = "1024:1024") -> Dict:
        """调用混元文生图"""
        if not self.secret_id or not self.secret_key:
            return {"success": False, "error": "未配置腾讯云密钥"}
        try:
            import hashlib
            import hmac
            import requests
            
            service = "aiart"
            host = "aiart.tencentcloudapi.com"
            endpoint = f"https://{host}"
            action = "TextToImage"
            version = "2022-12-29"
            region = "ap-guangzhou"
            timestamp = int(time.time())
            date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
            
            payload = {
                "Prompt": prompt,
                "Styles": [style],
                "Resolution": resolution,
                "LogoAdd": 0,
            }
            
            params = {
                "Action": action,
                "Version": version,
                "Region": region,
                "Timestamp": timestamp,
                **payload,
            }
            
            # 签名（简化版）
            headers = {
                "Content-Type": "application/json",
                "X-TC-Action": action,
                "X-TC-Version": version,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Region": region,
            }
            
            resp = requests.post(endpoint, headers=headers, json=params, timeout=60)
            data = resp.json()
            
            if "Response" in data and "ResultImage" in data["Response"]:
                img_data = base64.b64decode(data["Response"]["ResultImage"])
                filename = f"gen_{int(time.time())}_{hash(prompt) % 10000}.png"
                filepath = GEN_DIR / filename
                filepath.write_bytes(img_data)
                return {
                    "success": True,
                    "filepath": str(filepath),
                    "filename": filename,
                    "provider": "hunyuan",
                }
            return {"success": False, "error": data.get("Response", {}).get("Error", {}).get("Message", "未知错误")}
        except Exception as e:
            return {"success": False, "error": f"混元文生图失败: {e}"}


# ─── OpenAI DALL-E 兼容 API ───
class DallEImageGen:
    """OpenAI DALL-E 兼容文生图（支持任何兼容 base_url）"""
    
    def __init__(self, api_key: str = "", base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
    
    def generate(self, prompt: str, size: str = "1024x1024", model: str = "dall-e-3") -> Dict:
        if not self.api_key:
            return {"success": False, "error": "未配置 API Key"}
        try:
            import requests
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": size,
            }
            resp = requests.post(
                f"{self.base_url}/images/generations",
                headers=headers, json=payload, timeout=120
            )
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                url = data["data"][0].get("url")
                if url:
                    # 下载图片到本地
                    img_resp = requests.get(url, timeout=30)
                    filename = f"gen_{int(time.time())}_{hash(prompt) % 10000}.png"
                    filepath = GEN_DIR / filename
                    filepath.write_bytes(img_resp.content)
                    return {
                        "success": True,
                        "filepath": str(filepath),
                        "filename": filename,
                        "url": url,
                        "provider": "dalle",
                    }
            return {"success": False, "error": data.get("error", {}).get("message", str(data))}
        except Exception as e:
            return {"success": False, "error": f"DALL-E 生成失败: {e}"}


# ─── 本地 SD WebUI API ───
class StableDiffusionGen:
    """本地 Stable Diffusion WebUI API"""
    
    def __init__(self, base_url: str = "http://localhost:7860"):
        self.base_url = base_url.rstrip('/')
    
    def generate(self, prompt: str, negative_prompt: str = "", 
                 width: int = 1024, height: int = 1024, steps: int = 20) -> Dict:
        try:
            import requests
            payload = {
                "prompt": prompt,
                "negative_prompt": negative_prompt or "nsfw, lowres, bad anatomy",
                "width": width,
                "height": height,
                "steps": steps,
            }
            resp = requests.post(
                f"{self.base_url}/sdapi/v1/txt2img",
                json=payload, timeout=180
            )
            data = resp.json()
            if "images" in data and data["images"]:
                img_data = base64.b64decode(data["images"][0])
                filename = f"gen_{int(time.time())}_{hash(prompt) % 10000}.png"
                filepath = GEN_DIR / filename
                filepath.write_bytes(img_data)
                return {
                    "success": True,
                    "filepath": str(filepath),
                    "filename": filename,
                    "provider": "stable_diffusion",
                }
            return {"success": False, "error": str(data)}
        except Exception as e:
            return {"success": False, "error": f"Stable Diffusion 生成失败: {e}"}


# ─── 统一生成器 ───
class ImageGenerator:
    """统一图片生成入口，自动选择可用后端"""
    
    def __init__(self):
        self._load_config()
    
    def _load_config(self):
        """从配置文件加载 API 密钥"""
        config_path = PROJECT_ROOT / 'config.yaml'
        try:
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
            img_cfg = cfg.get('image_gen', {})
            self.default_provider = img_cfg.get('provider', 'auto')
            self.hunyuan_id = img_cfg.get('hunyuan', {}).get('secret_id', '')
            self.hunyuan_key = img_cfg.get('hunyuan', {}).get('secret_key', '')
            self.dalle_key = img_cfg.get('dalle', {}).get('api_key', '')
            self.dalle_url = img_cfg.get('dalle', {}).get('base_url', 'https://api.openai.com/v1')
            self.sd_url = img_cfg.get('stable_diffusion', {}).get('base_url', 'http://localhost:7860')
        except Exception:
            self.default_provider = 'auto'
            self.hunyuan_id = self.hunyuan_key = ''
            self.dalle_key = ''
            self.dalle_url = 'https://api.openai.com/v1'
            self.sd_url = 'http://localhost:7860'
    
    def generate(self, prompt: str, provider: str = None, **kwargs) -> Dict:
        """
        生成图片
        Returns: {"success": bool, "filepath": str, "filename": str, "error": str}
        """
        provider = provider or self.default_provider
        
        if provider == 'auto':
            # 自动选择：SD本地 > DALL-E > 混元 > 无可用
            return self._auto_generate(prompt, **kwargs)
        elif provider == 'hunyuan':
            gen = HunyuanImageGen(self.hunyuan_id, self.hunyuan_key)
            return gen.generate(prompt, **kwargs)
        elif provider in ('dalle', 'openai', 'dall-e'):
            gen = DallEImageGen(self.dalle_key, self.dalle_url)
            return gen.generate(prompt, **kwargs)
        elif provider in ('sd', 'stable_diffusion'):
            gen = StableDiffusionGen(self.sd_url)
            return gen.generate(prompt, **kwargs)
        else:
            return {"success": False, "error": f"不支持的生成器: {provider}"}
    
    def _auto_generate(self, prompt: str, **kwargs) -> Dict:
        """自动选择可用后端（双模型策略：本地优先 → 外接API → 兼容后备）"""
        failed_backends = []  # 收集所有失败原因

        # ── 检查可用后端 ──
        available_backends = self._check_available_backends()

        if not available_backends:
            return {
                "success": False,
                "error": self._build_no_backend_message(),
            }

        # ── 1. 本地 Stable Diffusion（最快，无需API key）──
        if 'sd' in available_backends:
            try:
                result = StableDiffusionGen(self.sd_url).generate(prompt)
                if result.get("success"):
                    return result
                failed_backends.append(f"• Stable Diffusion 本地服务：{result.get('error', '连接失败')}")
            except Exception as e:
                failed_backends.append(f"• Stable Diffusion 本地服务：{str(e)}")

        # ── 2. DALL-E / OpenAI 兼容 API ──
        if 'dalle' in available_backends:
            try:
                result = DallEImageGen(self.dalle_key, self.dalle_url).generate(prompt, **kwargs)
                if result.get("success"):
                    return result
                failed_backends.append(f"• DALL-E / OpenAI API：{result.get('error', '生成失败')}")
            except Exception as e:
                failed_backends.append(f"• DALL-E / OpenAI API：{str(e)}")

        # ── 3. 腾讯混元文生图 ──
        if 'hunyuan' in available_backends:
            try:
                result = HunyuanImageGen(self.hunyuan_id, self.hunyuan_key).generate(prompt)
                if result.get("success"):
                    return result
                failed_backends.append(f"• 腾讯混元文生图：{result.get('error', '生成失败')}")
            except Exception as e:
                failed_backends.append(f"• 腾讯混元文生图：{str(e)}")

        # ── 全部失败 ──
        error_msg = "所有已配置的图片生成后端均不可用：\n"
        error_msg += "\n".join(failed_backends)
        error_msg += "\n\n💡 你可以在 设置 → 模型管理 → 图片生成 中配置后端。"
        return {"success": False, "error": error_msg}

    def _check_available_backends(self) -> list:
        """检测当前可用的图片生成后端列表"""
        available = []

        # Stable Diffusion 本地：只要配置了URL就认为可用（连接性在生成时检测）
        if self.sd_url and self.sd_url.strip():
            available.append('sd')

        # DALL-E / OpenAI：需要API key
        if self.dalle_key and self.dalle_key.strip():
            available.append('dalle')

        # 混元：需要 secret_id + secret_key
        if self.hunyuan_id and self.hunyuan_id.strip() and self.hunyuan_key and self.hunyuan_key.strip():
            available.append('hunyuan')

        return available

    def _build_no_backend_message(self) -> str:
        """构建'没有可用后端'的友好提示"""
        return (
            "当前没有配置任何图片生成后端。Harven AI 支持以下方式：\n\n"
            "🖥️ **方案一：本地 Stable Diffusion**（免费，需安装）\n"
            "   下载 SD WebUI (https://github.com/AUTOMATIC1111/stable-diffusion-webui)\n"
            "   启动后在 设置 → 图片生成 中填入地址（默认 http://localhost:7860）\n\n"
            "☁️ **方案二：API 云端生成**（需付费/注册）\n"
            "   • DALL-E (OpenAI)：在 config.yaml → image_gen → dalle → api_key 填入密钥\n"
            "   • 腾讯混元文生图：在 config.yaml → image_gen → hunyuan 填入 secret_id/key\n\n"
            "⚡ **方案三：使用 WorkBuddy 内置生成**\n"
            "   如果你在 WorkBuddy 中使用，可以直接说「帮我画一张...」\n"
            "   我会尝试调用 WorkBuddy 的 ImageGen 能力。\n\n"
            "📝 配置位置：侧边栏 ⚙️ 设置 → 模型管理 → 图片生成"
        )
    
    def format_result_for_chat(self, result: Dict) -> str:
        """格式化生成结果为聊天消息"""
        if not result.get("success"):
            error = result.get('error', '未知错误')
            # 如果错误信息已经包含完整的指引，直接返回
            if "方案一" in error or "方案二" in error or "方案三" in error:
                return f"🎨 **图片生成**\n\n❌ {error}"
            return f"🎨 **图片生成**\n\n❌ 生成失败：{error}\n\n💡 请在 设置 → 模型管理 → 图片生成 中检查后端配置。"
        
        filename = result.get("filename", "unknown.png")
        provider = result.get("provider", "unknown")
        filepath = result.get("filepath", "")
        
        provider_names = {
            "stable_diffusion": "本地 Stable Diffusion 🖥️",
            "dalle": "DALL-E ☁️",
            "hunyuan": "腾讯混元 ☁️",
        }
        provider_display = provider_names.get(provider, provider)
        
        return f"""🎨 **已生成图片** — {provider_display}

![生成图片](/api/media/generated/{filename})

> 📁 已保存至: `{filepath}`
"""


# ─── 单例 ───
_generator: Optional[ImageGenerator] = None
_generator_lock = threading.Lock()

def get_image_generator() -> ImageGenerator:
    global _generator
    if _generator is None:
        with _generator_lock:
            if _generator is None:
                _generator = ImageGenerator()
    return _generator


def process_image_gen_message(user_message: str, provider: str = None) -> Optional[Dict]:
    """
    处理图片生成请求
    返回 None 表示不是生成意图
    返回 Dict 包含 success/filepath/error
    """
    if not detect_image_gen_intent(user_message):
        return None
    
    prompt = extract_gen_prompt(user_message)
    if not prompt or len(prompt) < 2:
        return {"success": False, "error": "请描述你想生成什么样的图片（如：生成一张山水画的图片）"}
    
    gen = get_image_generator()
    return gen.generate(prompt, provider=provider)


# ─── 媒体文件路由支持 ───
MEDIA_DIR = PROJECT_ROOT / 'data' / 'media'
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
GEN_DIR.mkdir(parents=True, exist_ok=True)
