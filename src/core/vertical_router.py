# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
垂直模型路由器 v1.0
- 意图检测：根据用户消息判断是否触发垂直模型
- 参数提取：从自然语言中提取模型参数
- 完整度评分：判断参数是否足够直接执行
- 确认卡片生成：不足时生成交互确认卡
- 模型执行：调用垂直模型生成结果

设计原则：
1. 外脑只分析意图 → 哈文负责参数映射和执行
2. 默认值优先 → 参数够多时不弹确认卡
3. 确认卡不建GUI → 对话内格式化卡片
"""

import re
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 全局缓存
_params_catalog: Optional[dict] = None


def _load_catalog() -> dict:
    """加载垂直模型参数目录"""
    global _params_catalog
    if _params_catalog is not None:
        return _params_catalog

    catalog_path = Path(__file__).parent.parent.parent / "data" / "vertical_params.json"
    try:
        if catalog_path.exists():
            _params_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            logger.info(f"[VerticalRouter] 参数目录已加载: {len(_params_catalog.get('models', {}))} 个模型")
        else:
            _params_catalog = {"models": {}}
            logger.warning("[VerticalRouter] vertical_params.json 不存在，垂直路由不可用")
    except Exception as e:
        _params_catalog = {"models": {}}
        logger.error(f"[VerticalRouter] 加载参数目录失败: {e}")
    return _params_catalog


def get_available_models(type_filter: str = None) -> list[dict]:
    """获取所有已注册的垂直模型"""
    catalog = _load_catalog()
    models = []
    for mid, info in catalog.get("models", {}).items():
        if type_filter and info.get("type") != type_filter:
            continue
        models.append({
            "id": mid,
            "name": info.get("name", mid),
            "type": info.get("type", ""),
            "icon": info.get("icon", "🤖"),
            "is_chat": info.get("is_chat", False),
        })
    return models


def detect_vertical_intent(user_message: str) -> Optional[str]:
    """
    检测用户意图是否匹配垂直模型。
    返回匹配的模型 ID，或 None。
    """
    catalog = _load_catalog()
    best_score = 0
    best_model = None
    msg_lower = user_message.lower()

    for mid, info in catalog.get("models", {}).items():
        score = 0
        keywords = info.get("trigger_keywords", [])
        for kw in keywords:
            if kw in msg_lower:
                score += 2  # 短词匹配

        regex = info.get("trigger_regex", "")
        if regex and re.search(regex, user_message):
            score += 5  # 正则匹配权重高

        if score > best_score:
            best_score = score
            best_model = mid

    if best_score >= 5 and best_model:  # 至少正则匹配
        return best_model
    if best_score >= 4 and best_model:   # 至少2个关键词
        return best_model
    return None


def extract_params(user_message: str, model_id: str) -> dict:
    """
    从用户消息中提取参数。
    返回 {param_name: {"raw": ..., "value": ..., "found": bool}}
    """
    catalog = _load_catalog()
    model_info = catalog.get("models", {}).get(model_id)
    if not model_info:
        return {}

    params_def = model_info.get("params", {})
    extracted = {}
    msg_lower = user_message.lower()

    for pname, pdef in params_def.items():
        result = {"raw": "", "value": pdef.get("default", "auto"), "found": False}

        if not pdef.get("auto_detect", True):
            extracted[pname] = result
            continue

        # 文本类型：从消息中提取核心内容
        if pdef.get("extract_from_message"):
            # 移除触发词，剩余作为文本参数
            cleaned = _remove_triggers(user_message, model_info.get("trigger_keywords", []))
            result["raw"] = cleaned
            result["value"] = cleaned
            result["found"] = bool(cleaned.strip())
            extracted[pname] = result
            continue

        # 正则提取模式
        pattern = pdef.get("extract_pattern", "")
        if pattern:
            m = re.search(pattern, user_message)
            if m:
                result["raw"] = m.group(1)
                result["value"] = _coerce_value(m.group(1), pdef)
                result["found"] = True
                extracted[pname] = result
                continue

        # 关键词匹配（enum/multi 类型）
        keywords_map = pdef.get("extract_keywords", {})
        if keywords_map:
            for value, kws in keywords_map.items():
                for kw in kws:
                    if kw in msg_lower:
                        result["raw"] = kw
                        result["value"] = value
                        result["found"] = True
                        break
                if result["found"]:
                    break

        extracted[pname] = result

    return extracted


def score_completeness(extracted: dict) -> dict:
    """
    计算参数完整度（0-100）。
    返回 {score, missing, filled, total_weight, max_weight}
    """
    total_weight = 0
    filled_weight = 0
    missing = []

    for pname, pdata in extracted.items():
        # weight 来自 params 定义（如果不可用则默认为 1）
        w = 1  # 默认权重
        # 从原始定义获取权重
        catalog = _load_catalog()
        for _, info in catalog.get("models", {}).items():
            if pname in info.get("params", {}):
                w = info["params"][pname].get("weight", 1)
                break

        total_weight += w
        if pdata.get("found"):
            filled_weight += w
        else:
            missing.append(pname)

    if total_weight == 0:
        return {"score": 100, "missing": [], "filled": 0, "total_weight": 0}

    score = int(filled_weight / total_weight * 100)
    return {
        "score": score,
        "missing": missing,
        "filled": filled_weight,
        "total_weight": total_weight,
    }


def build_confirmation_card(model_id: str, extracted: dict, completeness: dict) -> dict:
    """
    生成参数确认卡片。
    返回 {"type": "vertical_confirm", "model_id": ..., "params": [...], ...}
    """
    catalog = _load_catalog()
    model_info = catalog.get("models", {}).get(model_id, {})

    params_display = []
    for pname, pdata in extracted.items():
        params_display.append({
            "name": pname,
            "value": pdata["value"],
            "raw": pdata.get("raw", ""),
            "found": pdata.get("found", False),
            "defaulted": pdata["value"] == "auto",
        })

    return {
        "type": "vertical_confirm",
        "model_id": model_id,
        "model_name": model_info.get("name", model_id),
        "model_icon": model_info.get("icon", "🤖"),
        "model_type": model_info.get("type", "unknown"),
        "category_desc": model_info.get("category_desc", ""),
        "score": completeness["score"],
        "params": params_display,
        "missing_params": completeness.get("missing", []),
        "message": _build_confirm_message(model_info, completeness["score"]),
    }


def build_simple_card(model_id: str, message: str) -> dict:
    """
    生成简单引导卡片（新模型下载后的欢迎/介绍）。
    """
    catalog = _load_catalog()
    model_info = catalog.get("models", {}).get(model_id, {})

    return {
        "type": "vertical_intro",
        "model_id": model_id,
        "model_name": model_info.get("name", model_id),
        "model_icon": model_info.get("icon", "🤖"),
        "model_type": model_info.get("type", "unknown"),
        "category_desc": model_info.get("category_desc", ""),
        "is_chat": model_info.get("is_chat", False),
        "message": message,
        "example_triggers": model_info.get("trigger_keywords", [])[:5],
    }


# ── 内部辅助 ──

def _remove_triggers(message: str, keywords: list) -> str:
    """从消息中移除触发词，提取核心内容"""
    result = message
    for kw in sorted(keywords, key=len, reverse=True):
        result = result.replace(kw, "")
    # 移除常见前置词
    result = re.sub(r'^(生成|做|来|写|弄|创作|画|朗读|读|读出)\s*', '', result)
    return result.strip()


def _coerce_value(raw: str, pdef: dict) -> any:
    """将提取的原始值转换为参数类型"""
    ptype = pdef.get("type", "string")
    if ptype == "number":
        try:
            val = int(re.sub(r'[^\d]', '', raw))
            mn = pdef.get("min", 0)
            mx = pdef.get("max", 999)
            return max(mn, min(val, mx))
        except ValueError:
            return pdef.get("default", 30)
    return str(raw)


def _build_confirm_message(model_info: dict, score: int) -> str:
    """根据完整度生成确认提示语"""
    icon = model_info.get("icon", "🤖")
    name = model_info.get("name", "模型")

    if score >= 80:
        return f"{icon} **{name}** — 参数完整，即将生成..."
    elif score >= 40:
        return f"{icon} **{name}** — 部分参数使用默认值，请确认："
    else:
        return f"{icon} **{name}** — 请补充一些信息："


def get_spec_sheet(model_id: str) -> dict:
    """
    生成给外脑看的"说明书"（不含具体命令，只有能力描述）。
    外脑看到这个后知道这个模型能做什么，但不知道具体命令格式。
    """
    catalog = _load_catalog()
    model_info = catalog.get("models", {}).get(model_id, {})
    if not model_info:
        return {}

    params_plain = {}
    for pname, pdef in model_info.get("params", {}).items():
        params_plain[pname] = {
            "label": pdef.get("label", pname),
            "type": pdef.get("type", "string"),
            "options": pdef.get("options", []),
        }

    return {
        "model_name": model_info.get("name", model_id),
        "type": model_info.get("type", ""),
        "category_desc": model_info.get("category_desc", ""),
        "capabilities": model_info.get("trigger_keywords", [])[:5],
        "available_params": params_plain,
        "instruction": (
            "你不需要生成命令行参数。你只需要分析用户的自然语言输入，"
            "提取出意图和风格建议（如情绪、风格、乐器等），以 JSON 格式返回。"
            "哈文会负责将这些建议转换为精确的模型命令。"
        ),
    }


# ═══════════════════════════════════════════════════
# 用户垂直偏好记忆
# ═══════════════════════════════════════════════════

_PREF_FILE = None


def _get_pref_path() -> Path:
    global _PREF_FILE
    if _PREF_FILE is None:
        _PREF_FILE = Path(__file__).parent.parent.parent / "data" / "user_vertical_prefs.json"
    return _PREF_FILE


def apply_user_prefs(model_type: str, extracted: dict) -> dict:
    """
    将用户偏好应用到提取的参数中，替换 'auto' 默认值。
    仅在用户未明确指定时生效（found=False 且 value='auto'）。
    """
    prefs = _load_preferences().get(model_type, {})
    if not prefs:
        return extracted

    for pname, pdata in extracted.items():
        if not pdata.get("found") and str(pdata.get("value", "")) == "auto":
            if pname in prefs:
                pdata["value"] = prefs[pname]
                # 不标记为 found，以便完整度评分不受影响
    return extracted


def record_user_prefs(model_type: str, params: dict):
    """
    用户确认/使用垂直模型后，记录其偏好参数。
    后续使用同一类型模型时，自动应用偏好作为默认值。
    """
    all_prefs = _load_preferences()
    if model_type not in all_prefs:
        all_prefs[model_type] = {}

    for pname, pval in params.items():
        if pval and str(pval) != "auto":
            all_prefs[model_type][pname] = pval

    _save_preferences(all_prefs)
    logger.info(f"[VerticalRouter] 记录偏好: {model_type} → {list(all_prefs[model_type].keys())}")


def _load_preferences() -> dict:
    pref_path = _get_pref_path()
    try:
        if pref_path.exists():
            return json.loads(pref_path.read_text(encoding="utf-8"))
    except Exception as _e: logger.debug(f"[vertical_router] 忽略: {_e}")
    return {}


def _save_preferences(prefs: dict):
    pref_path = _get_pref_path()
    try:
        pref_path.parent.mkdir(parents=True, exist_ok=True)
        pref_path.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[VerticalRouter] 保存偏好失败: {e}")
