# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
IM 连接器 — 多渠道即时通讯接入框架

支持平台（优先级排序）：
- 企业微信（Webhook Bot）
- 钉钉（Webhook Bot + 加签）
- 飞书（Webhook Bot）
- QQ（官方 Bot API）

架构：
    im_connector.py       — 统一接口 + 渠道注册表
    adapters/wecom.py     — 企业微信适配器
    adapters/dingtalk.py  — 钉钉适配器
    adapters/feishu.py    — 飞书适配器
    adapters/qq.py        — QQ Bot 适配器
"""

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
CHANNELS_CONFIG = DATA_DIR / "im_channels.json"


# ═══════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ChannelConfig:
    """渠道配置"""
    id: str                          # 唯一标识
    name: str                        # 显示名称
    platform: str                    # wecom / dingtalk / feishu / qq
    webhook_url: str = ""            # 出站 Webhook URL
    secret: str = ""                 # 签名密钥（钉钉加签用）
    token: str = ""                  # 验证 Token（飞书/QQ 用）
    app_id: str = ""                 # QQ Bot AppID
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        # 不返回敏感字段
        d.pop("secret", None)
        d.pop("token", None)
        d["has_secret"] = bool(self.secret)
        d["has_token"] = bool(self.token)
        return d


@dataclass
class IncomingMessage:
    """入站消息"""
    platform: str           # wecom / dingtalk / feishu / qq
    channel_id: str
    sender_id: str = ""
    sender_name: str = ""
    content: str = ""
    msg_type: str = "text"  # text / image / file
    raw: dict = field(default_factory=dict)
    timestamp: str = ""


# ═══════════════════════════════════════════════════════════════════════
# 抽象适配器
# ═══════════════════════════════════════════════════════════════════════

class BaseAdapter(ABC):
    """IM 平台适配器基类"""

    platform: str = "unknown"

    @abstractmethod
    def send_text(self, webhook_url: str, content: str, **kwargs) -> tuple[bool, str]:
        """发送文本消息。返回 (ok, message)"""
        ...

    @abstractmethod
    def send_markdown(self, webhook_url: str, title: str, content: str, **kwargs) -> tuple[bool, str]:
        """发送 Markdown 消息。返回 (ok, message)"""
        ...

    @abstractmethod
    def parse_callback(self, payload: dict, headers: dict) -> Optional[IncomingMessage]:
        """解析 Webhook 回调。返回 IncomingMessage 或 None"""
        ...

    def verify_signature(self, payload: dict, signature: str, secret: str) -> bool:
        """验证回调签名（子类按需覆写）"""
        return True

    def health_check(self, webhook_url: str) -> tuple[bool, str]:
        """健康检查（子类按需覆写）"""
        return True, "ok"


# ═══════════════════════════════════════════════════════════════════════
# 适配器注册表
# ═══════════════════════════════════════════════════════════════════════

class AdapterRegistry:
    """适配器注册表"""

    def __init__(self):
        self._adapters: Dict[str, BaseAdapter] = {}
        self._lock = threading.Lock()

    def register(self, platform: str, adapter: BaseAdapter):
        with self._lock:
            self._adapters[platform] = adapter
            logger.info(f"[IM] 已注册适配器: {platform}")

    def get(self, platform: str) -> Optional[BaseAdapter]:
        return self._adapters.get(platform)

    def list_platforms(self) -> List[str]:
        return sorted(self._adapters.keys())

    @property
    def adapters(self) -> Dict[str, BaseAdapter]:
        return dict(self._adapters)


_registry = AdapterRegistry()


# ═══════════════════════════════════════════════════════════════════════
# 渠道管理器
# ═══════════════════════════════════════════════════════════════════════

class ChannelManager:
    """渠道配置管理器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._channels: Dict[str, ChannelConfig] = {}
        self._message_handler: Optional[Callable] = None
        self._load()

    def _load(self):
        """从持久化文件加载渠道配置"""
        try:
            if CHANNELS_CONFIG.exists():
                data = json.loads(CHANNELS_CONFIG.read_text(encoding="utf-8"))
                for item in data.get("channels", []):
                    ch = ChannelConfig(**item)
                    self._channels[ch.id] = ch
                logger.info(f"[IM] 已加载 {len(self._channels)} 个渠道")
        except Exception as e:
            logger.error(f"[IM] 加载渠道配置失败: {e}")

    def _save(self):
        """持久化渠道配置"""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "channels": [ch.__dict__ for ch in self._channels.values()],
                "updated_at": datetime.now().isoformat(),
            }
            CHANNELS_CONFIG.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"[IM] 保存渠道配置失败: {e}")

    def add(self, cfg: ChannelConfig) -> bool:
        with self._lock:
            if cfg.id in self._channels:
                return False
            cfg.created_at = cfg.created_at or datetime.now().isoformat()
            cfg.updated_at = datetime.now().isoformat()
            self._channels[cfg.id] = cfg
            self._save()
            logger.info(f"[IM] 新增渠道: {cfg.id} ({cfg.platform})")
            return True

    def update(self, ch_id: str, **kwargs) -> bool:
        with self._lock:
            ch = self._channels.get(ch_id)
            if not ch:
                return False
            for k, v in kwargs.items():
                if hasattr(ch, k):
                    setattr(ch, k, v)
            ch.updated_at = datetime.now().isoformat()
            self._save()
            return True

    def remove(self, ch_id: str) -> bool:
        with self._lock:
            if ch_id not in self._channels:
                return False
            del self._channels[ch_id]
            self._save()
            logger.info(f"[IM] 已删除渠道: {ch_id}")
            return True

    def get(self, ch_id: str) -> Optional[ChannelConfig]:
        return self._channels.get(ch_id)

    def list_all(self) -> List[ChannelConfig]:
        return list(self._channels.values())

    def list_enabled(self) -> List[ChannelConfig]:
        return [ch for ch in self._channels.values() if ch.enabled]

    def set_message_handler(self, handler: Callable):
        """设置消息回调处理器"""
        self._message_handler = handler

    def handle_incoming(self, msg: IncomingMessage) -> Optional[str]:
        """处理入站消息，返回 AI 回复文本或 None"""
        if self._message_handler:
            try:
                return self._message_handler(msg)
            except Exception as e:
                logger.error(f"[IM] 消息处理异常: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════
# 统一消息发送接口
# ═══════════════════════════════════════════════════════════════════════

def send_message(channel_id: str, content: str, msg_type: str = "text",
                 title: str = "") -> tuple[bool, str]:
    """通过指定渠道发送消息"""
    ch = _channel_manager.get(channel_id)
    if not ch:
        return False, f"渠道不存在: {channel_id}"
    if not ch.enabled:
        return False, f"渠道已禁用: {ch.name}"

    adapter = _registry.get(ch.platform)
    if not adapter:
        return False, f"平台适配器未注册: {ch.platform}"

    try:
        if msg_type == "markdown":
            return adapter.send_markdown(ch.webhook_url, title, content)
        else:
            return adapter.send_text(ch.webhook_url, content)
    except Exception as e:
        logger.error(f"[IM] 发送消息失败 ({ch.platform}/{channel_id}): {e}")
        return False, str(e)


def broadcast_to_enabled(content: str, exclude: Optional[List[str]] = None) -> Dict[str, tuple]:
    """向所有启用的渠道广播消息"""
    exclude = exclude or []
    results = {}
    for ch in _channel_manager.list_enabled():
        if ch.id in exclude:
            continue
        results[ch.id] = send_message(ch.id, content)
    return results


def get_all_platforms() -> List[Dict]:
    """获取所有支持的平台信息"""
    return [
        {
            "id": "wecom",
            "name": "企业微信",
            "icon": "💬",
            "description": "企业微信群机器人 Webhook",
            "requires": ["webhook_url"],
            "format_hint": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
        },
        {
            "id": "dingtalk",
            "name": "钉钉",
            "icon": "🔔",
            "description": "钉钉群机器人 Webhook（支持加签安全）",
            "requires": ["webhook_url", "secret"],
            "format_hint": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        },
        {
            "id": "feishu",
            "name": "飞书",
            "icon": "🐦",
            "description": "飞书自定义机器人 Webhook",
            "requires": ["webhook_url", "token"],
            "format_hint": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        },
        {
            "id": "qq",
            "name": "QQ",
            "icon": "🐧",
            "description": "QQ 官方机器人 API（需先在 QQ 开放平台注册）",
            "requires": ["app_id", "token", "webhook_url"],
            "format_hint": "https://bots.qq.com/",
        },
    ]


# ═══════════════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════════════

_channel_manager = ChannelManager()


def get_channel_manager() -> ChannelManager:
    return _channel_manager


def init_adapters():
    """注册所有适配器（应用启动时调用）"""
    from src.core.adapters.wecom import WeComAdapter
    from src.core.adapters.dingtalk import DingTalkAdapter
    from src.core.adapters.feishu import FeishuAdapter
    from src.core.adapters.qq import QQAdapter

    _registry.register("wecom", WeComAdapter())
    _registry.register("dingtalk", DingTalkAdapter())
    _registry.register("feishu", FeishuAdapter())
    _registry.register("qq", QQAdapter())
    logger.info(f"[IM] 已注册 {len(_registry.adapters)} 个平台适配器")


def check_all_health() -> Dict[str, dict]:
    """对所有启用的渠道执行健康检查"""
    results = {}
    for ch in _channel_manager.list_enabled():
        adapter = _registry.get(ch.platform)
        if adapter:
            try:
                ok, msg = adapter.health_check(ch.webhook_url)
            except Exception as e:
                ok, msg = False, str(e)
            results[ch.id] = {
                "name": ch.name,
                "platform": ch.platform,
                "healthy": ok,
                "message": msg,
            }
    return results
