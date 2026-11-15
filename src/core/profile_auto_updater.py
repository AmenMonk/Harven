# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
用户画像自动更新引擎（Profile Auto Updater）

功能：
  1. 批量分析对话历史 → LLM 提取用户特征
  2. 置信度评分 + 多次验证后自动合并
  3. 低置信度特征 → 生成"建议"供用户审核
  4. 与手动设置 (config.yaml) 合并优先级：手动 > 高置信自动 > 低置信自动

画像维度（6维 + 4个复合维度 = 10维）：
  - interests (兴趣)          / expertise (专业)        / communication_style (交流风格)
  - values (价值取向)         / habits (使用习惯)        / emotional_tone (情绪)
  - skill_level (技能水平)    / tech_stack (技术栈)      / goals (当前目标)
  - language_pref (语言偏好)  / attention_span (注意力跨度)

数据流：
  对话历史 → LLM 分析 → 新画像特征 → 置信度评分
    ↓
  高置信(≥0.7) → 自动合并到 cognitive_locks
  中置信(0.4-0.7) → 建议列表（用户可选审批）
  低置信(<0.4) → 丢弃
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sqlite" / "lingzhi.db"
SUGGESTIONS_FILE = PROJECT_ROOT / "data" / "profile_suggestions.json"

# ═══════════════════════════════════════════════════
# 画像维度定义
# ═══════════════════════════════════════════════════

BASIC_DIMENSIONS = {
    "interests": "兴趣爱好",
    "expertise": "专业领域",
    "communication_style": "交流风格",
    "values": "价值取向",
    "habits": "使用习惯",
    "emotional_tone": "情绪倾向",
}

COMPOUND_DIMENSIONS = {
    "skill_level": "技能水平（初学者/中级/高级/专家）",
    "tech_stack": "技术栈（编程语言/框架/工具偏好）",
    "goals": "当前目标",
    "language_pref": "语言偏好",
}

ALL_DIMENSIONS = {**BASIC_DIMENSIONS, **COMPOUND_DIMENSIONS}


# ═══════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════

@dataclass
class ProfileTrait:
    """单个画像特征"""
    label: str               # 标签文本，如"自驾游"
    confidence: float = 0.5  # 置信度 0-1
    evidence: str = ""       # 证据来源（哪段对话）
    source: str = "auto"     # auto / manual / llm
    created_at: str = ""
    verified_count: int = 0  # 验证次数


@dataclass
class DimensionState:
    """单个维度的状态"""
    values: List[ProfileTrait] = field(default_factory=list)
    last_analyzed_at: str = ""


@dataclass
class ProfileState:
    """完整画像状态"""
    dimensions: Dict[str, DimensionState] = field(default_factory=dict)
    total_conversations_analyzed: int = 0
    total_messages_analyzed: int = 0
    last_full_analysis: str = ""
    version: int = 1

    def __post_init__(self):
        for dim in ALL_DIMENSIONS:
            if dim not in self.dimensions:
                self.dimensions[dim] = DimensionState()


