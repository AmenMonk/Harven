"""
Harven AI 国际化 (i18n) 模块
轻量级 JSON 翻译方案，不依赖 Qt Linguist
"""
import logging
logger = logging.getLogger(__name__)
import json
import os
from pathlib import Path

# ── 全局状态 ──
_current_lang = "zh_CN"
_translations = {}
_fallback = {}
_default_lang = "zh_CN"
_locales_dir = Path(__file__).parent.parent.parent / "locales"


def set_language(lang: str) -> bool:
    """切换界面语言。成功返回 True。"""
    global _current_lang, _translations
    lang = lang.replace("-", "_")
    json_path = _locales_dir / f"{lang}.json"
    if not json_path.exists():
        return False
    with open(json_path, "r", encoding="utf-8") as f:
        _translations = json.load(f)
    _current_lang = lang
    return True


def get_language() -> str:
    """获取当前语言代码。"""
    return _current_lang


def get_available_languages() -> dict:
    """
    返回可用语言列表：{"zh_CN": "简体中文", "en_US": "English", ...}
    从 locales/ 目录扫描。
    """
    result = {}
    if not _locales_dir.exists():
        return result
    for fp in _locales_dir.glob("*.json"):
        lang_code = fp.stem
        try:
            with open(fp, "r", encoding="utf-8") as f:
                meta = json.load(f)
            # 翻译表中有 _meta.name 字段就显示本地名称
            local_name = meta.get("_meta", {}).get("name", lang_code)
            result[lang_code] = local_name
        except Exception:
            result[lang_code] = lang_code
    return result


def tr(key: str, **kwargs) -> str:
    """
    翻译函数。查找 key 对应的翻译文本。
    支持格式化参数：tr("greeting", name="用户") → "你好，用户！"
    找不到 key 时返回 key 本身。
    """
    global _current_lang, _translations, _fallback

    # 先查当前语言
    text = _translations.get(key)
    if text is None and _current_lang != _default_lang:
        # 回退到默认语言
        text = _fallback.get(key)

    if text is None:
        return key  # 未找到则返回 key

    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError as _e:
            logger.debug(f"[i18n] 忽略: {_e}")
    return text


def _load_default():
    """启动时加载默认语言。"""
    # ★ 2026-08-16: 修复 — 缺少 global 声明导致 _translations 未写入（zh模式下tr()返回key本身）
    global _fallback, _translations
    json_path = _locales_dir / f"{_default_lang}.json"
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            _fallback = json.load(f)
        _translations = dict(_fallback)
    else:
        _fallback = {}
        _translations = {}


# 模块导入时自动加载默认语言
_load_default()


def detect_system_language() -> str:
    """检测操作系统语言，返回 zh_CN / en_US"""
    import locale as _locale
    try:
        code = _locale.getdefaultlocale()[0] or "zh_CN"
    except Exception:
        code = "zh_CN"
    code = code.replace("-", "_")
    if code.lower().startswith("zh"):
        return "zh_CN"
    return "en_US"


def init_from_config():
    """
    ★ 2026-08-16: 从 config.yaml 读取界面语言并应用。
    在 web_app.py 启动时调用。
    config: language.ui_lang = "follow" | "zh_CN" | "en_US"
    """
    try:
        import yaml as _yaml
        cfg_path = _locales_dir.parent / "config.yaml"
        if not cfg_path.exists():
            return
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        ui_lang = (cfg.get("language") or {}).get("ui_lang", "follow")
        if ui_lang in ("", None):
            ui_lang = "follow"
        if ui_lang == "follow":
            ui_lang = detect_system_language()
        set_language(ui_lang)
        logger.info(f"[i18n] 界面语言已初始化: {get_language()}")
    except Exception as e:
        logger.debug(f"[i18n] init_from_config 忽略: {e}")


def get_stt_language() -> str:
    """语音识别(STT)语言代码：zh_CN→'zh', en_US→'en'"""
    return "zh" if _current_lang.startswith("zh") else "en"


def is_english() -> bool:
    """当前界面语言是否为英文（供后端输出文案选择）"""
    return _current_lang.startswith("en")


def refresh_ui():
    """
    触发全局 UI 刷新（重新设置所有界面文案）。
    在语言切换后调用。
    需要主窗口实现 refresh_translations() 方法。
    """
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app and hasattr(app, "main_window"):
            mw = app.main_window
            if hasattr(mw, "refresh_translations"):
                mw.refresh_translations()
    except Exception as _e: logger.debug(f"[i18n] 忽略: {_e}")
