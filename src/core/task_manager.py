# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
结构化任务管理系统
从对话中提取和管理结构化任务
支持任务创建、进度追踪、完成标记
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime

from src.models.database import DEFAULT_DB_PATH


class TaskManager:
    """任务管理器"""

    # 任务状态
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    # 任务优先级
    PRIORITY_LOW = 1
    PRIORITY_NORMAL = 2
    PRIORITY_HIGH = 3
    PRIORITY_URGENT = 4

    def __init__(self):
        self._db_path = os.path.normpath(DEFAULT_DB_PATH)
        self._init_table()

    def _init_table(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS structured_tasks (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
                conversation_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 2,
                progress INTEGER DEFAULT 0,
                due_date TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                completed_at TEXT DEFAULT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_conv ON structured_tasks(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON structured_tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_updated ON structured_tasks(updated_at DESC);
        """)
        conn.commit()
        conn.close()

    def create_task(self, conversation_id, title, description="", priority=2, tags=None, due_date=""):
        """创建任务"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        task_id = uuid.uuid4().hex[:16]
        tags_str = json.dumps(tags or [], ensure_ascii=False)
        conn.execute(
            "INSERT INTO structured_tasks "
            "(id, conversation_id, title, description, status, priority, progress, tags, due_date) "
            "VALUES (?, ?, ?, ?, 'pending', ?, 0, ?, ?)",
            (task_id, conversation_id, title, description, priority, tags_str, due_date)
        )
        conn.commit()
        conn.close()
        return task_id

    def update_task(self, task_id, title=None, description=None, status=None,
                    progress=None, tags=None, due_date=None, priority=None):
        """更新任务"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        updates = ["updated_at = datetime('now', 'localtime')"]
        params = []

        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status == self.STATUS_COMPLETED:
                updates.append("completed_at = datetime('now', 'localtime')")
        if progress is not None:
            updates.append("progress = ?")
            params.append(int(progress))
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if due_date is not None:
            updates.append("due_date = ?")
            params.append(due_date)
        if priority is not None:
            updates.append("priority = ?")
            params.append(int(priority))

        params.append(task_id)
        conn.execute(f"UPDATE structured_tasks SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        conn.close()

    def get_tasks(self, conversation_id=None, status=None, limit=50):
        """获取任务列表"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM structured_tasks WHERE 1=1"
        params = []
        if conversation_id:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY priority DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        tasks = []
        for r in rows:
            task = dict(r)
            try:
                task["tags"] = json.loads(task.get("tags", "[]"))
            except Exception:
                task["tags"] = []
            tasks.append(task)
        return tasks

    def get_active_tasks(self, conversation_id=None):
        """获取进行中的任务"""
        return self.get_tasks(
            conversation_id=conversation_id,
            status=self.STATUS_IN_PROGRESS
        )

    def get_pending_tasks(self, conversation_id=None):
        """获取待处理的任务"""
        return self.get_tasks(
            conversation_id=conversation_id,
            status=self.STATUS_PENDING
        )

    def complete_task(self, task_id):
        """完成任务"""
        self.update_task(task_id, status=self.STATUS_COMPLETED, progress=100)

    def cancel_task(self, task_id):
        """取消任务"""
        self.update_task(task_id, status=self.STATUS_CANCELLED)

    def delete_task(self, task_id):
        """删除任务"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("DELETE FROM structured_tasks WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()

    def build_task_context(self, conversation_id):
        """构建任务上下文（注入到系统提示词）"""
        active = self.get_active_tasks(conversation_id)
        pending = self.get_pending_tasks(conversation_id)
        if not active and not pending:
            return ""

        parts = []
        if active:
            task_text = "\n".join(
                f"  - [{t['title']}] 进度 {t['progress']}%"
                for t in active[:5]
            )
            parts.append(f"### 进行中的任务\n{task_text}")
        if pending:
            task_text = "\n".join(
                f"  - {t['title']}" + (f" (截止: {t['due_date']})" if t['due_date'] else "")
                for t in pending[:5]
            )
            parts.append(f"### 待处理的任务\n{task_text}")

        return "\n\n## 当前任务列表（主人正在进行的计划）\n" + "\n\n".join(parts)

    def extract_tasks_from_conversation(self, conversation_id, user_message, ai_response):
        """
        从对话中提取结构化任务
        简单实现：检测用户提到的待办/计划/目标
        """
        msg = user_message.lower()
        created = []

        # 检测任务相关关键词
        task_patterns = [
            "我需要", "帮我做", "我想", "我打算", "我要",
            "计划", "准备", "打算", "目标", "安排",
            "todo", "待办", "任务",
        ]
        is_task = any(p in msg for p in task_patterns)

        if is_task and len(user_message) >= 5:
            # 提取任务标题
            title = user_message.strip().rstrip("。！？.!?")
            if len(title) > 50:
                title = title[:50] + "..."

            # 避免重复
            existing = self.get_tasks(conversation_id, status=self.STATUS_PENDING)
            for t in existing:
                if t["title"] == title:
                    return created

            task_id = self.create_task(
                conversation_id=conversation_id,
                title=title,
                description=f"来源对话: {user_message[:200]}"
            )
            created.append(task_id)

        return created


# 全局单例
_task_manager = None


def get_task_manager():
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
