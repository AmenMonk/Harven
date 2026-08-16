# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
笔记插件 — 快速记录/搜索笔记
"""
import re
from src.core.plugin_system import PluginBase, PluginResult, PluginContext


class NotePlugin(PluginBase):
    name = "note"
    version = "1.0.0"
    description = "快速笔记：记录、搜索、列出笔记"
    author = "Harven AI"

    _NOTE_PATTERNS = [
        (r"(?:记一下|记笔记|记录一下|备注|备忘)[:：]?\s*(.+)", 0.9),
        (r"(?:查笔记|搜索笔记|我的笔记|列出笔记|查找笔记)", 0.95),
    ]

    def should_handle(self, message: str) -> float:
        for pattern, score in self._NOTE_PATTERNS:
            if re.search(pattern, message):
                return score
        return 0.0

    def execute(self, context: PluginContext) -> PluginResult:
        msg = context.user_message

        # 记录笔记
        record_match = re.search(
            r"(?:记一下|记笔记|记录一下|备注|备忘)[:：]?\s*(.+)", msg
        )
        if record_match:
            note_content = record_match.group(1).strip()
            if len(note_content) < 2:
                return PluginResult(success=False, message="笔记内容太短")

            context.save_to_knowledge(
                note_content,
                source=f"note:{context.conversation_id}"
            )
            return PluginResult(
                success=True,
                message=f"✅ 已记录笔记：{note_content[:80]}",
                data={"action": "save", "content": note_content},
            )

        # 搜索/列出笔记
        if any(kw in msg for kw in ["查笔记", "搜索笔记", "我的笔记", "列出笔记", "查找笔记"]):
            search_term = re.sub(r"(查笔记|搜索笔记|我的笔记|列出笔记|查找笔记)[:：]?", "", msg).strip()
            results = context.search_knowledge(search_term or "note:", max_results=5)
            if results:
                lines = ["📝 笔记列表："]
                for r in results[:5]:
                    content = r.get("content", "")[:100]
                    lines.append(f"  • {content}")
                return PluginResult(
                    success=True,
                    message="\n".join(lines),
                    data={"action": "search", "results": results},
                )
            else:
                return PluginResult(
                    success=True,
                    message="📝 暂无笔记记录",
                    data={"action": "search", "results": []},
                )

        return PluginResult(success=False, message="未识别笔记操作")


plugin_class = NotePlugin
