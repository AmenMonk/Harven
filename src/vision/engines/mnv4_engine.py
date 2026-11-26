r"""槽位引擎: 快速分类 — MobileNetV4 Conv Small（timm，Apache 2.0）。

角色：视觉流水线「反射弧」档——~1ms 级粗分类，出 Top-K ImageNet 类别。
与 L3 理解环（VLM）互补：分类环出候选标签喂给理解环做候选注入（方案B），
或简单图直接短路不惊动 VLM（方案A 路由）。

模型文件：H:\models\mobilenetv4_conv_small.e2400_r224_in1k\model.safetensors
  3.77M 参数，224x224 输入，ImageNet-1k 1000 类。
类名表：同目录 id2label.json（首次由 timm 内置 ImageNetInfo 生成，缺失时自动重建）。

依赖：timm>=1.0（torch/torchvision 哈文 venv 已内置）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .base import BaseEngine, EngineResult

MODEL_DIR = Path(r"H:\models\mobilenetv4_conv_small.e2400_r224_in1k")
WEIGHTS = MODEL_DIR / "model.safetensors"
LABELS = MODEL_DIR / "id2label.json"
MODEL_NAME = "mobilenetv4_conv_small.e2400_r224_in1k"


def _ensure_labels() -> dict:
    """类名表存在则读，缺失则从 timm 内置 ImageNetInfo 现场重建（装机免下载）。"""
    if LABELS.exists():
        try:
            return json.loads(LABELS.read_text(encoding="utf-8"))
        except Exception:
            pass
    from timm.data import ImageNetInfo
    info = ImageNetInfo()
    desc = {str(i): info.index_to_description(i) for i in range(info.num_classes())}
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        LABELS.write_text(json.dumps(desc, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return desc


class Mnv4Engine(BaseEngine):
    """MobileNetV4 快速分类引擎。load 一次、infer 多次；进程内单例。"""

    name = "mnv4"
    slot = "index"

    def __init__(self):
        self.model = None
        self.transform = None
        self.labels: dict = {}
        self._lock = threading.Lock()
        self.loaded = False

    def load(self) -> None:
        """加载 timm 模型（幂等）。GPU 可用则上 GPU，否则 CPU（15MB 模型 CPU 也毫秒级）。"""
        if self.loaded:
            return
        with self._lock:
            if self.loaded:
                return
            if not WEIGHTS.exists():
                raise FileNotFoundError(
                    f"MobileNetV4 权重缺失: {WEIGHTS}（H:\\models 未安装）")
            import torch
            import timm
            self.model = timm.create_model(
                MODEL_NAME, pretrained=False, checkpoint_path=str(WEIGHTS))
            self.model.eval()
            self._torch = torch
            if torch.cuda.is_available():
                self.model = self.model.cuda()
            cfg = timm.data.resolve_data_config({}, model=self.model)
            self.transform = timm.data.create_transform(**cfg)
            self.labels = _ensure_labels()
            self.loaded = True

    def infer(self, image_path: str, topk: int = 5, **kwargs) -> EngineResult:
        """单图分类。返回 Top-K 类别（含人话类名）。"""
        t0 = time.perf_counter()
        if not self.loaded:
            self.load()
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        x = self.transform(img).unsqueeze(0)
        torch = self._torch
        if getattr(self.model, "training", False) is False and next(
                self.model.parameters()).is_cuda:
            x = x.cuda()
        with torch.no_grad():
            out = self.model(x)
        k = max(1, min(int(topk), 10))
        probs = out.softmax(dim=1)[0]
        top = probs.topk(k)
        candidates = []
        for idx, p in zip(top.indices.tolist(), top.values.tolist()):
            candidates.append({
                "class_id": idx,
                "label": self.labels.get(str(idx), str(idx)),
                "prob": round(float(p), 4),
            })
        return EngineResult(
            source_layer="L1c",
            confidence=float(candidates[0]["prob"]) if candidates else 0.0,
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            data={"topk": candidates,
                  "top1": candidates[0]["label"] if candidates else ""},
        )

    def unload(self) -> None:
        self.model = None
        self.loaded = False
