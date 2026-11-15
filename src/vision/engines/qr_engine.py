# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""能力引擎③: 二维码/条形码识别 — OpenCV 4.13 自带（零下载零依赖）。

QRCodeDetector: QR 码（WiFi 配置 / 网址 / 支付码 / 快递码）
barcode.BarcodeDetector: 一维条码（EAN-13 商品码 / Code128 快递面单等，contrib 构建）
"""
from __future__ import annotations


def infer(image_path: str) -> dict:
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        return {"ok": False, "error": "图片读不出来"}
    found = []

    # ── QR ──
    try:
        det = cv2.QRCodeDetector()
        retval, _, _ = det.detectAndDecodeMulti(img)
        if retval:
            for s in retval:
                if s:
                    found.append({"kind": "qr", "data": s})
    except Exception:
        pass
    if not found:
        try:
            s, _, _ = det.detectAndDecode(img)
            if s:
                found.append({"kind": "qr", "data": s})
        except Exception:
            pass

    # ── 一维条码（contrib 构建才有 cv2.barcode）──
    if hasattr(cv2, "barcode"):
        try:
            bd = cv2.barcode.BarcodeDetector()
            ok, decoded, _, _ = bd.detectAndDecodeWithType(img)
            if ok:
                for d in (decoded or []):
                    if d:
                        found.append({"kind": "barcode", "data": d})
        except Exception:
            pass

    return {"ok": True, "codes": found, "count": len(found),
            "error": None if found else "这张图里没认出二维码/条码"}
