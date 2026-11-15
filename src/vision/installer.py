# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""视觉引擎安装器（v1.3 节点④后端）— yolox + chinese-clip 底座一键下载。

铁律：
  - 宪法 v2（2026-11-01 修订，定版拍板）：轻量底座（yolox 35MB + chinese-clip 753MB）
    随安装包内置（r44 起）——公益镜像下载易失败，装完零下载；本下载逻辑保留作兜底
    （老版本包/文件缺失时仍可补下）。通用大模型（qwen3-vl 等 3.1~18.4GB）仍绝不进包，
    按显存推荐、用户点头才下载（catalog.py + FC confirmed 门禁）
  - AGPL 禁用：YOLOX（Apache 2.0, Megvii）+ Chinese-CLIP（Apache 2.0, OFA-Sys）
  - 下载源双保险：官方源 + ghfast 镜像（GitHub 系自动加前缀，hf 系走 hf-mirror）
  - 断点续传：Range 接续 + 完成后 SHA256 对账，不一致即删重下
  - 进度内存态（哈文重启即失；下载中断重按即可续）
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path

import requests

from . import config

logger = logging.getLogger(__name__)

_GH = "https://github.com"
_GH_MIRROR = "https://ghfast.top/https://github.com"
_HF = "https://hf-mirror.com"   # HuggingFace 国内镜像（HF 官方域名本机常不可达）

# 底座引擎清单（v1.3 初级版固定：桌面档 yolox_s + chinese-clip vit-base-patch16）
# SHA256 与 Eye 发版 MANIFEST.txt 一致（v2, 2026-10-26）
ENGINE_MANIFEST: dict[str, list[dict]] = {
    "yolox": [
        {"rel": "yolox/yolox_s.onnx", "size": 35_858_002,
         "sha256": "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
         "urls": [f"{_GH}/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx"]},
    ],
    "chinese-clip": [
        {"rel": "chinese-clip/pytorch_model.bin", "size": 753_177_983,
         "sha256": "7b7b583c210c867410bc6bdb8a55fe14eec62999e0a9ea31ff222dc501f9cfbe",
         "urls": [f"{_HF}/OFA-Sys/chinese-clip-vit-base-patch16/resolve/main/pytorch_model.bin"]},
        {"rel": "chinese-clip/config.json", "size": 0, "sha256": "",
         "urls": [f"{_HF}/OFA-Sys/chinese-clip-vit-base-patch16/resolve/main/config.json"]},
        {"rel": "chinese-clip/preprocessor_config.json", "size": 0, "sha256": "",
         "urls": [f"{_HF}/OFA-Sys/chinese-clip-vit-base-patch16/resolve/main/preprocessor_config.json"]},
        {"rel": "chinese-clip/vocab.txt", "size": 0, "sha256": "",
         "urls": [f"{_HF}/OFA-Sys/chinese-clip-vit-base-patch16/resolve/main/vocab.txt"]},
    ],
    # ★ 2026-11-12 能力五件套④：活体检测双模型（Apache-2.0，yakhyo/face-anti-spoofing
    #   release 权重；SHA256 与其发布清单+arandu-liveness manifest 双重比对一致）
    "alive": [
        {"rel": "alive/MiniFASNetV2.onnx", "size": 1_743_581,
         "sha256": "b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907",
         "urls": [f"{_GH}/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx"]},
        {"rel": "alive/MiniFASNetV1SE.onnx", "size": 1_742_335,
         "sha256": "ebab7f90c7833fbccd46d3a555410e78d969db5438e169b6524be444862b3676",
         "urls": [f"{_GH}/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV1SE.onnx"]},
    ],
}

_state_lock = threading.Lock()
_state: dict = {"running": False, "phase": "", "file": "", "done_bytes": 0,
                "total_bytes": 0, "error": "", "finished_at": 0.0}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False   # 回环/镜像直连，不吃系统代理（eye_client 同款纪律）
    s.headers["User-Agent"] = "HarvenAI-VisionInstaller/1.0"
    return s


def engine_status() -> dict:
    """底座引擎文件就绪状态（面板状态卡用）。
    ★ alive（活体检测）是可选能力：单独报状态，不计入整体 ready——
      缺它不影响 yolox/clip 主链（2026-11-12 能力五件套口径）。"""
    out = {"groups": [], "ready": True}
    for group, files in ENGINE_MANIFEST.items():
        items = []
        g_ready = True
        for f in files:
            p = Path(config.MODELS_DIR) / f["rel"]
            ok = p.exists() and (not f["size"] or p.stat().st_size == f["size"])
            g_ready = g_ready and ok
            items.append({"rel": f["rel"], "present": ok,
                          "size": f["size"] or (p.stat().st_size if p.exists() else 0)})
        out["groups"].append({"name": group, "ready": g_ready, "files": items})
        if group != "alive":
            out["ready"] = out["ready"] and g_ready
    return out


