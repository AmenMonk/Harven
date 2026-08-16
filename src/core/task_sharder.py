# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
长任务分片器 v1.0
- 将复杂长任务拆分为原子子任务
- 每子任务有独立 checkpoint，支持断点续跑
- SSE 推送进度百分比
- 重启后自动检测未完成任务

使用：
  from src.core.task_sharder import TaskSharder
  sharder = TaskSharder(engine, conv_id)
  for progress in sharder.execute(task_description):
      yield ("task_progress", progress)
  yield ("task_done", result)
"""

import json
import uuid
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Generator

logger = logging.getLogger(__name__)

CHECKPOINT_TABLE = "task_checkpoints"


class TaskSharder:
    """长任务分片执行器"""

    def __init__(self, engine, conversation_id: str, db_path: str = None):
        self.engine = engine
        self.conversation_id = conversation_id
        self.db_path = db_path or str(
            Path(__file__).parent.parent.parent / "data" / "sqlite" / "lingzhi.db"
        )
        self._task_id = None
        self._ensure_table()

    # ── 数据库初始化 ──

    def _ensure_table(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
                    task_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    total_steps INTEGER DEFAULT 0,
                    current_step INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    subtasks TEXT DEFAULT '[]',
                    results TEXT DEFAULT '[]',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[TaskSharder] 初始化表失败: {e}")

    # ── 任务执行 ──

    def execute(self, description: str, max_per_step: int = 3) -> Generator:
        """
        执行长任务。

        Args:
            description: 任务描述
            max_per_step: 每步子任务数

        Yields:
            {"type": "progress", "task_id": ..., "step": N, "total": M, "pct": %, "status": "..."}
            {"type": "done", "task_id": ..., "summary": "..."}
            {"type": "error", "message": "..."}
        """
        self._task_id = f"task_{uuid.uuid4().hex[:12]}"

        # Step 1: 让语言模型分析任务，拆分子步骤
        yield {"type": "progress", "task_id": self._task_id, "step": 0, "total": 1,
               "pct": 0, "status": "分析任务中..."}

        subtasks = self._plan_subtasks(description, max_per_step)
        if not subtasks:
            yield {"type": "error", "task_id": self._task_id, "message": "无法拆分任务"}
            return

        total = len(subtasks)
        self._save_checkpoint(status="running", total_steps=total, subtasks=subtasks)

        # Step 2: 逐子任务执行
        results = []
        for i, subtask in enumerate(subtasks, 1):
            # 检查是否已取消
            if self._is_cancelled():
                yield {"type": "cancelled", "task_id": self._task_id}
                return

            pct = int(i / total * 100)
            yield {"type": "progress", "task_id": self._task_id,
                   "step": i, "total": total, "pct": pct,
                   "status": f"执行中 ({i}/{total}): {subtask[:40]}..."}

            # 执行子任务
            try:
                result = self._execute_subtask(subtask, i, total)
                results.append({"step": i, "task": subtask, "result": result, "ok": True})
            except Exception as e:
                results.append({"step": i, "task": subtask, "result": str(e), "ok": False})
                logger.warning(f"[TaskSharder] 子任务 {i} 失败: {e}")
                # 继续执行下一个子任务

            # 存 checkpoint
            self._save_checkpoint(current_step=i, results=results)

        # Step 3: 汇总结果
        summary = self._summarize(results)
        self._save_checkpoint(status="completed", current_step=total, results=results)

        yield {"type": "done", "task_id": self._task_id, "summary": summary}

    # ── 断点续跑 ──

    @classmethod
    def get_pending_tasks(cls, db_path: str) -> list[dict]:
        """获取所有未完成的任务"""
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM {CHECKPOINT_TABLE} WHERE status = 'running' ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[TaskSharder] 查询未完成任务失败: {e}")
            return []

    def resume(self, task_id: str) -> Generator:
        """断点续跑"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT * FROM {CHECKPOINT_TABLE} WHERE task_id = ?", (task_id,)
            ).fetchone()
            conn.close()

            if not row:
                yield {"type": "error", "task_id": task_id, "message": "任务不存在"}
                return

            row = dict(row)
            self._task_id = task_id
            subtasks = json.loads(row.get("subtasks", "[]"))
            results = json.loads(row.get("results", "[]"))
            current = row.get("current_step", 0)
            total = len(subtasks)

            yield {"type": "progress", "task_id": task_id, "step": current,
                   "total": total, "pct": int(current / total * 100) if total else 0,
                   "status": "从断点续跑..."}

            # 继续执行剩余子任务
            for i in range(current, total):
                subtask = subtasks[i]
                i += 1  # 转为1-based
                pct = int(i / total * 100)

                yield {"type": "progress", "task_id": task_id,
                       "step": i, "total": total, "pct": pct,
                       "status": f"续跑 ({i}/{total}): {subtask[:40]}..."}

                try:
                    result = self._execute_subtask(subtask, i, total)
                    results.append({"step": i, "task": subtask, "result": result, "ok": True})
                except Exception as e:
                    results.append({"step": i, "task": subtask, "result": str(e), "ok": False})

                self._save_checkpoint(current_step=i, results=results)

            summary = self._summarize(results)
            self._save_checkpoint(status="completed", current_step=total, results=results)
            yield {"type": "done", "task_id": task_id, "summary": summary}

        except Exception as e:
            logger.error(f"[TaskSharder] 续跑失败: {e}")
            yield {"type": "error", "task_id": task_id, "message": str(e)}

    def cancel(self, task_id: str):
        """取消任务"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                f"UPDATE {CHECKPOINT_TABLE} SET status = 'cancelled', updated_at = ? WHERE task_id = ?",
                (datetime.now(timezone.utc).isoformat(), task_id)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[TaskSharder] 取消任务失败: {e}")

    # ── 内部方法 ──

    def _plan_subtasks(self, description: str, max_per_step: int = 3) -> list[str]:
        """使用语言模型将任务拆分为子步骤"""
        prompt = (
            f"请将以下任务拆分为 {max_per_step} 个以内的原子步骤。\n"
            f"每个步骤应是一个独立的、可执行的子任务。\n"
            f"只列出步骤，每行一个，以 '- ' 开头。\n\n"
            f"任务：{description}\n\n"
            f"子步骤："
        )
        try:
            response = self.engine._quick_model_call(prompt)
            subtasks = []
            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("- ") or line.startswith("* "):
                    subtask = line[2:].strip()
                    if subtask:
                        subtasks.append(subtask)
            return subtasks[:max_per_step]
        except Exception as e:
            logger.warning(f"[TaskSharder] 任务拆分失败: {e}")
            # 回退：整个任务作为一个步骤
            return [description]

    def _execute_subtask(self, subtask: str, step: int, total: int) -> str:
        """执行单个子任务"""
        prompt = (
            f"你正在执行一个多步骤任务（步骤 {step}/{total}）。\n"
            f"当前步骤：{subtask}\n\n"
            f"请执行该步骤并返回结果。保持简洁，200字以内。"
        )
        return self.engine._quick_model_call(prompt)

    def _summarize(self, results: list[dict]) -> str:
        """汇总所有子任务结果"""
        ok_count = sum(1 for r in results if r.get("ok"))
        total = len(results)
        parts = [f"### 任务完成 ({ok_count}/{total} 步骤成功)\n"]
        for r in results:
            icon = "✅" if r.get("ok") else "❌"
            parts.append(f"{icon} **步骤 {r['step']}**: {r.get('result', '')[:100]}")
        return "\n".join(parts)

    def _save_checkpoint(self, **kwargs):
        """保存检查点到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            now = datetime.now(timezone.utc).isoformat()

            existing = conn.execute(
                f"SELECT task_id FROM {CHECKPOINT_TABLE} WHERE task_id = ?",
                (self._task_id,)
            ).fetchone()

            if existing:
                sets = []
                vals = []
                for k, v in kwargs.items():
                    sets.append(f"{k} = ?")
                    vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                sets.append("updated_at = ?")
                vals.append(now)
                vals.append(self._task_id)
                conn.execute(
                    f"UPDATE {CHECKPOINT_TABLE} SET {', '.join(sets)} WHERE task_id = ?",
                    vals
                )
            else:
                cols = ["task_id", "conversation_id", "created_at", "updated_at"]
                vals = [self._task_id, self.conversation_id, now, now]
                for k, v in kwargs.items():
                    cols.append(k)
                    vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                placeholders = ", ".join(["?"] * len(vals))
                conn.execute(
                    f"INSERT INTO {CHECKPOINT_TABLE} ({', '.join(cols)}) VALUES ({placeholders})",
                    vals
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[TaskSharder] checkpoint 保存失败: {e}")

    def _is_cancelled(self) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                f"SELECT status FROM {CHECKPOINT_TABLE} WHERE task_id = ?",
                (self._task_id,)
            ).fetchone()
            conn.close()
            return row and row[0] == "cancelled"
        except Exception:
            return False
