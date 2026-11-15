# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""槽位外挂: 人脸分支引擎 — YuNet 检测 + SFace 嵌入（OpenCV Zoo, Apache 2.0）。

★ #1470（2026-11-12）批次四轻量档。设计要点:
  - cv2.dnn 直载 ONNX（cv2>=4.5.1 内置 FaceDetectorYN/FaceRecognizerSF，零新增依赖）
  - YuNet 是全图检测器：管线门控（YOLO 检出 person 才触发）管「进不进」，
    进来后全图找脸比逐框裁剪更稳（侧脸/多人不漏）
  - SFace 嵌入 128 维，比对用官方 alignCrop 对齐 + 余弦相似度
  - 比对在内存做（face_store.load_embeddings），家庭规模毫秒级
  - 隐私：全链路进程内，零网络调用
模型（data/models/vision/face/，SHA256 开发机实测 2026-11-12）:
  face_detection_yunet_2023mar.onnx   232,589 B
  face_recognition_sface_2021dec.onnx 38,696,353 B
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import config, face_store
from .base import BaseEngine, EngineResult

FACE_DIR = Path(config.MODELS_DIR) / "face"
YUNET_PATH = FACE_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_PATH = FACE_DIR / "face_recognition_sface_2021dec.onnx"

DET_SCORE_THR = 0.6     # YuNet 人脸置信度门（漏检<误检）
DET_NMS_THR = 0.3
MAX_FACES = 10          # 单图最多认 10 张脸（防 LLM 上下文污染）