@dataclass
class AnalysisResult:
    """单次分析结果"""
    new_traits: Dict[str, List[ProfileTrait]] = field(default_factory=dict)
    suggestions: List[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    success: bool = True
    error: str = ""


# ═══════════════════════════════════════════════════
# 画像引擎
# ═══════════════════════════════════════════════════

class ProfileAutoUpdater:
    """用户画像自动更新器"""

    def __init__(self):
        self._db_path = str(DB_PATH)
        self._state: Optional[ProfileState] = None

    # ── 核心分析 ──

    def analyze_recent_conversations(
        self,
        limit: int = 50,
        since_hours: int = 72,
        llm_callback=None,
    ) -> AnalysisResult:
        """
        分析最近的对话，提取用户画像特征。

        Args:
            limit: 最多分析多少条消息
            since_hours: 只分析最近N小时的对话
            llm_callback: LLM调用函数 async fn(prompt) -> str
        """
        result = AnalysisResult()

        try:
            messages = self._fetch_recent_messages(limit, since_hours)
            if not messages:
                result.stats = {"messages": 0, "conversations": 0}
                return result

            # 统计
            conv_ids = set(m[1] for m in messages)
            result.stats = {
                "messages": len(messages),
                "conversations": len(conv_ids),
                "since_hours": since_hours,
            }

            # 有 LLM 回调就用 LLM 深度分析，否则用本地快速分析
            if llm_callback:
                new_traits, suggestions = self._llm_analyze(messages, llm_callback)
            else:
                new_traits, suggestions = self._local_analyze(messages)

            # 置信度评分 + 分类
            for dim, traits in new_traits.items():
                for t in traits:
                    t.created_at = datetime.now().isoformat()
                    if t.confidence >= 0.7:
                        t.source = "llm" if llm_callback else "auto"
                        self._merge_trait(dim, t)
                    elif t.confidence >= 0.4:
                        suggestions.append({
                            "dimension": dim,
                            "dimension_label": ALL_DIMENSIONS.get(dim, dim),
                            "label": t.label,
                            "confidence": t.confidence,
                            "evidence": t.evidence,
                            "action": "suggest",
                        })

            result.new_traits = new_traits
            result.suggestions = suggestions

            # 保存建议供前端查看
            if suggestions:
                self._save_suggestions(suggestions)

            # ★ 2026-09-18 主动服务：高置信新特征写入待提及列表，下次对话哈文自然提起
            merged_flat = [
                {"dimension": dim, "label": t.label, "dimension_label": ALL_DIMENSIONS.get(dim, dim)}
                for dim, traits in new_traits.items() for t in traits if t.confidence >= 0.7
            ]
            if merged_flat:
                self.write_pending_insights(merged_flat)

            # 更新状态
            self._update_analysis_state(len(messages), len(conv_ids))

        except Exception as e:
            logger.exception(f"[ProfileUpdater] 分析失败: {e}")
            result.success = False
            result.error = str(e)

        return result

    def _fetch_recent_messages(self, limit: int, since_hours: int) -> List[tuple]:
        """从数据库获取最近的用户消息"""
        since = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                """SELECT id, conversation_id, content, created_at
                   FROM messages
                   WHERE role = 'user'
                     AND created_at >= ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (since, limit),
            ).fetchall()
            conn.close()
            return rows
        except Exception:
            return []

    # ── LLM 深度分析 ──

    def _llm_analyze(
        self, messages: List[tuple], llm_callback
    ) -> Tuple[Dict[str, List[ProfileTrait]], List[dict]]:
        """用 LLM 深度分析用户画像"""
        # 拼接对话摘要
        conversation_text = self._format_conversations(messages)

        prompt = f"""你是一个用户画像分析专家。分析以下对话历史，提取用户特征。

对话历史（最近{len(messages)}条消息）：
---
{conversation_text[:4000]}
---

请分析并输出 JSON（仅输出 JSON，不要其他文字）：
{{
  "interests": [{{"label": "标签", "confidence": 0.0-1.0, "evidence": "简短证据"}}],
  "expertise": [{{"label": "领域", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "communication_style": [{{"label": "风格描述", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "values": [{{"label": "价值取向", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "habits": [{{"label": "使用习惯", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "emotional_tone": [{{"label": "情绪描述", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "skill_level": [{{"label": "技能水平描述", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "tech_stack": [{{"label": "技术/工具名", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "goals": [{{"label": "目标描述", "confidence": 0.0-1.0, "evidence": "证据"}}],
  "language_pref": [{{"label": "语言偏好", "confidence": 0.0-1.0, "evidence": "证据"}}]
}}

规则：
- confidence 基于证据充分度：有明确表述=0.9，多次提到=0.8，隐约暗示=0.5，猜测=0.3
- 如果某维度无充分证据，返回空数组 []
- 标签精简，2-6字
- 只分析用户（非AI）的特征
"""

        try:
            response = llm_callback(prompt)
            # 尝试提取 JSON
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])

                new_traits = {}
                for dim in ALL_DIMENSIONS:
                    items = data.get(dim, [])
                    new_traits[dim] = [
                        ProfileTrait(
                            label=item.get("label", ""),
                            confidence=float(item.get("confidence", 0.5)),
                            evidence=str(item.get("evidence", "")),
                        )
                        for item in items
                        if item.get("label")
                    ]
                return new_traits, []
        except Exception as e:
            logger.warning(f"[ProfileUpdater] LLM分析解析失败: {e}")

        return {}, []

    def _format_conversations(self, messages: List[tuple]) -> str:
        """格式化对话历史为可分析文本"""
        lines = []
        for i, (msg_id, conv_id, content, created_at) in enumerate(messages):
            # 截断过长内容
            content = content[:200] + "..." if len(content) > 200 else content
            lines.append(f"[{created_at[:16]}] {content}")
        return "\n".join(lines)

    # ── 本地快速分析（无 LLM 时） ──

    def _local_analyze(
        self, messages: List[tuple]
    ) -> Tuple[Dict[str, List[ProfileTrait]], List[dict]]:
        """基于规则的本地画像分析（快速，无需LLM）"""
        all_text = " ".join(m[2] or "" for m in messages)

        traits = {}

        # 兴趣检测
        interest_keywords = [
            "自驾", "编程", "AI", "人工智能", "机器学习", "深度学习",
            "音乐", "摄影", "读书", "跑步", "健身", "游戏", "投资",
            "股票", "旅游", "美食", "电影", "动漫", "设计", "写作",
            "Python", "Java", "Go", "Rust", "前端", "后端", "数据",
            "开源", "创业", "管理", "金融", "法律", "医学", "教育",
        ]
        detected_interests = []
        for kw in interest_keywords:
            count = all_text.count(kw)
            if count >= 3:
                detected_interests.append(ProfileTrait(
                    label=kw,
                    confidence=min(0.7 + count * 0.05, 0.95),
                    evidence=f"关键词'{kw}'出现{count}次",
                ))
        if detected_interests:
            traits["interests"] = detected_interests

        # 技术栈检测
        tech_patterns = {
            "Python": ["python", "django", "flask", "fastapi", "pytorch", "tensorflow", "pip"],
            "JavaScript": ["javascript", "node", "react", "vue", "angular", "typescript", "npm"],
            "Go": ["golang", "go模块", "goroutine"],
            "Rust": ["rust", "cargo", "tokio"],
            "Java": ["java", "spring", "maven", "gradle"],
            "C++": ["c++", "cpp", "cmake", "boost"],
            "SQL": ["sql", "mysql", "postgres", "sqlite", "mongodb"],
            "Docker": ["docker", "k8s", "kubernetes", "容器"],
            "Linux": ["linux", "bash", "shell", "ubuntu", "debian"],
        }
        detected_tech = []
        for tech, patterns in tech_patterns.items():
            score = sum(1 for p in patterns if p.lower() in all_text.lower())
            if score >= 2:
                detected_tech.append(ProfileTrait(
                    label=tech,
                    confidence=min(0.6 + score * 0.05, 0.9),
                    evidence=f"提及{tech}相关术语{score}次",
                ))
        if detected_tech:
            traits["tech_stack"] = detected_tech

        # 交流风格检测
        style_traits = []
        total_chars = sum(len(m[2] or "") for m in messages)
        avg_len = total_chars / max(len(messages), 1)
        if avg_len > 200:
            style_traits.append(ProfileTrait(label="详细陈述", confidence=0.7))
        elif avg_len > 80:
            style_traits.append(ProfileTrait(label="中等篇幅", confidence=0.7))
        else:
            style_traits.append(ProfileTrait(label="简洁精炼", confidence=0.7))
        traits["communication_style"] = style_traits

        # 技能水平推测
        if detected_tech and len(detected_tech) >= 3:
            traits["skill_level"] = [ProfileTrait(
                label="技术熟练（多技术栈）",
                confidence=0.6,
                evidence=f"提及{len(detected_tech)}个技术方向",
            )]

        # 目标检测
        goal_patterns = [
            ("学习新技术", ["学习", "学一下", "了解", "入门", "教程"]),
            ("开发项目", ["开发", "实现", "构建", "搭建", "完成"]),
            ("问题排查", ["错误", "bug", "修复", "不工作", "怎么回事"]),
            ("效率提升", ["快", "效率", "优化", "自动化"]),
        ]
        detected_goals = []
        for goal, keywords in goal_patterns:
            count = sum(all_text.count(k) for k in keywords)
            if count >= 3:
                detected_goals.append(ProfileTrait(
                    label=goal,
                    confidence=min(0.5 + count * 0.05, 0.8),
                    evidence=f"表达{goal}{count}次",
                ))
        if detected_goals:
            traits["goals"] = detected_goals

        return traits, []

    # ── 特征合并 ──

    def _merge_trait(self, dim: str, new_trait: ProfileTrait):
        """合并单个特征到现有画像（去重+置信度更新）"""
        state = self._load_state()
        dim_state = state.dimensions.get(dim)
        if not dim_state:
            dim_state = DimensionState()
            state.dimensions[dim] = dim_state

        # 查找相似标签
        for existing in dim_state.values:
            if self._labels_similar(existing.label, new_trait.label):
                # 已存在：加权更新置信度
                existing.confidence = round(
                    existing.confidence * 0.6 + new_trait.confidence * 0.4, 2
                )
                existing.verified_count += 1
                existing.evidence = new_trait.evidence  # 更新最新证据
                self._save_state(state)
                return

        # 新标签
        new_trait.verified_count = 1
        dim_state.values.append(new_trait)
        # 每个维度最多保留10个特征
        dim_state.values.sort(key=lambda x: x.confidence, reverse=True)
        dim_state.values = dim_state.values[:10]
        self._save_state(state)

    @staticmethod
    def _labels_similar(a: str, b: str) -> bool:
        """判断两个标签是否相似"""
        a, b = a.lower().strip(), b.lower().strip()
        if a == b:
            return True
        if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
            return True
        return False

    # ── 建议管理 ──

    def _save_suggestions(self, suggestions: List[dict]):
        """保存建议到文件"""
        try:
            existing = []
            if SUGGESTIONS_FILE.exists():
                existing = json.loads(SUGGESTIONS_FILE.read_text(encoding="utf-8"))
            # 去重
            for s in suggestions:
                s["created_at"] = datetime.now().isoformat()
                if not any(
                    e.get("dimension") == s["dimension"] and e.get("label") == s["label"]
                    for e in existing
                ):
                    existing.append(s)
            SUGGESTIONS_FILE.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"[ProfileUpdater] 保存建议失败: {e}")

    def get_suggestions(self) -> List[dict]:
        """获取待审批建议"""
        if not SUGGESTIONS_FILE.exists():
            return []
        try:
            return json.loads(SUGGESTIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []

    def approve_suggestion(self, dim: str, label: str) -> dict:
        """批准建议 → 合并到画像"""
        suggestions = self.get_suggestions()
        approved = None
        remaining = []
        for s in suggestions:
            if s.get("dimension") == dim and s.get("label") == label:
                approved = s
            else:
                remaining.append(s)

        if not approved:
            return {"success": False, "error": "建议不存在"}

        # 合并到画像
        trait = ProfileTrait(
            label=label,
            confidence=1.0,  # 手动批准→置信度1.0
            evidence=approved.get("evidence", "用户批准"),
            source="manual",
            created_at=datetime.now().isoformat(),
        )
        self._merge_trait(dim, trait)

        # 保存剩余建议
        SUGGESTIONS_FILE.write_text(
            json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return {"success": True, "message": f"已采纳: {label}"}

    def dismiss_suggestion(self, dim: str, label: str) -> dict:
        """忽略建议"""
        suggestions = self.get_suggestions()
        remaining = [
            s for s in suggestions
            if not (s.get("dimension") == dim and s.get("label") == label)
        ]
        SUGGESTIONS_FILE.write_text(
            json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"success": True, "message": "已忽略"}

    def dismiss_all_suggestions(self) -> dict:
        """忽略所有建议"""
        SUGGESTIONS_FILE.write_text("[]", encoding="utf-8")
        return {"success": True, "message": "已清空所有建议"}

    # ── 状态管理 ──

    def _load_state(self) -> ProfileState:
        if self._state:
            return self._state

        # 1. 尝试从 cognitive_locks 加载
        try:
            conn = sqlite3.connect(self._db_path)
            row = conn.execute(
                "SELECT value FROM cognitive_locks WHERE key = 'user_profile'"
            ).fetchone()
            conn.close()

            if row:
                data = json.loads(row[0])
                state = ProfileState()
                state.version = data.get("_version", 1)
                for dim in ALL_DIMENSIONS:
                    items = data.get(dim, [])
                    if isinstance(items, list):
                        state.dimensions[dim].values = [
                            ProfileTrait(
                                label=i if isinstance(i, str) else i.get("label", ""),
                                confidence=float(i.get("confidence", 0.5)) if isinstance(i, dict) else 0.5,
                                evidence=str(i.get("evidence", "")) if isinstance(i, dict) else "",
                                source=str(i.get("source", "auto")) if isinstance(i, dict) else "auto",
                                created_at=str(i.get("created_at", "")) if isinstance(i, dict) else "",
                                verified_count=int(i.get("verified_count", 1)) if isinstance(i, dict) else 1,
                            )
                            for i in items
                            if (isinstance(i, str) and i) or (isinstance(i, dict) and i.get("label"))
                        ]
                state.total_conversations_analyzed = data.get("_conversations", 0)
                state.total_messages_analyzed = data.get("_messages", 0)
                state.last_full_analysis = data.get("_last_analysis", "")
                self._state = state
                return state
        except Exception as _e: logger.debug(f"[profile_auto_updater] 忽略: {_e}")

        self._state = ProfileState()
        return self._state

    def _save_state(self, state: ProfileState):
        """保存画像状态到 cognitive_locks"""
        self._state = state

        data = {
            "_version": state.version + 1,
            "_conversations": state.total_conversations_analyzed,
            "_messages": state.total_messages_analyzed,
            "_last_analysis": state.last_full_analysis,
        }
        for dim in ALL_DIMENSIONS:
            data[dim] = [
                {
                    "label": t.label,
                    "confidence": t.confidence,
                    "evidence": t.evidence,
                    "source": t.source,
                    "created_at": t.created_at,
                    "verified_count": t.verified_count,
                }
                for t in state.dimensions[dim].values
            ]
        state.version = data["_version"]

        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """INSERT OR REPLACE INTO cognitive_locks (key, value, source, locked_at, priority)
                   VALUES (?, ?, 'profile', datetime('now', 'localtime'), 150)""",
                ("user_profile", json.dumps(data, ensure_ascii=False)),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[ProfileUpdater] 保存画像失败: {e}")

    def _update_analysis_state(self, messages: int, conversations: int):
        state = self._load_state()
        state.total_messages_analyzed += messages
        state.total_conversations_analyzed += conversations
        state.last_full_analysis = datetime.now().isoformat()
        self._save_state(state)

    # ── 公开 API ──

    def get_profile(self) -> dict:
        """获取完整画像（合并自动+手动）"""
        state = self._load_state()
        result = {"dimensions": {}, "stats": {
            "total_conversations": state.total_conversations_analyzed,
            "total_messages": state.total_messages_analyzed,
            "last_analysis": state.last_full_analysis,
            "version": state.version,
        }}

        for dim, label in ALL_DIMENSIONS.items():
            dim_state = state.dimensions.get(dim)
            if dim_state and dim_state.values:
                result["dimensions"][dim] = {
                    "label": label,
                    "traits": [
                        {
                            "label": t.label,
                            "confidence": t.confidence,
                            "source": t.source,
                            "verified_count": t.verified_count,
                        }
                        for t in sorted(dim_state.values, key=lambda x: x.confidence, reverse=True)
                    ],
                }

        return result

    def get_profile_summary(self) -> str:
        """生成画像文本摘要（用于注入system_prompt）"""
        profile = self.get_profile()
        lines = ["## 用户画像（AI自动分析，请在回答中自然体现）"]

        for dim, info in profile.get("dimensions", {}).items():
            traits = info.get("traits", [])
            if traits:
                high_conf = [t for t in traits if t["confidence"] >= 0.6]
                if high_conf:
                    labels = ", ".join(t["label"] for t in high_conf[:5])
                    lines.append(f"- {info['label']}: {labels}")

        stats = profile.get("stats", {})
        if stats.get("last_analysis"):
            lines.append(f"\n> 基于 {stats.get('total_messages', 0)} 条消息分析，"
                         f"更新时间: {stats['last_analysis'][:16]}")

        return "\n".join(lines) if len(lines) > 1 else ""

    # ── ★ 2026-09-18 画像增强：手动编辑 + 时间线 + 主动服务 ──

    def add_manual_trait(self, dim: str, label: str) -> dict:
        """手动添加画像特征（用户自己在画像页补充）"""
        label = (label or "").strip()
        if not label:
            return {"success": False, "error": "内容不能为空"}
        if dim not in ALL_DIMENSIONS:
            return {"success": False, "error": f"未知维度: {dim}"}
        if len(label) > 50:
            return {"success": False, "error": "太长了，50 字以内"}
        trait = ProfileTrait(
            label=label,
            confidence=1.0,
            evidence="用户手动添加",
            source="manual",
            created_at=datetime.now().isoformat(),
        )
        self._merge_trait(dim, trait)
        return {"success": True, "message": f"已添加: {label}"}

    def update_trait(self, dim: str, label: str, new_label: str) -> dict:
        """修改画像特征的标签文字"""
        new_label = (new_label or "").strip()
        if not new_label:
            return {"success": False, "error": "新内容不能为空"}
        if len(new_label) > 50:
            return {"success": False, "error": "太长了，50 字以内"}
        state = self._load_state()
        dim_state = state.dimensions.get(dim)
        if not dim_state:
            return {"success": False, "error": "该维度暂无内容"}
        for t in dim_state.values:
            if t.label == label:
                t.label = new_label
                t.source = "manual" if t.source == "auto" else t.source
                t.evidence = (t.evidence or "") if t.source != "auto" else "用户手动修改"
                self._save_state(state)
                return {"success": True, "message": "已修改"}
        return {"success": False, "error": "没找到这条内容"}

    def delete_trait(self, dim: str, label: str) -> dict:
        """删除画像特征（用户认为哈文学错了）"""
        state = self._load_state()
        dim_state = state.dimensions.get(dim)
        if not dim_state:
            return {"success": False, "error": "该维度暂无内容"}
        before = len(dim_state.values)
        dim_state.values = [t for t in dim_state.values if t.label != label]
        if len(dim_state.values) == before:
            return {"success": False, "error": "没找到这条内容"}
        self._save_state(state)
        return {"success": True, "message": f"已删除: {label}"}

    def get_profile_timeline(self, limit: int = 200) -> list:
        """画像时间线：全部特征平铺按时间倒序（含证据/来源/时间）"""
        state = self._load_state()
        items = []
        for dim, dim_state in state.dimensions.items():
            for t in dim_state.values:
                items.append({
                    "dimension": dim,
                    "dimension_label": ALL_DIMENSIONS.get(dim, dim),
                    "label": t.label,
                    "confidence": t.confidence,
                    "source": t.source,
                    "verified_count": t.verified_count,
                    "evidence": t.evidence,
                    "created_at": t.created_at or "",
                })
        # 有时间的按时间倒序；没时间的（早期数据）排最后
        dated = [x for x in items if x["created_at"]]
        undated = [x for x in items if not x["created_at"]]
        dated.sort(key=lambda x: x["created_at"], reverse=True)
        return (dated + undated)[:limit]

    PENDING_INSIGHTS_FILE = PROJECT_ROOT / "data" / "profile_insights_pending.json"

    def get_recent_insights(self, days: int = 7) -> list:
        """最近N天自动学到的新特征（画像页「最近新学到」+ 主动服务共用）"""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        out = []
        for item in self.get_profile_timeline(limit=1000):
            if item["source"] == "auto" and item["created_at"] and item["created_at"] >= since:
                out.append(item)
        return out

    def write_pending_insights(self, traits: List[dict]):
        """分析发现新特征后，写入待主动提及列表（engine 下次对话时读取）"""
        try:
            existing = []
            if self.PENDING_INSIGHTS_FILE.exists():
                existing = json.loads(self.PENDING_INSIGHTS_FILE.read_text(encoding="utf-8"))
            for t in traits:
                entry = {"dimension": t["dimension"], "label": t["label"],
                         "dimension_label": t.get("dimension_label", t["dimension"])}
                if not any(e["dimension"] == entry["dimension"] and e["label"] == entry["label"]
                           for e in existing):
                    existing.append(entry)
            # 最多攒 8 条，防膨胀
            existing = existing[-8:]
            self.PENDING_INSIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.PENDING_INSIGHTS_FILE.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"[ProfileUpdater] 写入画像提醒失败: {e}")

    def consume_pending_insights(self) -> List[dict]:
        """读取并清空待主动提及列表（读即清：提及一次就不再重复）"""
        try:
            if not self.PENDING_INSIGHTS_FILE.exists():
                return []
            data = json.loads(self.PENDING_INSIGHTS_FILE.read_text(encoding="utf-8"))
            self.PENDING_INSIGHTS_FILE.unlink()
            return data or []
        except Exception:
            return []

    def reset_profile(self) -> dict:
        """重置自动画像"""
        state = ProfileState()
        self._save_state(state)
        # 删除建议
        if SUGGESTIONS_FILE.exists():
            SUGGESTIONS_FILE.unlink()
        return {"success": True, "message": "画像已重置"}

    def force_analyze(self, llm_callback=None) -> AnalysisResult:
        """强制执行一次画像分析（不限时间范围）"""
        return self.analyze_recent_conversations(
            limit=200,
            since_hours=720,  # 30天
            llm_callback=llm_callback,
        )


# ═══════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════

_profile_updater: Optional[ProfileAutoUpdater] = None


def get_profile_updater() -> ProfileAutoUpdater:
    global _profile_updater
    if _profile_updater is None:
        _profile_updater = ProfileAutoUpdater()
    return _profile_updater
