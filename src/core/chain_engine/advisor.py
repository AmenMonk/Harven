# -*- coding: utf-8 -*-
"""AI 参谋（2026-11-24 全量开发 #1530）。

职责边界（设计稿 §9 定版）：
- 参谋只动嘴不动手：读配方单 + 物流单事实 → 给优化建议/回答提问；
  **绝不直接改配方单**（改要走工坊编辑器+三道门）。
- 运行时零 AI 铁律不变：本模块只在工坊页被用户主动点名时才调用，
  组合链路运行全程不经过这里。
- 铁律0（10-25 老谭死命令）：说话用**用户所选的模型**——
  MultiModelManager.generate_response(model=None) 即跟 current_model，
  外脑=multi_models.json current_model / 本地=用户选的主模型，绝不偷换兜底。
  用户所选模型没能力（如不支持复杂推理）→ 如实报错，不静默换模型。

提示词标准（定版）：
  系统 = 参谋角色 + 三条纪律（只建议/引用数字/尊重用户模型选择）；
  事实 = 配方单结构 + 物流单聚合（逐环成功率/耗时/模型/瓶颈）+ 三道门结论；
  要求 = 结论先行，建议可执行（具体到改哪环哪个字段），不输出代码。
"""
from __future__ import annotations

import json
import logging
import time

from .logistics import get_logistics
from .recipe import Recipe
from .registry import get_registry

logger = logging.getLogger(__name__)

_SAMPLE_ROOT_HINT = r"H:\HarvenAI\data\chains\samples"


# ══ 事实收集 ═════════════════════════════════════════════════
def collect_facts(recipe: Recipe, max_runs: int = 20) -> dict:
    """物流单事实聚合：逐环 成功率/平均耗时/最大耗时/用过的模型 + 最近运行概况。"""
    lg = get_logistics()
    rows = lg.query(recipe.id, limit=400)
    by_step: dict[str, dict] = {}
    runs: dict[str, dict] = {}
    for x in rows:
        sid = x.get("step_id") or "?"
        ok = bool(x.get("ok", True))
        ms = float(x.get("elapsed_ms") or 0)
        st = by_step.setdefault(sid, {
            "step_id": sid, "step_name": x.get("step_name") or sid,
            "n": 0, "fail": 0, "ms_sum": 0.0, "ms_max": 0.0,
            "models": set(), "last_error": "",
        })
        st["n"] += 1
        st["fail"] += 0 if ok else 1
        st["ms_sum"] += ms
        st["ms_max"] = max(st["ms_max"], ms)
        if x.get("model_id"):
            st["models"].add(x["model_id"])
        if not ok and x.get("error"):
            st["last_error"] = (x["error"] or "")[:120]
        rid = x.get("run_id")
        if rid:
            rr = runs.setdefault(rid, {"ts": x.get("ts"), "total_ms": 0.0, "all_ok": True})
            rr["total_ms"] += ms
            rr["all_ok"] = rr["all_ok"] and ok
            rr["ts"] = max(rr["ts"] or "", x.get("ts") or "")

    steps_facts = []
    for sid in [s.id for s in recipe.steps]:
        st = by_step.get(sid)
        if not st:
            steps_facts.append({"step_id": sid, "n": 0, "note": "无运行记录"})
            continue
        steps_facts.append({
            "step_id": sid, "step_name": st["step_name"], "runs": st["n"],
            "fail": st["fail"],
            "success_rate": round(1 - st["fail"] / max(st["n"], 1), 3),
            "avg_ms": round(st["ms_sum"] / max(st["n"], 1), 1),
            "max_ms": round(st["ms_max"], 1),
            "models": sorted(st["models"]),
            "last_error": st["last_error"] or None,
        })
    recent_runs = sorted(runs.values(), key=lambda r: r["ts"] or "", reverse=True)[:max_runs]
    facts = {
        "recipe": {
            "id": recipe.id, "name": recipe.name, "version": recipe.version,
            "target": recipe.target, "origin": recipe.origin,
            "on_step_fail": recipe.on_step_fail,
            "steps": [{
                "id": s.id, "name": s.name, "capability": s.capability,
                "model": s.model, "binding": s.binding, "engine": s.engine,
                "damage": s.damage, "input": s.input_type, "output": s.output_type,
                "optional": s.optional,
            } for s in recipe.steps],
        },
        "step_stats": steps_facts,
        "recent_runs": [{"at": r["ts"], "total_ms": round(r["total_ms"], 1),
                         "all_ok": r["all_ok"]} for r in recent_runs],
        "sample_dir": _SAMPLE_ROOT_HINT,
    }
    return facts


