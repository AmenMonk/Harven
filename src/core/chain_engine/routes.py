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
  POST   /<id>/test            试跑：真跑一次全链（样例按输入类型自动取），逐环耗时从物流单取
  POST   /<id>/advise          AI 参谋：物流单事实+目标描述→优化建议（跟用户所选模型，铁律0）
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


@bp.route("/entries", methods=["GET"])
def capability_entries():
    """统一能力查询：模型能力 + 组合能力（chain: 前缀一等条目）。
    消费方（聊天框/面板/FC 工具）查「本机现在有什么能力可用」用。"""
    from .binding import chain_entries
    return _ok(entries=chain_entries())


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
    _sync_binding()
    return _ok(recipe_id=res["recipe_id"])


def _sync_binding():
    """组合增删改后对账垂直页绑定（失败不拖累主流程，只记日志）。"""
    try:
        from .binding import sync_vertical_binding
        sync_vertical_binding()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[ChainWorkshop] 绑定对账失败: {e}")


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
        return _err("；".join(res.get("errors") or ["更新失败"]))
    _sync_binding()
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
    _sync_binding()
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
    _sync_binding()
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
    """试跑门：真跑一次全链。样例按首环输入类型自动取（samples 样例库优先）。"""
    r = get_registry().get_chain(recipe_id)
    if r is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    data = request.get_json(silent=True) or {}
    payload = data.get("payload") or {}
    sample_note = None
    if not payload.get("image_path"):
        sample, sample_note = _sample_for(r)
        if sample is None:
            return _err(f"试跑需要样例（首环输入 {r.steps[0].input_type if r.steps else '?'}）: "
                        f"{sample_note or '请上传或在 payload 指定'}")
        if isinstance(sample, dict):
            payload.update(sample)          # 文本类样例：直接注入 payload 字段
        else:
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
                    "sample_note": sample_note, "note": out.get("note")})


# ══ 试跑样例库（按首环输入类型取样）═══════════════════════════
_SAMPLES_DIR = r"H:\HarvenAI\data\chains\samples"


def _sample_for(r) -> tuple[object, str | None]:
    """按首环输入类型选试跑样例。返回 (样例, 说明)。
    图片类 → str 路径；文本类 → dict 注入 payload；没有 → (None, 缺样例说明)。"""
    from pathlib import Path
    it = r.steps[0].input_type if r.steps else "image_path"
    sdir = Path(_SAMPLES_DIR)
    if it in ("image", "image_path", "frames", "video"):
        # 视频链优先视频样例；图片链只取图（视频当图开 PIL 会炸）
        if it in ("video", "frames"):
            order = (("sample_video.mp4", "样例视频"),)
        else:
            order = (("sample_image.jpg", "合成样例图"),)
        for name, kind in order:
            p = sdir / name
            if p.exists():
                return str(p), f"{kind}（samples 样例库）"
        if it not in ("video", "frames"):
            photo = _latest_photo()
            if photo:
                return photo, "图片库最近一张"
            return None, "samples 样例库为空且图片库无照片（samples/sample_image.jpg 缺失）"
        return None, "样例视频缺失（samples/sample_video.mp4）"
    if it == "audio":
        p = sdir / "sample_audio.wav"
        if p.exists():
            return str(p), "样例音频（samples 样例库）"
        return None, "样例音频缺失（samples/sample_audio.wav）"
    if it in ("text", "table_row", "json"):
        txt_p = sdir / "sample_text.txt"
        if txt_p.exists():
            try:
                return {"text": txt_p.read_text(encoding="utf-8")}, "样例文本（samples 样例库）"
            except Exception:
                pass
        return {"text": "哈尔滨红肠 39.9 元一斤，买了两斤。"}, "内置兜底文本"
    return None, f"未知输入类型 {it}"


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


# ══ 说话级入口（大白话→草稿，跟用户所选模型铁律0）═══════════════
@bp.route("/draft", methods=["POST"])
def draft_recipe():
    """说话级入口：用户大白话 → 配方单草稿 + 需求↔环节对照表。
    草稿 origin=draft 不落盘，用户在编辑器里过目→保存才走三道门。"""
    from . import advisor
    data = request.get_json(silent=True) or {}
    res = advisor.draft(data.get("intent") or "")
    if not res.get("ok"):
        return _err(res.get("error") or "草稿生成失败", 502)
    return _ok(recipe=res["recipe"], mapping=res["mapping"],
               model_used=res["model_used"], elapsed_ms=res["elapsed_ms"])


# ══ AI 参谋（跟用户所选模型，铁律0）═══════════════════════════
@bp.route("/<recipe_id>/advise", methods=["POST"])
def advise_recipe(recipe_id: str):
    """AI 参谋：物流单事实+配方单 → 优化建议。只动嘴不动手，运行时零 AI 不变。"""
    from . import advisor
    data = request.get_json(silent=True) or {}
    res = advisor.advise(recipe_id, data.get("question"))
    if not res.get("ok"):
        return _err(res.get("error") or "参谋调用失败", 502)
    return _ok(advice=res["advice"], model_used=res["model_used"],
               elapsed_ms=res["elapsed_ms"])