def progress() -> dict:
    with _state_lock:
        return dict(_state)


def _emit(**kw) -> None:
    with _state_lock:
        _state.update(kw)


def _fetch_one(sess: requests.Session, f: dict, dest: Path) -> None:
    """单文件下载：多源轮询 + Range 续传 + SHA256 对账。"""
    dest.parent.mkdir(parents=True, exist_ok=True)   # ★ 先建目录再开 .part（裸机首装必踩）
    tmp = dest.with_suffix(dest.suffix + ".part")
    expect_sha = f.get("sha256", "")
    last_err = ""
    for url in f["urls"]:
        try:
            for attempt in range(6):
                done = tmp.stat().st_size if tmp.exists() else 0
                headers = {"Range": f"bytes={done}-"} if done else {}
                r = sess.get(url, headers=headers, stream=True, timeout=(10, 60),
                             allow_redirects=True)
                if r.status_code == 416:   # 已下完（Range 越界）
                    break
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0)) + done
                mode = "ab" if done else "wb"
                with open(tmp, mode) as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                        done += len(chunk)
                        _emit(done_bytes=done, total_bytes=total or f.get("size", 0))
                if total and done < total:
                    continue   # 截断，续传重试
                break
            # 完整性校验
            if expect_sha:
                h = hashlib.sha256()
                with open(tmp, "rb") as fh:
                    for b in iter(lambda: fh.read(1 << 22), b""):
                        h.update(b)
                if h.hexdigest() != expect_sha:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(f"SHA256 校验不符（{url[:60]}...），已删除重下")
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(dest)
            return
        except Exception as e:
            last_err = str(e)
            logger.warning(f"[VisionInstall] 源失败 {url[:70]}: {e}")
            continue
    raise RuntimeError(f"全部下载源失败（{f['rel']}）: {last_err}")


def install_engine_async() -> dict:
    """后台线程安装底座引擎。已在跑则直接返回进度（幂等防重复按下）。"""
    with _state_lock:
        if _state["running"]:
            return {"started": False, **dict(_state)}
        _state.update({"running": True, "phase": "准备", "error": "",
                       "done_bytes": 0, "total_bytes": 0, "finished_at": 0.0})

    def _job():
        sess = _session()
        try:
            all_files = [f for files in ENGINE_MANIFEST.values() for f in files]
            total = sum(f["size"] for f in all_files if f["size"])
            _emit(phase="下载中", total_bytes=total)
            t0 = time.time()
            for f in all_files:
                dest = Path(config.MODELS_DIR) / f["rel"]
                if dest.exists() and (not f["size"] or dest.stat().st_size == f["size"]):
                    continue   # 已就位跳过（幂等）
                _emit(file=f["rel"])
                _fetch_one(sess, f, dest)
            _emit(phase="完成", running=False, finished_at=time.time())
            logger.info(f"[VisionInstall] 底座引擎安装完成（{time.time()-t0:.0f}s）")
        except Exception as e:
            logger.error(f"[VisionInstall] 安装失败: {e}")
            _emit(phase="失败", error=str(e)[:200], running=False, finished_at=time.time())

    threading.Thread(target=_job, name="vision-install", daemon=True).start()
    return {"started": True, **progress()}


# ══ ★ #1470 人脸轻量档模型（2026-11-12，批次四）════════════════════
# 独立于底座 manifest：未开启人脸功能的用户 engine_status() 不受影响（ready 不含 face 组）。
# SHA256 开发机 ghfast 镜像实测（2026-11-12，与 OpenCV Zoo 官方 ONNX 一致）。
_FACE_MANIFEST: list[dict] = [
    {"rel": "face/face_detection_yunet_2023mar.onnx", "size": 232_589,
     "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
     "urls": [f"{_GH}/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
              f"{_GH_MIRROR}/https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"]},
    {"rel": "face/face_recognition_sface_2021dec.onnx", "size": 38_696_353,
     "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
     "urls": [f"{_GH}/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
              f"{_GH_MIRROR}/https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"]},
]


def face_models_status() -> dict:
    """人脸模型就绪状态（总 37MB：YuNet 227KB + SFace 37MB）。"""
    files = []
    ready = True
    for f in _FACE_MANIFEST:
        p = Path(config.MODELS_DIR) / f["rel"]
        ok = p.exists() and p.stat().st_size == f["size"]
        ready = ready and ok
        files.append({"rel": f["rel"], "present": ok, "size": f["size"]})
    return {"ready": ready, "total_bytes": sum(f["size"] for f in _FACE_MANIFEST), "files": files}


