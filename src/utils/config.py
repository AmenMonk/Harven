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
            "default_model": "qwen2.5:7b",
        },
        "llama_cpp": {
            "enabled": False,
            "model_path": "",
        },
    },
    # 兼容旧代码读取 ollama: 字段（从 backends.local 同步）
    "ollama": {
        "base_url": "http://localhost:11434",
        "default_model": "qwen2.5:7b",
    },
    "advanced_model": "qwen2.5:14b",
    "normal_model": "qwen2.5:7b",
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
        # Phase 2: 同步 backends.local -> ollama（兼容旧代码）
        _local = self.get("backends.local", {})
        if _local:
            if "base_url" in _local:
                self.setdefault("ollama", {})["base_url"] = _local["base_url"]
            if "default_model" in _local:
                self.setdefault("ollama", {})["default_model"] = _local["default_model"]
    
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
