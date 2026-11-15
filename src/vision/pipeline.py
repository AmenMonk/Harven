"""四层瀑布管线 — L0哈希查重 → L1检测 → L2索引 → L3理解。

M1 开发顺序红利：管线无状态识别 + 索引库挂载双动作。
  recognize()  识别：L0 秒回近重 → L1 检测 → L2 向量检索 → 按门控决定是否走 L3
  index_image() 入库：跑 L1+L2 → 按分级制度决定 index/candidate → 写库
溯源三件套（每层）: source_layer / confidence / elapsed_ms。
"""
from __future__ import annotations

import threading

import numpy as np

from . import config
from .dedup import dhash, hash_to_hex
from .engines.base import EngineResult
from .engines.clip_engine import ClipEngine
from .engines.vlm_engine import VlmDegraded, build_understand_engine
from .engines.yolox_engine import YoloxEngine
from .store import CatalogStore


class Pipeline:
    def __init__(self, preset: str = config.DEFAULT_PRESET, store: CatalogStore | None = None):
        self.preset = preset
        self.threshold = config.GATING_PRESETS[preset]
        self.store = store or CatalogStore()
        # 槽位引擎惰性加载（首次用到才吃内存/显存）
        self.detect = YoloxEngine()
        self.index = ClipEngine()
        self.understand = build_understand_engine()   # ★ v1.3 工厂: provider 三通道，用户选谁用谁
        self._vlm_lock = threading.Lock()      # 13.2 VLM 单例锁
        self._face_engine = None               # ★ #1470 人脸分支（惰性，开了才占内存）

    # ══ ★ #1470 人脸分支（2026-11-12，批次四）══════════════════
    def _run_face_branch(self, r1: EngineResult, image_path: str):
        """门控：功能开启 + L1 检出 person + 模型就位 → 全图找脸认人。
        失败一律软降（返回 None，绝不妨碍主链）。"""
        try:
            from . import face_store
            if not face_store.is_enabled():
                return None
            if not any(d.get("label") == "person" for d in r1.data.get("detections", [])):
                return None                    # 没人就不进人脸分支（省时省内存）
            from .engines.face_engine import FaceEngine
            if not FaceEngine.models_ready():
                return None                    # 模型未下载（设置页引导），主链照走
            if self._face_engine is None:
                self._face_engine = FaceEngine()
            return self._face_engine.infer(image_path)
        except Exception:
            return None

    def switch_preset(self, preset: str) -> None:
        if preset not in config.GATING_PRESETS:
            raise ValueError(f"未知档位: {preset}，可选 {list(config.GATING_PRESETS)}")
        self.preset = preset
        self.threshold = config.GATING_PRESETS[preset]

    # ══ 识别主入口 ══════════════════════════════════════════════
    def recognize(self, image_path: str, do_index: bool = False,
                  force_vlm: bool = False, skip_vlm: bool = False) -> dict:
        trace: list[dict] = []

        # ── L0 哈希查重（预算 <2ms）──
        h = dhash(image_path)
        h_hex = hash_to_hex(h)
        dup = self.store.find_near_duplicate(h)
        if dup:
            item = self.store.get_by_id(dup[0])
            trace.append({"source_layer": "L0", "confidence": 1.0,
                          "elapsed_ms": 0.0, "matched_id": dup[0],
                          "hamming": dup[1]})
            return self._wrap(image_path, trace, final_layer="L0",
                              note=f"近重复命中(汉明距离{dup[1]})，L0 秒回",
                              item=item, preset=self.preset)

        # ── L1 YOLOX 检测（预算 <30ms）──
        r1 = self.detect.infer(image_path)
        trace.append(r1.to_dict())

        # ── LF 人脸分支（★ #1470：开启+检出 person 才跑，身份标签注回）──
        face_r = self._run_face_branch(r1, image_path)
        if face_r is not None:
            trace.append(face_r.to_dict())
            names = face_r.data.get("matched_names") or []
            if names:
                has_visitor = face_r.data.get("has_visitor", False)
                note_names = "、".join(names) + ("、访客" if has_visitor else "")
                note_face = f"认出: {note_names}"
            elif face_r.data.get("num_faces"):
                note_face = "照片里有人脸（访客）"
            else:
                note_face = ""          # 没找到脸，不打扰主流程
        else:
            note_face = ""

        # ── L2 CLIP 索引/检索（预算 <60ms）──
        r2 = self.index.infer(image_path)
        vec = np.array(r2.data["vector"], dtype=np.float32)
        similar = self.store.search_vector(vec, top_k=5)
        trace.append({"source_layer": "L2", "confidence": 1.0,
                      "elapsed_ms": r2.elapsed_ms,
                      "dim": r2.data["dim"], "similar_count": len(similar),
                      "similar": [{"id": s["id"], "tags": s["tags"],
                                   "similarity": s.get("similarity")}
                                  for s in similar[:5]]})

        # ── 门控决策：是否需要 L3 ──
        # ★ #1244: skip_vlm=True 时禁用 L3（起名/补标等标签任务秒回，素材不足如实返回）
        top1_conf = r1.confidence
        best_sim = similar[0].get("similarity", 0.0) if similar else 0.0
        need_vlm = (not skip_vlm) and (force_vlm or (top1_conf < self.threshold and best_sim < 0.85))

        r3_dict = None
        if need_vlm:
            try:
                with self._vlm_lock:               # 13.2 单例锁
                    r3 = self.understand.infer(image_path, timeout_s=config.VLM_TIMEOUT_S)
                r3_dict = r3.to_dict()
                trace.append(r3_dict)
            except VlmDegraded as e:
                trace.append({"source_layer": "L3", "confidence": 0.0,
                              "elapsed_ms": 0.0, "degraded": True, "reason": str(e)})

        # ── 入库（分级制度，识别时可顺带入库）──
        item_id = None
        if do_index:
            item_id = self._auto_index(image_path, h_hex, vec, r1, r3_dict, face_r=face_r)

        final_layer = "LF+L3" if (note_face and need_vlm and r3_dict) else (
            "LF" if note_face else ("L3" if (need_vlm and r3_dict) else "L1+L2"))
        return self._wrap(image_path, trace, final_layer=final_layer,
                          note=(note_face + "；" if note_face else "") +
                               self._verdict(top1_conf, best_sim, need_vlm, r3_dict),
                          item=self.store.get_by_id(item_id) if item_id else None,
                          preset=self.preset)

    # ══ 入库（分级路由）══════════════════════════════════════════
    def index_image(self, image_path: str, pointer: str | None = None) -> dict:
        """入库入口。pointer=原图真家路径（默认=当前路径）。"""
        h = dhash(image_path)
        h_hex = hash_to_hex(h)
        dup = self.store.find_near_duplicate(h)
        if dup:
            return {"indexed": False, "reason": f"近重复(item {dup[0]})，不重复入库",
                    "matched_id": dup[0]}

        r1 = self.detect.infer(image_path)
        r2 = self.index.infer(image_path)
        vec = np.array(r2.data["vector"], dtype=np.float32)
        face_r = self._run_face_branch(r1, image_path)   # ★ #1470 入库也认人（标签进 tags）
        r3_dict = None
        # 分级路由：L1 搞不定 → L3 兜 → 疑难图才进候选标注池
        if r1.confidence < self.threshold:
            try:
                with self._vlm_lock:
                    r3 = self.understand.infer(image_path)
                r3_dict = r3.to_dict()
            except VlmDegraded:
                pass
        item_id = self._auto_index(pointer or image_path, h_hex, vec, r1, r3_dict,
                                   face_r=face_r)
        item = self.store.get_by_id(item_id)
        return {"indexed": True, "item_id": item_id, "level": item["level"],
                "tags": item["tags"]}

    def _auto_index(self, pointer: str, h_hex: str, vec: np.ndarray,
                    r1: EngineResult, r3_dict: dict | None,
                    face_r: EngineResult | None = None) -> int:
        """分级入库路由（第五章）:
           L1 高置信 → index（零信息样本，不进训练池）
           L3 兜底的疑难图 → candidate（候选标注池）
           用户纠错 → gold（调用 feedback，不在此处）
           ★ #1470: 身份标签 > 泛化描述（matched_names 前置进 tags）"""
        detections = r1.data.get("detections", [])
        face_names = (face_r.data.get("matched_names") or []) if face_r else []
        if r3_dict:                                     # 疑难图
            level = "candidate"
            tags = ",".join(face_names)
            description = r3_dict.get("description", "")
        else:
            level = "index"
            tags = ",".join(face_names + [d["label"] for d in detections[:5]])
            description = ""
        engine_ver = f"yolox-{r1.data.get('profile')},clip-{self.index.dim},vlm-qwen3vl-8b" + (
            ",face-sface" if face_r else "")
        return self.store.add_item(
            dhash_hex=h_hex, pointer=pointer, vector=vec,
            level=level, tags=tags, description=description,
            detections=detections, engine_ver=engine_ver,
        )

    def feedback(self, item_id: int, tags: str | None = None,
                 description: str | None = None, to_gold: bool = True) -> dict:
        """用户纠错 → 金样本池（最高价值训练资产）。"""
        if to_gold:
            self.store.set_level(item_id, "gold")
        if tags or description is not None:
            self.store.set_tags(item_id, tags or "", description)
        return {"ok": True, "item_id": item_id,
                "level": self.store.get_by_id(item_id)["level"]}

    # ══ 检索 ═════════════════════════════════════════════════════
    def search_text(self, query: str, top_k: int = 20) -> dict:
        hits_kw = self.store.search_keyword(query, top_k=top_k)
        hits_vec = self.store.search_vector(self.index.text_to_vec(query), top_k=top_k)
        return {"keyword": hits_kw, "semantic": hits_vec}

    # ══ 内部 ═════════════════════════════════════════════════════
    @staticmethod
    def _verdict(top1_conf: float, best_sim: float, need_vlm: bool, r3) -> str:
        if r3 and r3.get("description"):
            return f"VLM 兜底完成（L1置信{top1_conf:.2f}/相似{best_sim:.2f}低于门控）"
        if need_vlm:
            return "VLM 降级，回落 L1+L2 结果"
        return f"L1/L2 高置信直答（L1置信{top1_conf:.2f}/相似{best_sim:.2f}≥门控，跳过 L3 省时）"

    @staticmethod
    def _wrap(image_path: str, trace: list[dict], final_layer: str,
              note: str, item: dict | None = None, preset: str = "") -> dict:
        return {
            "image": image_path,
            "preset": preset,
            "final_layer": final_layer,
            "note": note,
            "trace": trace,                      # 溯源 JSON：每层带来源/置信/耗时
            "item": item,
            "total_ms": round(sum(t.get("elapsed_ms", 0) for t in trace), 1),
        }
