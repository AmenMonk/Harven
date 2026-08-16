# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Memory Dreaming — 记忆巩固三阶段模块
参考 OpenClaw 的 Memory Dreaming（Light → Deep → REM）
定期压缩、整理、蒸馏长期记忆，防止记忆膨胀冗余。

三阶段：
1. Light Dream（轻度梦）：快速去重，合并相似记忆（每 1 小时）
2. Deep Dream（深度梦）：语义蒸馏，提取核心要点（每天 1 次）
3. REM Dream（REM 梦）：跨领域关联，发现模式（每 3 天 1 次）
"""
import json
import time
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
DREAM_STATE_FILE = DATA_DIR / 'dream_state.json'

# ─── 数据结构 ───

class MemoryFragment:
    """记忆片段"""
    def __init__(self, content: str, category: str = "general",
                 importance: float = 0.5, timestamp: float = None):
        self.content = content
        self.category = category
        self.importance = importance
        self.timestamp = timestamp or time.time()
        self.embeddings = None  # 惰性计算
    
    def to_dict(self) -> Dict:
        return {
            "content": self.content,
            "category": self.category,
            "importance": self.importance,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, d: Dict) -> "MemoryFragment":
        return cls(d["content"], d.get("category", "general"),
                   d.get("importance", 0.5), d.get("timestamp"))


class DreamSession:
    """一次梦的记录"""
    def __init__(self, dream_type: str):
        self.dream_type = dream_type  # light / deep / rem
        self.started_at = time.time()
        self.input_count = 0      # 输入记忆数
        self.output_count = 0     # 输出记忆数
        self.merged_pairs = 0     # 合并相似对
        self.removed_count = 0    # 删除冗余数
        self.insights = []        # REM 模式的洞察
        self.duration_ms = 0
    
    def finish(self):
        self.duration_ms = (time.time() - self.started_at) * 1000


# ─── 记忆梦境引擎 ───

class MemoryDreamEngine:
    """
    三阶段记忆巩固引擎
    使用 LLM 辅助进行语义分析（如果没有 LLM 则降级为纯规则模式）
    """
    
    def __init__(self):
        self.state = self._load_state()
        self._running = False
        self._llm_fn = None
        
        # 确保 active_memory 已加载
        self._memory_store = None
    
    @property
    def memory_store(self):
        """懒加载 ActiveMemory"""
        if self._memory_store is None:
            try:
                from src.core.active_memory import get_active_memory
                self._memory_store = get_active_memory()
            except Exception:
                self._memory_store = None
        return self._memory_store
    
    def set_llm_fn(self, fn):
        """设置 LLM 调用函数 fn(prompt: str) -> str"""
        self._llm_fn = fn
    
    # ─── 状态管理 ───
    
    def _load_state(self) -> Dict:
        if DREAM_STATE_FILE.exists():
            try:
                return json.loads(DREAM_STATE_FILE.read_text(encoding='utf-8'))
            except Exception as _e: logger.debug(f"[memory_dreaming] 忽略: {_e}")
        return {
            "last_light_dream": None,
            "last_deep_dream": None,
            "last_rem_dream": None,
            "total_consolidations": 0,
            "total_memories_processed": 0,
        }
    
    def _save_state(self):
        DREAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DREAM_STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                                     encoding='utf-8')
    
    # ─── 主入口 ───
    
    def dream_if_needed(self) -> Optional[DreamSession]:
        """
        检查并执行需要的梦境阶段
        返回 DreamSession 或 None（无需执行）
        """
        now = time.time()
        
        # 1. Light Dream（每 1 小时）
        last_light = self.state.get("last_light_dream", 0) or 0
        if now - last_light > 3600:
            return self.light_dream()
        
        # 2. Deep Dream（每 24 小时）
        last_deep = self.state.get("last_deep_dream", 0) or 0
        if now - last_deep > 86400:
            return self.deep_dream()
        
        # 3. REM Dream（每 72 小时 / 3 天）
        last_rem = self.state.get("last_rem_dream", 0) or 0
        if now - last_rem > 259200:
            return self.rem_dream()
        
        return None
    
    # ─── Light Dream: 去重 & 合并相似记忆 ───
    
    def light_dream(self) -> DreamSession:
        """轻度梦：去重 + 合并相似记忆"""
        session = DreamSession("light")
        
        if not self.memory_store:
            session.finish()
            return session
        
        try:
            memories = self.memory_store.get_all(limit=500)
            session.input_count = len(memories)
            
            if len(memories) < 10:
                session.finish()
                self.state["last_light_dream"] = time.time()
                self._save_state()
                return session
            
            # 分组：按分类
            from collections import defaultdict
            by_category = defaultdict(list)
            for m in memories:
                cat = m.get("category", "general")
                by_category[cat].append(m)
            
            # 组内去重
            removed_ids = []
            for cat, items in by_category.items():
                seen = set()
                for item in sorted(items, key=lambda x: x.get("importance", 0), reverse=True):
                    content = item.get("content", "").strip()
                    # 简单的指纹去重（取前 80 字符作为指纹）
                    fingerprint = content[:80].lower()
                    if fingerprint in seen:
                        removed_ids.append(item.get("id"))
                        session.removed_count += 1
                    else:
                        seen.add(fingerprint)
            
            # 合并短记忆
            short_threshold = 30  # 少于30字符的算短记忆
            short_memories = [m for m in memories 
                              if len(m.get("content", "")) < short_threshold 
                              and m.get("id") not in removed_ids]
            
            if len(short_memories) >= 3 and self._llm_fn:
                combined = self._light_merge_with_llm(short_memories)
                if combined:
                    session.merged_pairs += 1
            
            # 删除冗余记忆
            if removed_ids:
                for mid in removed_ids:
                    try:
                        self.memory_store.delete(mid)
                    except Exception as _e: logger.debug(f"[memory_dreaming] 忽略: {_e}")
            
            session.output_count = session.input_count - session.removed_count
            session.finish()
            
            self.state["last_light_dream"] = time.time()
            self.state["total_consolidations"] += 1
            self.state["total_memories_processed"] += session.input_count
            self._save_state()
            
        except Exception as e:
            logger.error(f"Light Dream 失败: {e}")
            session.finish()
        
        return session
    
    def _light_merge_with_llm(self, short_memories: List[Dict]) -> Optional[str]:
        """用 LLM 合并相关短记忆"""
        try:
            if not self._llm_fn:
                return None
            
            texts = "\n".join(f"- {m.get('content', '')}" for m in short_memories[:10])
            prompt = f"""以下是从对话中提取的简短记忆片段，请将它们合并成 2-3 条概括性记忆：