class FaceEngine(BaseEngine):
    name = "face"
    slot = "face"          # 外挂槽位（不占三主槽）

    def __init__(self):
        self.detector = None       # cv2.FaceDetectorYN
        self.recognizer = None     # cv2.FaceRecognizerSF
        self._emb_cache: list[dict] | None = None   # 向量缓存（录入/删除时失效）

    # ── 就绪状态 ────────────────────────────────────────────────
    @staticmethod
    def models_ready() -> bool:
        return YUNET_PATH.exists() and SFACE_PATH.exists()

    def load(self) -> None:
        if self.loaded:
            return
        if not self.models_ready():
            raise FileNotFoundError(
                f"人脸模型未就位: {YUNET_PATH.name}={YUNET_PATH.exists()}, "
                f"{SFACE_PATH.name}={SFACE_PATH.exists()}（设置页开启后会自动下载）")
        import cv2
        self.detector = cv2.FaceDetectorYN.create(
            str(YUNET_PATH), "", (320, 320),
            score_threshold=DET_SCORE_THR, nms_threshold=DET_NMS_THR, top_k=5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(SFACE_PATH), "")
        self.loaded = True
        self._emb_cache = None

    # ── 向量缓存（录入后调用 invalidate）────────────────────────
    def invalidate_cache(self) -> None:
        self._emb_cache = None

    def _embeddings(self) -> list[dict]:
        if self._emb_cache is None:
            self._emb_cache = face_store.load_embeddings(face_store.get_tier())
        return self._emb_cache

    # ── 主推理：全图找脸 → 对齐 → 嵌入 → 本地比对 ────────────────
    def infer(self, image_path: str, **kwargs) -> EngineResult:
        if not self.loaded:
            self.load()
        import cv2
        t0 = self._now_ms()

        img = cv2.imread(image_path)
        if img is None:
            return EngineResult(source_layer="LF", confidence=0.0, elapsed_ms=0.0,
                                data={"error": "图片读不出来"})
        h, w = img.shape[:2]
        if w < 40 or h < 40:
            return EngineResult(source_layer="LF", confidence=0.0, elapsed_ms=0.0,
                                data={"faces": [], "num_faces": 0})

        # YuNet 要求 setInputSize 与图一致
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        threshold = face_store.get_threshold()
        embeddings = self._embeddings()

        out = []
        best_conf = 0.0
        if faces is not None:
            for row in faces[:MAX_FACES]:
                det_score = float(row[-1])
                best_conf = max(best_conf, det_score)
                hit = {"bbox": [round(float(v), 1) for v in row[:4]],
                       "det_score": round(det_score, 3),
                       "identity": "人脸", "matched": False, "similarity": 0.0}
                if embeddings:
                    aligned = self.recognizer.alignCrop(img, row)
                    feat = self.recognizer.feature(aligned).flatten().astype(np.float32)
                    best_sim, best_person = -1.0, None
                    for e in embeddings:
                        sim = float(np.dot(feat, e["vec"]) /
                                    (np.linalg.norm(feat) * np.linalg.norm(e["vec"]) + 1e-9))
                        if sim > best_sim:
                            best_sim, best_person = sim, e
                    hit["similarity"] = round(best_sim, 4)
                    if best_sim >= threshold and best_person:
                        hit["identity"] = best_person["name"]
                        hit["person_id"] = int(best_person["person_id"])
                        hit["matched"] = True
                    else:
                        hit["identity"] = "访客"     # 宁可标访客不标错人
                out.append(hit)

        return EngineResult(
            source_layer="LF",
            confidence=best_conf,
            elapsed_ms=self._now_ms() - t0,
            data={
                "num_faces": len(out),
                "faces": out,
                "matched_names": sorted({f["identity"] for f in out
                                         if f["matched"] and f["identity"] != "访客"}),
                "has_visitor": any(not f["matched"] for f in out),
                "threshold": threshold,
                "tier": face_store.get_tier(),
            },
        )

    # ── 录入辅助：单张照片抽向量（注册流程用）─────────────────────
    def embed_photo(self, image_path: str) -> dict:
        """抽一张照片里最大脸的嵌入向量。返回 {ok, vector?, face_count, error?}"""
        if not self.loaded:
            self.load()
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return {"ok": False, "error": "图片读不出来"}
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        if faces is None or len(faces) == 0:
            return {"ok": False, "error": "这张照片里没找到人脸"}
        # 取面积最大的脸（录入照通常主体唯一）
        areas = faces[:, 2] * faces[:, 3]
        row = faces[int(np.argmax(areas))]
        aligned = self.recognizer.alignCrop(img, row)
        feat = self.recognizer.feature(aligned).flatten().astype(np.float32)
        return {"ok": True, "vector": feat, "face_count": int(len(faces)),
                "det_score": round(float(row[-1]), 3)}

    # ── ★ 录入质检（2026-11-12 图片库录入口「当场质检」）：勾选即检，不合格当场标红不返工 ──
    def check_photo(self, image_path: str) -> dict:
        """质检单张录入照。grade: ok=合格 / warn=能用但一般 / bad=换一张。
        规则（家庭场景从宽）：0 脸=bad；最大脸宽<图宽 8%（脸太小）=bad；
        ≥2 脸=warn（一张多人）；脸区拉普拉斯方差低（糊）=warn。"""
        if not self.loaded:
            self.load()
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return {"grade": "bad", "reason": "图片读不出来"}
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        n = 0 if faces is None else len(faces)
        if n == 0:
            return {"grade": "bad", "reason": "没找到人脸（正面+光线好最稳）", "face_count": 0}
        areas = faces[:, 2] * faces[:, 3]
        row = faces[int(np.argmax(areas))]
        if float(row[2]) / max(w, 1) < 0.08:
            return {"grade": "bad", "reason": "脸太小（离近点再拍）", "face_count": n}
        reasons, grade = [], "ok"
        if n >= 2:
            grade = "warn"
            reasons.append(f"一张里有{n}个人（建议单人照）")
        x, y, ww, hh = (max(int(v), 0) for v in row[:4])
        crop = img[y:y + hh, x:x + ww]
        if crop.size:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < 60:
                grade = "warn"
                reasons.append("有点糊（对上焦再拍）")
        return {"grade": grade, "reason": "；".join(reasons) or "合格", "face_count": n}
