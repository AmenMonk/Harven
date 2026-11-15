# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
数据库路由器 — 三库物理隔离接入层（语义路由版）

自动根据数据敏感度将内容路由到正确的隔离数据库：
- 隐私库(privacy): 主人身份信息、偏好、口令、私密对话
- 过渡库(transient): 临时数据、搜索结果缓存、中间处理结果
- 商用库(commercial): 对外知识、可分享内容

分类策略：语义路由（embeddings 余弦相似度）→ 关键词回退 → 默认 transient
集成到 ConversationEngine 和 Memory 系统中。
"""

import logging
import threading
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# ── 语义路由配置 ────────────────────────────────────────────────────────────

# ★ 2026-09-26 审计（开发者死命令：模型必须用户自选）：不再内定 nomic-embed-text。
# 改为动态解析用户 Ollama 已装列表里的嵌入模型（含 embed/bge 字样），
# 一个都没有 → 语义路由自动禁用，退关键词分类（不下载、不内定任何模型）
_EMBED_MODEL = ""
_EMBED_MODEL_RESOLVED = False
_EMBED_MODEL_LOCK = threading.Lock()


def _resolve_embed_model() -> str:
    """从用户已装模型里找嵌入模型（懒加载+缓存）。返回 '' 表示没有，语义分类禁用。"""
    global _EMBED_MODEL, _EMBED_MODEL_RESOLVED
    if _EMBED_MODEL_RESOLVED:
        return _EMBED_MODEL
    with _EMBED_MODEL_LOCK:
        if _EMBED_MODEL_RESOLVED:
            return _EMBED_MODEL
        try:
            import requests as _r
            resp = _r.get(f"{_OLLAMA_BASE}/api/tags", timeout=3)
            for m in resp.json().get("models", []):
                low = m.get("name", "").lower()
                if "embed" in low or low.startswith("bge"):
                    _EMBED_MODEL = m.get("name", "")
                    break
        except Exception as e:
            logger.debug(f"[db_router] 解析嵌入模型失败: {e}")
        _EMBED_MODEL_RESOLVED = True
        if _EMBED_MODEL:
            logger.info(f"[db_router] 语义路由使用用户已装嵌入模型: {_EMBED_MODEL}")
        else:
            logger.info("[db_router] 用户未装嵌入模型，语义路由禁用（走关键词分类，不内定模型）")
        return _EMBED_MODEL

_OLLAMA_BASE = "http://localhost:11434"

# 三类锚点文本（每类多条，取平均向量作为类别中心）
_CATEGORY_ANCHORS: Dict[str, List[str]] = {
    "privacy": [
        "我的密码是",
        "我的身份证号是",
        "我的银行卡号",
        "我的手机号是",
        "我的家庭住址",
        "我的工资是",
        "不要告诉别人",
        "这是我的私人信息",
        "我的真实姓名是",
        "我的生日是",
        "我的账户口令是",
    ],
    "commercial": [
        "这篇文章可以公开发布",
        "这个教程可以分享到公众号",
        "这是一个公开的技术文档",
        "这个知识可以商用",
        "我可以把它写到博客里",
        "这份资料可以公开分享",
        "发布到官网",
        "开源协议",
        "公开API文档",
    ],
    "transient": [
        "今天天气怎么样",
        "帮我写一段代码",
        "解释一下这个概念",
        "帮我搜索一下",
        "给我一些建议",
        "谢谢你的帮助",
        "你觉得呢",
        "能不能详细说说",
        "有什么区别",
        "怎么实现的",
    ],
}

# 锚点向量缓存 {category: embedding_vector}
_ANCHOR_EMBEDDINGS: Dict[str, List[float]] = {}
_ANCHOR_LOCK = threading.Lock()

# 文本向量缓存（同一进程内避免重复计算）
_TEXT_EMBED_CACHE: Dict[int, List[float]] = {}
_TEXT_EMBED_LOCK = threading.Lock()
_TEXT_EMBED_CACHE_MAX = 500

# 分类结果缓存
_CLASSIFY_CACHE: Dict[int, str] = {}
_CLASSIFY_CACHE_LOCK = threading.Lock()
_CLASSIFY_CACHE_MAX = 200

# 相似度阈值（低于此值判为 transient）
_SIMILARITY_THRESHOLD = 0.25

# 触发语义分类的最小文本长度（字符）
_SEMANTIC_MIN_LENGTH = 15


def _get_embedding(text: str, model: str = None) -> Optional[List[float]]:
    """
    调用 Ollama Embeddings API 获取文本向量。
    ★ 2026-09-26 审计：model 为空时动态解析用户已装嵌入模型；解析不到返回 None（分类退关键词）。
    """
    if model is None:
        model = _resolve_embed_model()
    if not model:
        return None
    try:
        import requests as _r
        resp = _r.post(
            f"{_OLLAMA_BASE}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=30,
        )
        if resp.status_code == 200:
            emb = resp.json().get("embedding")
            if emb:
                return emb
        logger.debug(f"[db_router] Embedding API 返回异常: {resp.status_code}")
    except Exception as e:
        logger.debug(f"[db_router] Embedding 获取失败: {e}")
    return None


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """余弦相似度（纯 Python，无 numpy 依赖）"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    return dot / (norm_a * norm_b) if (norm_a * norm_b) > 0 else 0.0


