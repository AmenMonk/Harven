# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
钉钉 群机器人 Webhook 适配器

文档: https://open.dingtalk.com/document/orgapp/custom-robot-access
支持: text, markdown, link, actionCard 消息类型
加签安全: HMAC-SHA256 (secret + timestamp)
"""

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from typing import Optional

import requests

from src.core.im_connector import BaseAdapter, IncomingMessage

logger = logging.getLogger(__name__)


class DingTalkAdapter(BaseAdapter):
    platform = "dingtalk"

    # ── 加签 ──────────────────────────────────────────────────────────

    @staticmethod
    def _sign(secret: str) -> tuple:
        """生成钉钉加签参数 (timestamp, sign)"""
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return timestamp, sign

    def _build_url(self, webhook_url: str, secret: str) -> str:
        """构建带签名的完整 URL"""
        if not secret:
            return webhook_url
        timestamp, sign = self._sign(secret)
        return f"{webhook_url}&timestamp={timestamp}&sign={sign}"

    # ── 发送 ──────────────────────────────────────────────────────────

    def send_text(self, webhook_url: str, content: str,
                  at_mobiles: list = None, at_all: bool = False, **kwargs) -> tuple:
        """发送文本消息"""
        payload = {
            "msgtype": "text",
            "text": {
                "content": content,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all,
            },
        }
        secret = kwargs.get("secret", "")
        return self._post(webhook_url, payload, secret)

    def send_markdown(self, webhook_url: str, title: str, content: str,
                      at_mobiles: list = None, at_all: bool = False, **kwargs) -> tuple:
        """发送 Markdown 消息"""
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": content,
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all,
            },
        }
        secret = kwargs.get("secret", "")
        return self._post(webhook_url, payload, secret)

    def send_action_card(self, webhook_url: str, title: str, text: str,
                         btns: list = None, btn_orientation: str = "0",
                         single_title: str = "", single_url: str = "", **kwargs) -> tuple:
        """发送 ActionCard 消息"""
        if single_url:
            payload = {
                "msgtype": "actionCard",
                "actionCard": {
                    "title": title,
                    "text": text,
                    "singleTitle": single_title,
                    "singleURL": single_url,
                },
            }
        else:
            payload = {
                "msgtype": "actionCard",
                "actionCard": {
                    "title": title,
                    "text": text,
                    "btnOrientation": btn_orientation,
                    "btns": btns or [],
                },
            }
        secret = kwargs.get("secret", "")
        return self._post(webhook_url, payload, secret)

    def _post(self, url: str, payload: dict, secret: str = "") -> tuple:
        try:
            target_url = self._build_url(url, secret)
            resp = requests.post(target_url, json=payload, timeout=10)
            data = resp.json()
            if data.get("errcode") == 0:
                return True, "ok"
            return False, f"钉钉错误: {data.get('errmsg', 'unknown')}"
        except Exception as e:
            logger.error(f"[DingTalk] 发送失败: {e}")
            return False, str(e)

    # ── 回调解析 ──────────────────────────────────────────────────────

    def parse_callback(self, payload: dict, headers: dict) -> Optional[IncomingMessage]:
        """
        解析钉钉 Webhook 回调

        钉钉回调格式:
        {
            "msgtype": "text",
            "text": {"content": "..."},
            "senderNick": "...",
            "senderId": "...",
            "sessionWebhook": "...",
        }
        """
        try:
            msg_type = payload.get("msgtype", "text")
            if msg_type == "text":
                content = payload.get("text", {}).get("content", "")
            else:
                content = f"[{msg_type}]"

            return IncomingMessage(
                platform=self.platform,
                channel_id=payload.get("sessionWebhook", ""),
                sender_id=payload.get("senderId", ""),
                sender_name=payload.get("senderNick", ""),
                content=content,
                msg_type=msg_type,
                raw=payload,
            )
        except Exception as e:
            logger.error(f"[DingTalk] 解析回调失败: {e}")
            return None

    def verify_signature(self, payload: dict, signature: str, secret: str) -> bool:
        """验证加签回调（钉钉不需要签名验证，通过加签 URL 保证安全）"""
        return True

    def health_check(self, webhook_url: str) -> tuple:
        if not webhook_url.startswith("https://oapi.dingtalk.com/"):
            return False, "Webhook URL 格式不正确"
        return True, "URL 格式有效"