{texts}

要求：
1. 合并明显相关的条目
2. 去掉重复信息
3. 用一句话概括每组
4. 输出格式：每行一条，以 "- " 开头

合并结果："""
            result = self._llm_fn(prompt)
            if result and len(result.strip()) > 5:
                # 存储合并结果
                for line in result.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("- "):
                        content = line[2:].strip()
                        if len(content) > 5:
                            try:
                                self.memory_store.store(content, category="consolidated", 
                                                        source="light_dream")
                            except Exception as _e: logger.debug(f"[memory_dreaming] 忽略: {_e}")
                return result
        except Exception as e:
            logger.warning(f"Light Dream LLM 合并失败: {e}")
        return None
    
    # ─── Deep Dream: 语义蒸馏 ───
    
    def deep_dream(self) -> DreamSession:
        """深度梦：语义蒸馏，提取核心要点"""
        session = DreamSession("deep")
        
        if not self.memory_store:
            # 无记忆库时，仍然更新时间戳，避免无限重试
            self.state["last_deep_dream"] = time.time()
            self._save_state()
            session.finish()
            return session
        
        # _llm_fn 未设置时降级为仅统计（不做蒸馏）
        if not self._llm_fn:
            memories = []
            try:
                memories = self.memory_store.get_all(limit=200)
            except Exception as _e: logger.debug(f"[memory_dreaming] 忽略: {_e}")
            session.input_count = len(memories)
            session.finish()
            self.state["last_deep_dream"] = time.time()
            self._save_state()
            return session
        
        try:
            memories = self.memory_store.get_all(limit=200)
            session.input_count = len(memories)
            
            if len(memories) < 20:
                session.finish()
                self.state["last_deep_dream"] = time.time()
                self._save_state()
                return session
            
            # 按重要性排序，取高重要性记忆进行蒸馏
            high_importance = sorted(
                [m for m in memories if m.get("importance", 0) > 0.3],
                key=lambda x: x.get("importance", 0), reverse=True
            )[:50]
            
            if high_importance:
                # 分批次蒸馏（每次 15 条）
                batch_size = 15
                for i in range(0, len(high_importance), batch_size):
                    batch = high_importance[i:i + batch_size]
                    self._deep_distill_batch(batch)
                    session.output_count += 1
            
            session.finish()
            self.state["last_deep_dream"] = time.time()
            self.state["total_consolidations"] += 1
            self._save_state()
            
        except Exception as e:
            logger.error(f"Deep Dream 失败: {e}")
            # 失败时仍更新时间戳，避免永久重试损耗资源
            self.state["last_deep_dream"] = time.time()
            self._save_state()
            session.finish()
        
        return session
    
    def _deep_distill_batch(self, batch: List[Dict]) -> Optional[str]:
        """蒸馏一批记忆"""
        try:
            items = "\n".join(
                f"{i+1}. [{m.get('category', '?')}] {m.get('content', '')}"
                for i, m in enumerate(batch)
            )
            prompt = f"""分析以下记忆条目，提取 3-5 条核心知识点或用户偏好：

