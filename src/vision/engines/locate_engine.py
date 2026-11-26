# -*- coding: utf-8 -*-
"""槽位引擎: 目标定位 — LocateAnything-3B（独立服务 http 通道）。

模型本体不在本进程：H:\\venvs\\locate\\serve_locate.py 常驻服务
（127.0.0.1:8101，按需加载/空闲卸载，见 #1555）。本引擎只做 HTTP 客户端：
服务不在 → 如实报错（附启动方式），不偷换不兜底。

输出：boxes/points 像素坐标 + raw 原文（<box><x1><y1><x2><y2></box> 0~1000 归一化）。
"""
from __future__ import annotations

import json
import os
import urllib.request

from .base import BaseEngine, EngineResult

DEFAULT_BASE = "http://127.0.0.1:8101"
_TIMEOUT = float(os.environ.get("LOCATE_HTTP_TIMEOUT", "600"))


class LocateHttpEngine(BaseEngine):
    """LocateAnything-3B http 适配引擎（服务健康检查即 load）。"""

    name = "locate_http"
    slot = "detect"
    accepts_payload = True          # _SingletonSlot 会传完整 payload（含 prompt/target）

    def __init__(self, base_url: str = ""):
        self.base = (base_url or os.environ.get("LOCATE_SERVICE", DEFAULT_BASE)).rstrip("/")

    # ── 通道健康 = load ────────────────────────────────────────
    def load(self) -> None:
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=3) as r:
                info = json.loads(r.read().decode("utf-8"))
            if not info.get("ok"):
                raise RuntimeError("服务响应异常")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "LocateAnything 服务未就绪（%s）。启动方式："
                r"H:\venvs\locate\start_locate_service.bat" % e) from e
        self.loaded = True

    def is_available(self) -> bool:
        try:
            self.load()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ── 推理 ──────────────────────────────────────────────────
    def infer(self, payload, **kwargs) -> EngineResult:
        t0 = self._now_ms()
        if isinstance(payload, str):            # 兼容只传 image_path 的调用
            payload = {"image_path": payload}
        image_path = payload.get("image_path") or ""
        target = (payload.get("prompt") or payload.get("target") or "").strip()
        if not image_path:
            raise RuntimeError("定位引擎缺少 image_path")
        if not target:
            target = "the main object"
        body = json.dumps({"image_path": image_path, "target": target,
                           "mode": payload.get("mode") or "ground_multi"}).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/locate", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            # 如实失败不装好（B10）：抛错让编排器走 on_fail（不偷换不兜底）
            raise RuntimeError("定位服务调用失败: %s" % e) from e
        if not resp.get("ok"):
            raise RuntimeError("定位服务返回失败: %s" % str(resp.get("error"))[:200])
        boxes = resp.get("boxes") or []
        conf = 0.9 if boxes else 0.0
        return EngineResult(
            "L2", conf, self._now_ms() - t0,
            {"target": resp.get("target"), "boxes": boxes,
             "points": resp.get("points") or [], "raw": resp.get("raw", ""),
             "device": resp.get("device"), "cost_s": resp.get("cost_s"),
             "width": resp.get("width"), "height": resp.get("height"),
             "output_type": "boxes"})
