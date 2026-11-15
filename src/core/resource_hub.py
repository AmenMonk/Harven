# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 全网资源发现引擎 (Resource Hub)
==============================================
多渠道聚合搜索、智能分类、安全审计、版权检测、一键安装

覆盖渠道：
  - HarvenHub (官方技能市场)
  - SkillHub (WorkBuddy/OpenClaw 技能注册中心)
  - ClawHub (OpenClaw 生态 13K+ 技能)
  - GitHub (仓库 + Gist)
  - PyPI (Python 包)
  - HuggingFace (AI 模型/数据集/技能)
  - Gitee (国内镜像，高速下载)
  - Chocolatey/Winget (Windows 软件)
  - AI 自生成 (对话中即时创建)

分类体系：
  - plugin:  可执行代码插件 (Python/Node)
  - skill:   Markdown 提示词技能
  - knowledge: 知识库/数据集
  - tool:    可安装软件/CLI 工具
  - other:   未分类

安全五重防护：
  1. 来源验证 (星标数/活跃度/作者信誉)
  2. 静态扫描 (AST + 正则)
  3. 沙箱执行 (独立进程)
  4. 版权检查 (LICENSE 解析)
  5. 用户可见+可控
"""

import os
import re
import io
import ast
import json
import yaml
import hashlib
import zipfile
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse
import logging

logger = logging.getLogger(__name__)

# ====================================================================
# 全局配置
# ====================================================================

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 资源分类
RESOURCE_TYPES = {
    "plugin": {
        "label": "🔌 插件",
        "desc": "可执行代码，扩展 Harven 的能力",
        "extensions": [".py", ".js", ".ts", ".go"],
        "indicators": ["plugin", "extension", "addon", "tool"],
        "danger_level": "P2",
    },
    "skill": {
        "label": "🎯 技能",
        "desc": "Markdown 提示词，注入对话上下文",
        "extensions": [".md", ".yaml", ".yml", ".toml"],
        "indicators": ["skill", "prompt", "system-prompt", "instruction"],
        "danger_level": "P1",
    },
    "knowledge": {
        "label": "📚 知识",
        "desc": "知识库、数据集、文档集合",
        "extensions": [".json", ".csv", ".txt", ".md", ".pdf"],
        "indicators": ["dataset", "knowledge-base", "corpus", "rag", "faq"],
        "danger_level": "P0",
    },
    "tool": {
        "label": "🔧 工具",
        "desc": "可安装软件、CLI 工具、自动化脚本",
        "extensions": [".exe", ".msi", ".sh", ".bat", ".ps1"],
        "indicators": ["cli", "tool", "utility", "installer", "app"],
        "danger_level": "P2",
    },
    "other": {
        "label": "📦 其他",
        "desc": "未分类资源",
        "extensions": [],
        "indicators": [],
        "danger_level": "P1",
    },
}

# 开源许可证分类
LICENSE_CATEGORIES = {
    "safe": {
        "label": "🟢 安全",
        "desc": "可自由使用、修改、分发",
        "patterns": ["mit", "apache", "apache-2.0", "bsd", "bsd-2-clause",
                     "bsd-3-clause", "unlicense", "cc0", "isc", "wtfpl",
                     "zlib", "mpl-2.0", "artistic-2.0"],
    },
    "caution": {
        "label": "🟡 注意",
        "desc": "有传染性条款，商业使用需谨慎",
        "patterns": ["gpl", "gpl-2.0", "gpl-3.0", "agpl", "agpl-3.0",
                     "lgpl", "lgpl-2.1", "lgpl-3.0", "mpl", "epl",
                     "cc-by-sa", "cc-by-nc"],
    },
    "danger": {
        "label": "🔴 禁止",
        "desc": "无许可证或商业闭源，禁止分发",
        "patterns": [],  # 未匹配到任何许可证
    },
}

# 信赖的源（有最低质量保证）
TRUSTED_SOURCES = [
    "github.com", "gitee.com", "pypi.org", "huggingface.co",
    "gitlab.com", "bitbucket.org",
]

# 搜索关键词库（按类别）
SEARCH_KEYWORDS = {
    "plugin": [
        "ai-plugin", "agent-plugin", "llm-plugin", "langchain-tool",
        "crewai-tool", "autogen-skill", "cursor-rule",
        "harven-ai-plugin", "lingzhi-plugin",
    ],
    "skill": [
        "ai-skill", "agent-skill", "llm-skill", "claude-skill",
        "system-prompt", "prompt-template", "instruction-set",
        "SKILL.md", "skill.yaml", "manifest.yaml",
    ],
    "knowledge": [
        "knowledge-base", "awesome-list", "cheatsheet", "rag-corpus",
        "prompt-collection", "prompt-library", "faq-dataset",
        "domain-knowledge", "reference-data",
    ],
    "tool": [
        "cli-tool", "productivity-tool", "automation-script",
        "windows-utility", "desktop-app", "portable-app",
    ],
}

# PyPI 分类器映射
PYPI_CLASSIFIERS = {
    "plugin": [
        "Framework :: AI",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Intended Audience :: Developers",
    ],
    "tool": [
        "Topic :: Utilities",
        "Topic :: System :: Systems Administration",
    ],
}


# ====================================================================
# 工具函数
# ====================================================================

def compute_hash(data: bytes) -> str:
    """SHA-256 哈希"""
    return hashlib.sha256(data).hexdigest()


def classify_resource(name: str, description: str, filenames: list = None) -> str:
    """根据名称、描述、文件列表自动分类"""
    text = f"{name} {description}".lower()
    scores = {}
    for rtype, info in RESOURCE_TYPES.items():
        if rtype == "other":
            continue
        score = 0
        for indicator in info["indicators"]:
            if indicator.lower() in text:
                score += 2
        # Check files
        if filenames:
            for f in filenames:
                _, ext = os.path.splitext(f.lower())
                if ext in info["extensions"]:
                    score += 1
                for indicator in info["indicators"]:
                    if indicator.lower() in f.lower():
                        score += 1
        scores[rtype] = score
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def detect_license(text: str) -> dict:
    """检测 LICENSE 类型"""
    text_lower = text.lower()
    for category, info in LICENSE_CATEGORIES.items():
        for pattern in info["patterns"]:
            if pattern in text_lower:
                return {
                    "category": category,
                    "label": info["label"],
                    "desc": info["desc"],
                    "license": pattern,
                }
    # 未匹配到精确模式时，用模糊匹配
    if "general public license" in text_lower:
        return {
            "category": "caution",
            "label": LICENSE_CATEGORIES["caution"]["label"],
            "desc": LICENSE_CATEGORIES["caution"]["desc"],
            "license": "gpl",
        }
    # 默认：无许可证
    return {
        "category": "danger",
        "label": LICENSE_CATEGORIES["danger"]["label"],
        "desc": LICENSE_CATEGORIES["danger"]["desc"],
        "license": "unknown",
    }


def quality_score(repo_data: dict) -> float:
    """计算资源质量分 (0.0-10.0)"""
    score = 0.0
    # 星标
    stars = repo_data.get("stargazers_count", 0) or repo_data.get("stars", 0)
    if stars > 1000:
        score += 3.0
    elif stars > 100:
        score += 2.0
    elif stars > 10:
        score += 1.0
    # 最近更新
    updated = repo_data.get("updated_at") or repo_data.get("pushed_at", "")
    if updated:
        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00").replace("T", " "))
            age = (datetime.now() - dt.replace(tzinfo=None)).days
            if age < 30:
                score += 2.0
            elif age < 180:
                score += 1.0
            elif age < 365:
                score += 0.5
        except Exception as _e: logger.debug(f"[resource_hub] 忽略: {_e}")
    # 有描述
    if repo_data.get("description") and len(str(repo_data.get("description", ""))) > 20:
        score += 0.5
    # 有许可证
    if repo_data.get("license") or repo_data.get("license_info"):
        score += 1.0
    return min(score, 10.0)


def parse_skill_markdown(content: str) -> dict | None:
    """解析 Markdown 技能的 YAML front matter"""
    try:
        fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n?(.*)', content, re.DOTALL)
        if fm_match:
            meta = yaml.safe_load(fm_match.group(1)) or {}
            body = fm_match.group(2).strip()
            return {
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "triggers": meta.get("triggers", []),
                "author": meta.get("author", ""),
                "version": meta.get("version", "1.0"),
                "prompt": body,
            }
    except Exception as _e: logger.debug(f"[resource_hub] 忽略: {_e}")
    return None


# ====================================================================
# 渠道适配器
# ====================================================================

class ChannelAdapter:
    """渠道适配器基类"""

    name = "unknown"
    label = "未知渠道"
    icon = "📦"
    priority = 10  # 数字越小越优先（0=HarvenHub官方，5=GitHub/Gitee，10=其他）
    enabled = True
    timeout = 15
    rate_limited = False
    supports_browse = False  # 是否支持浏览模式（无需搜索词即可列出技能）
    is_official = False  # HarvenHub 官方渠道标记
    supports_browse = False  # 是否支持浏览模式（无需搜索词即可列出技能）

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        """搜索资源，返回标准化结果列表"""
        raise NotImplementedError

    def browse(self, category: str = None, limit: int = 20) -> list:
        """浏览资源（无需搜索词，列出热门/最新技能）

        默认实现：用空关键词搜索。子类可覆盖以使用专用列表 API。
        """
        return self.search("", "all", limit)

    def browse_categories(self) -> list:
        """获取渠道支持的分类列表"""
        return []

    def browse(self, category: str = None, limit: int = 20) -> list:
        """浏览资源（无需搜索词，列出热门/最新技能）- 子类可覆盖"""
        return self.search("", "all", limit)

    def browse_categories(self) -> list:
        """获取渠道支持的分类列表 - 子类可覆盖"""
        return []

    def standardize(self, raw: dict, rtype: str = "other") -> dict:
        """将渠道原始结果标准化为统一格式"""
        return {
            "source": self.name,
            "source_label": self.label,
            "source_icon": self.icon,
            "id": raw.get("id", ""),
            "name": raw.get("name", ""),
            "full_name": raw.get("full_name", raw.get("name", "")),
            "description": raw.get("description", ""),
            "url": raw.get("html_url", raw.get("url", "")),
            "download_url": raw.get("download_url", raw.get("clone_url", "")),
            "author": raw.get("author", raw.get("owner", "")),
            "stars": raw.get("stargazers_count", raw.get("stars", 0)),
            "updated_at": raw.get("updated_at", raw.get("pushed_at", "")),
            "resource_type": rtype,
            "resource_type_label": RESOURCE_TYPES.get(rtype, {}).get("label", "📦 其他"),
            "license": raw.get("license_info", raw.get("license", {})),
            "quality": quality_score(raw),
            "tags": raw.get("topics", raw.get("keywords", [])),
            "installable": True,
            "raw": raw,
        }


class GitHubAdapter(ChannelAdapter):
    """GitHub 搜索适配器"""

    name = "github"
    label = "GitHub"
    icon = "🐙"
    timeout = 15

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        results = []
        try:
            import urllib.request

            # 构建搜索词：原始 query + 分类关键词
            keywords = SEARCH_KEYWORDS.get(resource_type, []) if resource_type != "all" else []
            # 对所有分类的关键词进行采样
            if resource_type == "all":
                for kws in SEARCH_KEYWORDS.values():
                    keywords.extend(kws[:3])

            # 多轮搜索（用不同关键词组合提高命中率）
            search_terms = [query]
            for kw in keywords[:5]:  # 限制 5 个关键词避免太多请求
                search_terms.append(f"{query} {kw}")

            seen = set()
            for term in search_terms[:3]:  # 最多 3 轮搜索
                if len(results) >= limit:
                    break
                batch = self._search_single(term, limit=limit)
                for item in batch:
                    uid = item.get("id", item.get("full_name", ""))
                    if uid and uid not in seen:
                        seen.add(uid)
                        rtype = classify_resource(
                            item.get("name", ""),
                            item.get("description", ""),
                        )
                        results.append(self.standardize(item, rtype))
            return results[:limit]
        except Exception as e:
            logger.error(f"[GitHub] 搜索失败: {e}")
            return []

    def _search_single(self, query: str, limit: int = 10) -> list:
        try:
            import urllib.request
            q = quote(f"{query} in:name,description,topics,readme")
            url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page={limit}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/vnd.github.v3+json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("items", [])
        except Exception as e:
            logger.error(f"[GitHub] 单次搜索失败: {e}")
            return []


class GiteeAdapter(ChannelAdapter):
    """Gitee 搜索适配器（国内高速）"""

    name = "gitee"
    label = "Gitee 码云"
    icon = "🏮"
    timeout = 10

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        results = []
        try:
            import urllib.request
            import urllib.error
            q = quote(query)

            def _process_data(data):
                seen = set()
                for item in data if isinstance(data, list) else data.get("items", []):
                    uid = item.get("id", item.get("full_name", ""))
                    if uid and uid not in seen:
                        seen.add(uid)
                        rtype = classify_resource(
                            item.get("name", ""),
                            item.get("description", ""),
                        )
                        mapped = {
                            "id": str(item.get("id", "")),
                            "name": item.get("name", ""),
                            "full_name": item.get("full_name", ""),
                            "description": item.get("description", ""),
                            "html_url": item.get("html_url", ""),
                            "clone_url": item.get("ssh_url", item.get("html_url", "")),
                            "stargazers_count": item.get("stargazers_count", 0),
                            "updated_at": item.get("updated_at", ""),
                            "owner": (item.get("owner") or {}).get("login", ""),
                            "topics": item.get("topics", []),
                        }
                        results.append(self.standardize(mapped, rtype))

            # Gitee API v5 搜索要求认证，先尝试搜索接口
            url = f"https://gitee.com/api/v5/search/repositories?q={q}&sort=stars&order=desc&per_page={limit}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    _process_data(data)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    # 搜索 API 需认证 → 回退到精确仓库查询
                    repo_url = f"https://gitee.com/api/v5/repos/{q}"
                    repo_req = urllib.request.Request(repo_url, headers={
                        "User-Agent": "HarvenAI-ResourceHub/1.0",
                        "Accept": "application/json",
                    })
                    try:
                        with urllib.request.urlopen(repo_req, timeout=self.timeout) as resp:
                            item = json.loads(resp.read().decode("utf-8"))
                            if item.get("name"):
                                _process_data([item])
                    except Exception as _e: logger.debug(f"[resource_hub] 忽略: {_e}")  # 精确匹配也失败，返回空
                else:
                    raise
            return results[:limit]
        except Exception as e:
            logger.error(f"[Gitee] 搜索失败: {e}")
            return []


class PyPIAdapter(ChannelAdapter):
    """PyPI 搜索适配器"""

    name = "pypi"
    label = "PyPI"
    icon = "🐍"
    timeout = 10

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        results = []
        try:
            import urllib.request
            q = quote(query)
            # PyPI Warehouse JSON API（旧版 /pypi?q= 返回 HTML，已废弃）
            json_url = f"https://pypi.org/search/?q={q}"
            req = urllib.request.Request(json_url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
                # PyPI 搜索页返回 HTML（无公开 JSON 搜索 API），非 JSON 静默跳过（#1498 不再刷 ERROR）
                if not raw.lstrip().startswith(("{", "[")):
                    logger.debug("[PyPI] 搜索返回非 JSON（HTML 页），跳过解析")
                    return []
                data = json.loads(raw)
                seen = set()
                for item in data.get("results", [])[:limit * 2]:
                    name = item.get("name", "")
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    rtype = classify_resource(name, item.get("summary", ""))
                    mapped = {
                        "id": name,
                        "name": name,
                        "full_name": name,
                        "description": item.get("summary", ""),
                        "html_url": item.get("package_url", f"https://pypi.org/project/{name}/"),
                        "clone_url": f"https://pypi.org/project/{name}/",
                        "download_url": f"https://pypi.org/project/{name}/",
                        "stars": 0,  # PyPI 无星标
                        "updated_at": "",  # 需单独请求
                        "owner": "",
                        "topics": item.get("keywords", "").split() if item.get("keywords") else [],
                    }
                    results.append(self.standardize(mapped, rtype))
            return results[:limit]
        except Exception as e:
            logger.error(f"[PyPI] 搜索失败: {e}")
            return []


class HuggingFaceAdapter(ChannelAdapter):
    """HuggingFace 搜索适配器"""

    name = "huggingface"
    label = "HuggingFace"
    icon = "🤗"
    timeout = 12

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        results = []
        try:
            import urllib.request
            q = quote(query)
            # 搜索 models + datasets + spaces
            for category in ["models", "datasets", "spaces"]:
                if len(results) >= limit:
                    break
                url = f"https://huggingface.co/api/{category}?search={q}&sort=downloads&direction=-1&limit={limit}"
                req = urllib.request.Request(url, headers={
                    "User-Agent": "HarvenAI-ResourceHub/1.0",
                })
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        for item in data[:limit]:
                            rid = item.get("id", item.get("modelId", ""))
                            if not rid:
                                continue
                            description = (
                                item.get("pipeline_tag", "")
                                + " " + (item.get("cardData", {}) or {}).get("description", "")
                            ).strip()
                            rtype = classify_resource(rid, description)
                            if rtype == "other" and category == "datasets":
                                rtype = "knowledge"
                            mapped = {
                                "id": rid,
                                "name": rid.split("/")[-1] if "/" in rid else rid,
                                "full_name": rid,
                                "description": description or f"HuggingFace {category}: {rid}",
                                "html_url": f"https://huggingface.co/{rid}",
                                "clone_url": f"https://huggingface.co/{rid}",
                                "download_url": f"https://huggingface.co/{rid}",
                                "stargazers_count": item.get("downloads", item.get("likes", 0)),
                                "updated_at": item.get("lastModified", ""),
                                "owner": rid.split("/")[0] if "/" in rid else "",
                                "topics": item.get("tags", []),
                                "license": item.get("cardData", {}).get("license", ""),
                            }
                            results.append(self.standardize(mapped, rtype))
                except Exception as _e: logger.debug(f"[resource_hub] 忽略: {_e}")
            return results[:limit]
        except Exception as e:
            logger.error(f"[HuggingFace] 搜索失败: {e}")
            return []


class WingetAdapter(ChannelAdapter):
    """Windows Winget 搜索适配器"""

    name = "winget"
    label = "Winget 软件"
    icon = "💿"
    timeout = 8
    enabled = os.name == "nt"  # 仅在 Windows 上启用

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        results = []
        if not self.enabled:
            return results
        try:
            import subprocess
            result = subprocess.run(
                ["winget", "search", query, "--accept-source-agreements"],
                capture_output=True, timeout=self.timeout,
                encoding="utf-8", errors="replace",  # 系统默认 GBK 解 UTF-8 输出会崩线程（#1498）
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            if result.returncode != 0:
                return results
            lines = result.stdout.strip().split("\n")
            # 跳过表头（前 2 行）
            for line in lines[2:]:
                if not line.strip():
                    continue
                parts = line.split(None, 3)
                if len(parts) >= 2:
                    name = parts[0]
                    pkg_id = parts[1] if len(parts) > 1 else name
                    version = parts[2] if len(parts) > 2 else ""
                    desc = parts[3] if len(parts) > 3 else ""
                    rtype = classify_resource(name, desc)
                    if rtype == "other":
                        rtype = "tool"
                    mapped = {
                        "id": pkg_id,
                        "name": name,
                        "full_name": f"{name} v{version}",
                        "description": desc,
                        "html_url": f"https://winget.run/pkg/{pkg_id}",
                        "clone_url": "",
                        "download_url": f"winget://install/{pkg_id}",
                        "stargazers_count": 0,
                        "updated_at": "",
                        "owner": "Microsoft Community",
                        "topics": [],
                    }
                    results.append(self.standardize(mapped, rtype))
                if len(results) >= limit:
                    break
            return results[:limit]
        except Exception as e:
            logger.error(f"[Winget] 搜索失败: {e}")
            return []


class SkillHubAdapter(ChannelAdapter):
    """SkillHub 技能商店适配器（lightmake.site）

    SkillHub 是 WorkBuddy/OpenClaw 生态的技能注册中心，
    支持中英文语义搜索，覆盖 100+ 专家包和场景技能。
    """

    name = "skillhub"
    label = "SkillHub"
    icon = "🛒"
    priority = 5
    timeout = 10
    supports_browse = True
    _api_url = "https://lightmake.site/api/v1/search"
    _categories_url = "https://lightmake.site/api/v1/categories"

    def browse(self, category: str = None, limit: int = 20) -> list:
        """浏览 SkillHub 技能列表（无需搜索词）

        空搜索 + 可选分类过滤 → 展示全部/分类内热门技能。
        """
        try:
            import urllib.request
            params = f"q=&limit={limit}"
            if category:
                params += f"&category={category}"
            url = f"{self._api_url}?{params}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data if isinstance(data, list) else data.get("results", data.get("items", []))
                return self._process_items(items, limit)
        except Exception as e:
            logger.error(f"[SkillHub] 浏览失败: {e}")
            return []

    def browse_categories(self) -> list:
        """获取 SkillHub 分类列表"""
        try:
            import urllib.request
            req = urllib.request.Request(self._categories_url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", []) if isinstance(data, dict) else data
                return [
                    {"key": item.get("key", ""), "name": item.get("name", item.get("nameEn", "")),
                     "nameEn": item.get("nameEn", ""), "sortOrder": item.get("sortOrder", 99)}
                    for item in items if item.get("active", True)
                ]
        except Exception as e:
            logger.error(f"[SkillHub] 分类获取失败: {e}")
            return []

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        results = []
        try:
            import urllib.request
            q = quote(query)
            url = f"{self._api_url}?q={q}&limit={limit}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data if isinstance(data, list) else data.get("skills", data.get("results", data.get("items", [])))
                results = self._process_items(items, limit)
            return results
        except Exception as e:
            logger.error(f"[SkillHub] 搜索失败: {e}")
            return []

    def _process_items(self, items: list, limit: int = 20) -> list:
        """标准化 SkillHub 项目列表"""
        results = []
        seen = set()
        for item in items[:limit]:
            uid = item.get("id", item.get("slug", item.get("name", "")))
            if not uid or uid in seen:
                continue
            seen.add(uid)
            rtype = item.get("type", item.get("category", classify_resource(
                item.get("name", ""), item.get("description", ""))))
            if rtype == "other":
                rtype = "skill"
            mapped = {
                "id": str(uid),
                "name": item.get("name", item.get("displayName", uid)),
                "full_name": f"skillhub/{item.get('slug', item.get('name', uid))}",
                "description": item.get("description", item.get("summary", "")),
                "html_url": item.get("url", item.get("homepage", item.get("html_url", f"https://lightmake.site/skills/{uid}"))),
                "download_url": item.get("download_url", item.get("install_url", "")),
                "clone_url": item.get("download_url", item.get("install_url", "")),
                "stargazers_count": item.get("stars", item.get("rating", item.get("downloads", 0))),
                "updated_at": item.get("updated_at", item.get("updated", "")),
                "owner": item.get("owner_name", item.get("author", item.get("publisher", "SkillHub"))),
                "topics": item.get("tags", item.get("keywords", [])),
                "license": item.get("license", ""),
                "category": item.get("category", ""),
                "version": item.get("version", ""),
                "icon_url": item.get("icon_url", ""),
                "installs": item.get("installs", item.get("downloads", 0)),
            }
            results.append(self.standardize(mapped, rtype))
        return results


class ClawHubAdapter(ChannelAdapter):
    """ClawHub 技能市场适配器（clawhub.com）

    ClawHub 是 OpenClaw 生态最大的技能市场，拥有 13,000+ 技能，
    涵盖开发工具、自动化、数据分析、AI 应用等全品类。
    """

    name = "clawhub"
    label = "ClawHub"
    icon = "🦞"
    priority = 5
    timeout = 10
    supports_browse = True
    _api_url = "https://clawhub.com/api/v1/search"
    _list_url = "https://clawhub.com/api/v1/skills"

    def browse(self, category: str = None, limit: int = 20) -> list:
        """浏览 ClawHub 技能列表（使用专用列表 API，支持分页）

        ClawHub 有独立的 /api/v1/skills 端点，按活跃度排序，
        返回热门技能列表，支持 cursor 分页。
        """
        try:
            import urllib.request
            url = f"{self._list_url}?limit={limit}"
            if category:
                url += f"&category={category}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", []) if isinstance(data, dict) else data
                return self._process_items(items, limit)
        except Exception as e:
            logger.error(f"[ClawHub] 浏览失败: {e}")
            return []

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        results = []
        try:
            import urllib.request
            q = quote(query)
            url = f"{self._api_url}?q={q}&limit={limit}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data if isinstance(data, list) else data.get("skills", data.get("results", data.get("items", [])))
                return self._process_items(items, limit)
        except Exception as e:
            logger.error(f"[ClawHub] 搜索失败: {e}")
            return []

    def _process_items(self, items: list, limit: int = 20) -> list:
        """标准化 ClawHub 项目列表（search 和 browse 共用）"""
        results = []
        seen = set()
        for item in items[:limit]:
            uid = item.get("id", item.get("slug", item.get("name", "")))
            if not uid or uid in seen:
                continue
            seen.add(uid)
            rtype = item.get("type", item.get("category", classify_resource(
                item.get("name", ""), item.get("description", ""))))
            if rtype == "other":
                rtype = "skill"
            stats = item.get("stats", {}) if isinstance(item.get("stats"), dict) else {}
            mapped = {
                "id": str(uid),
                "name": item.get("name", item.get("displayName", uid)),
                "full_name": f"clawhub/{item.get('slug', item.get('name', uid))}",
                "description": item.get("description", item.get("summary", "")),
                "html_url": item.get("url", item.get("homepage", item.get("html_url", f"https://clawhub.com/skills/{uid}"))),
                "download_url": item.get("download_url", item.get("clone_url", "")),
                "clone_url": item.get("clone_url", item.get("download_url", "")),
                "stargazers_count": item.get("stars", stats.get("stars", item.get("downloads", 0))),
                "updated_at": item.get("updated_at", item.get("updated", "")),
                "owner": item.get("owner_name", item.get("author", item.get("publisher", item.get("owner", "ClawHub")))),
                "topics": item.get("tags", item.get("keywords", item.get("topics", []))),
                "license": item.get("license", ""),
                "version": item.get("latestVersion", item.get("version", "")),
                "installs": stats.get("installs", stats.get("downloads", 0)),
            }
            results.append(self.standardize(mapped, rtype))
        return results

class HarvenHubAdapter(ChannelAdapter):
    """HarvenHub 官方技能市场适配器（最高优先级）

    HarvenHub 是 Harven AI 的官方资源市场，优先推荐。
    如果 HarvenHub 尚未上线或无结果，自动降级到其他渠道搜索。
    """

    name = "harvenhub"
    label = "HarvenHub 官方"
    icon = "🏠"
    priority = 0  # 最高优先级，始终排第一
    timeout = 8
    is_official = True
    _harvenhub_url = "https://harvenhub.com/api/v1"

    def search(self, query: str, resource_type: str = "all", limit: int = 10) -> list:
        """搜索 HarvenHub 官方市场

        - 如果 HarvenHub 已上线且连通 → 返回官方精选资源
        - 如果 HarvenHub 未上线/不可达 → 返回空列表（触发前端自动降级）
        """
        results = []
        try:
            import urllib.request
            q = quote(query)
            url = f"{self._harvenhub_url}/skills/search?q={q}&type={resource_type}&limit={limit}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "HarvenAI-ResourceHub/1.0",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data if isinstance(data, list) else data.get("skills", data.get("results", []))
                seen = set()
                for item in items[:limit]:
                    uid = item.get("id", item.get("name", ""))
                    if uid and uid not in seen:
                        seen.add(uid)
                        rtype = item.get("type", classify_resource(
                            item.get("name", ""), item.get("description", "")))
                        mapped = {
                            "id": str(item.get("id", uid)),
                            "name": item.get("name", ""),
                            "full_name": f"harvenhub/{item.get('name', '')}",
                            "description": item.get("description", ""),
                            "html_url": item.get("url", f"https://harvenhub.com/skills/{uid}"),
                            "clone_url": item.get("download_url", ""),
                            "download_url": item.get("download_url", ""),
                            "stargazers_count": item.get("rating", item.get("stars", 0)),
                            "updated_at": item.get("updated_at", ""),
                            "owner": item.get("author", "HarvenHub Official"),
                            "topics": item.get("tags", item.get("keywords", [])),
                            "license": item.get("license", "Apache-2.0"),
                            "verified": item.get("verified", True),
                            "official": True,
                        }
                        results.append(self.standardize(mapped, rtype))
            return results
        except Exception:
            # HarvenHub 不可达（未上线或网络问题）→ 静默降级，返回空列表
            # 前端会自动展示 "HarvenHub 正在建设中，已为您搜索全网资源"
            return []


# ====================================================================
# 搜索引擎
# ====================================================================

class ResourceHub:
    """全网资源发现引擎（单例）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._channels: dict[str, ChannelAdapter] = {}
        self._register_channels()

    def _register_channels(self):
        """注册所有渠道（按优先级排序：HarvenHub → 外部技能商店 → 代码仓库 → 包管理 → 工具）"""
        channels = [
            HarvenHubAdapter(),      # priority=0  🏠 官方市场，始终第一
            SkillHubAdapter(),       # priority=5  🛒 技能注册中心
            ClawHubAdapter(),        # priority=5  🦞 OpenClaw 技能生态
            GitHubAdapter(),         # priority=10 🐙 最大开源池
            GiteeAdapter(),          # priority=10 🏮 国内高速镜像
            PyPIAdapter(),           # priority=10 🐍 Python 包
            HuggingFaceAdapter(),    # priority=10 🤗 AI 模型/数据集
            WingetAdapter(),         # priority=10 💿 Windows 软件
        ]
        for ch in channels:
            if ch.enabled:
                self._channels[ch.name] = ch
                logger.info(f"[ResourceHub] 已注册渠道: {ch.icon} {ch.label} (priority={ch.priority})")

        # 按优先级排序
        self._ordered_channels = sorted(
            self._channels.keys(),
            key=lambda k: self._channels[k].priority
        )

    def get_channels(self) -> list:
        """获取可用渠道列表（按优先级排序）"""
        ordered = self._ordered_channels if hasattr(self, '_ordered_channels') else list(self._channels.keys())
        return [
            {
                "id": ch.name,
                "label": ch.label,
                "icon": ch.icon,
                "enabled": ch.enabled,
                "priority": ch.priority,
                "is_official": ch.is_official,
            }
            for ch in [self._channels[k] for k in ordered if k in self._channels]
        ]

    def browse_channels(self, channels: list = None, categories: dict = None,
                        limit: int = 30) -> dict:
        """浏览模式：获取指定渠道的热门/推荐技能列表（无需搜索词）

        Args:
            channels: 渠道列表，None=所有支持浏览的渠道
            categories: {channel_name: category_key} 按渠道指定分类
            limit: 每个渠道最多返回数

        Returns:
            {
                "channels": [{id, label, icon, count, is_official, supports_browse, categories}],
                "by_channel": {channel_name: [standardized_results]},
                "total": int,
            }
        """
        all_results = {}
        total = 0
        channel_info = []

        import concurrent.futures

        cats = categories or {}

        def browse_ch(ch_name):
            ch = self._channels.get(ch_name)
            if not ch or not ch.supports_browse:
                return ch_name, [], []
            try:
                cat = cats.get(ch_name, None)
                results = ch.browse(category=cat, limit=limit // max(1, len(self._channels)) or 10)
                cats_list = ch.browse_categories()
                return ch_name, results, cats_list
            except Exception as e:
                logger.error(f"[ResourceHub] 浏览渠道 {ch_name} 异常: {e}")
                return ch_name, [], []

        target = channels or [k for k in self._ordered_channels
                            if k in self._channels and self._channels[k].supports_browse]

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(target)) as executor:
            futures = {executor.submit(browse_ch, ch): ch for ch in target}
            for future in concurrent.futures.as_completed(futures):
                ch_name, results, cats_list = future.result()
                all_results[ch_name] = results
                total += len(results)
                ch = self._channels.get(ch_name)
                channel_info.append({
                    "id": ch_name,
                    "label": ch.label if ch else ch_name,
                    "icon": ch.icon if ch else "📦",
                    "count": len(results),
                    "is_official": ch.is_official if ch else False,
                    "supports_browse": True,
                    "categories": cats_list,
                })

        return {
            "channels": sorted(channel_info, key=lambda c: c.get("priority", 99)),
            "by_channel": all_results,
            "total": total,
        }

    def browse_channel_categories(self, channel: str) -> dict:
        """获取单个渠道的分类列表"""
        ch = self._channels.get(channel)
        if not ch or not ch.supports_browse:
            return {"channel": channel, "categories": [], "error": "Channel does not support browsing"}
        try:
            cats = ch.browse_categories()
            return {"channel": channel, "categories": cats}
        except Exception as e:
            return {"channel": channel, "categories": [], "error": str(e)}


    def search(self, query: str, resource_type: str = "all",
               channels: list = None, limit: int = 20) -> dict:
        """
        全网聚合搜索

        Args:
            query: 搜索关键词
            resource_type: 资源类型 (all/plugin/skill/knowledge/tool)
            channels: 指定渠道列表，None 表示全部
            limit: 每个渠道最多返回数

        Returns:
            {
                "query": str,
                "total": int,
                "channels_searched": [str],
                "results": {channel_name: [standardized_results]},
                "by_type": {resource_type: [results]},
            }
        """
        target_channels = channels or list(self._channels.keys())
        all_results = {}
        by_type = {rt: [] for rt in RESOURCE_TYPES}
        total = 0

        import concurrent.futures

        def search_channel(ch_name):
            ch = self._channels.get(ch_name)
            if not ch:
                return ch_name, []
            try:
                results = ch.search(query, resource_type, limit=limit // len(target_channels) or 5)
                return ch_name, results
            except Exception as e:
                logger.error(f"[ResourceHub] 渠道 {ch_name} 搜索异常: {e}")
                return ch_name, []

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_channels)) as executor:
            futures = {executor.submit(search_channel, ch): ch for ch in target_channels}
            for future in concurrent.futures.as_completed(futures):
                ch_name, results = future.result()
                all_results[ch_name] = results
                total += len(results)
                for item in results:
                    rtype = item.get("resource_type", "other")
                    by_type.setdefault(rtype, []).append(item)

        # 检测 HarvenHub 是否连通
        channels_searched = list(all_results.keys())
        harvenhub_available = "harvenhub" in channels_searched and len(all_results.get("harvenhub", [])) > 0

        # 按优先级排序渠道
        channels_searched_sorted = sorted(
            channels_searched,
            key=lambda k: self._channels[k].priority if k in self._channels else 99
        )

        # 按质量分排序每种类型
        for rtype in by_type:
            # HarvenHub 官方资源排在同类型最前面
            by_type[rtype].sort(key=lambda x: (
                0 if x.get("source") == "harvenhub" else 1,
                -(x.get("quality", 0))
            ))

        return {
            "query": query,
            "total": total,
            "resource_type": resource_type,
            "channels_searched": channels_searched_sorted,
            "harvenhub_available": harvenhub_available,
            "harvenhub_status": {
                "available": harvenhub_available,
                "message": "🏠 HarvenHub 官方精选结果" if harvenhub_available
                           else "🏠 HarvenHub 正在建设中，已自动搜索全网资源",
                "hint": None if harvenhub_available
                        else "您的搜索已自动扩展到 GitHub/Gitee/PyPI 等渠道",
            },
            "channels": [
                {"id": ch, "label": self._channels[ch].label, "icon": self._channels[ch].icon,
                 "count": len(all_results.get(ch, [])), "is_official": self._channels[ch].is_official,
                 "priority": self._channels[ch].priority}
                for ch in channels_searched_sorted
            ],
            "by_channel": all_results,
            "by_type": {rt: items[:limit] for rt, items in by_type.items() if items},
            "top": sorted(
                [item for items in all_results.values() for item in items],
                key=lambda x: x.get("quality", 0), reverse=True
            )[:limit],
        }

    def download_resource(self, source: str, resource_id: str,
                          download_url: str = None) -> tuple:
        """
        下载资源

        Returns:
            (success: bool, data: bytes or error_msg: str)
        """
        try:
            import urllib.request

            if source == "github":
                # GitHub ZIP 下载
                if "/" in resource_id:
                    zip_url = f"https://github.com/{resource_id}/archive/refs/heads/main.zip"
                    req = urllib.request.Request(zip_url, headers={
                        "User-Agent": "HarvenAI-ResourceHub/1.0",
                    })
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        return True, resp.read()

            elif source == "gitee":
                # Gitee ZIP 下载
                if "/" in resource_id:
                    zip_url = f"https://gitee.com/{resource_id}/repository/archive/main.zip"
                    req = urllib.request.Request(zip_url, headers={
                        "User-Agent": "HarvenAI-ResourceHub/1.0",
                    })
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        return True, resp.read()

            elif source == "pypi":
                # PyPI: 返回包信息（不直接下载，交给 pip）
                return False, "PyPI 包需通过 pip install 安装，请在 Harven 对话中说「帮我安装 {name}」"

            elif source == "huggingface":
                # HuggingFace: 返回指向
                return False, "HuggingFace 资源需通过专用接口下载，请在对话中指定模型名"

            elif source == "winget":
                return False, "Winget 软件需通过系统包管理器安装"

            # 通用 HTTP 下载
            if download_url:
                req = urllib.request.Request(download_url, headers={
                    "User-Agent": "HarvenAI-ResourceHub/1.0",
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return True, resp.read()

            return False, f"不支持的下载方式: {source}"

        except Exception as e:
            return False, str(e)

    def install_from_github(self, repo_url: str) -> dict:
        """从 GitHub 安装插件/技能（完整流程：下载→扫描→安装）"""
        try:
            from src.core.plugin_installer import (
                install_plugin_from_github, download_github_plugin,
                extract_and_validate_plugin, compute_hash,
            )
            import re
            match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)', repo_url)
            if not match:
                return {"success": False, "error": "无效的 GitHub URL"}
            owner, repo = match.group(1), match.group(2).rstrip(".git")
            full_name = f"{owner}/{repo}"

            # 下载
            success, data = download_github_plugin(full_name)
            if not success:
                return {"success": False, "error": f"下载失败: {data}"}

            # 尝试作为插件安装
            from pathlib import Path
            plugin_dir = str(PROJECT_ROOT / "plugins")
            result = install_plugin_from_github(repo_url, plugin_dir)
            if result.get("success"):
                return result

            # 如果不是插件，尝试解压并检测 Markdown 技能文件
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                md_files = [f for f in zf.namelist()
                           if f.endswith(".md") and not f.startswith("__MACOSX")]
                if md_files:
                    # 检测是否为技能文件
                    for mf in md_files:
                        content = zf.read(mf).decode("utf-8", errors="replace")
                        skill_info = parse_skill_markdown(content)
                        if skill_info:
                            # 安装为技能
                            from src.core.skills_manager import get_skills_manager
                            sm = get_skills_manager()
                            result = sm.create_skill(
                                name=skill_info["name"] or os.path.basename(mf).replace(".md", ""),
                                description=skill_info["description"] or "",
                                triggers=skill_info.get("triggers", []),
                                prompt=skill_info["prompt"],
                                author=skill_info.get("author", "GitHub"),
                                version=skill_info.get("version", "1.0"),
                            )
                            if result.get("success"):
                                return {"success": True, "type": "skill",
                                        "name": result["skill"]["name"],
                                        "message": "技能安装成功"}
                    # 如果有 .md 但不是技能格式，作为知识导入
                    return self._import_as_knowledge(data, full_name)

            return {"success": False, "error": "无法识别资源格式（既不是插件也不是技能）"}

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}

    def _import_as_knowledge(self, zip_data: bytes, source_name: str) -> dict:
        """将 ZIP 内容导入为知识"""
        try:
            from src.core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            count = 0
            with tempfile.TemporaryDirectory() as tmpdir:
                with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                    zf.extractall(tmpdir)
                for root, dirs, files in os.walk(tmpdir):
                    for fname in files:
                        if fname.endswith((".md", ".txt", ".json", ".csv")):
                            fpath = os.path.join(root, fname)
                            try:
                                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                                    text = f.read()
                                if len(text) > 50:
                                    kb.add_document(text, source=f"github:{source_name}")
                                    count += 1
                            except Exception as _e: logger.debug(f"[resource_hub] 忽略: {_e}")
            return {"success": True, "type": "knowledge", "count": count,
                    "message": f"已导入 {count} 个文档到知识库"}
        except Exception as e:
            return {"success": False, "error": f"知识导入失败: {e}"}

    def enforce_license_policy(self, license_info: dict, source: str = "") -> dict:
        """强制执行许可证策略

        检查安装前的许可证合规性：
        - GPL/AGPL → 提示传染性风险，需用户确认
        - 无LICENSE → 警告版权风险，需用户确认
        - MIT/Apache/BSD → 直接放行

        Returns:
            {"allowed": bool, "require_confirm": bool, "reason": str, "license": dict}
        """
        if not license_info or not license_info.get("category"):
            # 无许可证信息
            return {
                "allowed": False,
                "require_confirm": True,
                "reason": "⚠️ 此资源未声明许可证（LICENSE），存在版权侵权风险。\n\n"
                          "开源资源通常包含 LICENSE 文件来声明使用条款。\n"
                          "没有许可证意味着默认保留所有权利（All Rights Reserved），\n"
                          "你不能自由使用、修改或分发此资源。\n\n"
                          "🔴 强烈建议：只安装有明确开源许可证的资源。",
                "license": {"category": "danger", "label": "🔴 无许可证", "desc": "未知许可状态"},
                "block_reason": "no_license",
            }

        category = license_info["category"]

        if category == "safe":
            # MIT/Apache/BSD → 直接放行
            return {
                "allowed": True,
                "require_confirm": False,
                "reason": f"✅ 许可证合规（{license_info.get('license', '')}），可自由使用",
                "license": license_info,
                "block_reason": None,
            }

        elif category == "caution":
            # GPL/AGPL/LGPL → 强制弹窗确认
            return {
                "allowed": False,
                "require_confirm": True,
                "reason": f"⚠️ 传染性许可证: {license_info.get('license', '').upper()}\n\n"
                          "此资源使用 GPL/AGPL 类许可证，具有「传染性」条款：\n"
                          "• 如果你修改或分发此代码，你的项目也必须以相同许可证开源\n"
                          "• 商业闭源项目使用 GPL 代码可能导致合规风险\n"
                          "• AGPL 进一步要求通过网络提供服务时也必须开源\n\n"
                          "📋 建议：\n"
                          "• 如果你只是个人使用、不修改分发 → 风险较低\n"
                          "• 如果你计划用于商业产品 → 请咨询法律顾问\n"
                          "• 优先寻找 MIT/Apache 许可的替代品",
                "license": license_info,
                "block_reason": "viral_license",
            }

        elif category == "danger":
            return {
                "allowed": False,
                "require_confirm": True,
                "reason": f"🔴 高危许可证状态: 此资源无法确认合法使用权限。\n\n"
                          "可能原因：\n"
                          "• 未找到 LICENSE 文件\n"
                          "• 声明了限制性条款\n"
                          "• 来源不可信\n\n"
                          "强烈建议放弃安装，寻找替代品。",
                "license": license_info,
                "block_reason": "dangerous_license",
            }

        return {
            "allowed": True, "require_confirm": False,
            "reason": "许可证检查通过", "license": license_info, "block_reason": None,
        }

    def record_attribution(self, name: str, source: str, author: str,
                           license_info: dict, resource_type: str = "plugin") -> dict:
        """记录安装资源的版权归属信息

        写入 data/attributions.json，用于：
        - 软件物料清单 (SBOM)
        - 开源合规审查
        - 致谢页面展示
        """
        try:
            attr_file = PROJECT_ROOT / "data" / "attributions.json"
            attr_file.parent.mkdir(parents=True, exist_ok=True)

            records = []
            if attr_file.exists():
                try:
                    with open(attr_file, "r", encoding="utf-8") as f:
                        records = json.load(f)
                except (json.JSONDecodeError, Exception):
                    records = []

            record = {
                "name": name,
                "source": source,
                "author": author or "Unknown",
                "license": license_info.get("license", "unknown"),
                "license_category": license_info.get("category", "danger"),
                "resource_type": resource_type,
                "installed_at": datetime.now().isoformat(),
                "verified": license_info.get("category") == "safe",
            }
            records.append(record)

            with open(attr_file, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)

            logger.info(f"[ResourceHub] 已记录归属: {name} (License: {license_info.get('license')})")
            return {"success": True, "record": record}
        except Exception as e:
            logger.error(f"[ResourceHub] 归属记录失败: {e}")
            return {"success": False, "error": str(e)}

    def ai_generate_skill(self, description: str) -> dict:
        """AI 根据描述生成技能文件"""
        try:
            from src.core.skills_manager import get_skills_manager
            from src.core.engine import get_conversation_engine

            sm = get_skills_manager()
            engine = get_conversation_engine()

            # 用 Harven 自己的 LLM 生成技能内容
            prompt = f"""你是一个AI技能设计师。根据以下用户描述，生成一个Harven AI技能配置。

用户需求: {description}

请生成：
1. 技能名称（中文，简短）
2. 技能描述（一句话）
3. 触发关键词（5-10个，逗号分隔）
4. 系统提示词（告诉AI如何响应用户的请求，200-500字）

请用以下 YAML 格式输出：
---
name: 【技能名称】
description: 【技能描述】
triggers: [【触发词1】, 【触发词2】, ...]
---
【系统提示词】
"""
            skill_content = engine.generate_sync(prompt)
            if not skill_content:
                return {"success": False, "error": "AI 生成失败"}

            # 解析 YAML 内容
            skill_info = parse_skill_markdown(skill_content)
            if not skill_info:
                # 回退：用简单方式创建
                skill_info = {
                    "name": f"AI生成-{description[:15]}",
                    "description": description,
                    "triggers": description.split(),
                    "prompt": skill_content,
                }

            result = sm.create_skill(
                name=skill_info["name"],
                description=skill_info.get("description", description),
                triggers=skill_info.get("triggers", []),
                prompt=skill_info.get("prompt", skill_content),
                author="Harven AI",
                version="1.0",
            )
            if result.get("success"):
                result["ai_generated"] = True
            return result
        except Exception as e:
            return {"success": False, "error": f"生成失败: {e}"}


# ====================================================================
# 全局单例
# ====================================================================

_hub: ResourceHub | None = None
_hub_lock = threading.Lock()


def get_resource_hub() -> ResourceHub:
    global _hub
    if _hub is None:
        with _hub_lock:
            if _hub is None:
                _hub = ResourceHub()
    return _hub