{items}

输出格式（每条一行）：
- 用户偏好：<具体偏好内容>
- 知识点：<具体知识点>
- 模式：<具体行为模式>"""
            result = self._llm_fn(prompt)
            if result and len(result.strip()) > 10:
                for line in result.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("- ") and len(line) > 5:
                        content = line[2:].strip()
                        try:
                            self.memory_store.store(
                                content, category="distilled",
                                source="deep_dream"
                            )
                        except Exception as _e: logger.debug(f"[memory_dreaming] 忽略: {_e}")
                return result
        except Exception as e:
            logger.warning(f"Deep Dream 蒸馏失败: {e}")
        return None
    
    # ─── REM Dream: 跨领域关联 ───
    
    def rem_dream(self) -> DreamSession:
        """REM 梦：跨领域关联，发现模式"""
        session = DreamSession("rem")
        
        if not self.memory_store:
            # 无记忆库时，仍然更新时间戳，避免无限重试
            self.state["last_rem_dream"] = time.time()
            self._save_state()
            session.finish()
            return session
        
        # _llm_fn 未设置时降级为仅统计
        if not self._llm_fn:
            memories = []
            try:
                memories = self.memory_store.get_all(limit=300)
            except Exception as _e: logger.debug(f"[memory_dreaming] 忽略: {_e}")
            session.input_count = len(memories)
            session.finish()
            self.state["last_rem_dream"] = time.time()
            self._save_state()
            return session
        
        try:
            memories = self.memory_store.get_all(limit=300)
            session.input_count = len(memories)
            
            if len(memories) < 30:
                session.finish()
                self.state["last_rem_dream"] = time.time()
                self._save_state()
                return session
            
            # 按分类聚合
            from collections import defaultdict
            by_category = defaultdict(list)
            for m in memories:
                cat = m.get("category", "general")
                by_category[cat].append(m)
            
            # 对每个分类生成概述
            category_summaries = []
            for cat, items in by_category.items():
                if len(items) >= 3:
                    summary = self._rem_summarize_category(cat, items[:20])
                    if summary:
                        category_summaries.append(summary)
            
            # 跨分类关联分析
            if len(category_summaries) >= 2:
                insights = self._rem_cross_analyze(category_summaries)
                session.insights = insights
            
            session.output_count = len(category_summaries)
            session.finish()
            
            self.state["last_rem_dream"] = time.time()
            self.state["total_consolidations"] += 1
            self._save_state()
            
        except Exception as e:
            logger.error(f"REM Dream 失败: {e}")
            # 失败时仍更新时间戳，避免永久重试
            self.state["last_rem_dream"] = time.time()
            self._save_state()
            session.finish()
        
        return session
    
    def _rem_summarize_category(self, category: str, items: List[Dict]) -> Optional[str]:
        """总结一个分类"""
        try:
            texts = "\n".join(f"- {m.get('content', '')}" for m in items[:10])
            prompt = f"""以下是关于「{category}」的记忆片段，请用 2-3 句话概括：

{texts}

概括："""
            result = self._llm_fn(prompt)
            return f"[{category}] {result}" if result else None
        except Exception:
            return None
    
    def _rem_cross_analyze(self, summaries: List[str]) -> List[str]:
        """跨分类关联分析"""
        try:
            combined = "\n".join(f"### {s}" for s in summaries)
            prompt = f"""以下是从不同领域提取的记忆总结：

{combined}

请找出 2-3 条跨领域的模式或关联。例如：用户在A领域的偏好如何影响B领域的决策等。

发现："""
            result = self._llm_fn(prompt)
            if result:
                insights = [line.strip() for line in result.strip().split("\n") 
                           if line.strip() and len(line.strip()) > 5]
                # 保存洞察
                for insight in insights:
                    try:
                        self.memory_store.store(
                            insight, category="insight",
                            source="rem_dream"
                        )
                    except Exception as _e: logger.debug(f"[memory_dreaming] 忽略: {_e}")
                return insights
        except Exception as e:
            logger.warning(f"REM 跨分析失败: {e}")
        return []
    
    # ─── 获取梦境报告 ───
    
    def get_dream_report(self) -> Dict:
        """获取最近一次梦的报告"""
        return {
            "light_dream": self.state.get("last_light_dream"),
            "deep_dream": self.state.get("last_deep_dream"),
            "rem_dream": self.state.get("last_rem_dream"),
            "total_consolidations": self.state.get("total_consolidations", 0),
            "total_processed": self.state.get("total_memories_processed", 0),
        }
    
    def force_dream(self, dream_type: str = "all") -> List[DreamSession]:
        """强制执行梦境（用于手动触发）"""
        sessions = []
        if dream_type in ("light", "all"):
            sessions.append(self.light_dream())
        if dream_type in ("deep", "all"):
            sessions.append(self.deep_dream())
        if dream_type in ("rem", "all"):
            sessions.append(self.rem_dream())
        return sessions


# ─── 定时任务集成 ───

def register_dreaming_scheduler(scheduler):
    """在定时任务系统中注册记忆梦境"""
    try:
        from src.core.scheduler import ScheduledTask, get_scheduler
        
        # Light Dream — 每小时
        get_scheduler().add_task(ScheduledTask(
            task_id="dream_light",
            name="记忆轻度梦（去重合并）",
            prompt="执行 Memory Dreaming Light Dream",
            schedule_type="cron",
            schedule_config={"hour": "*", "minute": "0"},
        ))
        
        # Deep Dream — 每天凌晨 3 点
        get_scheduler().add_task(ScheduledTask(
            task_id="dream_deep",
            name="记忆深度梦（语义蒸馏）",
            prompt="执行 Memory Dreaming Deep Dream",
            schedule_type="cron",
            schedule_config={"hour": "3", "minute": "0"},
        ))
        
        # REM Dream — 每 3 天凌晨 4 点
        get_scheduler().add_task(ScheduledTask(
            task_id="dream_rem",
            name="记忆 REM 梦（跨领域关联）",
            prompt="执行 Memory Dreaming REM Dream",
            schedule_type="cron",
            schedule_config={"hour": "4", "minute": "0", "day_of_week": "*/3"},
        ))
        
        logger.info("[Dreaming] 已注册梦境定时任务")
    except Exception as e:
        logger.warning(f"[Dreaming] 注册定时任务失败: {e}")


# ─── 单例 ───
_dream_engine: Optional[MemoryDreamEngine] = None
_dream_lock = threading.Lock()

def get_dream_engine() -> MemoryDreamEngine:
    global _dream_engine
    if _dream_engine is None:
        with _dream_lock:
            if _dream_engine is None:
                _dream_engine = MemoryDreamEngine()
    return _dream_engine
