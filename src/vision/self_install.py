# -*- coding: utf-8 -*-
"""★ #1292（2026-10-29）HarvenEye 理解大脑自装编排器（方案A②③）。

一条龙：检测硬件 → 推荐档位（铁律0：等用户点头）→ 安装（本地拷贝/在线下载）
→ 自动钉选 → 冒烟验证。下载复用 installer 下载器；拷贝自带后台线程+进度；
status() 在文件就绪后自动收尾（钉选+冒烟，结果缓存防重跑）。
"""
import logging
import shutil
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 拷贝进度（与 installer 下载进度分开，status 时合并展示）
_copy_state = {"running": False, "phase": "", "file": "", "done_bytes": 0,
               "total_bytes": 0, "error": "", "finished_at": 0.0}
_copy_lock = threading.Lock()
# 收尾缓存：{dir_name: {"pinned": str, "smoke": {...}}}
_finalized = {}


def _vram_gb() -> float:
    """显存探测（GB）。N 卡走 nvidia-smi；其余返回 0（推荐时按 CPU 档保守处理）。"""
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        mib = int((out.stdout or "").strip().splitlines()[0])
        return round(mib / 1024, 1)
    except Exception:
        return 0.0


def _models_dir() -> Path:
    from src.vision import config as vcfg
    return Path(vcfg.MODELS_DIR)


def _dir_ready(dir_name: str) -> dict:
    """检查 data/models/vision/<dir>/ 主模型+mmproj 是否齐备。"""
    d = _models_dir() / dir_name
    ggufs = sorted(d.glob("*.gguf")) if d.exists() else []
    mmproj = sorted(d.glob("*mmproj*.gguf")) if d.exists() else []
    main = [g for g in ggufs if g not in mmproj]
    return {"ready": bool(main and mmproj),
            "gguf": str(main[0]) if main else "",
            "mmproj": str(mmproj[0]) if mmproj else "",
            "size_gb": round(sum(g.stat().st_size for g in ggufs) / 1024 ** 3, 2)}


def _local_src_ok(entry) -> bool:
    src = entry.get("local_src") or {}
    g, m = src.get("gguf", ""), src.get("mmproj", "")
    try:
        return bool(g and m and Path(g).exists() and Path(m).exists())
    except Exception:
        return False


def _installed_pins() -> list:
    try:
        from src.core import vision_link
        return vision_link.list_local_vision_models()
    except Exception:
        return []


def recommend() -> dict:
    """检测硬件+盘点现状 → 给推荐清单（只荐不装，等用户确认）。"""
    from src.vision import catalog
    from src.vision import platform as vplat
    hw = vplat.detect()
    vram = _vram_gb()
    fitted = catalog.fit_list(vram)
    cands = []
    for e in fitted:
        r = _dir_ready(e["dir_name"])
        cands.append({
            "id": e["id"], "label": e["label"], "size_gb": e["size_gb"],
            "desc": e["desc"], "verified": e.get("verified", False),
            "installed": r["ready"],
            "local_free": _local_src_ok(e),   # 本机有现成文件=零下载可装
        })
    rec = fitted[0]["id"] if fitted else ""
    return {"hardware": {"gpu": hw.get("gpu"), "backend": hw.get("backend"),
                         "vram_gb": vram},
            "installed_models": _installed_pins(),
            "candidates": cands,
            "recommended": rec,
            "note": ("" if fitted else
                     "没有适配当前硬件的档位；可在「视觉」页面挂外脑视觉当大脑（本机零负担）")}


