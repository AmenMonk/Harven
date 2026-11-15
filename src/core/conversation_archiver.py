# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
对话归档模块 — 自动归档旧对话

策略：
- 超过 30 天未活跃的对话自动归档
- 归档到 data/archives/YYYY-MM/ 目录
- 保留 JSON 格式，支持恢复
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple
import logging
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sqlite" / "lingzhi.db"
ARCHIVE_ROOT = PROJECT_ROOT / "data" / "archives"


class ConversationArchiver:
    """对话归档管理器"""

    def __init__(self, db_path: str = None, archive_root: str = None):
        self._db_path = db_path or str(DB_PATH)
        self._archive_root = Path(archive_root or ARCHIVE_ROOT)

    def list_archivable(self, days_inactive: int = 30) -> List[dict]:
        """列出可归档的对话（超过N天未活跃）"""
        cutoff = (datetime.now() - timedelta(days=days_inactive)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT id, title, specialization_id, created_at, updated_at
                   FROM conversations
                   WHERE is_archived = 0
                     AND updated_at < ?
                   ORDER BY updated_at ASC""",
                (cutoff,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def archive_conversation(self, conv_id: str) -> Tuple[bool, str]:
        """归档单个对话（导出消息 + 标记为已归档）"""
        # 1. 读取消息
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            conv = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            if not conv:
                return False, "对话不存在"

            messages = conn.execute(
                # ★ 2026-09-20 修复: messages 表时间列实为 created_at（旧写 timestamp 导致
                # 启动报 no such column: timestamp，自动归档一直失败）
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
                (conv_id,),
            ).fetchall()
        finally:
            conn.close()

        # 2. 导出为 JSON
        updated_ts = conv["updated_at"]
        try:
            dt = datetime.strptime(updated_ts, "%Y-%m-%d %H:%M:%S")
            month_dir = dt.strftime("%Y-%m")
        except (ValueError, TypeError):
            month_dir = datetime.now().strftime("%Y-%m")

        archive_dir = self._archive_root / month_dir
        archive_dir.mkdir(parents=True, exist_ok=True)

        archive_data = {
            "conversation": dict(conv),
            "messages": [dict(m) for m in messages],
            "archived_at": datetime.now().isoformat(),
        }

        archive_path = archive_dir / f"{conv_id}.json"
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archive_data, f, ensure_ascii=False, indent=2, default=str)

        # 3. 标记为已归档（不删除，保留在 DB 中）
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE conversations SET is_archived = 1 WHERE id = ?",
                (conv_id,),
            )
            conn.commit()
        finally:
            conn.close()

        return True, f"已归档 {len(messages)} 条消息 → {archive_path}"

    def archive_all_old(self, days_inactive: int = 30) -> Tuple[int, List[str]]:
        """归档所有过期对话"""
        archivable = self.list_archivable(days_inactive)
        archived = 0
        errors = []

        for conv in archivable:
            success, msg = self.archive_conversation(conv["id"])
            if success:
                archived += 1
            else:
                errors.append(f"{conv['id']}: {msg}")

        return archived, errors

    def restore_conversation(self, conv_id: str) -> Tuple[bool, str]:
        """从归档恢复对话"""
        # 查找归档文件
        for month_dir in sorted(self._archive_root.glob("*"), reverse=True):
            if not month_dir.is_dir():
                continue
            archive_path = month_dir / f"{conv_id}.json"
            if archive_path.exists():
                # 取消归档标记
                conn = sqlite3.connect(self._db_path)
                try:
                    conn.execute(
                        "UPDATE conversations SET is_archived = 0 WHERE id = ?",
                        (conv_id,),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return True, f"已从 {archive_path} 恢复对话"

        return False, "未找到归档文件"

    def get_archive_stats(self) -> dict:
        """获取归档统计"""
        total_files = 0
        total_size = 0
        for archive_file in self._archive_root.rglob("*.json"):
            if archive_file.is_file():
                total_files += 1
                total_size += archive_file.stat().st_size
        return {
            "archive_count": total_files,
            "archive_size_mb": round(total_size / (1024 * 1024), 2),
        }


# 单例
_archiver = None


def get_archiver() -> ConversationArchiver:
    global _archiver
    if _archiver is None:
        _archiver = ConversationArchiver()
    return _archiver


def auto_archive_old_conversations(days: int = 30):
    """启动时调用：自动归档旧对话"""
    try:
        archiver = get_archiver()
        count, errors = archiver.archive_all_old(days)
        if count > 0:
            logger.debug(f"[Archiver] 已自动归档 {count} 个旧对话")
        if errors:
            for e in errors:
                logger.error(f"[Archiver] 归档错误: {e}")
        return count
    except Exception as e:
        logger.error(f"[Archiver] 自动归档失败: {e}")
        return 0
