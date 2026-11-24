# -*- coding: utf-8 -*-
"""视频速览组合打包执行器（官方组合第三号 · 2026-11-24 #1532）。

第一个用户侧示范组合：cv2 均匀抽帧 → dhash 帧间去重留关键帧 →
共享 Pipeline（HarvenEye 瀑布，skip_vlm 提速）逐关键帧识别 →
VLM 汇总成稿（跟用户所选视觉模型，铁律0：_camp_vision 选谁给谁）。

环节展开（配方单 video_brief 四环一一对应）：
  ① v_frames      cv2 均匀抽帧（缺省 8 帧，payload.max_frames 可调）
  ② v_keyframes   dhash 帧间去重（场景切换/静态镜头只剩关键帧）
  ③ v_recognize   共享 Pipeline 逐关键帧识别（vision_link 单例，防显存双载）
  ④ v_summarize   VLM 汇总成稿（识别标签+首末关键帧底图 → 大白话速览）

注意：抽帧临时文件落 data/chains/tmp/（跑完即清），不进图片库不进相册。
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

_TMP_DIR = Path(r"H:\HarvenAI\data\chains\tmp")
_PROMPT_HEAD = (
    "这是对一段视频的逐关键帧识别结果（JSON）+ 首末关键帧照片。"
    "请用大白话总结这段视频讲了什么（2~4 句），点出场景变化和主要对象，"
    "不要罗列术语，不要输出 JSON。")


def _extract_frames(video_path: str, max_frames: int) -> list[str]:
    """cv2 均匀抽帧，落临时 jpg，返回路径列表。"""
    import cv2
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"视频打不开: {video_path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if total <= 0:
        raise RuntimeError("视频读不出帧数（编码不支持或文件损坏）")
    n = min(max_frames, total)
    idxs = [round(i * (total - 1) / max(n - 1, 1)) for i in range(n)]
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, fi in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok:
            continue
        p = _TMP_DIR / f"{int(time.time()*1000)}_f{k:02d}.jpg"
        cv2.imwrite(str(p), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        paths.append(str(p))
    cap.release()
    if not paths:
        raise RuntimeError("一帧都没抽出来")
    return paths


def _dedup_frames(paths: list[str]) -> tuple[list[str], int]:
    """dhash 帧间去重：相邻近重复的帧只留第一张。返回 (关键帧, 去掉几张)。"""
    from src.vision.dedup import dhash, is_near_duplicate
    keep: list[str] = []
    last_h = None
    cut = 0
    for p in paths:
        try:
            h = dhash(p)
        except Exception:
            keep.append(p)
            continue
        if last_h is not None:
            try:
                if is_near_duplicate(h, last_h, threshold=6):
                    cut += 1
                    continue
            except Exception:
                pass
        keep.append(p)
        last_h = h
    return keep, cut


def _recognize_frames(paths: list[str], step_collect) -> list[dict]:
    """共享 Pipeline 逐关键帧识别（skip_vlm=True：VLM 留给汇总环一次成稿）。"""
    pipe = _get_shared_pipeline()
    out = []
    for p in paths:
        t = time.perf_counter()
        try:
            r = pipe.recognize(p, skip_vlm=True)
            ms = round((time.perf_counter() - t) * 1000, 1)
            out.append({"path": p, "ok": True, "elapsed_ms": ms,
                        "tags": r.get("tags") or r.get("labels") or [],
                        "caption": r.get("caption") or r.get("description") or "",
                        "dup": bool(r.get("duplicate"))})
        except Exception as e:
            out.append({"path": p, "ok": False, "elapsed_ms":
                        round((time.perf_counter() - t) * 1000, 1), "error": str(e)})
    return out


def _get_shared_pipeline():
    """与 orchestrator 同源：vision_link 全应用唯一 Pipeline 单例（防显存双载）。"""
    from .orchestrator import _get_shared_pipeline
    return _get_shared_pipeline()


def _summarize(keyframes: list[str], recs: list[dict], payload: dict) -> tuple[str, str, float]:
    """VLM 汇总成稿。返回 (成稿, 模型名, 耗时ms)。跟用户所选视觉模型（铁律0）。"""
    from src.core.panel_assist import _camp_vision_bg
    facts = [{"frame": i, "tags": r.get("tags") or [], "caption": r.get("caption") or ""}
             for i, r in enumerate(recs)]
    prompt = _PROMPT_HEAD + "\n逐帧识别结果：\n" + str(facts)
    b64 = ""
    if keyframes:
        first = Path(keyframes[0]).read_bytes()
        b64 = base64.b64encode(first).decode("ascii")
    t = time.perf_counter()
    text, model, err = _camp_vision_bg(prompt, b64, timeout=120)
    ms = round((time.perf_counter() - t) * 1000, 1)
    if err or not text:
        return "", model or "", ms
    return text.strip(), model, ms


def run_video_pipeline(payload: dict, run_id: str, step_collect) -> dict:
    video_path = payload.get("video_path") or payload.get("image_path") or ""
    if not video_path or not Path(video_path).exists():
        return {"ok": False, "error": f"视频不存在: {video_path}"}
    max_frames = int(payload.get("max_frames") or 8)

    # ① 抽帧
    t0 = time.perf_counter()
    try:
        frames = _extract_frames(video_path, max_frames)
    except Exception as e:
        step_collect({"step_id": "v_frames", "step_name": "抽帧", "ok": False,
                      "error": str(e), "elapsed_ms":
                          round((time.perf_counter() - t0) * 1000, 1)})
        return {"ok": False, "error": str(e)}
    step_collect({"step_id": "v_frames", "step_name": "均匀抽帧",
                  "model_id": "internal", "channel": "internal",
                  "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                  "ok": True, "meta": {"frames": len(frames), "max": max_frames}})

    # ② 关键帧去重
    t1 = time.perf_counter()
    try:
        keyframes, cut = _dedup_frames(frames)
    except Exception as e:
        step_collect({"step_id": "v_keyframes", "step_name": "关键帧去重", "ok": False,
                      "error": str(e), "elapsed_ms":
                          round((time.perf_counter() - t1) * 1000, 1)})
        return {"ok": False, "error": str(e)}
    step_collect({"step_id": "v_keyframes", "step_name": "dhash 帧间去重",
                  "model_id": "harven.base.dhash", "channel": "internal",
                  "elapsed_ms": round((time.perf_counter() - t1) * 1000, 1),
                  "ok": True, "meta": {"keyframes": len(keyframes), "cut": cut}})

    # ③ 逐关键帧识别
    recs = _recognize_frames(keyframes, step_collect)
    ok_recs = [r for r in recs if r["ok"]]
    step_collect({"step_id": "v_recognize", "step_name": "逐关键帧识别",
                  "model_id": "harveneye_pipeline", "channel": "internal",
                  "elapsed_ms": round(sum(r["elapsed_ms"] for r in recs), 1),
                  "ok": bool(ok_recs),
                  "error": "" if ok_recs else "所有关键帧识别失败",
                  "meta": {"frames_ok": len(ok_recs), "frames_total": len(recs)}})

    # ④ VLM 汇总成稿（跟用户所选视觉模型）
    summary, model, ms = "", "", 0.0
    if ok_recs and not payload.get("skip_summary"):
        summary, model, ms = _summarize(keyframes, ok_recs, payload)
        step_collect({"step_id": "v_summarize", "step_name": "VLM 汇总成稿",
                      "model_id": model or "vision(follow-user)", "channel": "vision",
                      "elapsed_ms": ms, "ok": bool(summary),
                      "error": "" if summary else "视觉模型没有返回",
                      "meta": {"summary_len": len(summary)}})
    elif payload.get("skip_summary"):
        step_collect({"step_id": "v_summarize", "step_name": "VLM 汇总成稿",
                      "model_id": "internal", "channel": "internal",
                      "elapsed_ms": 0, "ok": True, "meta": {"skipped": True}})

    # 临时帧清理（跑完即清，不进图片库）
    for p in frames:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception:
            pass

    total_ms = round(sum(r["elapsed_ms"] for r in recs) + ms, 1)
    result = {"video": video_path, "frames": len(frames), "keyframes": len(keyframes),
              "frames_detail": [{k: r[k] for k in ("tags", "caption", "ok", "elapsed_ms")
                                 if k in r} for r in recs],
              "summary": summary, "summary_model": model}
    if not summary and not payload.get("skip_summary"):
        # 成稿失败不算全链失败（识别结果仍是有效产出），如实注明
        result["note"] = "关键帧识别完成，但 VLM 汇总没有返回（可看逐帧标签）"
    return {"ok": bool(ok_recs), "result": result, "total_ms": total_ms}
