# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
企业微信 群机器人 Webhook 适配器

文档: https://developer.work.weixin.qq.com/document/path/91770
支持: text, markdown, image, news 消息类型
"""

import hashlib
import json
import logging
from typing import Optional

import requests

from src.core.im_connector import BaseAdapter, IncomingMessage

logger = logging.getLogger(__name__)


class WeComAdapter(BaseAdapter):
    platform = "wecom"

    # ── 发送 ──────────────────────────────────────────────────────────

    def send_text(self, webhook_url: str, content: str, mentioned_list: list = None,
                  mentioned_mobile_list: list = None, **kwargs) -> tuple:
        """发送文本消息"""
        payload = {
            "msgtype": "text",
            "text": {
                "content": content,
            },
        }
        if mentioned_list:
            payload["text"]["mentioned_list"] = mentioned_list
        if mentioned_mobile_list:
            payload["text"]["mentioned_mobile_list"] = mentioned_mobile_list

        return self._post(webhook_url, payload)

    def send_markdown(self, webhook_url: str, title: str, content: str, **kwargs) -> tuple:
        """发送 Markdown 消息"""
        # 企业微信 Markdown 不支持标题，合并到 content
        markdown_content = f"## {title}\n{content}" if title else content
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": markdown_content,
            },
        }
        return self._post(webhook_url, payload)

    def send_image(self, webhook_url: str, base64_data: str, md5_hash: str = "") -> tuple:
        """发送图片消息"""
        payload = {
            "msgtype": "image",
            "image": {
                "base64": base64_data,
                "md5": md5_hash or hashlib.md5(base64_data.encode()).hexdigest(),
            },
        }
        return self._post(webhook_url, payload)

    def send_news(self, webhook_url: str, articles: list) -> tuple:
        """发送图文消息
        articles: [{"title": "", "description": "", "url": "", "picurl": ""}]
        """
        payload = {
            "msgtype": "news",
            "news": {
                "articles": articles[:8],  # 最多8条
            },
        }
        return self._post(webhook_url, payload)

    def _post(self, url: str, payload: dict) -> tuple:
        try:
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            if data.get("errcode") == 0:
                return True, "ok"
            return False, f"企业微信错误: {data.get('errmsg', 'unknown')}"
        except Exception as e:
            logger.error(f"[WeCom] 发送失败: {e}")
            return False, str(e)

    # ── 回调解析 ──────────────────────────────────────────────────────

    def parse_callback(self, payload: dict, headers: dict) -> Optional[IncomingMessage]:
        """
        解析企业微信 Webhook 回调

        企业微信群机器人回调格式:
        {
            "msgtype": "text",
            "text": {"content": "..."},
            "from": {"userid": "...", "name": "..."},
            "chatid": "...",
            "msgid": "...",
        }
        """
        try:
            msg_type = payload.get("msgtype", "text")
            if msg_type == "text":
                content = payload.get("text", {}).get("content", "")
            elif msg_type == "image":
                content = "[图片]"
            else:
                content = f"[{msg_type}]"

            sender = payload.get("from", {})
            return IncomingMessage(
                platform=self.platform,
                channel_id=payload.get("chatid", ""),
                sender_id=sender.get("userid", ""),
                sender_name=sender.get("name", ""),
                content=content,
                msg_type=msg_type,
                raw=payload,
            )
        except Exception as e:
            logger.error(f"[WeCom] 解析回调失败: {e}")
            return None

    def health_check(self, webhook_url: str) -> tuple:
        """检查 Webhook 是否有效（空消息测试）"""
        # 企业微信不支持空测试，发送一个不可见的检测消息
        # 改用 URL 格式验证
        if not webhook_url.startswith("https://qyapi.weixin.qq.com/"):
            return False, "Webhook URL 格式不正确"
        return True, "URL 格式有效"