# ══ 跟用户所选模型（铁律0）═════════════════════════════════════
def _ask_current_model(messages: list[dict]) -> tuple[bool, str, str]:
    """一次性调用用户当前所选模型（外脑 current_model / 本地主模型）。
    返回 (ok, text, model_id)。失败如实报错——绝不偷换模型兜底。"""
    from src.core.multi_model_manager import get_multi_model_manager
    mm = get_multi_model_manager()
    model_id = getattr(mm, "current_model", "") or ""
    if not model_id:
        return False, "", "当前没有已选模型：请到设置页模型 tab 选定后再用 AI 参谋"
    full = ""
    try:
        gen = mm.generate_response(messages, stream=False)
        for chunk in gen:
            if isinstance(chunk, dict):
                if chunk.get("type") in ("text", "content"):
                    full += chunk.get("content", "") or ""
                elif chunk.get("type") == "error":
                    return False, "", f"所选模型 {model_id} 调用失败: {chunk.get('content', '')}"
    except Exception as e:
        return False, "", f"所选模型 {model_id} 调用异常: {e}"
    if not full.strip():
        return False, "", f"所选模型 {model_id} 返回为空"
    return True, full.strip(), model_id


# ══ 提示词标准 ═══════════════════════════════════════════════
_SYSTEM_PROMPT = (
    "你是「模型组合工坊」的 AI 参谋。用户在本机组装视觉/语音/视频多模型组合链，"
    "你根据配方单结构和物流单运行事实给优化建议。三条纪律：\n"
    "1. 只给建议，绝不说『我已帮你改好』——修改必须在工坊编辑器里由用户动手，"
    "保存时还有三道门把关；\n"
    "2. 每条建议必须引用事实数字（哪一环、成功率、耗时、报错原文），不许凭空猜；\n"
    "3. 不输出任何代码，建议落到具体字段（如『第2环 model 改为 xxx』"
    "『第3环 binding 从 fixed 改为 dynamic_follow』『第4环 damage 降到 observe』）。\n"
    "回答格式：先用一句话总结，再列 2~4 条编号建议（引用数字），"
    "没有可优化点就直说『当前结构已合理』，不许编问题。"
)


def _user_prompt(facts: dict, question: str | None) -> str:
    head = "这是组合的配方单与物流单事实（JSON）："
    q = f"\n\n用户的具体问题：{question}" if question else \
        "\n\n（用户没有具体问题，请基于事实给整体优化建议；没有可优化点就直说）"
    return f"{head}\n```json\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n```{q}"


# ══ 对外主入口 ═══════════════════════════════════════════════
def advise(recipe_id: str, question: str | None = None) -> dict:
    """AI 参谋：配方单 + 物流单事实 → 建议。跟用户所选模型（铁律0）。"""
    reg = get_registry()
    r = reg.get_chain(recipe_id)
    if r is None:
        return {"ok": False, "error": f"组合不存在: {recipe_id}"}
    facts = collect_facts(r)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(facts, (question or "").strip() or None)},
    ]
    t0 = time.perf_counter()
    ok, text, model_id = _ask_current_model(messages)
    return {"ok": ok, "advice": text, "error": None if ok else text,
            "model_used": model_id, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "facts": facts}


