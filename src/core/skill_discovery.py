# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 对话式技能自动发现

当用户消息未匹配本地技能时，自动搜索 Resource Hub，
如发现相关技能/插件，向用户推荐一键安装。

设计原则：
- 不阻塞对话：搜索超时 2 秒，超时静默跳过
- 去重：已安装的技能不再推荐
- 安全：仅推荐 MIT/Apache/BSD 许可资源（宽松原则）
- 克制：每个消息最多推荐 3 个
"""

import threading
import logging

logger = logging.getLogger(__name__)

# 意图 → 搜索关键词映射
INTENT_KEYWORDS = {
    # 开发
    "代码": "code helper developer",
    "编程": "programming coding",
    "debug": "debug debugging",
    "调试": "debug debugging",
    "bug": "debug debugging",
    "报错": "debug error fix",
    "重构": "refactor code",
    "写代码": "code generator",
    "git": "git helper",
    "docker": "docker container",
    "sql": "sql database query",
    "api": "api design rest",
    "正则": "regex pattern",
    "脚本": "script automation",
    "ci": "ci cd pipeline",
    "测试": "unit test testing",
    "数据库": "database design sql",

    # 办公
    "excel": "excel spreadsheet formula",
    "ppt": "presentation slide design",
    "会议": "meeting notes minutes",
    # "邮件" 已移除（#1498）：聊邮件=收发邮件，不是想装 email writing 技能；且会触发后台乱搜刷报错
    "报告": "report generation",
    "可视化": "data visualization chart",
    "简历": "resume cv career",
    "合同": "contract review legal",
    "工作流": "workflow automation optimization",
    "项目管理": "project management planning",
    "规划": "project planning",

    # 创意
    "文案": "copywriting content writing",
    "写文章": "writing article essay",
    "写故事": "storytelling creative writing",
    "头脑风暴": "brainstorming ideation",
    "取名": "naming brand name",
    "标语": "slogan tagline",
    "ux": "ux design review",
    "配色": "color palette design",
    "logo": "logo design brief",
    "命名": "naming brand",

    # 学习
    "学习": "study tutor learning",
    "辅导": "tutor education learning",
    "解释": "concept explain explainer",
    "题目": "quiz question generator",
    "笔记": "note organizer",
    "闪卡": "flashcard memory",
    "论文": "essay academic writing",
    "语言": "language tutor learning",
    "记忆": "memory technique",

    # 专业
    "财报": "financial analysis report",
    "财务": "financial analysis",
    "法律": "legal research",
    "医学": "medical explain health",
    "数学": "math problem solver",
    "科学": "science explain",
    "股票": "stock research analysis",
    "加密货币": "crypto cryptocurrency analysis",
    "投资": "investment analysis stock",

    # 系统
    "整理文件": "file organizer manager",
    "整理": "file organizer manager",
    "诊断": "system diagnostic doctor",
    "备份": "backup strategy plan",
    "隐私": "privacy security guard",
    "快捷键": "keyboard shortcut",
}


def extract_discovery_keywords(message: str) -> str | None:
    """从用户消息中提取搜索关键词，用于 Resource Hub 搜索"""
    msg_lower = message.lower()

    # 按匹配长度倒序排序，长匹配优先
    sorted_intents = sorted(INTENT_KEYWORDS.items(), key=lambda x: -len(x[0]))
    for keyword, search_query in sorted_intents:
        if keyword.lower() in msg_lower:
            return search_query

    return None


def discover_suggestions(message: str, installed_skill_ids: set = None,
                         timeout: float = 2.0, max_results: int = 3) -> list:
    """
    根据用户消息自动发现可推荐的技能

    Args:
        message: 用户消息
        installed_skill_ids: 已安装技能ID集合（用于去重）
        timeout: 搜索超时秒数
        max_results: 最多返回结果数

    Returns:
        推荐列表，每项为标准化格式:
        [{
            "name": "Excel大师",
            "description": "...",
            "source": "github",
            "source_label": "GitHub",
            "source_icon": "🐙",
            "url": "...",
            "license": "MIT",
            "quality": 8.5,
            "resource_type": "skill",
            "install_action": "install_from_suggestion",
        }]
    """
    installed_skill_ids = installed_skill_ids or set()

    # 提取搜索关键词
    keywords = extract_discovery_keywords(message)
    if not keywords:
        return []

    logger.info(f"[SkillDiscovery] 意图检测 → 搜索关键词: {keywords}")

    result_holder = []

    def _search():
        try:
            from src.core.resource_hub import get_resource_hub
            hub = get_resource_hub()
            # 搜索技能和插件
            results = hub.search(keywords, resource_type="skill", limit=5)
            top = results.get("top", [])

            suggestions = []
            for item in top:
                # 去重：跳过已安装的技能
                item_id = item.get("full_name") or item.get("name") or ""
                if item_id and item_id in installed_skill_ids:
                    continue
                # 检查许可证（仅推荐安全许可）
                license_info = item.get("license", "")
                if isinstance(license_info, dict):
                    license_spdx = (license_info.get("spdx_id") or
                                    license_info.get("key") or "").upper()
                else:
                    license_spdx = str(license_info).upper()

                # 只推荐 MIT/Apache/BSD/CC0/Unlicense，跳过 GPL/无许可/未知
                safe_licenses = {"MIT", "APACHE-2.0", "APACHE", "BSD", "BSD-2-CLAUSE",
                                 "BSD-3-CLAUSE", "CC0-1.0", "UNLICENSE", "ISC", "MPL-2.0"}
                if license_spdx and license_spdx not in safe_licenses:
                    logger.debug(f"[SkillDiscovery] 跳过非安全许可: {item.get('name')} ({license_spdx})")
                    continue

                suggestions.append({
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                    "source": item.get("source", "unknown"),
                    "source_label": item.get("source_label", ""),
                    "source_icon": item.get("source_icon", "📦"),
                    "url": item.get("url") or item.get("html_url", ""),
                    "license": license_spdx or "未知",
                    "quality": item.get("quality", 5.0),
                    "resource_type": item.get("resource_type", "skill"),
                    "resource_id": item.get("full_name") or item.get("id", ""),
                })
                if len(suggestions) >= max_results:
                    break

            result_holder.extend(suggestions)
        except Exception as e:
            logger.debug(f"[SkillDiscovery] 搜索失败（静默跳过）: {e}")

    t = threading.Thread(target=_search, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        logger.debug("[SkillDiscovery] 搜索超时，静默跳过")
        return []

    return result_holder
