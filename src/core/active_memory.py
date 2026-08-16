# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Active Memory — 主动记忆检索系统
在对话时自动拉取与当前任务相关的历史记忆、用户偏好、过往结论
让 Harven AI 不需要用户重复背景信息，自动"想起来"
"""
import sqlite3
import logging
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sqlite" / "lingzhi.db"


# ─── 记忆条目 ───
class MemoryEntry:
    def __init__(self, content: str, category: str, relevance: float = 0.0,
                 created_at: str = None, source: str = "auto"):
        self.content = content
        self.category = category
        self.relevance = relevance
        self.created_at = created_at or datetime.now().isoformat()
        self.source = source  # "auto" | "user" | "task"


# ─── Active Memory 核心类 ───
class ActiveMemory:
    """
    主动记忆系统
    在每次对话时，自动扫描相关记忆并注入上下文
    """

    # 关键词 → 记忆分类映射
    CATEGORY_KEYWORDS = {
        "identity": ["名字", "叫", "年龄", "住在", "城市", "职业", "公司", "学校"],
        "preference": ["喜欢", "不喜欢", "偏好", "习惯", "风格", "口味"],
        "project": ["项目", "开发", "代码", "功能", "需求", "计划", "版本"],
        "task": ["任务", "todo", "待办", "完成", "进度", "截止"],
        "knowledge": ["学过", "了解", "知道", "研究", "发现", "总结"],
        "emotion": ["心情", "感觉", "开心", "难过", "压力", "放松"],
    }

    def __init__(self):
        self._init_db()

    def _init_db(self):
        """初始化 Active Memory 表"""
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS active_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                keywords TEXT,
                source TEXT DEFAULT 'auto',
                access_count INTEGER DEFAULT 0,
                last_accessed TEXT,
                created_at TEXT
            )
        """)
        # 索引加速检索
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_active_memory_category
            ON active_memory (category)
        """)
        conn.commit()
        conn.close()

    def store(self, content: str, category: str = "general",
              keywords: List[str] = None, source: str = "auto") -> int:
        """存储一条记忆"""
        if not content.strip():
            return -1
        keywords_str = ",".join(keywords or self._extract_keywords(content))
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.execute("""
            INSERT INTO active_memory (content, category, keywords, source, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (content, category, keywords_str, source, datetime.now().isoformat()))
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id

    def retrieve(self, query: str, top_k: int = 5,
                 categories: List[str] = None) -> List[MemoryEntry]:
        """
        根据查询内容检索相关记忆
        使用关键词重叠度评分（轻量方案，无需向量数据库）
        """
        query_keywords = set(self._extract_keywords(query))
        if not query_keywords:
            return []

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # 构建查询
        if categories:
            placeholders = ",".join("?" * len(categories))
            rows = conn.execute(
                f"SELECT * FROM active_memory WHERE category IN ({placeholders}) "
                f"ORDER BY created_at DESC LIMIT 200",
                categories
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM active_memory ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        conn.close()

        # 计算相关度
        scored = []
        for row in rows:
            mem_keywords = set((row["keywords"] or "").split(","))
            mem_content_words = set(self._extract_keywords(row["content"]))
            all_mem_words = mem_keywords | mem_content_words

            if not all_mem_words:
                continue

            # Jaccard 相似度
            overlap = len(query_keywords & all_mem_words)
            if overlap == 0:
                continue
            score = overlap / len(query_keywords | all_mem_words)

            scored.append(MemoryEntry(
                content=row["content"],
                category=row["category"],
                relevance=score,
                created_at=row["created_at"],
                source=row["source"],
            ))

        # 按相关度排序，取 top_k
        scored.sort(key=lambda x: x.relevance, reverse=True)
        result = scored[:top_k]

        # 更新访问计数
        if result:
            self._update_access(rows, result)

        return result

    def _update_access(self, all_rows, retrieved: List[MemoryEntry]):
        """更新被检索记忆的访问次数"""
        contents = {e.content for e in retrieved}
        conn = sqlite3.connect(str(DB_PATH))
        for row in all_rows:
            if row["content"] in contents:
                conn.execute("""
                    UPDATE active_memory SET access_count = access_count + 1,
                    last_accessed = ? WHERE id = ?
                """, (datetime.now().isoformat(), row["id"]))
        conn.commit()
        conn.close()

    def retrieve_as_context(self, query: str, top_k: int = 5) -> str:
        """
        检索相关记忆，格式化为可直接注入系统提示词的上下文字符串
        """
        memories = self.retrieve(query, top_k=top_k)
        if not memories:
            return ""

        lines = ["【相关记忆】"]
        for i, mem in enumerate(memories, 1):
            lines.append(f"{i}. [{mem.category}] {mem.content}")
        return "\n".join(lines)

    def auto_extract_from_conversation(self, user_message: str, ai_response: str) -> int:
        """
        从对话中自动提取值得记住的信息并存储
        返回新存储的记忆数量
        """
        count = 0
        combined = user_message + " " + ai_response

        # 检测各类关键信息
        patterns = [
            # 用户身份信息
            (r"(?:我叫|我的名字是|my name is)\s*(\w+)", "identity", "用户名字：{}"),
            (r"(?:我住在|我在|我是|I live in|I am from)\s*(\w+(?:市|省|区|县|city)?)", "identity", "用户位置：{}"),
            (r"(?:我是|我从事|我的职业是|I am a)\s*(\w+(?:师|员|工|长|理|官)?)", "identity", "用户职业：{}"),
            # 项目偏好
            (r"(?:我们用的是|项目使用|技术栈是|we use|using)\s*([\w\s,]+?)(?:开发|作为|框架|$)", "project", "技术栈：{}"),
            # 用户偏好
            (r"(?:我喜欢|I prefer|I like)\s*(.{5,40}?)(?:，|。|$|\n)", "preference", "用户偏好：{}"),
            (r"(?:记住|请记住|remember that)\s*(.{5,80}?)(?:，|。|$|\n)", "explicit", "{}"),
        ]

        for pattern, category, template in patterns:
            m = re.search(pattern, combined, re.IGNORECASE)
            if m:
                value = m.group(1).strip()
                if len(value) > 1:
                    content = template.format(value)
                    # 检查是否重复
                    if not self._is_duplicate(content):
                        self.store(content, category=category, source="auto_extract")
                        count += 1

        return count

    def _is_duplicate(self, content: str, threshold: float = 0.8) -> bool:
        """检查是否已有相似记忆"""
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT content FROM active_memory ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        conn.close()

        kw_new = set(self._extract_keywords(content))
        for row in rows:
            kw_existing = set(self._extract_keywords(row[0]))
            if not kw_new or not kw_existing:
                continue
            overlap = len(kw_new & kw_existing) / max(len(kw_new | kw_existing), 1)
            if overlap >= threshold:
                return True
        return False

    def get_all(self, limit: int = 100) -> List[Dict]:
        """获取所有记忆（用于管理界面）"""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM active_memory ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def delete(self, memory_id: int) -> bool:
        """删除指定记忆"""
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("DELETE FROM active_memory WHERE id=?", (memory_id,))
        conn.commit()
        conn.close()
        return True

    def clear_category(self, category: str) -> int:
        """清空某类别的记忆"""
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.execute("DELETE FROM active_memory WHERE category=?", (category,))
        count = cur.rowcount
        conn.commit()
        conn.close()
        return count

    def _extract_keywords(self, text: str) -> List[str]:
        """简单关键词提取（去除停用词）"""
        stop_words = {
            "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
            "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你",
            "会", "着", "没有", "看", "好", "自己", "这", "那", "什么",
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "have", "has", "do", "does", "will", "can", "may", "should",
        }
        # 简单分词（中英文混合）
        words = re.findall(r'[\u4e00-\u9fff]{1,4}|[a-zA-Z0-9_]{2,20}', text.lower())
        return [w for w in words if w not in stop_words and len(w) > 1]


# ─── 单例 ───
_active_memory: Optional[ActiveMemory] = None

def get_active_memory() -> ActiveMemory:
    global _active_memory
    if _active_memory is None:
        _active_memory = ActiveMemory()
    return _active_memory