# ══ 说话级入口：大白话 → 配方单草稿（#1533）═════════════════════
_DRAFT_SCHEMA = (
    "recipe 的合法结构（严格遵守，多余字段去掉）：\n"
    '{"id": "英文小写短横线id", "name": "中文名", "version": "0.1.0", '
    '"target": "一句话目标描述", "origin": "draft", '
    '"input_type": "image_path|image|video|audio|text|any", '
    '"output_type": "text|table_row|boxes|any", '
    '"on_step_fail": "abort|skip_optional|use_backup", "steps": [...]}，\n'
    "每步：{\"id\": \"英文id\", \"name\": \"中文名\", \"capability\": \"能力名(见盘点)\", "
    "\"binding\": \"fixed|dynamic_follow\", \"model\": \"fixed时填盘点里的模型id，dynamic留空\", "
    "\"engine\": \"internal|program\", \"input_type\": \"...\", \"output_type\": \"...\", "
    "\"damage\": \"readonly|db|action\", \"optional\": false, \"note\": \"一句话\"}。\n"
    "硬规矩：\n"
    "1. capability 只能从盘点清单里选，编造能力=保存过不了能力门；\n"
    "2. binding=fixed 时 model 必须是盘点里真实存在的模型 id（底座 harven.base.* 除外但只用于内置组合，"
    "用户草稿别用底座）；看不懂画面/需要语言理解的环节一律 dynamic_follow（跟用户所选模型）；\n"
    "3. 相邻环节 output_type→input_type 必须衔接得上（结构门）；\n"
    "4. 步数 2~5 步，宁简勿繁；用户需求若是现成组合改一改能干，直接在 mapping 里建议 fork 该组合。"
)

_DRAFT_SYSTEM = (
    "你是「模型组合工坊」的装配参谋。用户说一句大白话需求，你把它翻译成模型组合"
    "配方单草稿（JSON）+ 需求↔环节对照表。草稿只是草稿：用户还要在编辑器里过目修改，"
    "保存时三道门（结构/能力/试跑）会把关，所以你不用追求完美，但绝不允许编造"
    "不存在的模型或能力。只输出一个 JSON 对象，不要解释不要代码块标记：\n"
    '{"recipe": {...}, "mapping": [{"need": "用户需求的一点", "step": "对应环节名或fork建议"}]}\n'
    + _DRAFT_SCHEMA)


def draft(intent: str) -> dict:
    """说话级入口：大白话需求 → 配方单草稿 + 需求↔环节对照表。
    跟用户所选模型（铁律0）。产出的 origin=draft，落盘前必须过三道门。"""
    intent = (intent or "").strip()
    if not intent:
        return {"ok": False, "error": "需求描述是空的"}
    reg = get_registry()
    models = []
    for m in reg.list_models(force=False):
        if m.get("builtin"):
            continue        # 底座内部件不进草稿盘点（用户草稿不用底座）
        models.append({"id": m.get("id"), "name": m.get("label"),
                       "capabilities": m.get("capabilities") or [],
                       "channel": m.get("channel"), "installed": m.get("installed")})
    chains = [{"id": c.id, "name": c.name, "target": (c.target or "")[:60],
               "capabilities": sorted(c.capabilities())}
              for c in reg.list_chains()]
    inventory = json.dumps({"本机模型盘点": models, "现有组合(可建议fork)": chains},
                           ensure_ascii=False, indent=1)
    messages = [
        {"role": "system", "content": _DRAFT_SYSTEM},
        {"role": "user", "content": f"本机盘点（唯一事实源，禁止编造）:\n{inventory}\n\n"
                                    f"用户需求：{intent}"},
    ]
    t0 = time.perf_counter()
    ok, text, model_id = _ask_current_model(messages)
    if not ok:
        return {"ok": False, "error": text}
    # 稳健解析：剥代码块标记 → 取首尾大括号
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    s, e = raw.find("{"), raw.rfind("}")
    if s < 0 or e <= s:
        return {"ok": False, "error": f"所选模型 {model_id} 没吐出合法 JSON，原话：{text[:200]}"}
    try:
        data = json.loads(raw[s:e + 1])
    except Exception as ex:
        return {"ok": False, "error": f"草稿 JSON 读不懂（{ex}），原话：{text[:200]}"}
    recipe = data.get("recipe") or {}
    if not recipe.get("steps"):
        return {"ok": False, "error": "草稿没有环节（steps 为空）"}
    recipe["origin"] = "draft"
    return {"ok": True, "recipe": recipe, "mapping": data.get("mapping") or [],
            "model_used": model_id,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}
