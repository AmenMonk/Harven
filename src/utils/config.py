"""
全局配置管理
"""
import os
import yaml


# 默认配置
DEFAULT_CONFIG = {
    # Phase 2: backends 统一配置
    "backends": {
        "local": {
            "engine": "ollama",
            "base_url": "http://localhost:11434",
            # ★ 2026-09-26 审计（开发者死命令：模型必须用户自选）：出厂不再内定 qwen2.5:14b。
            # 空 = 首次启动走硬件检测引导，用户自己选/下模型。已装用户 yaml 权威覆盖不受影响
            "default_model": "",
        },
        "llama_cpp": {
            "enabled": False,
            "model_path": "",
        },
    },
    # 兼容旧代码读取 ollama: 字段（从 backends.local 同步）
    "ollama": {
        "base_url": "http://localhost:11434",
        "default_model": "",  # ★ 2026-09-26 审计：出厂不内定，空 = 硬件检测引导用户自选
    },
    "advanced_model": "",
    "normal_model": "",  # ★ 2026-09-26 审计：出厂不内定
    "language": {
        "main_language": "zh-CN",
        "other_language_mode": "translate",
        "ui_language": "follow",
    },
    "ui": {
        "theme": "dark",
        "font_size": "medium",
    },
    "memory": {
        "max_history_rounds": 40,
        "auto_save_interval": 30,
    },
    "data_dir": "data",
    # 协作模式（Ollama + 1个外脑混合工作）
    "collab_enabled": False,
    "collab_foreign_model": "doubao",
    "collab_privacy_level": "balanced",
    # 本地双模配置（大脑模型 + 工具模型）
    "local_dual_brain": "",
    "local_dual_tools": [],
}


class Config(dict):
    """加载和管理配置，支持嵌套点号访问"""
    
    def __init__(self, config_path=None):
        super().__init__()
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "..", "config.yaml"
            )
        self._config_path = os.path.normpath(config_path)
        self._load()
    
    def _load(self):
        """加载 YAML 配置，不存在则用默认值"""
        if os.path.exists(self._config_path):
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}
        # 合并默认配置
        self._deep_update(self, DEFAULT_CONFIG)
        self._deep_update(self, data)
        # ★ 2026-09-24 反杀修复（实测实锤：选了 deepseek 重启变回 qwen）：
        #   旧逻辑 backends.local -> ollama，旧镜像 qwen 死值每次启动反杀 ollama 字段。
        #   现反转：以【用户 yaml 明确写的 ollama.default_model】为权威（set-current/模型中心写的就是它），
        #   反向同步到旧镜像；用户 yaml 没写 ollama 但写了 backends.local 时，旧镜像补进权威字段。
        _user_oll = (data or {}).get("ollama", {}) or {}
        _user_local = ((data or {}).get("backends", {}) or {}).get("local", {}) or {}
        if _user_oll.get("default_model"):
            # 权威字段存在（绝大多数场景）：反写旧镜像，两处永远一致
            self.setdefault("backends", {}).setdefault("local", {})
            if self.get("ollama", {}).get("base_url"):
                self["backends"]["local"]["base_url"] = self["ollama"]["base_url"]
            self["backends"]["local"]["default_model"] = self["ollama"]["default_model"]
        elif _user_local.get("default_model"):
            # 全新装机兜底：用户只写了旧镜像 → 补进权威字段
            self.setdefault("ollama", {})["default_model"] = _user_local["default_model"]
            if _user_local.get("base_url"):
                self["ollama"]["base_url"] = _user_local["base_url"]
    
    def _deep_update(self, target, source):
        for k, v in source.items():
            if k in target and isinstance(target[k], dict) and isinstance(v, dict):
                self._deep_update(target[k], v)
            else:
                target[k] = v
    
    def get(self, key, default=None):
        """用点号访问嵌套配置，如 'ollama.base_url'"""
        keys = key.split(".")
        val = self
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val
    
    def set(self, key, value):
        """设置配置值"""
        keys = key.split(".")
        d = self
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
    
    def save(self):
        """保存配置到文件"""
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.dump(dict(self), f, allow_unicode=True, default_flow_style=False)
    
    @property
    def root_dir(self):
        """项目根目录"""
        return os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
        )
    
    def resolve_path(self, relative_path):
        """将相对路径解析为绝对路径"""
        return os.path.normpath(os.path.join(self.root_dir, relative_path))


def load_config(config_path=None):
    """快捷函数：加载配置"""
    return Config(config_path)


# ── 全局单例 ──

_config_instance: Config | None = None


def get_config(config_path=None) -> Config:
    """获取全局配置单例"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config(config_path)
    return _config_instance


def reset_config():
    """重置配置单例（测试用）"""
    global _config_instance
    _config_instance = None
