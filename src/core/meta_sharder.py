# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
元数据分片管理 — 防止 knowledge_meta.json 无限膨胀

当 knowledge_meta.json 超过阈值（默认 1MB）时，自动拆分为分片文件：
- knowledge_meta.json → knowledge_meta_000.json, knowledge_meta_001.json, ...

优势：
- 单个文件不超过阈值，加载快
- 写入只影响最后一个分片，减少 IO
- 向后兼容：无分片时回退到原文件
"""

import os
import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import logging
logger = logging.getLogger(__name__)

# 分片配置
MAX_SHARD_SIZE_MB = 1  # 每个分片最大 1MB
MAX_SHARD_ENTRIES = 500  # 每个分片最多 500 条记录
SHARD_PATTERN = "knowledge_meta_{:03d}.json"
LEGACY_FILE = "knowledge_meta.json"


class MetaSharder:
    """
    知识库元数据分片管理器

    文件结构：
    data/faiss/
    ├── knowledge_meta.json        # 向后兼容：无分片时的单文件
    ├── knowledge_meta_000.json    # 分片 0
    ├── knowledge_meta_001.json    # 分片 1
    └── knowledge_meta_shard_info.json  # 分片元信息
    """

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data", "faiss"
            )
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._info_path = self._base_dir / "knowledge_meta_shard_info.json"

    def _get_shard_path(self, index: int) -> Path:
        return self._base_dir / SHARD_PATTERN.format(index)

    def _get_legacy_path(self) -> Path:
        return self._base_dir / LEGACY_FILE

    def load_all(self) -> List[dict]:
        """加载所有元数据条目（合并所有分片）"""
        # 1. 优先从分片加载
        shards = self._list_shards()
        if shards:
            entries = []
            for _, path in sorted(shards):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        entries.extend(data)
                    elif isinstance(data, dict):
                        # 兼容字典格式（key → entry）
                        entries.extend(data.values())
                except (json.JSONDecodeError, OSError) as e:
                    logger.error(f"[MetaSharder] 读取分片失败 {path}: {e}")
            return entries

        # 2. 回退到传统单文件
        legacy_path = self._get_legacy_path()
        if legacy_path.exists():
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return list(data.values())
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"[MetaSharder] 读取传统文件失败: {e}")

        return []

    def save_all(self, entries: List[dict]):
        """
        保存所有条目，自动分片。

        策略：超过阈值时触发分片，否则写入单文件。
        """
        # 估算大小
        estimated_size = len(json.dumps(entries, ensure_ascii=False))
        mb = estimated_size / (1024 * 1024)

        if mb > MAX_SHARD_SIZE_MB or len(entries) > MAX_SHARD_ENTRIES:
            self._save_sharded(entries)
        else:
            self._save_legacy(entries)

    def _save_legacy(self, entries: List[dict]):
        """保存为传统单文件"""
        legacy_path = self._get_legacy_path()
        tmp_path = legacy_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False)
            tmp_path.replace(legacy_path)
        except OSError as e:
            logger.error(f"[MetaSharder] 保存传统文件失败: {e}")
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def _save_sharded(self, entries: List[dict]):
        """保存为分片文件"""
        # 清除旧分片
        for idx, path in self._list_shards():
            try:
                path.unlink(missing_ok=True)
            except OSError as _e:
                logger.debug(f"[meta_sharder] 忽略: {_e}")

        # 写入新分片
        shard_info = {"shard_count": 0, "total_entries": len(entries), "shards": []}
        shard_index = 0
        current_shard = []
        current_size = 0

        for entry in entries:
            entry_json = json.dumps(entry, ensure_ascii=False)
            entry_size = len(entry_json)

            # 分片满了？
            should_split = (
                len(current_shard) >= MAX_SHARD_ENTRIES or
                (current_size + entry_size) > MAX_SHARD_SIZE_MB * 1024 * 1024
            )

            if should_split and current_shard:
                self._write_shard(shard_index, current_shard)
                shard_info["shards"].append({
                    "index": shard_index,
                    "count": len(current_shard),
                    "path": str(self._get_shard_path(shard_index).name),
                })
                shard_index += 1
                current_shard = []
                current_size = 0

            current_shard.append(entry)
            current_size += entry_size

        # 最后一个分片
        if current_shard:
            self._write_shard(shard_index, current_shard)
            shard_info["shards"].append({
                "index": shard_index,
                "count": len(current_shard),
                "path": str(self._get_shard_path(shard_index).name),
            })

        shard_info["shard_count"] = shard_index + 1
        self._write_shard_info(shard_info)

        # 删除旧的传统文件（已迁移到分片）
        legacy = self._get_legacy_path()
        if legacy.exists():
            # 保留备份
            backup = legacy.with_suffix(".json.bak")
            try:
                shutil.copy2(legacy, backup)
                legacy.unlink()
            except OSError as _e:
                logger.debug(f"[meta_sharder] 忽略: {_e}")

        logger.debug(f"[MetaSharder] 已分片: {len(entries)} 条 → {shard_info['shard_count']} 个分片")

    def _write_shard(self, index: int, entries: List[dict]):
        path = self._get_shard_path(index)
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False)
            tmp.replace(path)
        except OSError as e:
            logger.error(f"[MetaSharder] 写分片失败: {e}")
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _write_shard_info(self, info: dict):
        path = self._info_path
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
        except OSError as e:
            logger.error(f"[MetaSharder] 写分片信息失败: {e}")
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def _list_shards(self) -> List[Tuple[int, Path]]:
        """列出所有分片文件"""
        shards = []
        for f in self._base_dir.glob("knowledge_meta_*.json"):
            if f.name == "knowledge_meta_shard_info.json":
                continue
            try:
                index = int(f.stem.rsplit("_", 1)[-1])
                shards.append((index, f))
            except (ValueError, IndexError):
                pass
        return shards

    def get_shard_info(self) -> Optional[dict]:
        """获取分片元信息"""
        if self._info_path.exists():
            try:
                with open(self._info_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        # 计算传统文件信息
        legacy = self._get_legacy_path()
        if legacy.exists():
            try:
                with open(legacy, "r", encoding="utf-8") as f:
                    data = json.load(f)
                count = len(data) if isinstance(data, (list, dict)) else 0
                size_mb = legacy.stat().st_size / (1024 * 1024)
                return {
                    "shard_count": 0,
                    "total_entries": count,
                    "file_size_mb": round(size_mb, 2),
                    "format": "legacy",
                }
            except (json.JSONDecodeError, OSError):
                pass

        return None

    def auto_migrate_if_needed(self) -> bool:
        """
        自动迁移：如果传统文件超过阈值，自动分片。

        Returns: True 表示执行了迁移
        """
        legacy = self._get_legacy_path()
        if not legacy.exists():
            return False

        size_mb = legacy.stat().st_size / (1024 * 1024)
        if size_mb < MAX_SHARD_SIZE_MB:
            return False

        # 超过阈值，执行迁移
        logger.debug(f"[MetaSharder] knowledge_meta.json ({size_mb:.1f}MB) 超过阈值，开始自动分片...")
        entries = self.load_all()
        if entries:
            self._save_sharded(entries)
            return True
        return False


# ── 自动迁移（启动时调用） ──

def auto_shard_meta_on_startup():
    """启动时自动检查并执行元数据分片"""
    try:
        sharder = MetaSharder()
        if sharder.auto_migrate_if_needed():
            info = sharder.get_shard_info()
            if info:
                logger.info("[MetaSharder] 分片完成: %s 条 → %s 个分片",
                            info.get('total_entries', 0), info.get('shard_count', 0))
    except Exception as e:
        logger.error(f"[MetaSharder] 自动分片失败: {e}")
