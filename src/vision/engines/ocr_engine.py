# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""能力引擎①: OCR 拍照识字 — RapidOCR (PaddleOCR 模型的 ONNX 移植, Apache-2.0)。

用途：账单小票 / 快递单 / 说明书 / 门牌 —— 本地毫秒级抽文字，
喂给记账/管家链路，不劳烦视觉大模型（天条：数据提取→本地毫秒级）。
模型随 rapidocr_onnxruntime 包内置（det+cls+rec 约 15MB），零额外下载。
依赖纪律：pip 必须 --no-deps 安装（它声明依赖 opencv-python，
会顶掉哈文在用的 opencv-contrib-python —— 2026-11-12 实锤坑）。
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_ocr = None
_failed = False


def available() -> bool:
    try:
        import importlib.util as _u
        return bool(_u.find_spec("rapidocr_onnxruntime"))
    except Exception:
        return False


def _get():
    global _ocr, _failed
    if _ocr is not None:
        return _ocr
    with _lock:
        if _ocr is not None or _failed:
            return _ocr
        try:
            from rapidocr_onnxruntime import RapidOCR
            t0 = time.time()
            _ocr = RapidOCR()
            logger.info(f"[OCR] RapidOCR 就位 ({time.time()-t0:.1f}s)")
        except Exception as e:
            _failed = True
            logger.warning(f"[OCR] 初始化失败: {e}")
    return _ocr


def infer(image_path: str) -> dict:
    """单图抽文字。返回 {ok, lines:[{text,score}], text, elapsed_ms}。"""
    ocr = _get()
    if ocr is None:
        return {"ok": False, "error": "OCR 引擎不可用（rapidocr 未安装）"}
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        return {"ok": False, "error": "图片读不出来"}
    t0 = time.perf_counter() * 1000
    result, _ = ocr(img)
    elapsed = time.perf_counter() * 1000 - t0
    lines = [{"text": r[1], "score": round(float(r[2]), 3)} for r in (result or [])]
    return {"ok": True, "lines": lines,
            "text": "\n".join(l["text"] for l in lines),
            "elapsed_ms": round(elapsed)}