def _copy_job(entry):
    """后台拷贝本地现成文件 → data/models/vision/<dir>/（幂等：同大小跳过）。"""
    try:
        dest_dir = _models_dir() / entry["dir_name"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        pairs = [(entry["local_src"]["gguf"], entry["files"][0]["filename"]),
                 (entry["local_src"]["mmproj"], entry["files"][1]["filename"])]
        with _copy_lock:
            _copy_state.update({"running": True, "phase": "拷贝中", "error": "",
                                "done_bytes": 0, "finished_at": 0.0,
                                "total_bytes": sum(Path(s).stat().st_size
                                                   for s, _ in pairs)})
        for src, fn in pairs:
            s, d = Path(src), dest_dir / fn
            with _copy_lock:
                _copy_state.update({"file": fn})
            if d.exists() and d.stat().st_size == s.stat().st_size:
                with _copy_lock:
                    _copy_state["done_bytes"] += s.stat().st_size
                continue   # 幂等
            tmp = d.with_suffix(d.suffix + ".part")
            done = 0
            with open(s, "rb") as fi, open(tmp, "wb") as fo:
                while True:
                    buf = fi.read(1 << 22)   # 4MB
                    if not buf:
                        break
                    fo.write(buf)
                    done += len(buf)
                    with _copy_lock:
                        _copy_state["done_bytes"] += len(buf)
            tmp.replace(d)
        with _copy_lock:
            _copy_state.update({"running": False, "phase": "完成",
                                "finished_at": time.time()})
        logger.info(f"[SelfInstall] {entry['id']} 本地拷贝完成")
    except Exception as e:
        logger.error(f"[SelfInstall] {entry['id']} 拷贝失败: {e}")
        with _copy_lock:
            _copy_state.update({"running": False, "phase": "失败",
                                "error": str(e)[:200], "finished_at": time.time()})


def install(model_id: str, confirmed: bool = False) -> dict:
    """开始安装（铁律0门禁：confirmed 必须为 True=用户点头过的才放行）。
    路径：已就位→直接收尾；本机有现成文件→后台拷贝；都没有→在线下载（installer）。"""
    from src.vision import catalog
    if not confirmed:
        return {"success": False,
                "error": "未获用户确认（铁律0：先推荐，用户点头后 install(confirmed=True)）"}
    entry = catalog.get(model_id)
    if not entry:
        return {"success": False, "error": f"目录里没有 {model_id}"}
    if not entry.get("enabled"):
        return {"success": False, "error": f"{entry['label']} 暂缓：{entry['desc']}"}
    r = _dir_ready(entry["dir_name"])
    if r["ready"]:
        return {"success": True, "started": False, "state": "已就位",
                "note": "文件已在本，调 status 完成钉选+冒烟"}
    if _local_src_ok(entry):
        with _copy_lock:
            if _copy_state["running"]:
                return {"success": False, "error": "已有拷贝任务在跑，稍候"}
        threading.Thread(target=_copy_job, args=(entry,), daemon=True,
                         name=f"selfinstall-copy-{model_id}").start()
        return {"success": True, "started": True, "state": "拷贝中",
                "source": "本机现成文件（零下载）", "total_gb": entry["size_gb"]}
    from src.vision.installer import download_model_async
    dr = download_model_async(entry["dir_name"], entry["files"])
    if not dr.get("started"):
        return {"success": False, "error": dr.get("error", "下载启动失败")}
    return {"success": True, "started": True, "state": "下载中",
            "total_gb": entry["size_gb"],
            "note": "下载约几分钟到几十分钟（视网速），期间可用 status 查进度"}


def _smoke(dir_name: str) -> dict:
    """冒烟：拿图库最新一张图让新大脑看一眼，读出内容=全链通。"""
    try:
        from src.core import vision_link
        img = None
        for p in sorted((_models_dir().parent.parent / "media" / "img_store")
                        .rglob("*.jpg"), key=lambda x: x.stat().st_mtime,
                        reverse=True):
            if "_thumb" in p.name or p.name.startswith("."):
                continue   # ★ 缩略图分辨率低，冒烟用原图
            img = p
            break
        if not img:
            return {"ok": False, "note": "图库没有测试图片，跳过冒烟（钉选已完成）"}
        t0 = time.time()
        ans = vision_link.understand_image(str(img), "用一句话描述这张图",
                                           timeout_s=120.0)
        return {"ok": bool(ans), "seconds": round(time.time() - t0, 1),
                "image": img.name, "answer": str(ans)[:160]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _finalize(entry) -> dict:
    """文件就绪后的收尾：钉选 + 冒烟（每模型只跑一次，结果缓存）。"""
    dir_name = entry["dir_name"]
    if dir_name in _finalized:
        return _finalized[dir_name]
    from src.core import vision_link
    pin_id = f"local:vision/{dir_name}"
    pr = vision_link.set_pin(pin_id)
    out = {"pinned": pin_id if pr.get("success") else "",
           "pin_error": pr.get("error", "")}
    if pr.get("success"):
        out["smoke"] = _smoke(dir_name)
    _finalized[dir_name] = out
    return out


def status(model_id: str = "") -> dict:
    """查安装进度；文件就绪且未收尾 → 自动钉选+冒烟。"""
    from src.vision import catalog
    if model_id:
        entry = catalog.get(model_id)
        if not entry:
            return {"success": False, "error": f"目录里没有 {model_id}"}
        entries = [entry]
    else:
        entries = [e for e in catalog.CATALOG if e.get("enabled")]
    out = {"success": True, "models": []}
    with _copy_lock:
        copy = dict(_copy_state)
    from src.vision.installer import progress as dl_progress
    dl = dl_progress()
    for e in entries:
        r = _dir_ready(e["dir_name"])
        item = {"id": e["id"], "label": e["label"], "files_ready": r["ready"],
                "size_gb": r["size_gb"]}
        if not r["ready"]:
            # 找到属于这个模型的进行中任务
            prefix = e["dir_name"]
            if copy["running"] and prefix in str(copy.get("file", "")):
                done, total = copy.get("done_bytes", 0), copy.get("total_bytes", 0)
                item.update({"state": "拷贝中",
                             "percent": round(done * 100 / total, 1) if total else 0,
                             "file": copy.get("file", "")})
            elif dl.get("running") and prefix in str(dl.get("file", "")):
                done, total = dl.get("done_bytes", 0), dl.get("total_bytes", 0)
                item.update({"state": "下载中",
                             "percent": round(done * 100 / total, 1) if total else 0,
                             "file": dl.get("file", "")})
            elif copy.get("phase") == "失败" and prefix in str(copy.get("file", "")):
                item.update({"state": "失败", "error": copy.get("error", "")})
            elif dl.get("phase") == "失败" and prefix in str(dl.get("file", "")):
                item.update({"state": "失败", "error": dl.get("error", "")})
            else:
                item.update({"state": "未安装"})
        else:
            fin = _finalize(e)
            item.update({"state": "已就绪", **fin})
        out["models"].append(item)
    return out


def vision_install_tool(action: str = "recommend", model_id: str = "",
                        confirmed: bool = False) -> dict:
    """FC 工具入口：recommend（查硬件荐档）/ install（confirmed=True 才放行）/ status。"""
    action = str(action or "recommend").strip().lower()
    if action == "recommend":
        return recommend()
    if action == "install":
        if not confirmed:
            return {"success": False,
                    "error": "必须先把推荐展示给用户、得到明确同意后才能 install(confirmed=True)"
                             "（铁律0：模型选择权永远在用户）"}
        return install(model_id, confirmed=True)
    if action == "status":
        return status(model_id)
    return {"success": False, "error": f"未知 action: {action}（recommend/install/status）"}
