# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""能力引擎④: 活体检测 — MiniFASNetV2 + V1SE 双模型融合 (Apache-2.0)。

用途：防拿打印照片/屏幕翻拍骗过人脸识别。上游 minivision-ai/Silent-Face-Anti-Spoofing，
ONNX 版来自 yakhyo/face-anti-spoofing（release 权重，SHA256 已固化入 installer）。
预处理严格对齐 yakhyo onnx_inference.py（2026-11-12 踩坑实锤：
  - 像素【不除 255】，float32 原值直入
  - 输出类别 idx==1 才是真脸（[fake, real, fake] 序）
  - 裁剪：宽高各自 ×scale（scale 被图像边界封顶），中心对齐
活体分 = 双模型 p_real 均值。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from .. import config

logger = logging.getLogger(__name__)

MODELS = {
    "v2":   {"rel": "alive/MiniFASNetV2.onnx",   "scale": 2.7},
    "v1se": {"rel": "alive/MiniFASNetV1SE.onnx", "scale": 4.0},
}

_lock = threading.Lock()
_sess: dict = {}


def models_ready() -> bool:
    return all((Path(config.MODELS_DIR) / m["rel"]).exists() for m in MODELS.values())


def _session(key: str):
    if key in _sess:
        return _sess[key]
    import onnxruntime as ort
    p = Path(config.MODELS_DIR) / MODELS[key]["rel"]
    if not p.exists():
        return None
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1          # 0.43M 小模型，单线程足够
    s = ort.InferenceSession(str(p), sess_options=so,
                             providers=["CPUExecutionProvider"])
    _sess[key] = s
    return s


def _crop(img: np.ndarray, box, scale: float) -> np.ndarray:
    """yakhyo _crop_face 口径：宽高各自乘 scale、图像边界封顶、中心对齐。"""
    import cv2
    src_h, src_w = img.shape[:2]
    x, y, bw, bh = [float(v) for v in box]
    scale = min((src_h - 1) / bh, (src_w - 1) / bw, scale)
    new_w, new_h = bw * scale, bh * scale
    cx, cy = x + bw / 2, y + bh / 2
    x1 = max(0, int(cx - new_w / 2)); y1 = max(0, int(cy - new_h / 2))
    x2 = min(src_w - 1, int(cx + new_w / 2)); y2 = min(src_h - 1, int(cy + new_h / 2))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return cv2.resize(img[y1:y2 + 1, x1:x2 + 1], (80, 80))


def _predict_real(sess, crop: np.ndarray) -> float:
    """返回真脸概率。像素原值 float32（不除 255），softmax 后 idx==1 为 real。"""
    x = crop.astype(np.float32)
    x = x.transpose(2, 0, 1)[None]                      # HWC BGR → NCHW
    logits = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
    e = np.exp(logits - logits.max())
    p = e / e.sum()
    return float(p[1]) if len(p) > 1 else float(p[0])


def infer(image_path: str, face_box: list | None = None) -> dict:
    """face_box: [x,y,w,h]（face_engine 已检则传入免二次检测）。"""
    import cv2
    from .face_engine import FaceEngine

    img = cv2.imread(image_path)
    if img is None:
        return {"ok": False, "error": "图片读不出来"}
    if not models_ready():
        return {"ok": False, "error": "活体模型未就位（先在视觉引擎页下载）"}

    box = face_box
    if box is None:
        eng = FaceEngine()
        if not eng.loaded:
            eng.load()
        h, w = img.shape[:2]
        eng.detector.setInputSize((w, h))
        _, faces = eng.detector.detect(img)
        if faces is None or len(faces) == 0:
            return {"ok": False, "error": "这张图里没找到人脸"}
        areas = faces[:, 2] * faces[:, 3]
        box = faces[int(np.argmax(areas))][:4]

    probs = []
    for key, m in MODELS.items():
        s = _session(key)
        if s is None:
            continue
        crop = _crop(img, box, m["scale"])
        if crop is None:
            return {"ok": False, "error": "人脸太小，没法判活"}
        probs.append(_predict_real(s, crop))
    if not probs:
        return {"ok": False, "error": "模型加载失败"}

    live = float(np.mean(probs))
    return {"ok": True, "live_score": round(live, 3),
            "label": "真脸" if live >= 0.5 else "疑似翻拍/打印",
            "is_live": live >= 0.5}
