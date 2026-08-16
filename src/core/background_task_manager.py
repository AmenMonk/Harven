# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
后台任务管理器
支持多项专项培养任务并行执行（下载/搜索/安装等），
在任务管理对话框中显示进度，不阻塞聊天。
"""
import json
import os
import uuid
import threading
import time
from datetime import datetime
from enum import Enum

from src.models.database import DEFAULT_DB_PATH
import sqlite3
import logging
logger = logging.getLogger(__name__)


class TaskStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class BackgroundTaskType(str, Enum):
    SEARCH = "search"          # 联网搜索/知识搜索
    DOWNLOAD = "download"      # 下载资源
    INSTALL = "install"        # 安装插件/依赖
    PROCESS = "process"        # AI处理/分析
    CUSTOM = "custom"          # 自定义


class BackgroundTask:
    """单个后台任务"""

    def __init__(self, task_id, title, task_type="custom", conversation_id=None,
                 specialization_id=None, metadata=None):
        self.id = task_id
        self.title = title
        self.task_type = task_type
        self.conversation_id = conversation_id
        self.specialization_id = specialization_id
        self.metadata = metadata or {}

        # 状态
        self.status = "pending"        # pending / running / completed / failed / cancelled
        self.progress = 0              # 0-100
        self.current_step = ""         # 当前执行步骤描述
        self.error_message = ""

        # 步骤列表
        self.steps = []                # [{"name": str, "status": str, "detail": str}]
        self.current_step_index = -1

        # 时间
        self.created_at = datetime.now().isoformat()
        self.started_at = None
        self.completed_at = None

        # 线程
        self._thread = None
        self._cancel_event = threading.Event()

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "task_type": self.task_type,
            "conversation_id": self.conversation_id,
            "specialization_id": self.specialization_id,
            "status": self.status,
            "progress": self.progress,
            "current_step": self.current_step,
            "error_message": self.error_message,
            "steps": self.steps,
            "steps_total": len(self.steps),
            "steps_done": sum(1 for s in self.steps if s["status"] in ("success", "skipped")),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }


class BackgroundTaskManager:
    """
    后台任务管理器（单例）

    设计目标：
    - 多个专项培养任务可并行执行
    - 每个任务可包含多个步骤（搜索→下载→安装→验证...）
    - 进度实时更新，可在 TaskDialog 中查看
    - 不阻塞聊天界面
    """

    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()
        self._change_callbacks = []     # 注册 UI 更新回调
        self._max_concurrent = 5         # 最大并发数
        self._init_db()

    def _init_db(self):
        """初始化数据库表（持久化后台任务）"""
        db_path = os.path.normpath(DEFAULT_DB_PATH)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS background_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                task_type TEXT DEFAULT 'custom',
                conversation_id TEXT DEFAULT '',
                specialization_id TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                current_step TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                steps TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                started_at TEXT DEFAULT NULL,
                completed_at TEXT DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bgtask_status ON background_tasks(status);
            CREATE INDEX IF NOT EXISTS idx_bgtask_spec ON background_tasks(specialization_id);
        """)
        conn.commit()
        conn.close()

    def _load_pending_tasks(self):
        """从数据库恢复未完成的任务（标记为failed，因为进程已重启）"""
        db_path = os.path.normpath(DEFAULT_DB_PATH)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM background_tasks WHERE status IN ('pending', 'running') "
            "ORDER BY created_at DESC"
        ).fetchall()
        for r in rows:
            task = BackgroundTask(
                task_id=r["id"], title=r["title"], task_type=r["task_type"],
                conversation_id=r["conversation_id"] or None,
                specialization_id=r["specialization_id"] or None,
                metadata=json.loads(r["metadata"] or "{}"),
            )
            task.status = "failed"
            task.error_message = "进程重启，任务中断"
            task.progress = r["progress"] or 0
            task.current_step = r["current_step"] or ""
            task.steps = json.loads(r["steps"] or "[]")
            task.created_at = r["created_at"]
            task.started_at = r["started_at"]
            task.completed_at = datetime.now().isoformat()
            self._tasks[task.id] = task
            self._persist_task(task)
        # 也加载最近已完成的（不执行，仅显示）
        recent = conn.execute(
            "SELECT * FROM background_tasks WHERE status NOT IN ('pending', 'running') "
            "ORDER BY completed_at DESC LIMIT 50"
        ).fetchall()
        for r in recent:
            if r["id"] in self._tasks:
                continue
            task = BackgroundTask(
                task_id=r["id"], title=r["title"], task_type=r["task_type"],
                conversation_id=r["conversation_id"] or None,
                specialization_id=r["specialization_id"] or None,
                metadata=json.loads(r["metadata"] or "{}"),
            )
            task.status = r["status"]
            task.progress = r["progress"] or 0
            task.current_step = r["current_step"] or ""
            task.error_message = r["error_message"] or ""
            task.steps = json.loads(r["steps"] or "[]")
            task.created_at = r["created_at"]
            task.started_at = r["started_at"]
            task.completed_at = r["completed_at"]
            self._tasks[task.id] = task
        conn.close()

    def register_change_callback(self, callback):
        """注册变化回调（UI 更新用）"""
        if callback not in self._change_callbacks:
            self._change_callbacks.append(callback)

    def unregister_change_callback(self, callback):
        self._change_callbacks = [c for c in self._change_callbacks if c != callback]

    def _notify_change(self):
        """通知 UI 刷新"""
        for cb in self._change_callbacks:
            try:
                cb()
            except Exception as e:
                logger.debug(f"[BgTaskManager] 回调异常: {e}")

    def _persist_task(self, task: BackgroundTask):
        """持久化任务到数据库"""
        db_path = os.path.normpath(DEFAULT_DB_PATH)
        conn = sqlite3.connect(db_path)
        conn.execute("""
            INSERT OR REPLACE INTO background_tasks
            (id, title, task_type, conversation_id, specialization_id, status, progress,
             current_step, error_message, steps, metadata, created_at, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.id, task.title, task.task_type, task.conversation_id or "",
            task.specialization_id or "", task.status, task.progress,
            task.current_step, task.error_message,
            json.dumps(task.steps, ensure_ascii=False),
            json.dumps(task.metadata, ensure_ascii=False),
            task.created_at, task.started_at, task.completed_at
        ))
        conn.commit()
        conn.close()

    # ── 任务创建 ──

    def create_task(self, title, task_type="custom", conversation_id=None,
                    specialization_id=None, steps=None, metadata=None,
                    execute_func=None) -> str:
        """
        创建后台任务

        Args:
            title: 任务标题
            task_type: 任务类型 (search/download/install/process/custom)
            conversation_id: 关联对话ID
            specialization_id: 关联专项培养ID
            steps: 预定义步骤列表 [{"name": str}, ...]
            metadata: 额外元数据
            execute_func: 执行函数，签名为 func(task: BackgroundTask) -> None

        Returns:
            task_id: 任务ID
        """
        task_id = uuid.uuid4().hex[:16]
        task = BackgroundTask(
            task_id=task_id, title=title, task_type=task_type,
            conversation_id=conversation_id,
            specialization_id=specialization_id,
            metadata=metadata
        )

        # 预定义步骤
        if steps:
            task.steps = [{"name": s.get("name", str(s)), "status": "pending", "detail": ""}
                          for s in steps]

        with self._lock:
            self._tasks[task_id] = task

        self._persist_task(task)
        self._notify_change()

        # 如果提供了执行函数，立即在后台线程启动
        if execute_func:
            self._start_task_thread(task, execute_func)

        return task_id

    def create_and_execute(self, title, execute_func, task_type="custom",
                           conversation_id=None, specialization_id=None,
                           steps=None, metadata=None) -> str:
        """
        创建并立即执行后台任务（便捷方法）
        """
        return self.create_task(
            title=title, task_type=task_type,
            conversation_id=conversation_id,
            specialization_id=specialization_id,
            steps=steps, metadata=metadata,
            execute_func=execute_func
        )

    # ── 任务执行 ──

    def _start_task_thread(self, task: BackgroundTask, execute_func):
        """在后台线程中执行任务"""
        # 检查并发限制
        running = sum(1 for t in self._tasks.values()
                      if t.status == "running")
        if running >= self._max_concurrent:
            logger.debug(f"[BgTaskManager] 达到最大并发数 {self._max_concurrent}，任务 {task.id} 排队等待")
            # 延迟启动
            def _delayed_start():
                self._wait_and_execute(task, execute_func)
            threading.Thread(target=_delayed_start, daemon=True).start()
            return

        task.status = "running"
        task.started_at = datetime.now().isoformat()
        self._persist_task(task)
        self._notify_change()

        def _run():
            try:
                execute_func(task)
                if task.status != "cancelled":
                    task.status = "completed"
                    task.progress = 100
                    task.completed_at = datetime.now().isoformat()
            except Exception as e:
                if task.status != "cancelled":
                    task.status = "failed"
                    task.error_message = str(e)
                    task.completed_at = datetime.now().isoformat()
                    logger.error(f"[BgTaskManager] 任务 {task.id} 失败: {e}")
            finally:
                self._persist_task(task)
                self._notify_change()

        task._thread = threading.Thread(target=_run, daemon=True)
        task._thread.start()

    def _wait_and_execute(self, task: BackgroundTask, execute_func):
        """等待有空闲位置再执行"""
        while True:
            running = sum(1 for t in self._tasks.values()
                          if t.status == "running")
            if running < self._max_concurrent:
                break
            if task.status == "cancelled":
                return
            time.sleep(1)
        self._start_task_thread(task, execute_func)

    # ── 任务控制 ──

    def cancel_task(self, task_id) -> bool:
        """取消任务"""
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return False
        task._cancel_event.set()
        task.status = "cancelled"
        task.completed_at = datetime.now().isoformat()
        self._persist_task(task)
        self._notify_change()
        return True

    def is_cancelled(self, task_id) -> bool:
        """检查任务是否已取消"""
        task = self._tasks.get(task_id)
        return task._cancel_event.is_set() if task else True

    # ── 任务查询 ──

    def get_task(self, task_id) -> dict | None:
        """获取单个任务"""
        task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def get_all_tasks(self, status=None, specialization_id=None,
                      limit=50) -> list[dict]:
        """获取任务列表"""
        with self._lock:
            tasks = list(self._tasks.values())

        if status:
            tasks = [t for t in tasks if t.status == status]
        if specialization_id:
            tasks = [t for t in tasks if t.specialization_id == specialization_id]

        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def get_active_tasks(self) -> list[dict]:
        """获取运行中的任务"""
        return self.get_all_tasks(status="running")

    def get_running_count(self) -> int:
        """获取正在运行的任务数"""
        with self._lock:
            return sum(1 for t in self._tasks.values() if t.status == "running")

    # ── 步骤进度更新（执行函数中调用） ──

    def update_progress(self, task_id, progress: int, step_desc: str = ""):
        """
        更新任务进度

        Args:
            task_id: 任务ID
            progress: 进度百分比 (0-100)
            step_desc: 当前步骤描述
        """
        task = self._tasks.get(task_id)
        if not task:
            return
        task.progress = min(100, max(0, progress))
        task.current_step = step_desc
        self._persist_task(task)
        self._notify_change()

    def set_step_running(self, task_id, step_index: int):
        """标记步骤开始执行"""
        task = self._tasks.get(task_id)
        if task and step_index < len(task.steps):
            task.current_step_index = step_index
            task.steps[step_index]["status"] = "running"
            task.current_step = task.steps[step_index]["name"]
            self._persist_task(task)
            self._notify_change()

    def set_step_success(self, task_id, step_index: int, detail: str = ""):
        """标记步骤完成"""
        task = self._tasks.get(task_id)
        if task and step_index < len(task.steps):
            task.steps[step_index]["status"] = "success"
            task.steps[step_index]["detail"] = detail
            # 自动计算总进度
            done = sum(1 for s in task.steps if s["status"] in ("success", "skipped"))
            task.progress = int(done / max(1, len(task.steps)) * 100)
            self._persist_task(task)
            self._notify_change()

    def set_step_failed(self, task_id, step_index: int, detail: str = ""):
        """标记步骤失败"""
        task = self._tasks.get(task_id)
        if task and step_index < len(task.steps):
            task.steps[step_index]["status"] = "failed"
            task.steps[step_index]["detail"] = detail
            self._persist_task(task)
            self._notify_change()

    def set_step_skipped(self, task_id, step_index: int, detail: str = ""):
        """标记步骤跳过"""
        task = self._tasks.get(task_id)
        if task and step_index < len(task.steps):
            task.steps[step_index]["status"] = "skipped"
            task.steps[step_index]["detail"] = detail
            done = sum(1 for s in task.steps if s["status"] in ("success", "skipped"))
            task.progress = int(done / max(1, len(task.steps)) * 100)
            self._persist_task(task)
            self._notify_change()

    def add_step(self, task_id, name: str) -> int:
        """动态添加步骤"""
        task = self._tasks.get(task_id)
        if not task:
            return -1
        idx = len(task.steps)
        task.steps.append({"name": name, "status": "pending", "detail": ""})
        self._persist_task(task)
        return idx

    # ── 清理 ──

    def delete_task(self, task_id):
        """删除任务"""
        with self._lock:
            self._tasks.pop(task_id, None)
        # 同时从数据库删除
        db_path = os.path.normpath(DEFAULT_DB_PATH)
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM background_tasks WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        self._notify_change()

    def clear_completed(self, days=7):
        """清理已完成的任务"""
        cutoff = datetime.now().isoformat()
        with self._lock:
            to_remove = [
                tid for tid, t in self._tasks.items()
                if t.status in ("completed", "failed", "cancelled")
            ]
        for tid in to_remove:
            self.delete_task(tid)


# ── 全局单例 ──
_bg_task_manager = None
_bg_lock = threading.Lock()


def get_background_task_manager() -> BackgroundTaskManager:
    global _bg_task_manager
    if _bg_task_manager is None:
        with _bg_lock:
            if _bg_task_manager is None:
                _bg_task_manager = BackgroundTaskManager()
                # 恢复数据库中未完成的任务
                _bg_task_manager._load_pending_tasks()
    return _bg_task_manager
