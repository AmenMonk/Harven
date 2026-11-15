# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
定时任务调度器 — Harven AI
基于 APScheduler，支持 Cron 表达式和自然语言配置定时任务

功能：
- 添加/删除/暂停/恢复定时任务
- 支持 Cron 表达式和简单间隔
- 任务持久化到 SQLite
- 到时间执行 AI 对话（调用 engine 生成回复）
"""
import json
import logging
import threading
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sqlite" / "lingzhi.db"


def _get_conn():
    """创建带 WAL + 超时的 SQLite 连接（防止 database is locked）"""
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _dedup_window_seconds(schedule_type: str, schedule_config_json: str) -> Optional[float]:
    """
    ★ 2026-09-12: 防重窗口计算 — 多进程并存兜底
    背景：旧进程未杀干净 + 新进程同时运行时，两个 APScheduler 都会在整点触发同一任务
    （实测 2026-06-30 跑了 72 次、07-01 跑了 83 次，正常应为 24 次/天）。
    返回值：同一任务两次触发之间的最小安全间隔（秒）。None 表示不防重（once 任务）。
    """
    try:
        cfg = json.loads(schedule_config_json or '{}')
    except Exception:
        cfg = {}
    try:
        if schedule_type == 'interval':
            total = float(cfg.get('minutes', 60)) * 60 + float(cfg.get('hours', 0)) * 3600
            return max(60.0, total * 0.9)
        if schedule_type == 'cron':
            hour = cfg.get('hour', '*')
            minute = cfg.get('minute', 0)
            if hour == '*' or hour == '*/1':
                # 分钟级循环
                if isinstance(minute, int):
                    return 54 * 60  # 每小时一次 → 54 分钟窗口
                m = str(minute)
                if m.startswith('*/'):
                    step = float(m[2:]) * 60
                    return max(60.0, step * 0.9)
                return 54 * 60
            # 每天固定时刻（含 day_of_week 限定）→ 20 小时窗口
            return 20 * 3600
        return None  # once 及其他：不防重
    except Exception:
        return None

# ─── 检测 APScheduler ───
def _check_apscheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        return True
    except ImportError:
        return False

APSCHEDULER_AVAILABLE = _check_apscheduler()


# ─── 任务数据结构 ───
class ScheduledTask:
    def __init__(self,
                 task_id: str,
                 name: str,
                 prompt: str,
                 schedule_type: str,     # "cron" | "interval" | "once"
                 schedule_config: Dict,  # cron: {hour, minute, ...} | interval: {minutes} | once: {run_at}
                 enabled: bool = True,
                 created_at: str = None):
        self.task_id = task_id
        self.name = name
        self.prompt = prompt
        self.schedule_type = schedule_type
        self.schedule_config = schedule_config
        self.enabled = enabled
        self.created_at = created_at or datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "prompt": self.prompt,
            "schedule_type": self.schedule_type,
            "schedule_config": self.schedule_config,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


# ─── 核心调度器 ───
class TaskScheduler:
    """
    Harven AI 定时任务调度器
    """

    def __init__(self):
        self._scheduler = None
        self._lock = threading.Lock()
        self._task_callback: Optional[Callable] = None  # 执行任务时的回调
        self._init_db()
        if APSCHEDULER_AVAILABLE:
            self._init_scheduler()
        else:
            logger.warning("[Scheduler] APScheduler 未安装，定时任务功能不可用")

    def _init_db(self):
        """初始化任务表"""
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                task_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                prompt TEXT NOT NULL,
                schedule_type TEXT NOT NULL,
                schedule_config TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                last_run TEXT,
                next_run TEXT,
                created_at TEXT
            )
        """)
        # 执行历史表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                name TEXT,
                prompt TEXT,
                started_at TEXT,
                completed_at TEXT,
                status TEXT DEFAULT 'running',
                result TEXT,
                error TEXT,
                FOREIGN KEY (task_id) REFERENCES scheduled_tasks(task_id) ON DELETE CASCADE
            )
        """)
        conn.commit()
        conn.close()

    def _init_scheduler(self):
        """初始化 APScheduler"""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.executors.pool import ThreadPoolExecutor

        jobstores = {"default": MemoryJobStore()}
        executors = {"default": ThreadPoolExecutor(4)}
        self._scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            timezone="Asia/Shanghai"
        )
        self._scheduler.start()
        logger.info("[Scheduler] APScheduler 已启动")
        # 从数据库恢复任务
        self._restore_tasks()

    def set_callback(self, callback: Callable):
        """设置任务执行回调函数"""
        self._task_callback = callback

    def _restore_tasks(self):
        """服务启动时从 DB 恢复所有启用的任务"""
        tasks = self.get_all_tasks()
        for task in tasks:
            if task.enabled:
                try:
                    self._register_to_scheduler(task)
                except Exception as e:
                    logger.error(f"[Scheduler] 恢复任务失败 {task.task_id}: {e}")

    def add_task(self, task: ScheduledTask) -> Dict:
        """添加定时任务"""
        if not APSCHEDULER_AVAILABLE:
            return {"success": False, "error": "APScheduler 未安装，请运行：pip install apscheduler"}

        with self._lock:
            # 保存到 DB
            conn = _get_conn()
            conn.execute("""
                INSERT OR REPLACE INTO scheduled_tasks
                (task_id, name, prompt, schedule_type, schedule_config, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                task.task_id, task.name, task.prompt,
                task.schedule_type, json.dumps(task.schedule_config, ensure_ascii=False),
                1 if task.enabled else 0,
                task.created_at
            ))
            conn.commit()
            conn.close()

            # 注册到调度器
            if task.enabled:
                try:
                    self._register_to_scheduler(task)
                except Exception as e:
                    return {"success": False, "error": f"调度注册失败：{str(e)}"}

        return {"success": True, "task_id": task.task_id}

    def _register_to_scheduler(self, task: ScheduledTask):
        """注册任务到 APScheduler"""
        job_id = task.task_id

        # 先移除旧的同 ID 任务
        try:
            self._scheduler.remove_job(job_id)
        except Exception as _e: logger.debug(f"[scheduler] 忽略: {_e}")

        if task.schedule_type == "cron":
            cfg = task.schedule_config
            self._scheduler.add_job(
                self._run_task,
                trigger="cron",
                id=job_id,
                kwargs={"task_id": task.task_id, "prompt": task.prompt},
                hour=cfg.get("hour", "*"),
                minute=cfg.get("minute", 0),
                day_of_week=cfg.get("day_of_week", "*"),
                day=cfg.get("day", "*"),
                month=cfg.get("month", "*"),
            )
        elif task.schedule_type == "interval":
            cfg = task.schedule_config
            self._scheduler.add_job(
                self._run_task,
                trigger="interval",
                id=job_id,
                kwargs={"task_id": task.task_id, "prompt": task.prompt},
                minutes=cfg.get("minutes", 60),
                hours=cfg.get("hours", 0),
            )
        elif task.schedule_type == "once":
            cfg = task.schedule_config
            run_at = datetime.fromisoformat(cfg["run_at"])
            self._scheduler.add_job(
                self._run_task,
                trigger="date",
                id=job_id,
                run_date=run_at,
                kwargs={"task_id": task.task_id, "prompt": task.prompt},
            )

    def _run_task(self, task_id: str, prompt: str):
        """执行定时任务（APScheduler 调用此方法）"""
        logger.info(f"[Scheduler] 执行任务 {task_id}：{prompt[:50]}")

        # 记录执行开始
        started_at = datetime.now().isoformat()
        run_id = None
        conn = _get_conn()
        try:
            # 获取任务名称 + 调度配置（用于防重窗口计算）
            task_name = ""
            row = conn.execute(
                "SELECT name, schedule_type, schedule_config FROM scheduled_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row:
                task_name = row[0]

            # ★ 2026-09-12: 原子占位防重 — 同一时间窗内只允许一个进程触发
            #   UPDATE 带 last_run 条件是原子操作：多进程同时触发时只有一个 rowcount=1，其余跳过
            _window = _dedup_window_seconds(row[1], row[2]) if row else None
            if _window is not None:
                from datetime import timedelta
                _cutoff = (datetime.now() - timedelta(seconds=_window)).isoformat()
                _cur = conn.execute(
                    "UPDATE scheduled_tasks SET last_run=? "
                    "WHERE task_id=? AND (last_run IS NULL OR last_run < ?)",
                    (started_at, task_id, _cutoff)
                )
                conn.commit()
                if _cur.rowcount == 0:
                    logger.warning(
                        f"[Scheduler] 任务 {task_id} 在防重窗口（{_window/60:.0f}分钟）内已被触发，本次跳过"
                    )
                    return
            else:
                # 更新最后执行时间（once 任务或无防重）
                conn.execute(
                    "UPDATE scheduled_tasks SET last_run=? WHERE task_id=?",
                    (started_at, task_id)
                )
                conn.commit()

            # 插入执行记录
            conn.execute(
                """INSERT INTO scheduled_task_runs (task_id, name, prompt, started_at, status)
                   VALUES (?, ?, ?, ?, 'running')""",
                (task_id, task_name, prompt, started_at)
            )
            conn.commit()
            run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

        # 调用回调（由 web_app 注入）
        error_msg = ""
        if self._task_callback:
            try:
                self._task_callback(task_id=task_id, prompt=prompt)
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[Scheduler] 任务 {task_id} 执行失败：{e}")

        # 更新执行结果
        completed_at = datetime.now().isoformat()
        conn = _get_conn()
        try:
            conn.execute(
                """UPDATE scheduled_task_runs
                   SET completed_at=?, status=?, error=?
                   WHERE id=?""",
                (completed_at, "completed" if not error_msg else "failed", error_msg, run_id)
            )
            conn.commit()
        finally:
            conn.close()

    def delete_task(self, task_id: str) -> Dict:
        """删除任务"""
        with self._lock:
            conn = _get_conn()
            conn.execute("DELETE FROM scheduled_tasks WHERE task_id=?", (task_id,))
            conn.commit()
            conn.close()
            if self._scheduler:
                try:
                    self._scheduler.remove_job(task_id)
                except Exception as _e: logger.debug(f"[scheduler] 忽略: {_e}")
        return {"success": True}

    def toggle_task(self, task_id: str, enabled: bool) -> Dict:
        """启用/禁用任务"""
        with self._lock:
            conn = _get_conn()
            conn.execute(
                "UPDATE scheduled_tasks SET enabled=? WHERE task_id=?",
                (1 if enabled else 0, task_id)
            )
            conn.commit()
            conn.close()

            if self._scheduler:
                if enabled:
                    tasks = self.get_all_tasks()
                    for t in tasks:
                        if t.task_id == task_id:
                            self._register_to_scheduler(t)
                            break
                else:
                    try:
                        self._scheduler.pause_job(task_id)
                    except Exception as _e: logger.debug(f"[scheduler] 忽略: {_e}")
        return {"success": True}

    def get_all_tasks(self) -> List[ScheduledTask]:
        """获取所有任务"""
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY created_at DESC").fetchall()
        conn.close()

        tasks = []
        for row in rows:
            tasks.append(ScheduledTask(
                task_id=row["task_id"],
                name=row["name"],
                prompt=row["prompt"],
                schedule_type=row["schedule_type"],
                schedule_config=json.loads(row["schedule_config"]),
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
            ))
        return tasks

    def get_next_runs(self) -> List[Dict]:
        """获取各任务下次执行时间"""
        if not self._scheduler:
            return []
        result = []
        for job in self._scheduler.get_jobs():
            result.append({
                "task_id": job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            })
        return result

    def get_run_history(self, task_id: str = None, limit: int = 30) -> List[Dict]:
        """获取任务执行历史"""
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        if task_id:
            rows = conn.execute(
                """SELECT * FROM scheduled_task_runs
                   WHERE task_id=? ORDER BY started_at DESC LIMIT ?""",
                (task_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_task_runs ORDER BY started_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def shutdown(self):
        """关闭调度器"""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)


# ─── 自然语言解析定时任务描述 ───
def parse_schedule_from_text(text: str) -> Optional[Dict]:
    """
    从自然语言中解析定时配置
    例：
      "每天早上8点" → {"type": "cron", "hour": 8, "minute": 0}
      "每小时" → {"type": "interval", "minutes": 60}
      "每30分钟" → {"type": "interval", "minutes": 30}
      "明天下午3点" → {"type": "once", "run_at": "..."}
    """
    import re
    text = text.strip().lower()

    # 每N分钟
    m = re.search(r"每\s*(\d+)\s*分钟", text)
    if m:
        return {"type": "interval", "minutes": int(m.group(1))}

    # 每N小时
    m = re.search(r"每\s*(\d+)\s*小时", text)
    if m:
        return {"type": "interval", "minutes": int(m.group(1)) * 60}

    # 每小时
    if "每小时" in text or "每个小时" in text:
        return {"type": "interval", "minutes": 60}

    # 每天 + 时间
    hour_map = {"早上": 8, "上午": 9, "中午": 12, "下午": 14, "傍晚": 17, "晚上": 20, "凌晨": 3}
    for word, default_hour in hour_map.items():
        if word in text:
            h_match = re.search(r"(\d{1,2})点", text)
            hour = int(h_match.group(1)) if h_match else default_hour
            m_match = re.search(r"(\d{1,2})分", text)
            minute = int(m_match.group(1)) if m_match else 0
            if any(d in text for d in ["每天", "每日", "每天"]):
                return {"type": "cron", "hour": hour, "minute": minute}

    # 每周几 + 时间
    weekday_map = {"周一": "mon", "周二": "tue", "周三": "wed", "周四": "thu",
                   "周五": "fri", "周六": "sat", "周日": "sun",
                   "星期一": "mon", "星期二": "tue", "星期三": "wed", "星期四": "thu",
                   "星期五": "fri", "星期六": "sat", "星期天": "sun"}
    for word, day_abbr in weekday_map.items():
        if word in text:
            h_match = re.search(r"(\d{1,2})点", text)
            hour = int(h_match.group(1)) if h_match else 9
            return {"type": "cron", "hour": hour, "minute": 0, "day_of_week": day_abbr}

    return None


# ─── 单例 ───
_scheduler: Optional[TaskScheduler] = None

def get_scheduler() -> TaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler
