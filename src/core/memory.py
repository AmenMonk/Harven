# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
记忆管理系统 - 灵识五重防失忆机制
1. 全量持久化：所有消息实时保存到 SQLite
2. 三级记忆分层：L0原始对话 → L1任务记忆 → L2长期记忆
3. 永久会话ID：每个对话唯一标识
4. 结构化任务管理：关键信息提取和存储
5. 主人认知锁定：记住主人的偏好和信息
"""
import os
import json
import hashlib
from datetime import datetime
from pathlib import Path
import logging
logger = logging.getLogger(__name__)

# 记忆上下文缓存（TTL 5秒，避免同一对话反复查库）
_MEMORY_CACHE: dict = {}
_MEMORY_CACHE_TTL = 5  # 秒
import time as _time

# ★ Engram 式选择性记忆：重要性评分 + 优先级 + 自动清理
_MEMORY_WEIGHTS = {
    'identity': 1.0,      # 身份信息 → 最高
    'project': 0.9,       # 项目上下文 → 高
    'fact': 0.8,          # 事实 → 高（客观知识比主观偏好重要）
    'decision': 0.7,      # 决策记录 → 中高
    'task': 0.6,          # 任务 → 中
    'preference': 0.5,    # 用户偏好 → 中
    'concept': 0.4,       # 概念 → 低中
    'chat': 0.2,          # 普通对话 → 低
}
_MEMORY_RETENTION_MAX = 200  # 最大记忆数
_MEMORY_RETENTION_MIN = 50   # 清理后保留的最少数量

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "sqlite", "lingzhi.db"
)
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "snapshots"
)


class MemoryManager:
    """记忆管理系统"""

    def __init__(self):
        self._db_path = DB_PATH

    # ── L0: 原始对话记忆（已在 database.py 中实现） ──
    # 所有消息通过 save_message 持久化，此处不再重复

    # ── L1: 任务记忆（提取关键信息） ──

    def extract_task_memory(self, conversation_id, user_message, ai_response, model=None):
        """
        从对话中提取关键信息存为任务记忆
        AI 语义提取优先，规则提取兜底
        ★ 2026-07-18: 方案C——接收引擎模型，不硬编码Ollama
        """
        # 获取已有的任务记忆
        existing = self._get_task_memories(conversation_id)

        # 1. 尝试 AI 语义提取（用引擎传进来的模型）
        new_memories = self._ai_extract(user_message, ai_response, model=model)

        # 2. AI 提取失败时回退到规则提取
        if not new_memories:
            new_memories = self._simple_extract(user_message, ai_response)

        if not new_memories:
            return 0

        # 保存新记忆
        count = 0
        for mem in new_memories:
            if not self._is_duplicate(existing, mem):
                self._save_task_memory(conversation_id, mem)
                count += 1
        return count

    def _ai_extract(self, user_msg, ai_response, model=None):
        """
        AI 语义提取：用 LLM 从对话中智能提取结构化记忆

        ★ 2026-07-18: 方案C——优先用引擎传入的模型，兜底Ollama，最后规则提取
        """
        if not user_msg:
            return []

        try:
            # 构造提取 prompt
            prompt = f"""请从以下对话中提取关键信息，只返回 JSON 数组，不返回其他内容。

对话：
用户: {user_msg[:500]}
AI: {ai_response[:300] if ai_response else "(无)"}

提取规则：
- identity: 用户的身份信息（姓名、城市、生日、职业等）
- preference: 用户的偏好、喜欢/不喜欢
- decision: 用户做的决定、计划、承诺
- task: 待办任务或后续行动
- knowledge: 值得记住的事实或知识

只提取有明确依据的信息。每条格式: {{"content": "内容", "category": "类别", "confidence": 0.0-1.0}}
如果没有可提取的信息，返回空数组 []。

JSON:"""

            full_text = ""
            used_model = "none"

            # ★ 2026-07-18: 方案C——优先用引擎传入的模型（DeepSeek/ModelScope/Ollama都行）
            if model:
                try:
                    full_text = model.chat(
                        [{"role": "user", "content": prompt}], stream=False
                    )
                    used_model = "engine-model"
                except Exception as model_e:
                    logger.debug(f"[Memory] 引擎模型调用失败: {model_e}")

            # ★ 2026-09-19 (#796) 本地兜底：统一本地推理入口（进程内GGUF→Ollama→按需GGUF），不再裸调 11434
            if not full_text:
                try:
                    from src.core.local_infer import chat_local
                    _text, _backend, _err = chat_local(prompt, timeout=60, temperature=0.4)
                    if _text:
                        full_text = _text
                        used_model = f"local:{_backend}"
                    else:
                        logger.debug(f"[Memory] 本地兜底失败: {_err}")
                except Exception as local_e:
                    logger.debug(f"[Memory] 本地兜底不可用: {local_e}")

            if not full_text:
                logger.debug("[Memory] AI 提取：无可用模型，回退规则提取")
                return []

            logger.debug(f"[Memory] AI 提取使用模型：{used_model}")

            # 解析 JSON 响应
            import json as _json
            text = full_text.strip()
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                json_str = text[start:end + 1]
                memories = _json.loads(json_str)
                validated = []
                for m in memories:
                    if isinstance(m, dict) and "content" in m:
                        validated.append({
                            "content": m.get("content", ""),
                            "category": m.get("category", "knowledge"),
                            "confidence": float(m.get("confidence", 0.7)),
                        })
                return validated[:5]
        except Exception as e:
            logger.error(f"[Memory] AI 提取失败，回退规则: {e}")

        return []

    def _simple_extract(self, user_msg, ai_response):
        """
        关键信息提取（规则+上下文增强版）
        从用户消息和AI回复中提取偏好、事实、要求等信息
        ★ 2026-07-18: 过滤问题句，添加语义前缀
        """
        memories = []
        msg = user_msg.lower()

        def _is_question(text):
            """检查是否是问题（包含问号或疑问词）"""
            if '?' in text or '？' in text:
                return True
            if any(w in text for w in ['吗', '呢', '怎么', '什么', '为什么', '哪里', '哪个']):
                # 只有这些词在句末时才算问题
                text_stripped = text.strip()
                if text_stripped.endswith('吗') or text_stripped.endswith('呢'):
                    return True
            return False

        def _clean_sentence(text):
            """清理提取的句子，去掉前缀词，保留语义内容"""
            # 去掉匹配的模式词，保留后面的内容
            for prefix in ['我喜欢', '我不喜欢', '我希望', '请记住', '记住', '我决定', '决定了', '确定', '打算', '计划', '一定要', '以后', '从现在起', '以后不再']:
                if text.startswith(prefix):
                    rest = text[len(prefix):].strip()
                    if rest:
                        return rest
            return text

        # ── 检测用户偏好 ──
        preference_patterns = [
            ("我喜欢", "preference"),
            ("我不喜欢", "preference"),
            ("我希望", "preference"),
            ("请记住", "explicit_memory"),
            ("记住", "explicit_memory"),
            ("我的名字是", "identity"),
            ("我叫", "identity"),
            ("我住在", "identity"),
            ("我的城市", "identity"),
            ("我的生日", "identity"),
        ]
        for pattern, category in preference_patterns:
            if pattern in msg:
                idx = msg.find(pattern)
                sentence_end = min(idx + 100, len(user_msg))
                for i in range(idx, min(idx + 100, len(user_msg))):
                    if user_msg[i] in "。！？\n!?;；":
                        sentence_end = i + 1
                        break
                sentence = user_msg[idx:sentence_end].strip()
                # ★ 过滤问题句和太短的句子
                # ★ #1587 修复：identity 类门槛降为 3（"我叫张三"只有 4 字，5 字门槛把
                #    最经典的自我介绍拦在了门外；身份短语天然短，问句过滤照旧）
                _min_len = 3 if category == "identity" else 5
                if len(sentence) >= _min_len and not _is_question(sentence):
                    # ★ 添加语义前缀
                    if category == "preference":
                        content = "用户偏好：" + _clean_sentence(sentence)
                    elif category == "explicit_memory":
                        content = "用户要求记住：" + _clean_sentence(sentence)
                    elif category == "identity":
                        content = "用户身份：" + _clean_sentence(sentence)
                    else:
                        content = sentence
                    memories.append({
                        "content": content,
                        "category": category,
                        "confidence": 0.9 if category == "explicit_memory" else 0.7,
                    })

        # ── 检测重要决定/承诺 ──
        decision_patterns = [
            "决定", "决定了", "打算", "计划",
            "一定要", "从现在起", "以后不再",
        ]
        for pattern in decision_patterns:
            if pattern in msg and not any(m["category"] == "decision" for m in memories):
                idx = msg.find(pattern)
                sentence_end = min(idx + 80, len(user_msg))
                for i in range(idx, min(idx + 80, len(user_msg))):
                    if user_msg[i] in "。！？\n!?;；":
                        sentence_end = i + 1
                        break
                sentence = user_msg[idx:sentence_end].strip()
                # ★ 过滤问题句
                if len(sentence) >= 5 and not _is_question(sentence):
                    memories.append({
                        "content": "用户决定：" + _clean_sentence(sentence),
                        "category": "decision",
                        "confidence": 0.8,
                    })

        # ── 从AI回复中提取重要承诺（当AI说"我记住了"或"好的"并重复了信息）──
        ai_lower = ai_response.lower()
        if any(p in ai_lower for p in ["我记住了", "好的，我会记住", "已记录", "我已记住"]):
            # 提取AI确认的信息
            for pattern, category in [("名字", "identity"), ("住", "identity"), ("生日", "identity")]:
                if pattern in user_msg and not any(m["category"] == category for m in memories):
                    # 查找对应的信息
                    idx = msg.find(pattern)
                    sentence = user_msg[max(0, idx-10):min(len(user_msg), idx+50)].strip()
                    if len(sentence) >= 3:
                        memories.append({
                            "content": sentence,
                            "category": category,
                            "confidence": 0.95,  # AI确认过的记忆，高置信度
                        })

        # ── 去重 ──
        seen_contents = set()
        unique_memories = []
        for m in memories:
            if m["content"] not in seen_contents:
                seen_contents.add(m["content"])
                unique_memories.append(m)

        return unique_memories[:3]  # 每次最多提取3条

    def _is_duplicate(self, existing, new_mem):
        """检查是否已存在相似记忆"""
        for e in existing:
            if e.get("content", "") == new_mem.get("content", ""):
                return True
        return False

    def _get_all_task_memories(self):
        """获取所有对话的任务记忆（全局，跨模式共享）"""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT key, value FROM cognitive_locks WHERE key LIKE ? ORDER BY key",
                ("task_mem_%",)
            ).fetchall()
            return [json.loads(r[1]) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def _get_task_memories(self, conversation_id):
        """获取对话的任务记忆"""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT key, value FROM cognitive_locks WHERE key LIKE ?",
                (f"task_mem_{conversation_id}_%",)
            ).fetchall()
            return [json.loads(r[1]) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def _save_task_memory(self, conversation_id, mem):
        """保存任务记忆到 cognitive_locks 表（Engram 式选择性存储）"""
        # 计算记忆重要性分数
        category = mem.get('category', 'chat')
        base_weight = _MEMORY_WEIGHTS.get(category, 0.2)
        confidence = mem.get('confidence', 0.5)
        importance = base_weight * confidence  # 0.0 ~ 1.0

        # ★ 低重要性记忆直接丢弃（Engram 的"选择性"原则）
        if importance < 0.1:
            logger.debug(f"[Memory] 丢弃低价值记忆 ({category}, score={importance:.2f})")
            return

        import sqlite3
        conn = sqlite3.connect(self._db_path)
        mem['importance'] = round(importance, 3)
        mem['retrieval_count'] = 0  # 检索次数，后续逐次+1
        key = f"task_mem_{conversation_id}_{hashlib.md5(mem['content'].encode()).hexdigest()[:8]}"
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cognitive_locks (key, value, source, locked_at, priority) "
                "VALUES (?, ?, 'task_memory', datetime('now', 'localtime'), ?)",
                (key, json.dumps(mem, ensure_ascii=False), int(importance * 200))
            )
            conn.commit()

            # Engram 式自动清理：超过上限时删除最低优先级的记忆
            self._prune_low_value_memories(conversation_id)
        finally:
            conn.close()

    def _prune_low_value_memories(self, conversation_id):
        """Engram 式记忆清理：保留高价值记忆，删除低价值记忆"""
        import sqlite3
        try:
            conn = sqlite3.connect(self._db_path)
            prefix = f'task_mem_{conversation_id}_'
            rows = conn.execute(
                "SELECT key, value, priority FROM cognitive_locks WHERE key LIKE ? ORDER BY priority DESC",
                (prefix + '%',)
            ).fetchall()

            if len(rows) <= _MEMORY_RETENTION_MAX:
                return  # 未超上限

            # 只保留前 _MEMORY_RETENTION_MIN 条高优先级记忆
            keep_count = _MEMORY_RETENTION_MIN
            for row in rows[keep_count:]:
                # 提高检索次数后才删除（避免误删新记忆）
                val = json.loads(row[1])
                if val.get('retrieval_count', 0) < 2 and val.get('importance', 0) < 0.3:
                    conn.execute("DELETE FROM cognitive_locks WHERE key = ?", (row[0],))

            conn.commit()
            logger.debug(f"[Memory] Engram 清理: {len(rows)} → {keep_count}+ (保留高价值)")
        except Exception as e:
            logger.debug(f"[Memory] 清理跳过: {e}")
        finally:
            try: conn.close()
            except Exception as _e: logger.debug(f"[memory] 忽略: {_e}")

    # ── L2: 长期记忆（跨会话持久化） ──

    def save_long_term_memory(self, key, value, category="general"):
        """保存长期记忆"""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cognitive_locks (key, value, source, locked_at, priority) "
                "VALUES (?, ?, ?, datetime('now', 'localtime'), 100)",
                (f"long_term_{key}", json.dumps({
                    "value": value,
                    "category": category,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                }, ensure_ascii=False), category)
            )
            conn.commit()
        finally:
            conn.close()

    def get_long_term_memories(self):
        """获取所有长期记忆"""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT key, value FROM cognitive_locks WHERE key LIKE 'long_term_%'"
            ).fetchall()
            memories = {}
            for r in rows:
                key = r[0].replace("long_term_", "")
                try:
                    data = json.loads(r[1])
                    memories[key] = data
                except Exception:
                    memories[key] = r[1]
            return memories
        finally:
            conn.close()

    def _retrieve_relevant_memories(self, query, memories_dict, top_k=5):
        """
        按语义相关性检索长期记忆（基于关键词重叠 + TF-IDF 简化版）

        Args:
            query: 用户当前问题
            memories_dict: 长期记忆字典 {key: {value, category, ...}}
            top_k: 返回前 K 条

        Returns:
            dict: 最相关的 top_k 条记忆
        """
        import re

        # 1. 提取 query 关键词（中文分词简化版：2-4字滑动窗口 + 英文单词）
        query_lower = query.lower()
        query_words = set()

        # 中文 2-3 字组合
        cn_chars = re.findall(r'[\u4e00-\u9fff]+', query_lower)
        for segment in cn_chars:
            if len(segment) >= 2:
                query_words.add(segment)
                # 2字滑动窗口
                for i in range(len(segment) - 1):
                    query_words.add(segment[i:i+2])
                # 3字滑动窗口
                for i in range(len(segment) - 2):
                    query_words.add(segment[i:i+3])

        # 英文单词
        en_words = re.findall(r'[a-z0-9]+', query_lower)
        for w in en_words:
            if len(w) >= 2:
                query_words.add(w)

        if not query_words:
            # 无关键词，回退最近 top_k 条
            return dict(list(memories_dict.items())[-top_k:])

        # 2. 计算每条记忆的相关性分数
        scored = []
        for key, mem_data in memories_dict.items():
            mem_text = str(mem_data.get('value', mem_data)) + ' ' + str(key)
            mem_lower = mem_text.lower()

            # 关键词匹配计数
            match_count = 0
            for qw in query_words:
                if qw in mem_lower:
                    match_count += 1

            # 时效性加成（最近更新的记忆加分）
            updated = mem_data.get('updated_at', '')
            if updated:
                try:
                    mem_date = datetime.strptime(updated, '%Y-%m-%d %H:%M')
                    days_ago = (datetime.now() - mem_date).days
                    recency_bonus = max(0, 1 - days_ago / 30)  # 30天内加分
                except Exception:
                    recency_bonus = 0
            else:
                recency_bonus = 0

            # 综合分数 = 匹配数 * 2 + 时效性
            score = match_count * 2 + recency_bonus
            scored.append((key, mem_data, score))

        # 3. 按分数排序，取 top_k
        scored.sort(key=lambda x: x[2], reverse=True)

        # 过滤掉分数为0的（完全不相关）
        relevant = [(k, v) for k, v, s in scored if s > 0][:top_k]

        # 如果相关记忆不足 top_k，补充最近的记忆
        if len(relevant) < top_k:
            existing_keys = set(k for k, v in relevant)
            for key, mem_data in list(memories_dict.items())[::-1]:
                if key not in existing_keys:
                    relevant.append((key, mem_data))
                    if len(relevant) >= top_k:
                        break

        return {k: v for k, v in relevant}

    # ── 主人认知锁定 ──

    def lock_master_info(self, info_type, value):
        """锁定主人的认知信息（高优先级）"""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cognitive_locks (key, value, source, locked_at, priority) "
                "VALUES (?, ?, 'master', datetime('now', 'localtime'), 200)",
                (f"master_{info_type}", json.dumps({
                    "value": value,
                    "type": info_type,
                    "locked_at": datetime.now().strftime("%Y-%m-%d %H:%M")
                }, ensure_ascii=False))
            )
            conn.commit()
        finally:
            conn.close()

    def get_master_info(self, info_type=None):
        """获取主人认知信息"""
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        try:
            if info_type:
                row = conn.execute(
                    "SELECT value FROM cognitive_locks WHERE key = ?",
                    (f"master_{info_type}",)
                ).fetchone()
                if row:
                    try:
                        return json.loads(row[0])["value"]
                    except Exception:
                        return row[0]
                return None
            else:
                rows = conn.execute(
                    "SELECT key, value FROM cognitive_locks WHERE key LIKE 'master_%'"
                ).fetchall()
                info = {}
                for r in rows:
                    key = r[0].replace("master_", "")
                    try:
                        info[key] = json.loads(r[1])["value"]
                    except Exception:
                        info[key] = r[1]
                return info
        finally:
            conn.close()

    # ── 会话快照（防丢失） ──

    def save_snapshot(self, conversation_id):
        """保存会话快照"""
        from src.models.database import get_messages, get_conversations
        os.makedirs(DATA_DIR, exist_ok=True)

        msgs = get_messages(conversation_id, limit=200)
        if not msgs:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot = {
            "conversation_id": conversation_id,
            "timestamp": timestamp,
            "messages": msgs
        }

        # 获取对话标题
        convs = get_conversations()
        title = "unknown"
        for c in convs:
            if c["id"] == conversation_id:
                title = c["title"]
                break

        filepath = os.path.join(DATA_DIR, f"{conversation_id}_{timestamp}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Memory] 保存快照失败: {e}")

    def build_memory_context(self, conversation_id=None, query=None):
        """
        构建记忆增强上下文（注入到系统提示词中）

        Args:
            conversation_id: 对话ID
            query: 当前用户问题（用于语义检索相关长期记忆）
        """
        # TTL缓存：同一会话+同一query 5秒内复用结果
        cache_key = f"mem_ctx:{conversation_id or 'global'}:{hash(query or '')}"
        now = _time.time()
        cached = _MEMORY_CACHE.get(cache_key)
        if cached and (now - cached['ts']) < _MEMORY_CACHE_TTL:
            return cached['result']

        parts = []

        # 主人信息
        master_info = self.get_master_info()
        if master_info:
            info_text = "\n".join(f"  - {k}: {v}" for k, v in master_info.items())
            parts.append(f"## 关于主人（请始终记住）\n{info_text}")

        # 长期记忆（相关性检索 Top-5，而非只取最近3条）
        long_term = self.get_long_term_memories()
        if long_term:
            # ★ 改进：如果提供了 query，按相关性检索 Top-5；否则回退最近5条
            if query and len(long_term) > 5:
                relevant = self._retrieve_relevant_memories(query, long_term, top_k=5)
            else:
                # 无 query 或记忆数少时，取最近5条
                relevant = dict(list(long_term.items())[-5:])

            mem_text = "\n".join(f"  - {k}: {v.get('value', v)}" for k, v in relevant.items())
            parts.append(f"## 长期记忆（{len(relevant)} 条）\n{mem_text}")
            if len(long_term) > 5:
                logger.debug(f"[Memory] 长期记忆检索：{len(long_term)} 条 → Top-{len(relevant)} 条")

        # 任务记忆（跨对话共享，让所有模式都能看到之前保存的重要信息）
        task_mems = self._get_all_task_memories()
        if task_mems:
            mem_text = "\n".join(f"  - {m['content']}" for m in task_mems[-5:])  # 最多5条
            parts.append(f"## 跨对话重要信息\n{mem_text}")

        if not parts:
            _MEMORY_CACHE[cache_key] = {'result': '', 'ts': _time.time()}
            return ""

        result = "\n\n" + "\n\n".join(parts) + "\n\n请在回答中自然地利用以上记忆信息。"
        _MEMORY_CACHE[cache_key] = {'result': result, 'ts': _time.time()}
        return result


# 全局单例
_memory_manager = None

def get_memory_manager():
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


# ── AI 智能提炼记忆（增强功能）───

def ai_consolidate_memories(conversation_id=None, model_name=None):
    """
    用 AI 智能提炼记忆（可异步调用）
    从对话历史中提取结构化记忆，替代/增强规则提取

    Args:
        conversation_id: 对话ID（None 表示所有对话）
        model_name: Ollama 模型名（None 则自动检测）

    Returns:
        dict: {"status": "success"/"error", "memories": [...]}
    """
    import requests
    import sqlite3

    # 1. 获取 Ollama 地址
    try:
        import yaml
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.yaml"
        )
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        base_url = cfg.get("ollama", {}).get("base_url", "http://localhost:11434")
    except Exception:
        base_url = "http://localhost:11434"

    # 2. 获取模型名
    if not model_name:
        try:
            resp = requests.get(f"{base_url}/api/tags", timeout=5)
            models = resp.json().get("models", [])
            if models:
                model_name = models[0]["name"]
        except Exception:
            return {"status": "error", "message": "无法连接 Ollama"}

    # 3. 读取对话历史
    db_path = DB_PATH
    conn = sqlite3.connect(db_path)
    try:
        if conversation_id:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY timestamp LIMIT 200",
                (conversation_id,)
            ).fetchall()
        else:
            # 所有对话的最近 300 条
            rows = conn.execute(
                "SELECT role, content FROM messages ORDER BY timestamp DESC LIMIT 300"
            ).fetchall()
            rows.reverse()

        if not rows:
            return {"status": "error", "message": "没有对话历史"}

        # 构造对话文本（截断过长消息）
        dialog_text = ""
        for role, content in rows:
            if not content:
                continue
            content_short = content[:300] if len(content) > 300 else content
            dialog_text += f"{role}: {content_short}\n"

    finally:
        conn.close()

    # 4. 构造 AI 提炼 prompt
    extract_prompt = f"""请从以下对话历史中提取关键信息，按类别输出 JSON。

类别说明：
- identity: 主人的身份信息（姓名、城市、生日、职业等）
- preference: 主人的偏好（喜欢/不喜欢什么）
- decision: 主人的决定或计划
- knowledge: 重要的知识或事实
- task: 待办事项或任务

只输出 JSON 数组，格式如下（不要输出其他内容）：
```json
[
  {{"category": "identity", "content": "用户叫小明", "confidence": 0.95}},
  {{"category": "preference", "content": "主人喜欢简洁的回答", "confidence": 0.8}}
]
```

对话历史：
{dialog_text[:4000]}
```"""

    # 5. 调用 Ollama
    try:
        resp = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model_name,
                "prompt": extract_prompt,
                "stream": False,
                "options": {"num_predict": 1000, "temperature": 0.3}
            },
            timeout=60
        )
        if resp.status_code != 200:
            return {"status": "error", "message": f"Ollama 调用失败: {resp.status_code}"}

        ai_text = resp.json().get("response", "")

        # 6. 解析 JSON
        import re
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', ai_text)
        if json_match:
            ai_text = json_match.group(1)
        else:
            # 尝试直接解析
            json_match = re.search(r'\[\s*\{.*?\}\s*\]', ai_text, re.DOTALL)
            if json_match:
                ai_text = json_match.group(0)

        memories = json.loads(ai_text)
        if not isinstance(memories, list):
            return {"status": "error", "message": "AI 返回格式错误"}

        # 7. 保存记忆
        count = 0
        for mem in memories[:10]:  # 最多保存10条
            if not isinstance(mem, dict):
                continue
            category = mem.get("category", "general")
            content = mem.get("content", "").strip()
            if not content or len(content) < 3:
                continue
            # 保存到长期记忆
            mm = get_memory_manager()
            mm.save_long_term_memory(
                key=f"ai_{category}_{hashlib.md5(content.encode()).hexdigest()[:8]}",
                value=content,
                category=category
            )
            count += 1

        return {"status": "success", "memories": memories[:count], "count": count}

    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"AI 返回解析失败: {e}", "raw": ai_text[:500]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def search_history(query, limit=5):
    """搜索历史对话内容

    Args:
        query: 搜索关键词
        limit: 返回条数上限

    Returns:
        list: [{conversation_id, title, role, content, created_at}, ...]
    """
    try:
        from src.models.database import get_connection
        from pathlib import Path
        db_path = Path(__file__).parent.parent.parent / 'data' / 'sqlite' / 'lingzhi.db'
        conn = get_connection(str(db_path))
        conn.row_factory = __import__('sqlite3').Row
        rows = conn.execute(
            "SELECT m.conversation_id, c.title, m.role, m.content, m.created_at "
            "FROM messages m JOIN conversations c ON m.conversation_id = c.id "
            "WHERE m.content LIKE ? AND m.role IN ('user','assistant') "
            "ORDER BY m.created_at DESC LIMIT ?",
            [f'%{query}%', limit]
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[Memory] search_history 失败: {e}")
        return []

