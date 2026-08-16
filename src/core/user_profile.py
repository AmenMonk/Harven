# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
用户画像系统
基于对话历史构建用户偏好模型，实现千人千面个性化响应
"""
import logging
logger = logging.getLogger(__name__)
import json
import os
from datetime import datetime
from src.models.database import DEFAULT_DB_PATH
import sqlite3


class UserProfile:
    """用户画像管理"""

    # 画像维度
    DIMENSIONS = {
        "interests": "兴趣爱好",
        "expertise": "专业领域",
        "communication_style": "交流风格",
        "values": "价值取向",
        "habits": "使用习惯",
        "emotional_tone": "情绪倾向",
    }

    def __init__(self):
        self._db_path = os.path.normpath(DEFAULT_DB_PATH)

    def get_profile(self):
        """获取完整用户画像"""
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT value FROM cognitive_locks WHERE key = 'user_profile'"
            ).fetchone()
            if row:
                return json.loads(row[0])
        except Exception as _e: logger.debug(f"[user_profile] 忽略: {_e}")
        finally:
            conn.close()

        # 返回默认画像
        return {k: [] for k in self.DIMENSIONS}

    def save_profile(self, profile):
        """保存用户画像"""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cognitive_locks (key, value, source, locked_at, priority) "
                "VALUES (?, ?, 'profile', datetime('now', 'localtime'), 150)",
                ("user_profile", json.dumps(profile, ensure_ascii=False))
            )
            conn.commit()
        finally:
            conn.close()

    def update_dimension(self, dimension, values):
        """更新某个维度的画像"""
        if dimension not in self.DIMENSIONS:
            return
        profile = self.get_profile()
        if not isinstance(values, list):
            values = [values]
        profile[dimension] = values
        self.save_profile(profile)

    def add_to_dimension(self, dimension, value):
        """向某个维度添加一个标签"""
        if dimension not in self.DIMENSIONS:
            return
        profile = self.get_profile()
        if value not in profile.get(dimension, []):
            profile.setdefault(dimension, []).append(value)
            self.save_profile(profile)

    def remove_from_dimension(self, dimension, value):
        """从某个维度移除一个标签"""
        profile = self.get_profile()
        if dimension in profile and value in profile[dimension]:
            profile[dimension].remove(value)
            self.save_profile(profile)

    def build_profile_context(self):
        """构建用户画像上下文（注入到系统提示词）"""
        profile = self.get_profile()
        parts = []

        for dim, label in self.DIMENSIONS.items():
            values = profile.get(dim, [])
            if values:
                if dim == "communication_style":
                    desc = "、".join(values)
                    parts.append(f"  - 交流风格：{desc}")
                elif dim == "interests":
                    parts.append(f"  - 兴趣爱好：{', '.join(values)}")
                elif dim == "expertise":
                    parts.append(f"  - 专业领域：{', '.join(values)}")
                elif dim == "habits":
                    parts.append(f"  - 使用习惯：{', '.join(values)}")

        if not parts:
            return ""

        return "\n\n## 用户画像（个性化参考，请在回答中自然体现）\n" + "\n".join(parts)

    def extract_from_conversation(self, user_message, ai_response):
        """
        从对话中简单提取用户画像信息
        （后续可替换为AI提取）
        """
        msg = user_message.lower()
        updated = False

        # 兴趣爱好检测
        interest_patterns = [
            "我喜欢", "我爱", "我经常", "我平时", "我最近在",
            "我的爱好", "我喜欢看", "我喜欢听", "我喜欢玩",
        ]
        for p in interest_patterns:
            if p in msg:
                # 提取后面的内容
                idx = msg.find(p)
                fragment = user_message[idx + len(p):idx + len(p) + 50].strip().rstrip("。，！？,.!?")
                if len(fragment) >= 2 and len(fragment) <= 20:
                    self.add_to_dimension("interests", fragment)
                    updated = True
                break

        # 专业领域检测
        if any(w in msg for w in ["我是做", "我的工作", "我在工厂", "我从事", "我的职业"]):
            for w in ["我是做", "我的工作是", "我在", "我从事", "我的职业是"]:
                if w in msg:
                    idx = msg.find(w)
                    fragment = user_message[idx + len(w):idx + len(w) + 30].strip().rstrip("。，！？,.!?")
                    if len(fragment) >= 2:
                        self.add_to_dimension("expertise", fragment)
                        updated = True
                    break

        # 交流风格检测
        if len(user_message) < 10:
            self.add_to_dimension("communication_style", "简洁短句")
            updated = True
        elif len(user_message) > 100:
            self.add_to_dimension("communication_style", "详细描述")
            updated = True

        # 情绪检测
        if any(w in msg for w in ["谢谢", "感谢", "太好了", "不错", "很棒"]):
            self.add_to_dimension("emotional_tone", "积极正面")
            updated = True
        elif any(w in msg for w in ["烦", "不爽", "累", "烦死了", "头疼"]):
            self.add_to_dimension("emotional_tone", "需要安慰")
            updated = True

        return updated


# 全局单例
_user_profile = None


def get_user_profile():
    global _user_profile
    if _user_profile is None:
        _user_profile = UserProfile()
    return _user_profile