def _ensure_anchor_embeddings(model: str = None) -> bool:
    """
    懒加载：计算并缓存三个类别的锚点向量。
    返回 True 表示锚点已就绪，可以执行语义分类。
    ★ 2026-09-26 审计：model 为空时动态解析用户已装嵌入模型；解析不到返回 False（禁用语义分类）。
    """
    if model is None:
        model = _resolve_embed_model()
    if not model:
        return False
    global _ANCHOR_EMBEDDINGS
    with _ANCHOR_LOCK:
        if _ANCHOR_EMBEDDINGS:
            return True  # 已计算

        logger.info("[db_router] 正在计算锚点向量（首次使用）...")

        for category, anchors in _CATEGORY_ANCHORS.items():
            embeddings = []
            for anchor in anchors:
                emb = _get_embedding(anchor, model)
                if emb:
                    embeddings.append(emb)

            if not embeddings:
                logger.warning(
                    f"[db_router] 类别「{category}」锚点向量计算失败"
                    f"（Ollama 可能未启动或 {model} 模型未拉取）"
                )
                return False

            # 平均向量
            dim = len(embeddings[0])
            avg = [
                sum(x[i] for x in embeddings) / len(embeddings)
                for i in range(dim)
            ]
            _ANCHOR_EMBEDDINGS[category] = avg
            logger.info(f"[db_router] 锚点向量已缓存: {category}（维度 {dim}）")

        return True


def _ollama_reachable() -> bool:
    """快速探测 Ollama 是否在线"""
    try:
        import requests as _r
        _r.get(f"{_OLLAMA_BASE}/api/tags", timeout=2)
        return True
    except Exception:
        return False


# ── 关键词回退（语义路由不可用时的安全网） ──────────────────────────────────

_PRIVACY_KEYWORDS_FALLBACK = [
    "密码", "口令", "账户", "银行卡", "身份证", "手机号", "地址",
    "真实姓名", "生日", "家庭成员", "住址", "公司", "工资", "薪资",
    "密码是", "账号是", "私密的", "不要告诉别人", "仅限你知道",
    "password", "secret", "private",
]

_COMMERCIAL_KEYWORDS_FALLBACK = [
    "公开", "分享", "发布", "公众号", "博客", "教程", "文档",
    "public", "share", "tutorial", "guide",
]


def _classify_by_keywords(text: str) -> str:
    """关键词回退分类（语义路由不可用时的安全网）"""
    text_lower = text.lower()
    for kw in _PRIVACY_KEYWORDS_FALLBACK:
        if kw in text_lower:
            logger.debug("[db_router] 关键词回退 → privacy")
            return "privacy"
    for kw in _COMMERCIAL_KEYWORDS_FALLBACK:
        if kw in text_lower:
            logger.debug("[db_router] 关键词回退 → commercial")
            return "commercial"
    return "transient"


