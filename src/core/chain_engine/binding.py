# -*- coding: utf-8 -*-
"""组合绑定落点（设计稿 §5「组合被谁选用」· 2026-11-24 #1534）。

组合即模型：每个启用的组合以「动态垂直模型」身份出现在垂直模型页，
统一走 /api/vertical/execute 执行（engine='chain' 分支）——组合的第四落点。

三件职责：
1. chain_entries()      统一能力查询（registry.capability_entries 的 JSON 版，
                        chain: 前缀一等条目，消费方查"有什么能力可用"用）；
2. sync_vertical_binding()  组合 → vertical_api._dynamic_models 双向对账
                        （启用的注册/停用的移除，幂等可反复调）；
3. execute_chain_model()    垂直执行入口 → 编排器 run → 输出按 VerticalPage
                        契约包成 {success, data:{output, ...}}，B1 身份标注带上。

铁律0：组合内部模型选择全在配方单/编排器里定（fixed 钉死 / dynamic 跟用户
所选），这里只做搬运，不碰模型决策。
"""
from __future__ import annotations

import base64
import logging
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CHAIN_ENGINE = "chain"
_INPUT_TYPE_PAGE = {"image_path": "vision", "image": "vision", "video": "vision",
                    "audio": "voice", "text": "text", "any": "vision"}


def chain_entries() -> list[dict]:
    """统一能力查询：模型能力 + 组合能力（chain: 前缀一等条目）。"""
    return get_registry().capability_entries()


# ══ 组合 → 垂直模型页（动态适配器）════════════════════════════
def sync_vertical_binding() -> dict:
    """把启用的组合注册成垂直动态模型；停用/卸载的对账移除。幂等。

    #1537c 收紧：meta.user_facing=False 的后台链（如账单链，由相册按钮/心跳
    触发）不注册身份证——垂直页只留用户侧组合，不再一堆重复条目。"""
    from src.core import vertical_api as va
    reg = get_registry()
    registered, removed = 0, 0
    # 该有户口的集合（启用 + 用户侧）；不在集合里的 chain:* 一律对账移除
    reg_ids = {f"chain:{c.id}" for c in reg.list_chains()
               if c.enabled and c.meta.get("user_facing", True)}
    for mid in list(va._dynamic_models.keys()):
        if mid.startswith("chain:") and mid not in reg_ids:
            va._dynamic_models.pop(mid, None)
            removed += 1
    for c in reg.list_chains():
        if not c.enabled or not c.meta.get("user_facing", True):
            continue
        mid = f"chain:{c.id}"
        caps = sorted(c.capabilities())
        page_type = _INPUT_TYPE_PAGE.get(c.input_type, "vision")
        va._dynamic_models[mid] = {
            "id": mid,
            "name": f"🧩 {c.name}",
            "icon": "🧩",
            "type": page_type,
            "canvas_type": "generic",
            # ★ #1537c 说明文案：讲清这是流水线 + 日常已自动调用，别让用户摸不着头脑
            "description": f"[组合] {c.target or c.name}\n"
                           f"💡 这是多环节自动接力的流水线，不是聊天模型——"
                           f"日常场景（拍照打标、账单分析等）已自动调用它，聊天框不用选；"
                           f"想手动跑一次时，在这里附上图片/视频点执行。",
            "engine": _CHAIN_ENGINE,
            "chain_id": c.id,
            "input_type": c.input_type,
            "capabilities": caps,
            "params": {
                "text": {"type": "textarea", "label": "补充说明（可选）",
                         "placeholder": "想重点看什么/额外要求，留空也行"},
            },
            "capability": {
                "can_do": [f"组合链：{c.target or c.name}（{'→'.join(s.name for s in c.steps)}）"],
                "cannot_do": ["超出组合目标描述的事——那是聊天框/其他模型的事"],
            },
        }
        registered += 1
    if registered or removed:
        logger.info(f"[ChainBinding] 组合绑定对账：注册 {registered}，移除 {removed}")
    return {"registered": registered, "removed": removed}


