"""L0 感知哈希查重 — dhash 9x8 → 64bit 汉明距离。

性能预算: P50 <2ms / P95 <5ms（第十三章 13.3）。
纯 Pillow + numpy 实现，零模型依赖，任何图片进来第一站。
"""
from __future__ import annotations

from PIL import Image
import numpy as np


def dhash(image_path: str, hash_size: int = 8) -> np.ndarray:
    """计算 dhash：缩到 9x8 灰度，横向相邻像素比较 → 64bit。"""
    img = Image.open(image_path).convert("L").resize(
        (hash_size + 1, hash_size), Image.LANCZOS
    )
    arr = np.asarray(img, dtype=np.int16)
    diff = arr[:, 1:] > arr[:, :-1]          # 8x8 bool
    return diff.flatten()


def hash_to_hex(h: np.ndarray) -> str:
    """64bit 哈希 → 16 位 hex 字符串（入库主键格式）。"""
    bits = int("".join("1" if b else "0" for b in h), 2)
    return f"{bits:016x}"


def hamming(h1: np.ndarray, h2: np.ndarray) -> int:
    """汉明距离：不同 bit 数。0=完全相同，<=10 视为近重复。"""
    return int(np.count_nonzero(h1 != h2))


def is_near_duplicate(h1: np.ndarray, h2: np.ndarray, threshold: int = 10) -> bool:
    return hamming(h1, h2) <= threshold
