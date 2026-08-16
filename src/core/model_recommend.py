"""
model_recommend.py - 模型推荐引擎 v2.0
从 model_catalog.json 读取 40+ 模型，根据硬件配置推荐
"""
import logging
from typing import List, Dict, Optional
from src.core.model_catalog import get_all_models, get_by_category, get_by_hardware, recommend_by_user_need

logger = logging.getLogger(__name__)


def recommend_by_hardware(vram_gb: float, ram_total_gb: float,
                           category: str = None, limit: int = 8) -> List[Dict]:
    """根据硬件推荐模型"""
    return get_by_hardware(vram_gb, ram_total_gb, category)[:limit]


def get_recommended_models(vram_gb: float, ram_gb: float) -> dict:
    """获取分类推荐（前端展示用）"""
    cats = {
        "llm": "💬 对话模型",
        "image": "🎨 图片生成",
        "music": "🎵 音乐生成",
        "video": "🎬 视频生成",
        "multimodal": "👁️ 多模态理解",
        "embedding": "🔍 向量嵌入"
    }
    result = {}
    for cat_id, cat_name in cats.items():
        models = get_by_hardware(vram_gb, ram_gb, cat_id)[:3]
        if models:
            result[cat_name] = models
    return result
