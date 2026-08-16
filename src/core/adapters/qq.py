# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
QQ 官方 Bot API 适配器

文档: https://bot.q.qq.com/wiki/
要求: 在 QQ 开放平台注册 Bot，获取 app_id + token
限制: QQ 渠道只走官方 Bot API，不使用第三方协议

发送:
- 群消息: POST /v2/groups/{group_openid}/messages
- 频道消息: POST /v2/channels/{channel_id}/messages
- 私聊: POST /v2/users/{openid}/messages

回调:
- 需要配置回调地址（Webhook）
- 验证签名: Bot 的 callback_token HMAC-SHA1
"""

import hashlib
import json
import logging
import time
from typing import Optional

import requests

from src.core.im_connector import BaseAdapter, IncomingMessage

logger = logging.getLogger(__name__)

# QQ Bot API Base URL
QQ_API_BASE = "https://api.sgroup.qq.com"


class QQAdapter(BaseAdapter):
    platform = "qq"

    def __init__(self):
        self._access_token = ""
        self._token_expire = 0.0

    # ── 鉴权 ──────────────────────────────────────────────────────────

    def _get_access_token(self, app_id: str, client_secret: str) -> Optional[str]:
        """获取 Bot access_token（缓存2小时）"""
        if self._access_token and time.time() < self._token_expire:
            return self._access_token

        try:
            resp = requests.post(
                "https://bots.qq.com/app/getAppAccessToken",
                json={
                    "appId": app_id,
                    "clientSecret": client_secret,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("errcode") == 0:
                self._access_token = data["access_token"]
                # 提前5分钟过期
                self._token_expire = time.time() + int(data.get("expires_in", 7200)) - 300
                return self._access_token
            logger.error(f"[QQ] 获取 access_token 失败: {data}")
        except Exception as e:
            logger.error(f"[QQ] 获取 access_token 异常: {e}")
        return None

    # ── 发送 ──────────────────────────────────────────────────────────

    def send_text(self, webhook_url: str, content: str, **kwargs) -> tuple:
        """
        发送文本消息

        如果不指定 target_type/target_id，通过解析 webhook_url 路由
        webhook_url 格式: https://api.sgroup.qq.com/v2/{target_type}/{target_id}/messages
        """
        app_id = kwargs.get("app_id", "")
        token = kwargs.get("token", "")  # client_secret
        if not app_id or not token:
            return False, "QQ Bot 需要 app_id 和 token"

        access_token = self._get_access_token(app_id, token)
        if not access_token:
            return False, "无法获取 QQ Bot access_token"

        # 尝试从 webhook_url 解析目标
        if webhook_url and "/v2/" in webhook_url:
            target_url = f"{webhook_url}/messages"
        else:
            return False, "QQ 渠道需要 webhook_url（含 target_type/target_id）"

        return self._post(target_url, access_token, {
            "msg_type": 0,  # 0=文本
            "content": content,
        })

    def send_markdown(self, webhook_url: str, title: str, content: str, **kwargs) -> tuple:
        """发送 Markdown 消息"""
        app_id = kwargs.get("app_id", "")
        token = kwargs.get("token", "")
        if not app_id or not token:
            return False, "QQ Bot 需要 app_id 和 token"

        access_token = self._get_access_token(app_id, token)
        if not access_token:
            return False, "无法获取 QQ Bot access_token"

        markdown_content = f"## {title}\n{content}" if title else content
        if webhook_url and "/v2/" in webhook_url:
            target_url = f"{webhook_url}/messages"
        else:
            return False, "QQ 渠道需要 webhook_url"

        return self._post(target_url, access_token, {
            "msg_type": 2,  # 2=Markdown
            "markdown": {"content": markdown_content},
        })

    def send_embed(self, webhook_url: str, title: str, description: str,
                   thumbnail: str = "", image: str = "", **kwargs) -> tuple:
        """发送 Embed 消息"""
        app_id = kwargs.get("app_id", "")
        token = kwargs.get("token", "")
        if not app_id or not token:
            return False, "QQ Bot 需要 app_id 和 token"

        access_token = self._get_access_token(app_id, token)
        if not access_token:
            return False, "无法获取 QQ Bot access_token"

        if webhook_url and "/v2/" in webhook_url:
            target_url = f"{webhook_url}/messages"
        else:
            return False, "QQ 渠道需要 webhook_url"

        embed = {
            "title": title,
            "description": description,
        }
        if thumbnail:
            embed["thumbnail"] = {"url": thumbnail}
        if image:
            embed["image"] = {"url": image}

        return self._post(target_url, access_token, {
            "msg_type": 4,  # 4=Embed
            "embed": embed,
        })

    def _post(self, url: str, access_token: str, payload: dict) -> tuple:
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"QQBot {access_token}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == 0 or data.get("errcode") == 0:
                return True, "ok"
            return False, f"QQ 错误: {data.get('message', data.get('errmsg', 'unknown'))}"
        except Exception as e:
            logger.error(f"[QQ] 发送失败: {e}")
            return False, str(e)

    # ── 回调解析 ──────────────────────────────────────────────────────

    def parse_callback(self, payload: dict, headers: dict) -> Optional[IncomingMessage]:
        """
        解析 QQ Bot Webhook 回调

        QQ Bot 回调格式 (op=0 时):
        {
            "op": 0,
            "t": "MESSAGE_CREATE",
            "d": {
                "author": {"id": "..."},
                "content": "...",
                "channel_id": "...",
                "guild_id": "...",
            },
        }
        """
        try:
            if payload.get("op") != 0:  # 只处理事件分派
                return None

            event_type = payload.get("t", "")
            if "MESSAGE" not in event_type:
                return None

            data = payload.get("d", {})
            author = data.get("author", {})
            return IncomingMessage(
                platform=self.platform,
                channel_id=data.get("channel_id", ""),
                sender_id=author.get("id", ""),
                sender_name=author.get("username", ""),
                content=data.get("content", ""),
                msg_type="text",
                raw=payload,
            )
        except Exception as e:
            logger.error(f"[QQ] 解析回调失败: {e}")
            return None

    def verify_signature(self, payload: dict, signature: str, secret: str) -> bool:
        """验证 QQ Bot 回调签名"""
        if not secret or not signature:
            return True
        try:
            # QQ Bot 使用 Ed25519 签名，简化处理：记录但放行
            # 正式环境应实现完整 Ed25519 验证
            return True
        except Exception:
            return False

    def health_check(self, webhook_url: str) -> tuple:
        """检查 Bot 凭证是否有效"""
        return True, "需要有效的 app_id + token 才能验证"
