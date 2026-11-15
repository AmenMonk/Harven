"""槽位1: 检测引擎 — YOLOX (Apache 2.0, Megvii 0.1.1rc0 官方 ONNX)。

双档（models/MANIFEST.txt）:
  nano  416x416  输出 [1,3549,85]  边缘/AR/手机档
  s     640x640  输出 [1,8400,85]  桌面基线档

⚠️ 0.1.1rc0 版 ONNX 导出**不含图内解码**，输出为裸回归值，
   必须按官方 demo 复刻后处理（对齐 0.1.1rc0 onnx_inference.py）:
   前处理: 左上对齐 letterbox(114灰) → BGR→RGB(此处 PIL 直读 RGB) → 归一化(按档分流)
   解码:   xy = (raw + grid) * stride ; wh = exp(raw) * stride   [cxcywh, 输入尺度]
   还原:   cxcywh → xyxy → /r → 原图坐标
   （以上经白圆受控实验反推验证: cx=(-0.03+10)*32≈319 ✓, e^2.52*32≈200≈真值192 ✓）

⚠️ 归一化按档分流（2026-10-26 M2-1 实测实锤）:
   官方 release 的 s 与 nano/tiny 两个 ONNX 输入约定不一致——
   s    → /255 + ImageNet mean/std（常规）
   nano → 原值 0~255（不做任何归一化；喂 s 同款预处理 obj_max 仅 0.007 全灭，
          原值直喂 obj_max 0.994 复活）
   tiny 同病（未随包发布，仅留档）。根因: YOLOX 新旧版本预处理变更，
   release 混装了两代导出。
"""
from __future__ import annotations

import numpy as np
import onnxruntime as ort
from PIL import Image

from .. import config
from .base import BaseEngine, EngineResult

# COCO 80 类（YOLOX 官方顺序）
COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

INPUT_SIZE = {"nano": (416, 416), "s": (640, 640)}
NORMALIZE = {"s": True, "nano": False}     # 输入归一化按档分流（见文件头说明）
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)


class YoloxEngine(BaseEngine):
    name = "yolox"
    slot = "detect"

    def __init__(self, profile: str | None = None):
        self.profile = profile or config.ENGINES["detect"]["profile"]
        self.session: ort.InferenceSession | None = None
        self.input_size = INPUT_SIZE[self.profile]
        self._grids: np.ndarray | None = None      # 解码网格缓存
        self._strides: np.ndarray | None = None

    def load(self) -> None:
        if self.loaded:
            return
        path = config.ENGINES["detect"][self.profile]
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self._build_grids()
        self.loaded = True

    # ── 解码网格（按档位只建一次）────────────────────────────────
    def _build_grids(self) -> None:
        h, w = self.input_size
        grids, strides = [], []
        for stride in (8, 16, 32):
            gv, ghs = np.meshgrid(np.arange(w // stride), np.arange(h // stride))
            grid = np.stack((gv, ghs), 2).reshape(-1, 2)
            grids.append(grid)
            strides.append(np.full((grid.shape[0], 1), stride, dtype=np.float32))
        self._grids = np.concatenate(grids, 0).astype(np.float32)          # [N,2]
        self._strides = np.concatenate(strides, 0)                          # [N,1]

    # ── 前处理: 0.1.1rc0 官方 preproc（左上对齐，不居中；归一化按档分流）──
    def _preprocess(self, img: Image.Image) -> tuple[np.ndarray, float]:
        w, h = self.input_size
        iw, ih = img.size
        r = min(h / ih, w / iw)
        nw, nh = int(iw * r), int(ih * r)
        padded = np.full((h, w, 3), 114.0, dtype=np.float32)
        resized = img.resize((nw, nh), Image.BILINEAR)
        padded[:nh, :nw] = np.asarray(resized, dtype=np.float32)
        blob = padded.transpose(2, 0, 1)          # CHW, RGB
        if NORMALIZE[self.profile]:               # s 档; nano 原值 0~255 直喂
            blob = blob / 255.0
            blob = (blob - MEAN) / STD
        return blob[None].astype(np.float32), r

    # ── NMS（纯 numpy, 类内）────────────────────────────────────
    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float = 0.45) -> list[int]:
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size > 0:
            i = order[0]
            keep.append(int(i))
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
            order = order[1:][iou <= iou_thr]
        return keep

    def infer(self, image_path: str, conf_thr: float = 0.25, **kwargs) -> EngineResult:
        if not self.loaded:
            self.load()
        assert self.session is not None
        t0 = self._now_ms()

        img = Image.open(image_path).convert("RGB")
        orig_w, orig_h = img.size
        blob, r = self._preprocess(img)

        raw = self.session.run(None, {"images": blob})[0][0]    # [N, 85]

        # ── 官方 grid 解码: xy=(raw+grid)*stride, wh=exp(raw)*stride ──
        boxes = np.empty_like(raw[:, :4])
        boxes[:, 0] = (raw[:, 0] + self._grids[:, 0]) * self._strides[:, 0]
        boxes[:, 1] = (raw[:, 1] + self._grids[:, 1]) * self._strides[:, 0]
        boxes[:, 2] = np.exp(np.clip(raw[:, 2], -10, 10)) * self._strides[:, 0]
        boxes[:, 3] = np.exp(np.clip(raw[:, 3], -10, 10)) * self._strides[:, 0]
        # cxcywh → xyxy（输入尺度）
        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2

        # 阈值过滤: obj * cls 联合置信度
        scores_all = raw[:, 4:5] * raw[:, 5:]
        cls_ids = scores_all.argmax(axis=1)
        confs = scores_all.max(axis=1)
        mask = confs >= conf_thr
        sel_xyxy, confs, cls_ids = xyxy[mask], confs[mask], cls_ids[mask]

        detections = []
        if sel_xyxy.shape[0] > 0:
            # /r 还原到原图
            sel_xyxy = sel_xyxy / r
            sel_xyxy[:, [0, 2]] = sel_xyxy[:, [0, 2]].clip(0, orig_w)
            sel_xyxy[:, [1, 3]] = sel_xyxy[:, [1, 3]].clip(0, orig_h)

            for cid in np.unique(cls_ids):
                m = cls_ids == cid
                keep = self._nms(sel_xyxy[m], confs[m])
                sub = np.where(m)[0][keep]
                for idx in sub:
                    detections.append({
                        "label": COCO_CLASSES[int(cls_ids[idx])],
                        "conf": round(float(confs[idx]), 4),
                        "bbox": [round(float(v), 1) for v in sel_xyxy[idx]],
                    })
            detections.sort(key=lambda d: -d["conf"])

        elapsed = self._now_ms() - t0
        top = detections[0]["conf"] if detections else 0.0
        return EngineResult(
            source_layer="L1",
            confidence=top,
            elapsed_ms=elapsed,
            data={
                "profile": self.profile,
                "num_detections": len(detections),
                "detections": detections[:30],   # 防 LLM 上下文污染，最多 30 个
            },
        )
