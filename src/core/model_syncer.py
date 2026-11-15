"""
Harven AI — ModelScope 模型同步器
定期从 ModelScope API 拉取最新 GGUF 模型，自动更新 model_catalog.json

用法：
  手动: python -m src.core.model_syncer
  自动: 由 scheduler 每日调用
"""

import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).parent.parent.parent / "data" / "model_catalog.json"
MODELSCOPE_API = "https://modelscope.cn/api/v1/models"

# ── 搜索关键词（定期扫描的热门模型） ──
SEARCH_QUERIES = [
    ("GGUF", "llm"),
    ("qwen", "llm"),
    ("deepseek", "llm"),
    ("llama", "llm"),
    ("chatglm", "llm"),
    ("baichuan", "llm"),
    ("stable-diffusion", "image"),
    ("sd", "image"),
    ("flux", "image"),
    ("kolors", "image"),
    ("cogvideo", "video"),
    ("animatediff", "video"),
    ("musicgen", "music"),
    ("bge", "embedding"),
    ("nomic", "embedding"),
]

# ── ModelScope 已知模型 ID → 监控这些仓库的新版本 ──
WATCHED_REPOS = [
    "Qwen/Qwen2.5-14B-Instruct-GGUF",
    "Qwen/Qwen2.5-7B-Instruct-GGUF",
    "Qwen/Qwen2.5-3B-Instruct-GGUF",
    "Qwen/Qwen2.5-32B-Instruct-GGUF",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B-GGUF",
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B-GGUF",
    "unsloth/Qwen3.5-9B-GGUF",
    "unsloth/Qwen3.5-4B-GGUF",
    "Qwen/Qwen3-14B-GGUF",
    "unsloth/gemma-4-12b-it-QAT-GGUF",
    "AI-ModelScope/stable-diffusion-xl-base-1.0",
    "Kwai-Kolors/Kolors",
    "Tencent-Hunyuan/HunyuanDiT",
    "ZhipuAI/CogVideoX-5b",
    "BAAI/bge-m3",
    "BAAI/bge-large-zh-v1.5",
]


def fetch_modelscope_search(query: str, category: str, page: int = 1, page_size: int = 20) -> List[Dict]:
    """从 ModelScope 搜索模型"""
    try:
        resp = requests.get(
            MODELSCOPE_API,
            params={
                "search": query,
                "pageSize": page_size,
                "pageNumber": page,
            },
            headers={"User-Agent": "HarvenAI/2.0 ModelSyncer"},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"[Syncer] ModelScope API 返回 {resp.status_code}: {query}")
            return []

        data = resp.json()
        results = []
        for item in data.get("Data", {}).get("Content", []) if isinstance(data.get("Data"), dict) else data.get("Data", []):
            results.append({
                "id": item.get("Name", ""),
                "name": item.get("ChineseName") or item.get("Name", ""),
                "description": item.get("Description", "")[:200],
                "category": category,
                "downloads": item.get("Downloads", 0),
                "updated": item.get("GmtModified", ""),
                "tags": item.get("Tags", []),
            })
        return results
    except Exception as e:
        logger.error(f"[Syncer] 搜索失败 ({query}): {e}")
        return []


def sync_catalog(dry_run: bool = False) -> Dict:
    """
    同步模型目录
    
    Returns:
        {"added": [...], "updated": [...], "total_models": N}
    """
    logger.info("[Syncer] 🚀 开始同步模型目录...")

    # 加载现有目录
    catalog = {"_meta": {"version": "1.0.0", "updated": "", "total_models": 0}, "models": []}
    if CATALOG_PATH.exists():
        try:
            catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception as _e: logger.debug(f"[model_syncer] 忽略: {_e}")

    existing_ids = {m["id"] for m in catalog.get("models", [])}
    added = []
    updated = []

    # 1. 从搜索关键词查找新模型
    for query, category in SEARCH_QUERIES:
        time.sleep(0.5)  # 请求间隔
        results = fetch_modelscope_search(query, category, page_size=10)
        for r in results:
            model_id = r["id"]
            if model_id in existing_ids:
                # 更新已有模型的信息
                for m in catalog["models"]:
                    if m["id"] == model_id:
                        if r.get("description"):
                            m["description"] = r["description"]
                        if r.get("tags"):
                            existing_tags = set(m.get("tags", []))
                            for t in r.get("tags", []):
                                existing_tags.add(t)
                            m["tags"] = list(existing_tags)[:10]
                        updated.append(model_id)
                        break
            else:
                # 新模型 — 先加入到"待确认"区
                new_model = {
                    "id": model_id,
                    "name": r["name"][:50],
                    "category": category,
                    "subcategory": "",
                    "size_gb": 0,
                    "vram_gb": 0,
                    "ram_gb": 0,
                    "description": r.get("description", ""),
                    "language": ["中文", "英文"],
                    "tags": r.get("tags", [])[:5] + ["待验证"],
                    "priority": 9,
                    "quality": "待确认",
                    "download": {"modelscope": model_id, "huggingface": "", "filename": ""},
                }
                catalog["models"].append(new_model)
                existing_ids.add(model_id)
                added.append(model_id)
                logger.info(f"[Syncer] ➕ 新增: {model_id}")

    # 2. 更新元数据
    catalog["_meta"]["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    catalog["_meta"]["total_models"] = len(catalog["models"])

    # 3. 保存
    if not dry_run:
        CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 备份旧文件
        backup = CATALOG_PATH.with_suffix(".json.bak")
        if CATALOG_PATH.exists():
            backup.write_bytes(CATALOG_PATH.read_bytes())
        CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[Syncer] ✅ 同步完成: +{len(added)} 新增, {len(updated)} 更新, 共 {len(catalog['models'])} 模型")

    return {
        "added": added,
        "updated": updated,
        "total_models": len(catalog["models"]),
        "updated_at": catalog["_meta"]["updated"],
    }


def get_latest_versions() -> List[Dict]:
    """获取关注仓库的最新版本信息"""
    results = []
    for repo in WATCHED_REPOS:
        try:
            resp = requests.get(
                f"{MODELSCOPE_API}/{repo}/revisions",
                headers={"User-Agent": "HarvenAI/2.0"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                revisions = data.get("Data", []) if isinstance(data.get("Data"), list) else []
                if revisions:
                    results.append({
                        "repo": repo,
                        "latest_version": revisions[0].get("Revision", ""),
                        "updated": revisions[0].get("GmtModified", ""),
                    })
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"[Syncer] 版本检查失败 ({repo}): {e}")
    return results


# ── CLI 入口 ──
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    result = sync_catalog(dry_run=dry)
    print(f"\n{'[预览] ' if dry else ''}同步完成:")
    print(f"  新增: {len(result['added'])} 个模型")
    print(f"  更新: {len(result['updated'])} 个模型")
    print(f"  总计: {result['total_models']} 个模型")
    if result["added"]:
        print(f"\n  新增列表:")
        for aid in result["added"]:
            print(f"    · {aid}")
