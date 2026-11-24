# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""哈文视觉子系统枢纽（v1.3 融合版）— 旧 eye_client(HTTP) 的进程内继任者。

职责：
  1. 理解通道解析（铁律 0）：用户钉的视觉模型 > 当前外脑(带 vision 能力位) > 配置默认
     —— 只解析不代选：解析不出就保持原通道/如实报错
  2. Pipeline 进程内单例（原 7001 独立进程的继任）
  3. 本地视觉模型发现（data/models/vision/<name>/ 的 GGUF+mmproj，模型管家落位后自动可选用）

节点③起，批改/看图/打标调用方从 eye_client HTTP 改走本模块。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_VISION_MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "models" / "vision"
_mm_json = Path(__file__).resolve().parents[2] / "data" / "multi_models.json"

_pipeline = None
_pipe_lock = threading.Lock()


# ── 通道解析（铁律 0：只解析用户的选择，绝不代选）──────────────────

def _load_mm() -> dict:
    try:
        return json.loads(_mm_json.read_text(encoding="utf-8"))
    except Exception as _e:
        logger.debug(f"[VisionLink] multi_models.json 读取失败: {_e}")
        return {}


def _provider_has_vision(provider_id: str) -> bool:
    """查 PROVIDERS 能力位（vision: True 的外脑才配当视觉大脑）。"""
    try:
        from src.core.multi_model_manager import PROVIDERS
        return bool((PROVIDERS.get(provider_id) or {}).get("vision"))
    except Exception:
        return False


def _pinned_channel() -> dict:
    """只认用户钉选（multi_models.json 的 vision_model 字段）——不落出厂默认。

    ★ #1291（2026-10-29）从 resolve_understand_channel 抽出：HarvenEye 文字分流
    专用。定版拍板：选 HarvenEye 发纯文字，没钉大脑就如实报错，绝不顶号。
    """
    mm = _load_mm()
    pinned = (mm.get("vision_model") or "").strip()
    if not pinned:
        return {}
    if pinned.startswith("local:vision/"):
        name = pinned.split("/", 1)[1]
        d = _VISION_MODELS_DIR / name
        ggufs = sorted(d.glob("*.gguf")) if d.exists() else []
        mmproj = sorted(d.glob("*mmproj*.gguf")) if d.exists() else []
        # ★ 主模型 = 非 mmproj 的 GGUF（字母序坑：mmproj-* 常排在主模型前面）
        main = [g for g in ggufs if g not in mmproj]
        if main and mmproj:
            # ★ #1323（2026-10-30 定版拍板）：钉选本地 GGUF 统一注册进 Ollama 执行——
            #   根治「Ollama 版 + 进程内版两份模型抢 12GB 显存」（10-30 凌晨事故实锤：
            #   进程内 llama.cpp 分不到显存掉 CPU，非流式对话静默假死「没回答」）。
            #   一份模型常驻 Ollama 100% GPU，闸门/热身/keep_alive 全链真正生效；
            #   注册失败如实回落进程内 llama_cpp（无 Ollama 用户兜底），绝不代选。
            try:
                omodel = _ollama_model_from_gguf(name, main[0], mmproj[0])
                return {"provider": "ollama", "model": omodel, "label": name}
            except Exception as e:
                logger.warning(f"[VisionLink] 钉选 GGUF 注册 Ollama 失败（回落进程内 llama_cpp）: {e}")
            return {"provider": "llama_cpp", "gguf": str(main[0]),
                    "mmproj": str(mmproj[0]), "label": name}
        logger.warning(f"[VisionLink] 钉的本地视觉模型 {name} 文件不全（缺 GGUF 或 mmproj）")
        return {}
    if _provider_has_vision(pinned):
        return {"provider": "api", "api_model": pinned}
    logger.warning(f"[VisionLink] 钉的 vision_model={pinned!r} 无 vision 能力位，不采纳（不代选）")
    return {}


def _ollama_base_url() -> str:
    """Ollama 地址（与融合版理解槽 OllamaVlmEngine 同源配置）。"""
    try:
        from src.vision import config as _vcfg
        return _vcfg.ENGINES["understand"].get("base_url", "http://127.0.0.1:11434")
    except Exception:
        return "http://127.0.0.1:11434"