# ══ 垂直执行入口（/api/vertical/execute engine='chain' 分支）════
def execute_chain_model(model_meta: dict, params: dict,
                        attachments: list | None = None) -> dict:
    """垂直模型页执行组合：装配 payload → 编排器 → VerticalPage 输出契约。"""
    from .orchestrator import get_orchestrator
    chain_id = model_meta.get("chain_id") or ""
    payload: dict = {}
    for k, v in (params or {}).items():
        if isinstance(v, str) and v.strip():
            payload[k] = v.strip()
    text = payload.pop("text", "")

    # 附件装配：图片→image_path 临时文件；视频→video_path；音频→audio_path
    for att in (attachments or []):
        if not isinstance(att, dict):
            continue
        dataurl = att.get("dataUrl") or att.get("data") or ""
        if not dataurl:
            continue
        suffix = Path(att.get("name") or "file").suffix.lower() or ".bin"
        mime = (att.get("type") or att.get("mime") or "").lower()
        try:
            raw = _dataurl_bytes(dataurl)
        except Exception as ex:
            return {"success": False, "data": {"error": f"附件读不出来: {ex}"}}
        fd, tmp = tempfile.mkstemp(suffix=suffix or ".bin")
        import os
        os.close(fd)
        Path(tmp).write_bytes(raw)
        if suffix in (".jpg", ".jpeg", ".png", ".webp", ".bmp") or mime.startswith("image/"):
            payload.setdefault("image_path", tmp)
        elif suffix in (".mp4", ".mov", ".avi", ".mkv", ".webm") or mime.startswith("video/"):
            payload.setdefault("video_path", tmp)
        elif suffix in (".wav", ".mp3", ".m4a", ".webm") or mime.startswith("audio/"):
            payload.setdefault("audio_path", tmp)

    t0 = time.perf_counter()
    out = get_orchestrator().run(chain_id, payload)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if not out.get("ok"):
        return {"success": False, "data": {"error": out.get("error") or "组合执行失败"}}

    result = out.get("result")
    output = _humanize(result)
    data = {"output": output, "elapsed_ms": ms,
            "label": out.get("label"), "chain": chain_id}
    # 视频速览等组合的关键帧细节随附（JSON 折叠在 output 之后由用户展开看）
    if isinstance(result, dict) and result.get("frames_detail") is not None:
        data["frames_detail"] = result["frames_detail"]
    note = ""
    if text:
        note = f"（补充说明「{text[:40]}」已随链执行）"
    if note:
        data["note"] = note
    return {"success": True, "data": data}


def _dataurl_bytes(dataurl: str) -> bytes:
    s = dataurl.strip()
    if s.startswith("data:"):
        _, _, b64 = s.partition(",")
        return base64.b64decode(b64)
    return base64.b64decode(s)


def _humanize(result) -> str:
    """组合结果 → 大白话文本（VerticalPage r-text 直接展示）。"""
    if result is None:
        return "（组合跑完了，但没有产出内容）"
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        # 各官方组合的成稿字段优先
        for k in ("summary", "caption", "description", "answer", "text", "message"):
            v = result.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # 账单链的记录列表
        recs = result.get("records")
        if isinstance(recs, list) and recs:
            lines = [f"· {r.get('项目', '?')} {r.get('金额', '?')}元（{r.get('分类', '')}"
                     f"{'，' + r.get('日期') if r.get('日期') else ''}）" for r in recs[:20]]
            head = result.get("message") or f"识别出 {len(recs)} 条记录："
            return str(head) + "\n" + "\n".join(lines)
        # HarvenEye 识别 dict：trace 各层挖成稿（#1537c：VLM 描述直接端出来，
        # 不再让用户看到「详情看工坊试跑页」这种没头没脑的话）
        if result.get("trace"):
            tr = result.get("trace")
            if isinstance(tr, list):
                # 1) 从后往前找理解环/VLM 描述（L3 成稿最有内容）
                for layer in reversed(tr):
                    if isinstance(layer, dict):
                        d = layer.get("description")
                        if isinstance(d, str) and d.strip():
                            src = layer.get("source_layer") or ""
                            head = f"【{src} 理解】" if src else ""
                            return f"{head}{d.strip()}"
                # 2) 没有描述 → 报检测到的对象清单（L1 目标检测）
                for layer in tr:
                    if isinstance(layer, dict):
                        dets = layer.get("detections") or []
                        names = [d.get("name") or d.get("label") or d.get("cls")
                                 for d in dets if isinstance(d, dict)]
                        names = [str(n) for n in names if n]
                        if names:
                            src = layer.get("source_layer") or "检测"
                            return f"检测到 {len(names)} 个对象：{'、'.join(names[:12])}（{src}）"
                # 3) 只有语义检索线索（L2 相似图）
                for layer in tr:
                    if isinstance(layer, dict):
                        sim = layer.get("similar")
                        if isinstance(sim, list) and sim:
                            top = sim[0] if isinstance(sim[0], dict) else {}
                            pct = round(float(top.get("similarity", 0)) * 100)
                            return f"图库里有 {len(sim)} 张相似照片（最像 {pct}%），已按图库路由入库"
            tags = result.get("tags") or result.get("labels") or []
            if tags:
                return "识别结果：" + "、".join(map(str, tags))
            note = result.get("note")
            if isinstance(note, str) and note.strip():
                return f"识别完成：{note.strip()}"
            return "识别完成（本次没有可成稿的内容），物流单里有完整环节记录"
    return "（组合产出见工坊试跑页/物流单——此结果没有文本成稿）"


# 延迟导入（避免循环）
def get_registry():
    from .registry import get_registry as _g
    return _g()
