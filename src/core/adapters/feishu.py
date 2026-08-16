# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
飞书 自定义机器人 Webhook 适配器

文档: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
支持: text, post (富文本), interactive (卡片)
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

import requests

from src.core.im_connector import BaseAdapter, IncomingMessage

logger = logging.getLogger(__name__)


class FeishuAdapter(BaseAdapter):
    platform = "feishu"

    # ── 发送 ──────────────────────────────────────────────────────────

    def send_text(self, webhook_url: str, content: str, **kwargs) -> tuple:
        """发送文本消息"""
        payload = {
            "msg_type": "text",
            "content": {
                "text": content,
            },
        }
        return self._post(webhook_url, payload)

    def send_markdown(self, webhook_url: str, title: str, content: str, **kwargs) -> tuple:
        """发送富文本消息（飞书不支持原生 Markdown，使用 post 富文本）"""
        full_text = f"**{title}**\n{content}" if title else content
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": self._text_to_post_paragraphs(full_text),
                    },
                },
            },
        }
        return self._post(webhook_url, payload)

    def send_interactive(self, webhook_url: str, card: dict, **kwargs) -> tuple:
        """发送交互式卡片消息"""
        payload = {
            "msg_type": "interactive",
            "card": card,
        }
        return self._post(webhook_url, payload)

    def _text_to_post_paragraphs(self, text: str) -> list:
        """将纯文本转换为飞书 post 段落"""
        paragraphs = []
        for line in text.split("\n"):
            if line.strip():
                paragraphs.append([{"tag": "text", "text": line}])
            else:
                paragraphs.append([{"tag": "text", "text": ""}])
        return paragraphs

    def _post(self, url: str, payload: dict) -> tuple:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if data.get("code") == 0 or data.get("StatusCode") == 0:
                return True, "ok"
            return False, f"飞书错误: {data.get('msg', 'unknown')}"
        except Exception as e:
            logger.error(f"[Feishu] 发送失败: {e}")
            return False, str(e)

    # ── 回调解析 ──────────────────────────────────────────────────────

    def parse_callback(self, payload: dict, headers: dict) -> Optional[IncomingMessage]:
        """
        解析飞书 Webhook 回调

        飞书回调格式:
        {
            "header": {"event_type": "im.message.receive_v1", ...},
            "event": {
                "message": {
                    "msg_type": "text",
                    "content": "{\"text\":\"...\"}",
                },
                "sender": {"sender_id": {}},
            },
        }
        """
        try:
            event = payload.get("event", {})
            message = event.get("message", {})
            msg_type = message.get("msg_type", "text")

            if msg_type == "text":
                try:
                    content_obj = json.loads(message.get("content", "{}"))
                    content = content_obj.get("text", "")
                except (json.JSONDecodeError, TypeError):
                    content = str(message.get("content", ""))
            else:
                content = f"[{msg_type}]"

            sender = event.get("sender", {}).get("sender_id", {})
            return IncomingMessage(
                platform=self.platform,
                channel_id=payload.get("header", {}).get("app_id", ""),
                sender_id=sender.get("open_id", ""),
                sender_name="",
                content=content,
                msg_type=msg_type,
                raw=payload,
            )
        except Exception as e:
            logger.error(f"[Feishu] 解析回调失败: {e}")
            return None

    def verify_signature(self, payload: dict, signature: str, secret: str) -> bool:
        """验证飞书签名（使用 token 进行 HMAC-SHA256）"""
        if not secret or not signature:
            return True
        try:
            import time
            timestamp = payload.get("timestamp", "")
            nonce = payload.get("nonce", "")
            raw = f"{timestamp}\n{nonce}\n{secret}"
            expected = hashlib.sha256(raw.encode()).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception:
            return False

    def health_check(self, webhook_url: str) -> tuple:
        if not webhook_url.startswith("https://open.feishu.cn/"):
            return False, "Webhook URL 格式不正确"
        return True, "URL 格式有效"
