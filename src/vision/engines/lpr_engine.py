# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""能力引擎⑤: 中国车牌识别 — HyperLPR3 (Apache-2.0, 模型随包)。

用途：拍车自动抽车牌号（车辆面板联动）。首次调用会从 GitHub 下载
~12MB 模型包到 ~/.hyperlpr3/（已预下载则直接用）。
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_catcher = None
_failed = False


def available() -> bool:
    try:
        import importlib.util as _u
        return bool(_u.find_spec("hyperlpr3"))
    except Exception:
        return False


def _get():
    global _catcher, _failed
    if _catcher is not None:
        return _catcher
    with _lock:
        if _catcher is not None or _failed:
            return _catcher
        try:
            from hyperlpr3 import LicensePlateCatcher
            _catcher = LicensePlateCatcher()
            logger.info("[LPR] HyperLPR3 就位")
        except Exception as e:
            _failed = True
            logger.warning(f"[LPR] 初始化失败: {e}")
    return _catcher


def infer(image_path: str) -> dict:
    catcher = _get()
    if catcher is None:
        return {"ok": False, "error": "车牌引擎不可用（hyperlpr3 未安装/模型未就位）"}
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        return {"ok": False, "error": "图片读不出来"}
    results = catcher(img)
    plates = []
    for r in (results or []):
        # r: (x1,y1,x2,y2, 车牌号, 置信度, 车牌类型索引, 边框四点)
        plates.append({"plate": r[4], "score": round(float(r[5]), 3),
                       "box": [int(r[0]), int(r[1]), int(r[2]), int(r[3])]})
    return {"ok": True, "plates": plates, "count": len(plates),
            "error": None if plates else "这张图里没认出车牌"}
