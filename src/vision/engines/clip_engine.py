"""槽位2: 索引引擎 — Chinese-CLIP (图文检索/以文搜图)。

本地 transformers 加载 models/chinese-clip/（完整 ChineseCLIPModel 图文双塔）。
输出归一化投影向量；维度以模型实际 projection_dim 为准（vit-base = 512）。
同一向量空间: 图→vec / 文→vec，余弦相似即图文相关度。
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .. import config
from .base import BaseEngine, EngineResult


class ClipEngine(BaseEngine):
    name = "chinese-clip"
    slot = "index"
    dim: int = 0                       # 加载后填充实际维度

    def load(self) -> None:
        if self.loaded:
            return
        import torch
        from transformers import AutoProcessor, ChineseCLIPModel

        self._torch = torch
        model_dir = config.ENGINES["index"]["model_dir"]
        torch.set_num_threads(max(1, torch.get_num_threads() // 2))  # 不吃满核
        self.model = ChineseCLIPModel.from_pretrained(model_dir)
        self.model.eval()
        self.processor = AutoProcessor.from_pretrained(model_dir)
        self.dim = self.model.config.projection_dim
        self.loaded = True

    @staticmethod
    def _norm(v: np.ndarray) -> np.ndarray:
        return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)

    def infer(self, image_path: str, **kwargs) -> EngineResult:
        """图片 → 归一化向量（L2 入库/检索主通道）。"""
        if not self.loaded:
            self.load()
        import time
        t0 = time.perf_counter() * 1000

        img = Image.open(image_path).convert("RGB")
        inputs = self.processor(images=img, return_tensors="pt")
        with self._torch.no_grad():
            feat = self.model.get_image_features(**inputs)
            # transformers 5.x 返回输出对象，投影向量在 pooler_output
            if not self._torch.is_tensor(feat):
                feat = feat.pooler_output
        vec = self._norm(feat.float().numpy()[0])

        elapsed = time.perf_counter() * 1000 - t0
        return EngineResult(
            source_layer="L2",
            confidence=1.0,            # 索引层无置信概念，恒 1（门控在 pipeline）
            elapsed_ms=elapsed,
            data={"vector": vec.tolist(), "dim": int(self.dim)},
        )

    def text_to_vec(self, text: str) -> np.ndarray:
        """文本 → 归一化向量（以文搜图同空间）。"""
        if not self.loaded:
            self.load()
        inputs = self.processor(text=[text], return_tensors="pt",
                                padding=True, truncation=True)
        with self._torch.no_grad():
            feat = self.model.get_text_features(**inputs)
            if not self._torch.is_tensor(feat):
                feat = feat.pooler_output
        return self._norm(feat.float().numpy()[0])

    def similarity(self, vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """两向量余弦相似度（已归一化则纯点积）。"""
        return float(np.dot(vec_a, vec_b))