def _ollama_model_from_gguf(vname: str, gguf_path, mmproj_path) -> str:
    """★ #1323：把钉选的本地视觉 GGUF 注册进 Ollama（blob 上传 + files create）。

    - 模型名内容寻址 `hv-pin-<主GGUF sha256 前12>`：GGUF 不变 → 缓存命中毫秒级幂等；
      换模型/重量化 → 自动注册新版本
    - 缓存文件 data/models/vision/<名字>/.ollama_registered.json（免重复算 6.7GB 哈希）
    - blob 幂等（digest 已存在跳过上传）；C 盘 ~/.ollama/blobs 一次性占用 ≈ GGUF 总大小
    - 注册成功返回 Ollama 模型名；任何一步失败抛异常（调用方回落 llama_cpp）
    """
    import hashlib
    import time as _time
    import requests as _rq

    base = _ollama_base_url().rstrip('/')
    gp, mp = Path(gguf_path), Path(mmproj_path)
    cache_p = gp.parent / ".ollama_registered.json"
    tags_names = set()
    try:
        # Ollama 库内名字带 :latest 后缀（hv-pin-xxx → hv-pin-xxx:latest），判定时兼容
        for m in _rq.get(f"{base}/api/tags", timeout=5).json().get("models", []):
            n = m.get("name", "")
            tags_names.add(n)
            tags_names.add(n.split(":")[0])
    except Exception as e:
        raise RuntimeError(f"Ollama 不可达（{base}）: {e}")

    # 缓存命中：GGUF 大小/mtime 未变 且 模型仍在库 → 秒回
    try:
        cache = json.loads(cache_p.read_text(encoding="utf-8"))
        if (cache.get("main_size") == gp.stat().st_size
                and cache.get("main_mtime") == int(gp.stat().st_mtime)
                and cache.get("model") in tags_names):
            return cache["model"]
    except Exception:
        pass

    def _sha256(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 22), b""):
                h.update(chunk)
        return h.hexdigest()

    t0 = _time.time()
    d_main, d_mm = _sha256(gp), _sha256(mp)
    for label, path, dig in [("主模型", gp, d_main), ("mmproj", mp, d_mm)]:
        url = f"{base}/api/blobs/sha256:{dig}"
        try:
            _rq.head(url, timeout=10).raise_for_status()
            logger.info(f"[VisionLink] blob {label} 已在 Ollama（跳过上传）")
        except Exception:
            t1 = _time.time()
            with open(path, "rb") as f:
                r = _rq.post(url, data=f, timeout=3600,
                             headers={"Content-Type": "application/octet-stream"})
            r.raise_for_status()
            logger.info(f"[VisionLink] blob {label} 上传完成（{_time.time()-t1:.0f}s）")
    payload = {"model": f"hv-pin-{d_main[:12]}",
               "files": {"gguf": f"sha256:{d_main}", "mmproj": f"sha256:{d_mm}"},
               "stream": False}
    r = _rq.post(f"{base}/api/create", json=payload, timeout=1800)
    r.raise_for_status()
    omodel = payload["model"]
    try:
        cache_p.write_text(json.dumps({
            "model": omodel, "sha256": d_main, "mmproj_sha256": d_mm,
            "main_size": gp.stat().st_size, "main_mtime": int(gp.stat().st_mtime),
            "mm_size": mp.stat().st_size}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug(f"[VisionLink] 注册缓存写盘失败（不影响）: {e}")
    logger.info(f"[VisionLink] 钉选 GGUF 已注册进 Ollama: {vname} → {omodel}（{_time.time()-t0:.0f}s）")
    return omodel


def human_label_for(omodel: str) -> str:
    """★ #1324：hv-pin-* 内部注册名 → 人读名（查各模型目录的注册缓存）。

    查不到 / 非 hv-pin 名原样返回。给模型中心等 UI 展示用，注册键不受影响。
    """
    omodel = str(omodel or '').strip()
    if not omodel.startswith('hv-pin-'):
        return omodel
    base = omodel.split(':')[0]
    try:
        for c in _VISION_MODELS_DIR.glob('*/.ollama_registered.json'):
            d = json.loads(c.read_text(encoding='utf-8'))
            m = str(d.get('model', ''))
            if m == omodel or m.split(':')[0] == base:
                return c.parent.name
    except Exception as e:
        logger.debug(f"[VisionLink] 人读名换算失败（原样返回）: {e}")
    return omodel


def resolve_pinned_channel() -> dict:
    """对外入口（#1291 文字分流）：只认钉选，绝不落出厂默认/当前外脑。"""
    return _pinned_channel()


def resolve_understand_channel() -> dict:
    """解析用户的选择 → {'provider': ..., 'api_model': ..., 'gguf': ..., 'mmproj': ...}

    优先级（只认用户的选择）:
      1. multi_models.json 的 vision_model 字段（用户在模型管理钉的视觉模型）
         - "local:vision/<名字>" → llama_cpp 通道 + 该目录的 GGUF/mmproj
         - 其他值 → 视为外脑 provider_id，带 vision 能力位才采纳
      2. 当前外脑 current_model 且其 provider vision:True → api 通道
      3. 都没有 → 返回 {}（保持 config 里的默认通道，如 ollama）
    """
    ch = _pinned_channel()
    if ch:
        return ch
    mm = _load_mm()
    current = (mm.get("current_model") or "").strip()
    if current and current not in ("ollama", "harveneye", "") and _provider_has_vision(current):
        return {"provider": "api", "api_model": current}
    # ★ #1280 节点⑥ 退役 7001 后：无钉选 → 出厂默认通道（config.ENGINES['understand']，
    #   出厂配置 provider=ollama/qwen3-vl——产品默认配置，非 AI 代选，铁律 0 合规）
    try:
        from src.vision import config as vcfg
        u = vcfg.ENGINES.get("understand", {})
        prov = u.get("provider", "")
        if prov == "ollama" and u.get("model"):
            return {"provider": "ollama", "model": u["model"], "label": u["model"]}
        if prov == "llama_cpp" and u.get("gguf") and Path(u["gguf"]).is_file():
            return {"provider": "llama_cpp", "gguf": u["gguf"], "mmproj": u.get("mmproj", "")}
    except Exception:
        pass
    return {}


def apply_understand_channel() -> None:
    """把解析结果写进 vision config（理解槽工厂按此建引擎）。解析不出则不动配置。"""
    from src.vision import config as vcfg
    ch = resolve_understand_channel()
    if not ch:
        return
    u = vcfg.ENGINES["understand"]
    u["provider"] = ch["provider"]
    if ch["provider"] == "api":
        u["api_model"] = ch["api_model"]
    elif ch["provider"] == "llama_cpp":
        u["gguf"], u["mmproj"] = ch["gguf"], ch["mmproj"]
    elif ch["provider"] == "ollama":
        u["model"] = ch.get("model", u.get("model", ""))
    logger.info(f"[VisionLink] 理解通道: {ch.get('provider')} {ch.get('api_model', '') or ch.get('model', '')}")


def set_pin(model_id: str) -> dict:
    """钉选/清除视觉模型（★ #1292 自装编排与 web_app pin 路由共用核心）。
    model_id: 'local:vision/<名字>' 或带 vision 位的外脑 id；空串=清除钉选。
    钉选后即时重建理解引擎，无需重启。"""
    from src.core.multi_model_manager import PROVIDERS
    model_id = str(model_id or "").strip()
    if model_id in ("ollama", "harveneye"):
        return {"success": False,
                "error": f"「{model_id}」是本地后端，不是可钉选的视觉模型；"
                         "本地视觉模型请用 local:vision/<名字> 形式"}
    if model_id and not model_id.startswith("local:vision/"):
        if model_id not in (PROVIDERS or {}) or not (PROVIDERS.get(model_id) or {}).get("vision"):
            return {"success": False,
                    "error": f"「{model_id}」不是可用的视觉模型（无看图能力或不存在）"}
    mm = _load_mm()
    if model_id:
        mm["vision_model"] = model_id
    else:
        mm.pop("vision_model", None)
    _mm_json.write_text(json.dumps(mm, ensure_ascii=False, indent=2), encoding="utf-8")
    rebuild_pipeline()   # 换引擎实例，即时生效
    logger.info(f"[VisionLink] 视觉模型钉选: {model_id or '(清除)'}")
    return {"success": True, "pinned": model_id}


# ── Pipeline 单例 ─────────────────────────────────────────────────

def get_pipeline():
    """进程内视觉管线单例（线程安全）。首次调用即生效，未装引擎模型时 L1/L2 惰性加载。"""
    global _pipeline
    if _pipeline is None:
        with _pipe_lock:
            if _pipeline is None:
                from src.vision.pipeline import Pipeline
                _pipeline = Pipeline()
                logger.info("[VisionLink] 视觉管线就绪（进程内单例）")
    return _pipeline


def rebuild_pipeline():
    """理解通道变化后重建（换 provider 需换引擎实例）。"""
    global _pipeline
    with _pipe_lock:
        from src.vision.pipeline import Pipeline
        _pipeline = Pipeline()
    logger.info("[VisionLink] 视觉管线已重建（理解通道变更）")
    return _pipeline


# ── 对外 API（节点③起调用方统一走这里）──────────────────────────

def understand_image(image_path: str, prompt: str = "", timeout_s: float | None = None) -> str:
    """图片理解（L3 同步直答通道：当场识别/批改走这里）。失败抛 VlmDegraded。"""
    apply_understand_channel()
    return get_pipeline().understand.infer(image_path, prompt=prompt,
                                           timeout_s=timeout_s).data["description"]


def chat(messages: list[dict], stream: bool = False):
    """理解槽对话（带图多轮）。流式仅 ollama 通道支持。"""
    apply_understand_channel()
    return get_pipeline().understand.chat(messages, stream=stream)


def recognize(image_path: str, **kw) -> dict:
    """四层瀑布识别（L0~L3，含入库/检索）。

    ★ 骨架开工（2026-11-24）：正式经「模型组合工坊」编排器路由——
    HarvenEye=官方组合第一号，同图同模型零行为变化，新增物流单记账；
    骨架路径异常时回退原直调（同一 Pipeline 实例、同一批模型，非模型偷换）。"""
    apply_understand_channel()
    try:
        from core.chain_engine import get_orchestrator
        out = get_orchestrator().run("harveneye", {"image_path": image_path, **kw})
        if out.get("ok"):
            r = out["result"]
            r["chain"] = {"id": "harveneye", "run_id": out["run_id"],
                          "label": out["label"], "total_ms": out.get("total_ms")}
            return r
        logger.warning(f"[VisionLink] 骨架路由未过 preflight，回退原链: {out.get('error')}")
    except Exception as e:
        logger.warning(f"[VisionLink] 骨架路由异常回退原链: {e}")
    return get_pipeline().recognize(image_path, **kw)


# ── 本地视觉模型发现（模型管家落位 → 自动可选用）──────────────────

def list_local_vision_models() -> list[dict]:
    """扫描 data/models/vision/<名字>/，返回已就位的本地视觉模型。

    就位标准: 目录内同时有 主模型 GGUF 和 *mmproj*.gguf。
    返回: [{'id': 'local:vision/<名字>', 'name': <名字>, 'size_gb': x, 'ready': bool}]

    ★ #1287（2026-10-29）：排除底座引擎目录（yolox/chinese-clip，installer.ENGINE_MANIFEST）——
    底座是哈文必装引擎不是理解大脑，混进列表会以 size0/ready:false 空壳误导用户（全面检测实锤）。
    """
    out = []
    if not _VISION_MODELS_DIR.exists():
        return out
    try:
        from src.vision.installer import ENGINE_MANIFEST as _BASE
        baseline = {str(k).lower() for k in _BASE}
    except Exception:
        baseline = {"yolox", "chinese-clip"}
    for d in sorted(_VISION_MODELS_DIR.iterdir()):
        if not d.is_dir() or d.name.lower() in baseline:
            continue
        ggufs = list(d.glob("*.gguf"))
        mmproj = list(d.glob("*mmproj*.gguf"))
        main = [g for g in ggufs if g not in mmproj]
        size_gb = round(sum(g.stat().st_size for g in ggufs) / 1024**3, 2)
        out.append({"id": f"local:vision/{d.name}", "name": d.name,
                    "size_gb": size_gb, "ready": bool(main and mmproj)})
    return out
