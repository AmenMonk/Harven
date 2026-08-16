"""
Harven AI — 模型目录加载器
从 model_catalog.json 加载 40+ 模型，支持按类型/硬件过滤
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).parent.parent.parent / "data" / "model_catalog.json"

_cache = None


def load_catalog() -> dict:
    """加载模型目录（带缓存）"""
    global _cache
    if _cache is not None:
        return _cache
    try:
        if CATALOG_PATH.exists():
            _cache = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            logger.info(f"[Catalog] 已加载 {_cache['_meta']['total_models']} 个模型")
            return _cache
    except Exception as e:
        logger.error(f"[Catalog] 加载失败: {e}")
    _cache = {"_meta": {"total_models": 0}, "models": []}
    return _cache


def get_all_models() -> List[Dict]:
    """获取所有模型"""
    return load_catalog().get("models", [])


def get_by_category(category: str) -> List[Dict]:
    """按类别获取模型 (llm/music/image/video/multimodal/embedding)"""
    return [m for m in get_all_models() if m.get("category") == category]


def get_by_hardware(vram_gb: float, ram_gb: float, category: str = None) -> List[Dict]:
    """根据硬件配置过滤可运行的模型"""
    models = get_all_models()
    if category:
        models = [m for m in models if m.get("category") == category]

    compatible = []
    for m in models:
        req_vram = m.get("vram_gb", 999)
        req_ram = m.get("ram_gb", 999)
        if vram_gb >= req_vram and ram_gb >= req_ram:
            compatible.append(m)

    # 按优先级排序
    compatible.sort(key=lambda m: (m.get("priority", 99), m.get("size_gb", 999)))
    return compatible


def recommend_by_user_need(hardware_spec: dict, user_text: str) -> dict:
    """
    根据用户需求文本和硬件配置，返回双路径推荐结果
    
    Returns:
        {
            "detected_category": "music/image/video/llm",
            "path_a": [...],  # 最佳匹配（硬件够+需求吻合）
            "path_b": [...],  # 妥协方案（硬件不够时的降级建议）
            "warnings": []    # 硬件不足警告
        }
    """
    import re

    text_lower = user_text.lower()

    # 识别需求类型
    cat_map = {
        "music": [r"音乐", r"作曲", r"编曲", r"旋律", r"音乐生成", r"做音乐", r"写歌", r"BGM", r"配乐", r"audio", r"music", r"melody", r"sound"],
        "image": [r"图片", r"绘图", r"画图", r"图像", r"绘画", r"生成图片", r"做图", r"photos?", r"img", r"illustration", r"portrait", r"landscape"],
        "video": [r"视频", r"动画", r"生成视频", r"做视频", r"录像", r"video", r"animation", r"clip"],
        "multimodal": [r"看图", r"识图", r"图片理解", r"照片分析", r"OCR", r"识别图中", r"vision", r"visual"],
        "embedding": [r"知识库", r"检索", r"搜索", r"向量", r"embedding", r"retrieval"],
        "llm": [r"聊天", r"对话", r"写代码", r"翻译", r"写作", r"文案", r"问答", r"分析", r"总结"],
    }

    detected = "llm"  # 默认
    for cat, patterns in cat_map.items():
        for p in patterns:
            if any(p in user_text or p.lower() in text_lower for p in [p]):
                detected = cat
                break

    vram = hardware_spec.get("gpu_vram_gb", 0)
    ram = hardware_spec.get("ram_total_gb", 8)

    # 路径 A：完全匹配
    path_a = get_by_hardware(vram, ram, detected)
    path_a = [m for m in path_a if m.get("quality") in ("S+", "S", "A")][:5]

    # 路径 B：降级方案
    path_b = []
    warnings = []
    if not path_a:
        # 推荐低配版
        path_b = [m for m in get_by_category(detected) if m.get("quality") in ("B",) and vram >= m.get("vram_gb", 999) * 0.5]
        if not path_b:
            path_b = get_by_category(detected)[:3]
        warnings.append(f"⚠️ 你的配置（VRAM: {vram:.0f}GB, RAM: {ram:.0f}GB）不足以运行 {detected} 类推荐模型")
        for m in get_by_category(detected):
            if vram < m.get("vram_gb", 999):
                warnings.append(f"  · {m['name']} 需要 {m['vram_gb']}GB VRAM（你只有 {vram:.0f}GB）")

    return {
        "detected_category": detected,
        "path_a": path_a[:5],
        "path_b": path_b[:3],
        "warnings": warnings[:5],
        "all_categories": list(set(m.get("category") for m in get_all_models()))
    }


def search_models(query: str) -> List[Dict]:
    """按名称/标签/描述搜索模型"""
    q = query.lower()
    results = []
    for m in get_all_models():
        score = 0
        if q in m.get("name", "").lower(): score += 10
        if q in m.get("description", "").lower(): score += 5
        for tag in m.get("tags", []):
            if q in tag.lower(): score += 3
        if score > 0:
            results.append((score, m))
    results.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in results[:10]]