def classify_sensitivity(text: str, use_llm: bool = True) -> str:
    """
    语义路由：根据文本向量与类别锚点的余弦相似度自动分类。

    策略：
    1. 文本过短（<15字符）→ 直接返回 transient
    2. 尝试语义路由（Ollama embeddings）
    3. 语义不可用 → 关键词回退
    4. 均失败 → 默认 transient

    Args:
        text:     待分类文本
        use_llm:  保留参数兼容性（当前未使用，语义路由不依赖 LLM 生成）

    Returns:
        'privacy' | 'transient' | 'commercial'
    """
    if not text:
        return "transient"

    text = text.strip()

    # 1. 过短文本直接返回 transient（无分类意义）
    if len(text) < _SEMANTIC_MIN_LENGTH:
        return "transient"

    # 2. 检查分类缓存
    cache_key = hash(text[:500])
    with _CLASSIFY_CACHE_LOCK:
        if cache_key in _CLASSIFY_CACHE:
            return _CLASSIFY_CACHE[cache_key]

    # 3. 尝试语义路由
    sensitivity = "transient"  # 默认值
    try:
        if _ollama_reachable() and _ensure_anchor_embeddings():
            # 检查文本向量缓存
            with _TEXT_EMBED_LOCK:
                if cache_key in _TEXT_EMBED_CACHE:
                    text_emb = _TEXT_EMBED_CACHE[cache_key]
                else:
                    text_emb = _get_embedding(text[:2000])  # 截取前2000字符
                    if text_emb and len(_TEXT_EMBED_CACHE) < _TEXT_EMBED_CACHE_MAX:
                        _TEXT_EMBED_CACHE[cache_key] = text_emb

            if text_emb:
                # 计算与各类别锚点的相似度
                scores = {}
                for category, anchor_emb in _ANCHOR_EMBEDDINGS.items():
                    scores[category] = _cosine_similarity(text_emb, anchor_emb)

                best_category = max(scores, key=scores.get)
                best_score = scores[best_category]

                logger.info(
                    f"[db_router] 语义路由: {best_category}"
                    f" (score={best_score:.3f}), 全部分数: {scores}"
                )

                # 阈值判断：相似度过低视为普通对话
                if best_score >= _SIMILARITY_THRESHOLD:
                    sensitivity = best_category
                else:
                    sensitivity = "transient"
            else:
                # embedding 获取失败，回退关键词
                sensitivity = _classify_by_keywords(text)
        else:
            # Ollama 不可达，回退关键词
            sensitivity = _classify_by_keywords(text)
    except Exception as e:
        logger.debug(f"[db_router] 语义路由异常，回退关键词: {e}")
        sensitivity = _classify_by_keywords(text)

    # 写入分类缓存
    with _CLASSIFY_CACHE_LOCK:
        if len(_CLASSIFY_CACHE) < _CLASSIFY_CACHE_MAX:
            _CLASSIFY_CACHE[cache_key] = sensitivity

    return sensitivity


# ── 公开 API（保持不变） ─────────────────────────────────────────────────────

def route_to_isolated_db(
    text: str,
    category: str = "conversation",
    key: str = "",
    metadata: Optional[Dict] = None,
) -> str:
    """
    自动路由数据到正确的隔离数据库。

    Args:
        text:     要存储的内容
        category: 分类标签
        key:      键名
        metadata: 额外元数据

    Returns:
        record_id
    """
    sensitivity = classify_sensitivity(text)

    if sensitivity == "privacy":
        from src.core.isolated_db import get_privacy_db
        db = get_privacy_db()
    elif sensitivity == "commercial":
        from src.core.isolated_db import get_commercial_db
        db = get_commercial_db()
    else:
        from src.core.isolated_db import get_transient_db
        db = get_transient_db()

    return db.add(
        category=category,
        key=key,
        value=text,
        metadata=metadata or {"sensitivity": sensitivity},
    )


def search_isolated_dbs(keyword: str, limit: int = 20) -> list:
    """
    跨三库搜索。

    Returns:
        [{db: 'privacy'|'transient'|'commercial', records: [...]}]
    """
    from src.core.isolated_db import (
        get_commercial_db,
        get_privacy_db,
        get_transient_db,
    )

    results = []
    for db_name, db in [
        ("privacy", get_privacy_db()),
        ("transient", get_transient_db()),
        ("commercial", get_commercial_db()),
    ]:
        records = db.search(keyword, limit=limit)
        if records:
            results.append({"db": db_name, "records": records})

    return results


def auto_cleanup_transient():
    """自动清理过渡库过期数据（每次启动时调用）"""
    try:
        from src.core.isolated_db import (
            TRANSIENT_OBSERVATION_DAYS,
            cleanup_transient,
        )
        deleted = cleanup_transient(max_age_days=TRANSIENT_OBSERVATION_DAYS)
        if deleted > 0:
            logger.debug(f"[DB Router] 已清理过渡库 {deleted} 条过期记录")
        return deleted
    except Exception as e:
        logger.error(f"[DB Router] 过渡库清理失败: {e}")
        return 0


def get_isolated_stats() -> dict:
    """获取三库统计信息"""
    from src.core.isolated_db import (
        get_commercial_db,
        get_privacy_db,
        get_transient_db,
    )

    return {
        "privacy": {
            "count": get_privacy_db().count(),
            "categories": get_privacy_db().get_categories(),
        },
        "transient": {
            "count": get_transient_db().count(),
            "categories": get_transient_db().get_categories(),
        },
        "commercial": {
            "count": get_commercial_db().count(),
            "categories": get_commercial_db().get_categories(),
        },
    }