# ══ 物流单历史 ═══════════════════════════════════════════════
@bp.route("/<recipe_id>/logs", methods=["GET"])
def chain_logs(recipe_id: str):
    limit = int(request.args.get("limit", 100))
    rows = get_logistics().query(recipe_id, limit=min(limit, 500))
    return _ok(rows=rows)


# ══ 组合包分享/导入（#1535：Hub 第 8 内容类型 chain）════════════════════
@bp.route("/<recipe_id>/share", methods=["POST"])
def share_recipe(recipe_id: str):
    """导出组合包 zip 并上传 HarvenHub（type=chain，带许可证）。
    内置原件也可分享（示范田分享的是原件副本，Hub 上人人可导入 fork）。"""
    src = get_registry().get_chain(recipe_id)
    if src is None:
        return _err(f"组合不存在: {recipe_id}", 404)
    data = request.get_json(silent=True) or {}
    import io
    import os
    import zipfile

    d = src.to_dict()
    d.pop("enabled", None)  # 接收方自己决定启用
    license_id = (data.get("license") or src.license or "MIT").strip()[:50]
    desc = (data.get("description") or f"哈文组合包：{src.name}（{len(src.steps)} 环）").strip()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("chain.json", json.dumps(d, ensure_ascii=False, indent=2))
        z.writestr("README.txt",
                   f"{src.name}\n{'=' * len(src.name)}\n\n"
                   f"哈文模型组合包（chain）· {len(src.steps)} 环\n"
                   f"许可证: {license_id}\n\n"
                   f"导入方式：哈文 → 工坊 → 从 Hub 导入\n")
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "chains", "tmp")
    tmp_dir = os.path.abspath(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"share_{recipe_id}.zip")
    with open(tmp_path, "wb") as f:
        f.write(buf.getvalue())

    try:
        from src.core.harvenhub_client import hub_upload
        up = hub_upload(tmp_path, "chain", data.get("name") or src.name,
                        description=desc, license=license_id)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    if not up.get("success"):
        return _err(up.get("error") or "上传 Hub 失败", 502)
    return _ok(item_id=up.get("item_id"), license=license_id)


@bp.route("/import", methods=["POST"])
def import_recipe():
    """从 HarvenHub 下载组合包（item_id）→ 解包 → 三道门校验 → 落为 fork 组合。
    origin=forked：行为等同用户自建（可改可删可停用），Hub 原件不追踪。"""
    data = request.get_json(silent=True) or {}
    item_id = (data.get("item_id") or "").strip()
    if not item_id:
        return _err("缺少 item_id")
    from src.core.harvenhub_client import hub_download
    dl = hub_download(item_id)
    if not dl.get("success"):
        return _err(dl.get("error") or "下载失败", 502)
    file_path = dl["file_path"]

    import os
    import zipfile
    try:
        if not zipfile.is_zipfile(file_path):
            return _err("不是合法的组合包（zip）")
        with zipfile.ZipFile(file_path) as z:
            if "chain.json" not in z.namelist():
                return _err("组合包缺少 chain.json")
            raw = json.loads(z.read("chain.json").decode("utf-8"))
    except json.JSONDecodeError as e:
        return _err(f"chain.json 解析失败: {e}")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

    src_id = str(raw.get("id") or "chain")
    new_id = (data.get("id") or src_id).strip().lower()
    reg = get_registry()
    # B7 查重按 id+名称双口径，导入侧两个都要消歧
    taken_ids = {c.id for c in reg.list_chains()}
    taken_names = {c.name for c in reg.list_chains()}
    base_id = new_id
    n = 0
    while new_id in taken_ids:  # id 撞户口循环消歧
        n += 1
        new_id = f"{base_id}-hub{n}" if n > 1 else f"{base_id}-hub"
    base_name = str(raw.get("name") or new_id).strip()
    new_name = base_name
    n = 0
    while new_name in taken_names:  # B7 按名称精确查重，循环消歧到不撞
        n += 1
        new_name = f"{base_name}·Hub{n}" if n > 1 else f"{base_name}·Hub"
    raw["id"] = new_id
    raw["name"] = new_name
    raw["origin"] = "forked"
    raw["forked_from"] = f"hub:{item_id}"
    raw["enabled"] = False  # 导入先停用，过三道门+用户确认再启用
    try:
        r = Recipe.from_dict(raw)
    except TypeError as e:
        return _err(f"配方单字段非法: {e}")
    res = reg.register_chain(r)
    if not res.get("ok"):
        return _err("；".join(res.get("errors") or ["导入校验失败"]))
    _sync_binding()
    # 结构门+能力门立即体检一遍，把结果一并告诉前端
    check = {"ok": True}
    try:
        from .gates import gate_structure, gate_capability
        s = gate_structure(r)
        c2 = gate_capability(r, reg)
        check = {"ok": bool(s.get("passed") and c2.get("passed")),
                 "structure": s.get("problems"), "capability": c2.get("problems")}
    except Exception:
        pass
    return _ok(recipe_id=new_id, check=check,
               hint="已导入并停用，请在工坊里试跑确认后再启用")
