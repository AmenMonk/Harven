# -*- coding: utf-8 -*-
"""模型体检（#1557）：扫描本地模型目录 → 三态报告。

三态：
  ✅ integrated   已接入（registry 已登记 / 垂直页已可用）
  ⚠️ known       认识但未登记（架构在已知家族表，导入接入包即可用）
  ❌ unknown     架构不认识（附已装依赖/缺失依赖提示，指路 manifest）

设计原则：只读零动手（体检不改任何状态）；已知家族表可扩充。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 扫描目录（可被配置扩展）
_SCAN_DIRS = [Path("H:/models")]

# 已知架构家族 → 接入方式提示（key 对 config.json 的 architectures/architecture 字段模糊匹配）
_KNOWN_FAMILIES = [
    {"keys": ("mobilenetv4", "timm"), "family": "timm 分类模型",
     "channel": "internal", "capability": "classify", "deps": ["timm", "torch"],
     "hint": "timmm 家族：写一份接入包 manifest（architecture=timm_classify）导入即可"},
    {"keys": ("qwen2vl", "qwen2_vl", "qwen3vl", "qwen3_vl", "internvl", "minicpmv",
              "minicpm_v", "llava", "locateanything", "vision2seq"),
     "family": "transformers 视觉语言模型", "channel": "internal", "capability": "vision_understand",
     "deps": ["transformers", "torch", "pillow"],
     "hint": "标准 transformers VL：写 manifest（architecture=transformers_vl）导入；"
             "若要求 transformers<5 需独立 venv（参考 H:\\venvs\\locate 模板）"},
    {"keys": ("stable-diffusion", "stable_diffusion", "diffusion"),
     "family": "扩散生成模型", "channel": "internal", "capability": "image_generation",
     "deps": ["diffusers", "torch"],
     "hint": "diffusers 家族：manifest（architecture=diffusers）导入"},
]

# 依赖检测白名单（体检时查主 venv 是否可导入）
_IMPORT_NAME = {"timm": "timm", "torch": "torch", "transformers": "transformers",
                "pillow": "PIL", "diffusers": "diffusers", "peft": "peft",
                "opencv": "cv2", "lmdb": "lmdb", "decord": "decord",
                "torchvision": "torchvision"}


def _read_config_json(d: Path) -> dict:
    for name in ("config.json", "configuration.json"):
        p = d / name
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _has_weights(d: Path) -> bool:
    for pat in ("*.safetensors", "*.gguf", "pytorch_model*.bin"):
        if list(d.glob(pat)):
            return True
    return False


def _check_deps(deps: list) -> tuple:
    """返回 (installed: list, missing: list)。"""
    import importlib.util
    ok, miss = [], []
    for dep in deps:
        mod = _IMPORT_NAME.get(dep.lower(), dep)
        try:
            if importlib.util.find_spec(mod) is not None:
                ok.append(dep)
                continue
        except Exception:
            pass
        miss.append(dep)
    return ok, miss


def _match_family(cfg: dict, dirname: str) -> dict | None:
    archs = " ".join(cfg.get("architectures") or []) + " " + str(cfg.get("architecture") or "")
    model_type = str(cfg.get("model_type") or "")
    hay = (archs + " " + model_type + " " + dirname).lower()
    for fam in _KNOWN_FAMILIES:
        if any(k in hay for k in fam["keys"]):
            return fam
    return None


def _integrated_ids() -> tuple:
    """收集已接入的模型标识（registry 底座 + 垂直内置/动态）。

    返回 (ids: set, dirs: set)——ids 管名字模糊匹配，dirs 管 model_dir 路径命中。
    """
    ids, dirs = set(), set()
    try:
        from src.core.chain_engine.registry import get_registry
        for m in get_registry().list_models(force=True):
            ids.add(str(m.get("id", "")).lower())
            if m.get("model_dir"):
                dirs.add(str(Path(m["model_dir"]).resolve()).lower())
            # 标签关键词（如 "mobilenetv4 快速分类"→mobilenetv4）
            lbl = str(m.get("label", "")).lower()
            for tok in lbl.split():
                if len(tok) >= 5:
                    ids.add(tok)
    except Exception:
        pass
    try:
        from src.core import vertical_api
        for m in vertical_api._BUILTIN_MODELS:
            ids.add(m["id"].lower())
        for mid in vertical_api._dynamic_models:
            ids.add(mid.lower())
    except Exception:
        pass
    return ids, dirs


def run_checkup(scan_dirs: list | None = None) -> dict:
    """执行体检。返回 {ok, scanned_dir, items, summary}。"""
    dirs = [Path(p) for p in (scan_dirs or _SCAN_DIRS)]
    integrated, int_dirs = _integrated_ids()
    items = []
    for root in dirs:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name == ".cache":
                continue
            if not _has_weights(d):
                continue
            cfg = _read_config_json(d)
            name = d.name.lower()
            dir_resolved = str(d.resolve()).lower()
            # ① 已接入？（目录路径命中 model_dir / 名字命中 id 或标签词）
            hit = dir_resolved in int_dirs or any(
                name in i or i in name for i in integrated)
            fam = _match_family(cfg, name)
            if hit:
                status = "integrated"
                hint = "已接入哈文（registry/垂直页可见），无需处理"
            elif fam:
                ok_deps, miss_deps = _check_deps(fam["deps"])
                status = "known"
                hint = fam["hint"]
                if miss_deps:
                    hint += f"；缺依赖: {', '.join(miss_deps)}"
            else:
                ok_deps, miss_deps = _check_deps(["transformers", "torch"])
                status = "unknown"
                archs = cfg.get("architectures") or [cfg.get("architecture") or "未知"]
                hint = (f"架构 {archs[0]} 不在已知家族表——需要定制接入"
                        f"（独立服务+http 适配，参考 LocateAnything 样板）；"
                        + (f"缺依赖: {', '.join(miss_deps)}" if miss_deps else "主 venv 依赖基本齐"))
            items.append({
                "dir": str(d), "name": d.name, "status": status,
                "family": (fam or {}).get("family") if status == "known" else None,
                "architectures": cfg.get("architectures") or cfg.get("model_type"),
                "hint": hint,
            })
    summary = {
        "integrated": sum(1 for i in items if i["status"] == "integrated"),
        "known": sum(1 for i in items if i["status"] == "known"),
        "unknown": sum(1 for i in items if i["status"] == "unknown"),
    }
    return {"ok": True, "scan_dirs": [str(p) for p in dirs],
            "items": items, "summary": summary}
