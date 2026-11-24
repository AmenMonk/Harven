# -*- coding: utf-8 -*-
"""模型组合工坊 · 后端 API（2026-11-24 工坊页面开工）。

蓝图：/api/chains
  GET    /                     组合列表（摘要 + 上次运行状态）
  GET    /models               本机模型盘点（编辑页第 0 步，v1.6 修①）
  GET    /<id>                 配方单全文
  POST   /                     新建/保存组合（B7 查重+内置保护在 registry）
  PUT    /<id>                 更新配方单（内置原件保护 → 引导 fork）
  POST   /<id>/fork            fork 另存（示范田照改，内置唯一修改通道）
  DELETE /<id>                 卸载（底座模型保留，三轮定版）
  POST   /<id>/check           三道门干跑（结构+能力+preflight，不真跑）
  POST   /<id>/test            试跑：真跑一次全链（样例图片），逐环耗时从物流单取
  GET    /<id>/logs            物流单历史（每环耗时/成功率/瓶颈事实）

铁律对照：
- A1 模型跟用户所选：dynamic_follow 环不改配方单，运行时解析；
- B4 权限挂接：damage=action 环在 safe 模式下 preflight 标记跳过（编排器已实现）；
- 内置组合原件保护：PUT/DELETE 对 origin=builtin 一律拒绝，只许 fork。
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

from .recipe import Recipe, load
from .registry import get_registry
from .logistics import get_logistics
from .orchestrator import get_orchestrator

bp = Blueprint("chain_workshop", __name__, url_prefix="/api/chains")


def _ok(data=None, **kw):
    return jsonify({"ok": True, **(data or {}), **kw})


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ══ 列表 + 盘点 ═══════════════════════════════════════════════
@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
def list_chains():
    reg = get_registry()
    lg = get_logistics()
    out = []
    for r in reg.list_chains():
        caps = sorted(r.capabilities())
        last = None
        try:
            rows = lg.query(r.id, limit=50)
            if rows:
                ok_rows = [x for x in rows if x.get("step_id") not in ("preflight", "abort")]
                last = {
                    "run_id": rows[0].get("run_id"),
                    "ok": all(x.get("ok", True) for x in ok_rows[:12]) if ok_rows else None,
                    "total_ms": sum(float(x.get("elapsed_ms") or 0)
                                    for x in rows if x.get("run_id") == rows[0].get("run_id")),
                    "at": rows[0].get("ts"),
                }
        except Exception:
            pass
        out.append({
            "id": r.id, "name": r.name, "version": r.version, "target": r.target,
            "origin": r.origin, "forked_from": r.forked_from, "license": r.license,
            "identity": r.identity(), "capabilities": caps, "enabled": r.enabled,
            "steps": [{"id": s.id, "name": s.name, "model": s.model, "engine": s.engine,
                       "binding": s.binding, "damage": s.damage} for s in r.steps],
            "last_run": last,
            "builtin_protected": r.origin == "builtin",
        })
    return _ok(chains=out)


@bp.route("/models", methods=["GET"])
def inventory_models():
    """编辑页第 0 步：本机模型盘点（v1.6 修①）——只列真实存在的。"""
    reg = get_registry()
    force = request.args.get("force") == "1"
    models = reg.list_models(force=force)
    return _ok(models=models)


# ══ 配方单读写 ═══════════════════════════════════════════════
@bp.route("/<recipe_id>", methods=["GET"])
def get_recipe(recipe_id: str):
    r = get_registry().get_chain(recipe_id)
    if r is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    return _ok(recipe=r.to_dict(), identity=r.identity(),
               capabilities=sorted(r.capabilities()))


@bp.route("", methods=["POST"])
@bp.route("/", methods=["POST"])
def save_recipe():
    data = request.get_json(silent=True) or {}
    raw = data.get("recipe") or data
    raw["origin"] = raw.get("origin") or "user"
    try:
        r = Recipe.from_dict(raw)
    except TypeError as e:
        return _err(f"配方单字段非法: {e}")
    res = get_registry().register_chain(r)
    if not res.get("ok"):
        return _err("；".join(res.get("errors") or ["校验失败"]))
    return _ok(recipe_id=res["recipe_id"])


@bp.route("/<recipe_id>", methods=["PUT"])
def update_recipe(recipe_id: str):
    old = get_registry().get_chain(recipe_id)
    if old is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    if old.origin == "builtin":
        return _err("内置组合受原件保护，请用「fork 另存」修改（示范田只读）", 403)
    data = request.get_json(silent=True) or {}
    raw = data.get("recipe") or data
    raw["id"] = recipe_id                      # 不许改 id（文件户口唯一）
    raw["origin"] = old.origin
    try:
        r = Recipe.from_dict(raw)
    except TypeError as e:
        return _err(f"配方单字段非法: {e}")
    res = get_registry().register_chain(r)
    if not res.get("ok"):
        return _err("；".join(res.get("errors") or ["校验失败"]))
    return _ok(recipe_id=recipe_id)


@bp.route("/<recipe_id>/fork", methods=["POST"])
def fork_recipe(recipe_id: str):
    """fork 另存：内置组合的唯一修改通道（示范田照改）。"""
    src = get_registry().get_chain(recipe_id)
    if src is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    data = request.get_json(silent=True) or {}
    new_id = (data.get("id") or f"{recipe_id}-fork").strip().lower()
    new_name = (data.get("name") or f"{src.name}·副本").strip()
    r = src.copy()
    r.id = new_id
    r.name = new_name
    r.origin = "forked"
    r.forked_from = src.id
    r.enabled = True
    res = get_registry().register_chain(r)
    if not res.get("ok"):
        return _err("；".join(res.get("errors") or ["fork 失败"]))
    return _ok(recipe_id=new_id, forked_from=src.id)


@bp.route("/<recipe_id>", methods=["DELETE"])
def remove_chain(recipe_id: str):
    r = get_registry().get_chain(recipe_id)
    if r is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    if r.origin == "builtin":
        return _err("内置组合不可删除（原件保护）", 403)
    res = get_registry().unregister_chain(recipe_id)
    if not res.get("ok"):
        return _err("；".join(res.get("errors") or ["卸载失败"]))
    return _ok(removed=recipe_id, base_models_kept=True)


# ══ 三道门 + 试跑 ═════════════════════════════════════════════
@bp.route("/<recipe_id>/check", methods=["POST"])
def check_recipe(recipe_id: str):
    """干跑：结构门+能力门+preflight（不加载模型不真跑）。"""
    r = get_registry().get_chain(recipe_id)
    if r is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    orch = get_orchestrator()
    pre = orch.preflight(r)
    return _ok(preflight=pre, identity=r.identity(),
               vram_peak_gb=round(r.segment_vram_peak_gb(get_registry().model_vram_gb), 2))


@bp.route("/<recipe_id>/test", methods=["POST"])
def test_recipe(recipe_id: str):
    """试跑门：真跑一次全链。样例=请求里的 image_path（缺省用最近一张图片库照片）。"""
    r = get_registry().get_chain(recipe_id)
    if r is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    data = request.get_json(silent=True) or {}
    payload = data.get("payload") or {}
    if not payload.get("image_path"):
        sample = _latest_photo()
        if not sample:
            return _err("试跑需要一张样例图片：请上传或在 payload.image_path 指定路径")
        payload["image_path"] = sample
    out = get_orchestrator().run(recipe_id, payload)
    # 样例若命中 L0 查重短路（trace 只有 L0 一行），旋转 90° 造一张「指纹不同、内容相同」的
    # 新图重跑一次——工坊试跑的目的是让用户看到全链逐环点亮，短路不是用户想看的东西。
    res = out.get("result") or {}
    layers = {t.get("source_layer") for t in (res.get("trace") or [])}
    dup_hit = any((r.get("meta") or {}).get("dup_hit")
                  for r in (out.get("steps") or []) if r.get("step_id") == "l0_dedup") \
        or len(layers - {"L0"}) == 0
    if out.get("ok") and dup_hit and payload.get("image_path"):
        rotated = _rotated_sample(payload["image_path"])
        if rotated:
            payload["image_path"] = rotated
            out = get_orchestrator().run(recipe_id, payload)
    lg = get_logistics()
    lg.flush_now()          # 异步批量记账立即落库（试跑页要当场看逐环耗时）
    rows = lg.query(recipe_id, limit=40)
    steps = [x for x in rows if x.get("run_id") == out.get("run_id")] if out.get("run_id") else []
    return jsonify({"ok": out.get("ok", False), "error": out.get("error"),
                    "run_id": out.get("run_id"), "total_ms": out.get("total_ms"),
                    "label": out.get("label"), "identity": out.get("identity"),
                    "result": out.get("result"), "steps": steps,
                    "note": out.get("note")})


def _rotated_sample(image_path: str) -> str | None:
    """把样例旋转 90° 落临时文件（dhash 指纹变、内容不变），专治试跑撞上查重短路。"""
    try:
        import tempfile
        from PIL import Image
        img = Image.open(image_path).rotate(90, expand=True)
        fd, tmp = tempfile.mkstemp(suffix=".jpg")
        import os
        os.close(fd)
        img.convert("RGB").save(tmp, "JPEG", quality=92)
        return tmp
    except Exception:
        return None


def _latest_photo() -> str | None:
    """样例兜底：图片库最近一张（photo_center 统一索引）→ data/media 扫盘 → 测试样例图。"""
    from pathlib import Path
    root = Path(r"H:\HarvenAI")
    try:
        from src.core.photo_center import scan_photos
        items = scan_photos(limit=1)
        if items:
            src = (items[0].get("src") or "").lstrip("/")
            p = root / src
            if src and p.exists():
                return str(p)
    except Exception:
        pass
    for scan_dir in (root / "data" / "media", root / "data" / "chains"):
        if scan_dir.exists():
            files = [p for p in scan_dir.rglob("*")
                     if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                     and "_broken_backup" not in str(p)]
            if files:
                return str(max(files, key=lambda p: p.stat().st_mtime))
    return None


# ══ 物流单历史 ═══════════════════════════════════════════════
@bp.route("/<recipe_id>/logs", methods=["GET"])
def chain_logs(recipe_id: str):
    limit = int(request.args.get("limit", 100))
    rows = get_logistics().query(recipe_id, limit=min(limit, 500))
    return _ok(rows=rows)