def install_face_async() -> dict:
    """后台下载人脸轻量档模型（YuNet+SFace，37MB）。复用底座下载机件与进度态。"""
    with _state_lock:
        if _state["running"]:
            return {"started": False, **dict(_state), "error": "已有下载任务在跑，稍候再试"}
        _state.update({"running": True, "phase": "准备", "error": "",
                       "done_bytes": 0, "total_bytes": sum(f["size"] for f in _FACE_MANIFEST),
                       "finished_at": 0.0})

    def _job():
        sess = _session()
        try:
            _emit(phase="下载中")
            for f in _FACE_MANIFEST:
                dest = Path(config.MODELS_DIR) / f["rel"]
                if dest.exists() and dest.stat().st_size == f["size"]:
                    continue
                _emit(file=f["rel"])
                _fetch_one(sess, f, dest)
            _emit(phase="完成", running=False, finished_at=time.time())
            logger.info("[VisionInstall] 人脸模型（YuNet+SFace）下载完成")
        except Exception as e:
            logger.error(f"[VisionInstall] 人脸模型下载失败: {e}")
            _emit(phase="失败", error=str(e)[:200], running=False, finished_at=time.time())

    threading.Thread(target=_job, name="vision-face-dl", daemon=True).start()
    return {"started": True, **progress()}


# ══ ⑤ 模型管家·在线下载器（v1.3，2026-10-29）══════════════════════
# 用户在面板选模型/给 URL → 哈文自己下载装进 data/models/vision/<名>/
# 铁律：哈文不内置不代选任何通用模型——下什么、何时下，用户说了算。

_SAFE_NAME = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ ")


def _infer_filename(url: str, idx: int) -> str:
    """从 URL 推断落盘文件名（去 query；推断不出用 file_<i>.bin）。"""
    tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return tail if tail and "." in tail else f"file_{idx}.bin"


def download_model_async(name: str, files: list[dict]) -> dict:
    """后台线程下载通用模型（GGUF+mmproj 等）到 data/models/vision/<name>/。
    files: [{url 或 urls:[多源列表], sha256?, size?, filename?}, ...]；
    ★ #1292 兼容 catalog 的 urls 多源格式（主源在前，失败自动轮询备用源）。
    与底座安装共用进度状态（防并发互踩）。完成/失败均不影响现有引擎；
    落位即被 list_local_vision_models 自动发现。"""
    name = str(name or "").strip()
    if not name or not set(name) <= _SAFE_NAME:
        return {"started": False, "error": "模型名只允许字母/数字/中横线/下划线/空格"}
    if not isinstance(files, list) or not files:
        return {"started": False, "error": "files 不能为空（至少一个 url）"}
    specs = []
    for i, f in enumerate(files):
        urls = [str(u).strip() for u in ((f or {}).get("urls") or []) if str(u).strip()]
        if not urls:
            u0 = str((f or {}).get("url", "")).strip()
            urls = [u0] if u0 else []
        if not urls or not urls[0].startswith(("http://", "https://")):
            return {"started": False, "error": f"第{i+1}个 url 非法"}
        specs.append({"rel": f"{name}/{(f or {}).get('filename') or _infer_filename(urls[0], i)}",
                      "sha256": str((f or {}).get("sha256", "")).strip().lower(),
                      "size": int((f or {}).get("size") or 0), "urls": urls})

    with _state_lock:
        if _state["running"]:
            return {"started": False, **dict(_state), "error": "已有下载任务在跑（底座或模型），稍候再试"}
        _state.update({"running": True, "phase": "准备", "error": "",
                       "done_bytes": 0, "total_bytes": 0, "finished_at": 0.0})

    def _job():
        sess = _session()
        try:
            total = sum(f["size"] for f in specs if f["size"])
            _emit(phase="下载中", total_bytes=total, file=specs[0]["rel"])
            for f in specs:
                dest = Path(config.MODELS_DIR) / f["rel"]
                if dest.exists():
                    continue   # 幂等：同名文件已落位跳过
                _emit(file=f["rel"])
                _fetch_one(sess, f, dest)
            _emit(phase="完成", running=False, finished_at=time.time())
            logger.info(f"[VisionInstall] 模型 {name} 下载完成")
        except Exception as e:
            logger.error(f"[VisionInstall] 模型 {name} 下载失败: {e}")
            _emit(phase="失败", error=str(e)[:200], running=False, finished_at=time.time())

    threading.Thread(target=_job, name="vision-model-dl", daemon=True).start()
    return {"started": True, **progress()}
