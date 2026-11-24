# -*- coding: utf-8 -*-
"""账单链打包执行器（官方组合第二号 · 2026-11-24 #1531 迁移）。

搬家不装修（v1.9 迁移轨）：#1377 账单分析管线（bills_scan.py）原样复用——
本执行器只把 scan_bills 的四段流程按环展开计时进物流单，逻辑函数全部
调用 bills_scan / img_store / panel_manager 的公开函数，零复制零改写：
  ① collect  取未标记账单照片（img_store.unprocessed_bills）
  ② extract  逐张视觉抽取（bills_scan.extract_bill_records，_camp_vision 铁律0）
  ③ dedup    #913 查重（panel_manager.find_duplicate）
  ④ pending  待确认卡+照片级标记（panel_assist.add_pending + mark_bills_processed）

两种模式：
- dry（默认，工坊试跑用）：只跑 ①②③——真抽取真计时，但不写待确认卡
  不打标记，不污染真实账本（物流单如实记 skipped_dry）；
- 正式（payload.official=1，管家/心跳入口用）：四段全跑，与 scan_bills 同一落点。

并发安全：复用 bills_scan._scan_lock（与 A 按钮/C 心跳互斥，防双跑双标记）。
"""
from __future__ import annotations

import time


def run_bills_pipeline(payload: dict, run_id: str, step_collect) -> dict:
    from src.core import img_store
    from src.core.bills_scan import _scan_lock, extract_bill_records, _BATCH_LIMIT

    official = bool(payload.get("official"))
    dry = not official

    if not _scan_lock.acquire(blocking=False):
        return {"ok": False, "error": "上一次账单分析还在跑，等它完成再说"}

    try:
        # ── ① collect ──────────────────────────────────────────
        t0 = time.perf_counter()
        ids = payload.get("ids") or None
        if ids:
            _marked = set(img_store.unprocessed_bills(limit=5000))
            ids = [i for i in ids if i in _marked]   # 只认未标记（防重死命令）
        else:
            ids = img_store.unprocessed_bills(limit=_BATCH_LIMIT)
        step_collect({"step_id": "bills_collect", "step_name": "取待分析账单照片",
                      "model_id": "internal", "channel": "internal",
                      "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                      "ok": True, "meta": {"count": len(ids), "dry": dry}})
        if not ids:
            return {"ok": True,
                    "result": {"total": 0, "records": [], "message": "账单相册没有待分析的照片"},
                    "total_ms": 0.0}

        # ── ② extract（逐张视觉抽取，铁律0 选谁给谁）──────────────
        records: list[dict] = []
        failed: list[dict] = []
        per_photo_ms: list[float] = []
        models_used: set[str] = set()
        for img_id in ids:
            t1 = time.perf_counter()
            try:
                recs, model, err = extract_bill_records(img_id)
            except Exception as e:
                recs, model, err = [], "", str(e)
            ms = round((time.perf_counter() - t1) * 1000, 1)
            per_photo_ms.append(ms)
            if model:
                models_used.add(model)
            if err:
                failed.append({"id": img_id, "reason": err})
            step_collect({"step_id": "bills_extract", "step_name": "账单视觉抽取",
                          "model_id": model or "vision(follow-user)", "channel": "vision",
                          "elapsed_ms": ms, "ok": not err,
                          "error": err or "",
                          "meta": {"img_id": img_id, "records": len(recs), "dry": dry}})
            for d in recs:
                d.setdefault("来源", f"账单照片#{img_id}")
            records.extend(recs)

        # ── ③ dedup（#913 同日+同金额+近似名）────────────────────
        t2 = time.perf_counter()
        fresh: list[dict] = []
        dup_n = 0
        if records:
            from src.core.panel_manager import get_panel_manager
            pm = get_panel_manager()
            from src.core.bills_scan import PANEL_ID
            for d in records:
                try:
                    if pm.find_duplicate(PANEL_ID, d):
                        dup_n += 1
                        continue
                except Exception:
                    pass
                fresh.append(d)
        step_collect({"step_id": "bills_dedup", "step_name": "重复票查重（#913）",
                      "model_id": "internal", "channel": "internal",
                      "elapsed_ms": round((time.perf_counter() - t2) * 1000, 1),
                      "ok": True, "meta": {"dup": dup_n, "fresh": len(fresh)}})

        # ── ④ pending+mark（正式模式才落库；dry 只如实记录）─────────
        cards = 0
        skip_note = ""
        if dry:
            skip_note = "试跑 dry 模式：不写待确认卡不打标记（正式跑加 payload.official=1）"
            step_collect({"step_id": "bills_pending", "step_name": "待确认卡+照片标记",
                          "model_id": "internal", "channel": "internal",
                          "elapsed_ms": 0, "ok": True,
                          "meta": {"skipped_dry": True}})
        else:
            t3 = time.perf_counter()
            from src.core.panel_assist import add_pending
            from src.core.bills_scan import PANEL_ID as _PID
            from src.core import img_store as _is
            for img_id in ids:
                img_recs = [d for d in fresh if d.get("来源") == f"账单照片#{img_id}"]
                try:
                    if img_recs:
                        add_pending(_PID, '', img_recs,
                                    source_text=f'💰 账单照片#{img_id}（组合链分析）')
                        cards += 1
                    _is.mark_bills_processed([img_id],
                                             note=f'已分析(组合链)：{len(img_recs)}条入待确认')
                except Exception:
                    pass    # 卡没写成也不重扫（防重死命令同 scan_bills）
            step_collect({"step_id": "bills_pending", "step_name": "待确认卡+照片标记",
                          "model_id": "internal", "channel": "internal",
                          "elapsed_ms": round((time.perf_counter() - t3) * 1000, 1),
                          "ok": True, "meta": {"cards": cards, "marked": len(ids)}})

        total_ms = round(sum(per_photo_ms), 1)
        msg = (f"{len(ids)} 张账单照片抽取完成：{len(fresh)} 条有效"
               f"（{dup_n} 条重复丢弃，{len(failed)} 张没认出金额）")
        result = {"total": len(ids), "records": fresh, "dup": dup_n,
                  "failed": failed, "cards": cards, "message": msg,
                  "models": sorted(models_used), "dry": dry}
        if skip_note:
            result["note"] = skip_note
        return {"ok": True, "result": result, "total_ms": total_ms}
    finally:
        _scan_lock.release()
