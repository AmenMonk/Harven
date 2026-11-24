# -*- coding: utf-8 -*-
"""三道门（骨架核心 · 设计稿 §8：错误组合活不过第三关）。

一·结构门：程序零 AI —— 前环输出类型 vs 后环输入类型；
二·能力门：查表零 AI —— 每环模型有没有登记该能力（fixed 查钉死模型，
   dynamic_follow 查当前所选+能力过滤）；
三·试跑门：实测零判断 —— 样例真跑全链，每环输出非空/格式合法/全程耗时。
三道门全过 + 用户亲眼看试跑结果确认，才允许「保存并选用」。
"""
from __future__ import annotations

import time
import uuid

from . import recipe as recipe_mod
from .registry import Registry

# 结构门类型相容表：后环输入 ← 可接受的前环输出
_COMPAT = {
    "image_path": {"image_path", "image", "any"},
    "image": {"image", "image_path", "any"},
    "boxes": {"boxes", "image", "image_path", "any"},
    "vector": {"vector", "any"},
    "text": {"text", "any"},
    "table_row": {"table_row", "text", "any"},
    "audio": {"audio", "any"},
    "video": {"video", "any"},
    "any": {"*"},
}


def gate_structure(r: recipe_mod.Recipe) -> dict:
    """一·结构门。返回 {passed, problems:[...]}。"""
    problems = []
    steps = [s for s in r.steps]
    for i in range(1, len(steps)):
        prev, cur = steps[i - 1], steps[i]
        allowed = _COMPAT.get(cur.input_type, {"any"})
        if "*" not in allowed and prev.output_type not in allowed:
            problems.append(
                f"第{i}环「{cur.name}」输入 {cur.input_type} 接不上第{i-1}环"
                f"「{prev.name}」输出 {prev.output_type}（结构门）")
    bad = r.validate()
    problems += bad
    return {"gate": "structure", "passed": not problems, "problems": problems}


def gate_capability(r: recipe_mod.Recipe, reg: Registry,
                    current_models: dict | None = None) -> dict:
    """二·能力门。current_models: {capability: model_id} 用户当前所选（dynamic 环解析用）。"""
    problems = []
    for i, s in enumerate(r.steps, 1):
        if s.engine == "program":
            continue
        if s.binding == recipe_mod.BIND_FIXED:
            model_id = s.model
            m = reg.find_model(model_id)
            if m is None:
                problems.append(f"第{i}环「{s.name}」钉死的模型 {model_id} 未登记/未安装"
                                f"（B10：缺模型如实报错，不偷换）")
            elif s.capability and s.capability not in (m.get("capabilities") or []):
                problems.append(f"第{i}环「{s.name}」要能力 {s.capability}，"
                                f"但 {model_id} 只登记了 {m.get('capabilities')}")
            else:
                for b in s.backup_models:      # 备选链也查（用户预先点头的才许留）
                    bm = reg.find_model(b)
                    if bm is None:
                        problems.append(f"第{i}环「{s.name}」备选 {b} 未登记")
                    elif s.capability and s.capability not in (bm.get("capabilities") or []):
                        problems.append(f"第{i}环「{s.name}」备选 {b} 不具备 {s.capability}")
        else:   # dynamic_follow：运行时跟当前所选；保存前先验能力过滤可满足
            cands = reg.models_with_capability(s.capability) if s.capability else []
            if not cands:
                problems.append(f"第{i}环「{s.name}」能力 {s.capability} 在本机/外脑清单中"
                                f"无任何胜任者（能力门）")
            cur = (current_models or {}).get(s.capability)
            if cur:
                cm = reg.find_model(cur)
                if cm is None or (s.capability and s.capability not in (cm.get("capabilities") or [])):
                    problems.append(f"第{i}环「{s.name}」当前所选 {cur} 不胜任 {s.capability}"
                                    f"（A1：如实提示，不偷换）")
    return {"gate": "capability", "passed": not problems, "problems": problems}


def gate_trial(r: recipe_mod.Recipe, run_fn, sample_payload: dict) -> dict:
    """三·试跑门。run_fn(payload)->{ok, steps:[{step_id,ok,elapsed_ms,output_summary}], error}。
    实测零判断：每环输出非空 + 全程跑通即过（质量好不好 AI 点评+用户眼睛说了算）。"""
    run_id = f"trial-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    try:
        out = run_fn(sample_payload, run_id=run_id)
    except Exception as e:
        return {"gate": "trial", "passed": False, "run_id": run_id,
                "problems": [f"试跑异常: {e}"]}
    problems = []
    for st in out.get("steps", []):
        if not st.get("ok") and not st.get("optional"):
            problems.append(f"第「{st.get('step_id')}」环失败: {st.get('error', '')}")
        elif st.get("ok") and st.get("empty_output"):
            problems.append(f"第「{st.get('step_id')}」环输出为空（试跑门：空转不放行）")
    total_ms = out.get("total_ms") or round((time.perf_counter() - t0) * 1000, 1)
    return {"gate": "trial", "passed": not problems and out.get("ok", False),
            "run_id": run_id, "total_ms": total_ms,
            "steps": out.get("steps", []), "problems": problems}


def run_all_gates(r: recipe_mod.Recipe, reg: Registry, run_fn=None,
                  sample_payload: dict | None = None,
                  current_models: dict | None = None) -> dict:
    """三道门连跑。run_fn 缺省时只跑前两道（草稿校验）。"""
    g1 = gate_structure(r)
    g2 = gate_capability(r, reg, current_models)
    out = {"structure": g1, "capability": g2,
           "passed": g1["passed"] and g2["passed"]}
    if run_fn is not None and sample_payload is not None:
        g3 = gate_trial(r, run_fn, sample_payload)
        out["trial"] = g3
        out["passed"] = out["passed"] and g3["passed"]
    return out
