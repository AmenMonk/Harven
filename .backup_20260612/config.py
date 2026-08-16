"""
全局配置管理
"""
import os
import yaml


# 默认配置
DEFAULT_CONFIG = {
    "ollama": {
        "base_url": "http://localhost:11434",
        "default_model": "qwen2.5:7b",
    },
    "advanced_model": "qwen2.5:14b",
    "normal_model": "qwen2.5:7b",
    "language": {
        "primary": "zh-CN",
        "auto_translate": True,
        "display_mode": "translate",  # translate | original | bilingual
    },
    "ui": {
        "theme": "light",
        "font_size": 14,
    },
    "memory": {
        "max_history_rounds": 40,
        "auto_save_interval": 30,
    },
    "data_dir": "data",
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
