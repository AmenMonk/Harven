# -*- coding: utf-8 -*-
"""能力登记表（骨架核心 · 组合即模型的第一张骨牌）。

- 模型侧：底座内部件 + 视觉目录（vision.catalog）+ Ollama 在装 + 外脑（multi_models），
  统一登记 channel / capabilities / vram_gb ——「通道适配层的事实源」（B2）；
- 组合侧：保存并选用后登记为一等条目，与单个模型平权出现在能力查询结果里（§4）；
- B7 命名空间：组合名与模型 id / 内置组合名互斥查重；
- 能力标签诚实登记：组合能力 = 各引擎环能力交集（recipe.capabilities()）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import recipe as recipe_mod

# ══ 底座内部件（哈文图片智能 · 底座预置层，装机即有）══════════════
# vram 估值为进程内工作集近似值（体检口径；YOLOX onnx ~0.3G / CLIP ~0.5G / SFace ~0.3G）
BASE_MODELS = [
    {"id": "harven.base.dhash", "label": "dhash 指纹查重", "channel": "internal",
     "capabilities": ["dedup"], "vram_gb": 0.0, "builtin": True},
    {"id": "harven.base.yolox", "label": "YOLOX 目标检测", "channel": "internal",
     "capabilities": ["detect"], "vram_gb": 0.3, "builtin": True},
    {"id": "harven.base.clip", "label": "Chinese-CLIP 语义打标", "channel": "internal",
     "capabilities": ["embed", "tag"], "vram_gb": 0.5, "builtin": True},
    {"id": "harven.base.sface", "label": "SFace 人脸识别+活体", "channel": "internal",
     "capabilities": ["face"], "vram_gb": 0.3, "builtin": True},
]

# 外脑视觉能力名单（跟阵营走的事实：_camp_vision 现用 glm-4v-flash 起步）
_CLOUD_VISION_HINTS = ("glm-4v", "qwen-vl", "qvq", "doubao-vision", "vision", "4v")


class Registry:
    """能力登记表。进程内单例（get_registry()），线程安全。"""

    def __init__(self, chains_dir: str | Path):
        self.chains_dir = Path(chains_dir)
        self._chains: dict[str, dict] = {}      # recipe_id -> {"recipe": Recipe, "registered_at": ts}
        self._model_cache: list | None = None
        self._model_cache_ts = 0.0
        self._lock = threading.RLock()

    # ══ 模型侧（动态盘点 + 缓存 30s）════════════════════════════
    def list_models(self, force: bool = False) -> list:
        with self._lock:
            if not force and self._model_cache is not None \
                    and time.time() - self._model_cache_ts < 30:
                return self._model_cache
            models = [dict(m) for m in BASE_MODELS]
            models += self._vision_catalog_models()
            models += self._ollama_models()
            models += self._cloud_models()
            self._model_cache = models
            self._model_cache_ts = time.time()
            return models

    def _vision_catalog_models(self) -> list:
        """视觉目录（vision.catalog CATALOG）→ llama_cpp 通道条目。"""
        out = []
        try:
            import sys
            if r"H:\HarvenAI" not in sys.path:
                sys.path.insert(0, r"H:\HarvenAI")
            if r"H:\HarvenAI\src" not in sys.path:
                sys.path.insert(0, r"H:\HarvenAI\src")
            from src.vision import catalog as vc
            for e in vc.CATALOG:
                if not e.get("enabled"):
                    continue
                out.append({
                    "id": e["id"], "label": e["label"], "channel": "llama_cpp",
                    "capabilities": ["vision_understand", "text_gen", "locate"],
                    "vram_gb": float(e.get("min_vram_gb") or 0),
                    "size_gb": float(e.get("size_gb") or 0),
                    "builtin": False, "installed": None,   # installed 由依赖对账时实查
                })
        except Exception:
            pass
        return out

    def _ollama_models(self) -> list:
        out = []
        try:
            import urllib.request, json as _json
            req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            for m in data.get("models", []):
                name = m.get("name", "")
                cap = ["text_gen"]
                low = name.lower()
                if any(k in low for k in ("vl", "llava", "minicpm-v", "vision")):
                    cap.append("vision_understand")
                if "embed" in low or "bge" in low:
                    cap = ["embed"]
                out.append({"id": f"ollama:{name}", "label": f"{name}（本地 Ollama）",
                            "channel": "ollama", "capabilities": cap,
                            "vram_gb": round((m.get("size") or 0) / 1e9, 1),
                            "builtin": False, "installed": True})
        except Exception:
            pass
        return out

    def _cloud_models(self) -> list:
        """外脑清单（data/multi_models.json models 键）。视觉能力按名单提示词判定。"""
        out = []
        try:
            import json as _json
            p = Path(r"H:\HarvenAI\data\multi_models.json")
            if p.exists():
                data = _json.loads(p.read_text(encoding="utf-8"))
                for m in (data.get("models") or []):
                    mid = m.get("id") or m.get("name") or ""
                    if not mid:
                        continue
                    low = (mid + (m.get("label") or "")).lower()
                    caps = ["text_gen"]
                    if any(h in low for h in _CLOUD_VISION_HINTS):
                        caps.append("vision_understand")
                    out.append({"id": f"cloud:{mid}", "label": m.get("label") or mid,
                                "channel": "cloud", "capabilities": caps,
                                "vram_gb": 0.0, "builtin": False, "installed": True})
        except Exception:
            pass
        return out

    def find_model(self, model_id: str, force: bool = False) -> dict | None:
        for m in self.list_models(force=force):
            if m["id"] == model_id:
                return m
        return None

    def model_vram_gb(self, model_id: str) -> float:
        m = self.find_model(model_id)
        return float(m["vram_gb"]) if m else 0.0

    def models_with_capability(self, cap: str) -> list:
        return [m for m in self.list_models() if cap in (m.get("capabilities") or [])]

    # ══ 组合侧（组合即模型）══════════════════════════════════════
    def register_chain(self, r: recipe_mod.Recipe, *, overwrite_builtin: bool = False) -> dict:
        errs = r.validate()
        if errs:
            return {"ok": False, "errors": errs}
        with self._lock:
            old = self._chains.get(r.id)
            if old and old["recipe"].origin == "builtin" and not overwrite_builtin \
                    and r.origin != "builtin":
                return {"ok": False, "errors": [f"内置组合 {r.id} 受原件保护，只能 fork 另存"]}
            # B7 命名空间查重：与其他组合名互斥
            for cid, entry in self._chains.items():
                if cid != r.id and entry["recipe"].name == r.name:
                    return {"ok": False, "errors": [f"组合名「{r.name}」已被 {cid} 占用（B7）"]}
            # 与模型 id 互斥
            if self.find_model(r.id, force=True) or self.find_model(f"chain:{r.id}", force=True):
                return {"ok": False, "errors": [f"id {r.id!r} 与模型命名冲突（B7）"]}
            self._chains[r.id] = {"recipe": r, "registered_at": time.time()}
            self._model_cache = None     # 失效缓存（组合条目并入模型查询）
            recipe_mod.save(r, self.chains_dir)
        return {"ok": True, "recipe_id": r.id}

    def unregister_chain(self, recipe_id: str) -> dict:
        """卸载组合：只删配方单，底座模型保留（三轮定版）。"""
        with self._lock:
            entry = self._chains.pop(recipe_id, None)
            self._model_cache = None
        if entry:
            recipe_mod.delete(self.chains_dir, recipe_id)
            # 在用底座模型一个不动（解耦定版）；如实返回影响面
            return {"ok": True, "removed": recipe_id, "base_models_kept": True}
        return {"ok": False, "errors": [f"组合不存在: {recipe_id}"]}

    def get_chain(self, recipe_id: str) -> recipe_mod.Recipe | None:
        with self._lock:
            e = self._chains.get(recipe_id)
            return e["recipe"] if e else None

    def list_chains(self) -> list:
        with self._lock:
            return [e["recipe"] for e in self._chains.values()]

    def bootstrap_from_disk(self) -> int:
        """启动时从 data/chains/ 装载全部配方单。"""
        n = 0
        with self._lock:
            for r in recipe_mod.load_all(self.chains_dir):
                if not r.validate():
                    self._chains[r.id] = {"recipe": r, "registered_at": time.time()}
                    n += 1
        return n

    # ══ 组合作为一等条目进能力查询（组合即模型）══════════════════
    def capability_entries(self, cap: str | None = None) -> list:
        """统一查询：模型 + 组合（chain: 前缀）。"""
        out = []
        for m in self.list_models():
            if cap is None or cap in (m.get("capabilities") or []):
                out.append({"kind": "model", **m})
        with self._lock:
            for entry in self._chains.values():
                r = entry["recipe"]
                caps = r.capabilities()
                if cap is None or cap in caps:
                    out.append({
                        "kind": "chain", "id": f"chain:{r.id}",
                        "label": f"{r.name}（组合）",
                        "channel": "chain",
                        "capabilities": sorted(caps),
                        "identity": r.identity(),
                        "origin": r.origin,
                        "target": r.target,
                    })
        return out


# ══ 模块级单例 ═════════════════════════════════════════════════
_registry: Registry | None = None
_reg_lock = threading.Lock()

CHAINS_DIR = r"H:\HarvenAI\data\chains"


def get_registry() -> Registry:
    global _registry
    with _reg_lock:
        if _registry is None:
            _registry = Registry(CHAINS_DIR)
            Path(CHAINS_DIR).mkdir(parents=True, exist_ok=True)
            _registry.bootstrap_from_disk()
        return _registry
