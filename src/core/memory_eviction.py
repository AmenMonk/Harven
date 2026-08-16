# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
记忆驱逐策略 — 防止记忆无限膨胀

驱逐策略（优先级从低到高）：
1. FIFO（先进先出）: 超过容量上限时淘汰最旧的记忆
2. 重要性加权: 低重要性 + 长时间未访问优先淘汰
3. 相似度去重: 高度相似（>0.95）的记忆合并
4. 时间衰减: 超过 N 天未访问的记忆自动降级
"""

import time
import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import logging
logger = logging.getLogger(__name__)


class MemoryEvictionPolicy:
    """
    记忆驱逐策略管理器

    配置:
    - max_memories: 最大记忆总数（默认 1000）
    - max_age_days: 驱逐阈值天数（默认 90）
    - importance_threshold: 低于此分数的记忆优先淘汰（默认 0.3）
    """

    def __init__(self,
                 max_memories: int = 1000,
                 max_age_days: int = 90,
                 importance_threshold: float = 0.3):
        self.max_memories = max_memories
        self.max_age_days = max_age_days
        self.importance_threshold = importance_threshold

    def should_evict(self, memories: List[dict]) -> Tuple[List[dict], List[dict]]:
        """
        判断哪些记忆应该驱逐。

        Args:
            memories: 记忆列表，每条包含 {content, importance, timestamp, access_count, ...}

        Returns:
            (keep, evict): 保留列表和驱逐列表
        """
        if len(memories) <= self.max_memories:
            # 仅驱逐过期记忆
            return self._evict_expired_only(memories)

        # 超出容量，需要同时驱逐过期 + 低重要性记忆
        keep, evict = self._evict_expired_only(memories)

        # 还超出容量，按分数驱逐
        if len(keep) > self.max_memories:
            excess = len(keep) - self.max_memories
            # 按驱逐分数排序（分数低的优先淘汰）
            keep.sort(key=lambda m: self._eviction_score(m))
            evict.extend(keep[:excess])
            keep = keep[excess:]

        return keep, evict

    def _evict_expired_only(self, memories: List[dict]) -> Tuple[List[dict], List[dict]]:
        """仅驱逐过期记忆"""
        cutoff = time.time() - (self.max_age_days * 86400)
        keep = []
        evict = []
        for m in memories:
            ts = m.get("timestamp", 0)
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts).timestamp()
                except (ValueError, TypeError):
                    ts = 0
            if ts < cutoff and m.get("importance", 0.5) < self.importance_threshold:
                evict.append(m)
            else:
                keep.append(m)
        return keep, evict

    def _eviction_score(self, memory: dict) -> float:
        """
        计算驱逐分数（越低越容易被淘汰）

        综合考虑：
        - importance: 重要性 (0-1, 越高越保留)
        - recency: 最近访问时间（越新越保留）
        - access_count: 访问次数（越多越保留）
        - pinned: 是否被锁定（锁定不淘汰）
        """
        if memory.get("pinned", False):
            return float("inf")  # 锁定记忆永不过期

        importance = memory.get("importance", 0.5)
        access_count = memory.get("access_count", 0)

        # 时间衰减因子
        ts = memory.get("timestamp", time.time())
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts).timestamp()
            except (ValueError, TypeError):
                ts = time.time()
        age_days = (time.time() - ts) / 86400
        recency_factor = max(0, 1 - (age_days / self.max_age_days))

        # 综合分数
        score = (
            importance * 0.4 +
            recency_factor * 0.3 +
            min(1.0, math.log(access_count + 1) / math.log(10)) * 0.3
        )
        return score

    def deduplicate_similar(self, memories: List[dict],
                            similarity_threshold: float = 0.95) -> List[dict]:
        """
        相似记忆去重（需要外部提供相似度计算）

        Args:
            memories: 记忆列表（需要包含 embedding 或 content）
            similarity_threshold: 相似度阈值

        Returns:
            去重后的记忆列表
        """
        # 简化实现：基于内容精确去重
        seen = set()
        deduped = []
        for m in memories:
            content = m.get("content", "")
            # 简单哈希去重
            key = content[:100]
            if key not in seen:
                seen.add(key)
                deduped.append(m)
        return deduped

    def get_eviction_stats(self, memories: List[dict]) -> dict:
        """获取记忆统计（用于决策）"""
        now = time.time()
        stats = {
            "total": len(memories),
            "expired": 0,
            "low_importance": 0,
            "pinned": 0,
            "avg_importance": 0,
            "avg_age_days": 0,
            "oldest_days": 0,
        }

        if not memories:
            return stats

        importances = []
        ages = []
        for m in memories:
            if m.get("pinned"):
                stats["pinned"] += 1
            imp = m.get("importance", 0.5)
            importances.append(imp)
            if imp < self.importance_threshold:
                stats["low_importance"] += 1

            ts = m.get("timestamp", now)
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts).timestamp()
                except (ValueError, TypeError):
                    ts = now
            age = (now - ts) / 86400
            ages.append(age)
            if age > self.max_age_days:
                stats["expired"] += 1

        stats["avg_importance"] = round(sum(importances) / len(importances), 3)
        stats["avg_age_days"] = round(sum(ages) / len(ages), 1)
        stats["oldest_days"] = round(max(ages), 1) if ages else 0

        return stats


def auto_evict_memories(db_path: str = None, max_memories: int = 1000):
    """
    自动驱逐过期/冗余记忆（启动时调用）

    对 memory_manager 中的记忆表执行驱逐。
    """
    import sqlite3
    policy = MemoryEvictionPolicy(max_memories=max_memories)

    try:
        if db_path is None:
            from src.core.memory import DB_PATH as _db_path
            db_path = _db_path
    except ImportError:
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 任务记忆实际存储在 cognitive_locks 表中（key 格式: task_mem_{conv_id}_{hash}）
        rows = conn.execute(
            "SELECT key, value, locked_at FROM cognitive_locks WHERE key LIKE 'task_mem_%' ORDER BY locked_at DESC LIMIT 2000"
        ).fetchall()
        if not rows:
            conn.close()
            return 0

        memories = [{
            "id": r["key"],
            "content": r["value"],
            "timestamp": r["locked_at"],
            "importance": 0.5,
        } for r in rows]
        keep, evict = policy.should_evict(memories)

        if evict:
            evict_ids = [m["id"] for m in evict]
            placeholders = ",".join("?" for _ in evict_ids)
            conn.execute(
                f"DELETE FROM cognitive_locks WHERE key IN ({placeholders})",
                evict_ids,
            )
            conn.commit()
            evicted = len(evict)
            logger.debug(f"[MemoryEviction] 已驱逐 {evicted} 条过期/低重要性记忆")
            return evicted
    except Exception as e:
        logger.error(f"[MemoryEviction] 驱逐失败: {e}")
        return 0
    finally:
        conn.close()
    return 0
