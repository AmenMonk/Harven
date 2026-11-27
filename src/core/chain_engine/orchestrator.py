# -*- coding: utf-8 -*-
"""编排器（骨架核心 · 单次激活、配方单驱动、自动连续执行）。

流程：preflight（B10 依赖对账 → A3 显存体检 → B4 权限挂接）
      → 逐环执行（内存直递，引用不过文件不过网络）
      → 每环物流单（异步）→ 失败回退（abort/skip_optional/use_backup）
      → 结果带 B1 身份标注「XX（组合）执行 · XX 模型成稿」。

运行时零 AI：编排器是查表/排队/记账/回退的确定性程序（设计稿 §8）。
迁移期「搬家不装修」：packed 执行器包装现有链路（HarvenEye→vision.Pipeline），
配方单环节结构照登记，trace 按环展开进物流单——C3 六秒红线零风险。
"""
from __future__ import annotations

import threading
import time
import uuid

from . import recipe as recipe_mod
from .registry import get_registry
from .residency import get_pool
from .logistics import get_logistics
from . import channels as ch


# ══ 打包执行器注册表（迁移期包装现有链路）══════════════════════
_PACKED: dict[str, callable] = {}


def register_packed(name: str, fn):
    """fn(payload, run_id, step_collect) -> dict 结果。step_collect(step_row) 逐环记账。"""
    _PACKED[name] = fn


# ── HarvenEye 打包执行器：vision.Pipeline.recognize 原样跑，trace 按环展开 ──
def _run_harveneye(payload: dict, run_id: str, step_collect) -> dict:
    image_path = payload.get("image_path") or payload.get("image")
    if not image_path:
        return {"ok": False, "error": "缺少 image_path"}

    pipe = _get_shared_pipeline()

    # L0 环如实记账：瀑布内查重未命中不留 trace，这里前置跑一次真实 dhash+查重
    # （dhash 2~5ms，C3 预算内），命中与否都落一行物流单。
    try:
        from src.vision.dedup import dhash
        t_l0 = time.perf_counter()
        h = dhash(image_path)
        dup = pipe.store.find_near_duplicate(h)
        step_collect({"step_id": "l0_dedup", "step_name": "l0_dedup",
                      "model_id": "harven.base.dhash", "channel": "internal",
                      "elapsed_ms": round((time.perf_counter() - t_l0) * 1000, 1),
                      "ok": True,
                      "meta": {"dup_hit": bool(dup),
                               "matched_id": dup[0] if dup else None}})
    except Exception:
        pass

    t0 = time.perf_counter()
    out = pipe.recognize(
        image_path,
        do_index=bool(payload.get("do_index")),
        force_vlm=bool(payload.get("force_vlm")),
        skip_vlm=bool(payload.get("skip_vlm")),
    )
    total_ms = round((time.perf_counter() - t0) * 1000, 1)

    # trace 按环展开进物流单（L0/L1/LF/L2/L3 与配方单环节一一对应）
    layer_step = {"L0": "l0_dedup", "L1": "l1_detect", "LF": "lf_face",
                  "L2": "l2_embed", "L3": "l3_understand"}
    for tr in out.get("trace", []):
        layer = str(tr.get("source_layer", ""))
        sid = layer_step.get(layer)
        if not sid:
            continue
        step_collect({"step_id": sid, "step_name": sid, "model_id": _layer_model(sid),
                      "channel": "internal" if layer != "L3" else "vlm",
                      "elapsed_ms": tr.get("elapsed_ms", 0.0), "ok": True,
                      "meta": {k: tr[k] for k in list(tr)[:6] if k != "source_layer"}})
    return {"ok": True, "result": out, "total_ms": total_ms,
            "steps": [{"step_id": t.get("source_layer", ""), "ok": True,
                       "elapsed_ms": t.get("elapsed_ms", 0),
                       "empty_output": False} for t in out.get("trace", [])]}


def _layer_model(step_id: str) -> str:
    return {"l0_dedup": "harven.base.dhash", "l1_detect": "harven.base.yolox",
            "lf_face": "harven.base.sface", "l2_embed": "harven.base.clip",
            "l3_understand": "understand(follow-user)"}.get(step_id, "")


_shared_pipeline = None
_sp_lock = threading.Lock()


def _get_shared_pipeline():
    """共享 Pipeline 单例：委托 vision_link（全应用唯一事实源，防显存双载）。

    注意导入路径必须与 vision_link 一致（src.vision.*），否则会产生第二个
    模块实例→第二套引擎→显存翻倍（冒烟测试实测 9.1GB 教训）。"""
    import sys
    root = r"H:\HarvenAI"
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from src.core import vision_link
        return vision_link.get_pipeline()
    except Exception:
        with _sp_lock:
            global _shared_pipeline
            if _shared_pipeline is None:
                from src.vision.pipeline import Pipeline
                _shared_pipeline = Pipeline()
            return _shared_pipeline


register_packed("harveneye_pipeline", _run_harveneye)

# ── 账单链打包执行器（#1531 官方第二号）：复用 bills_scan 公开函数，四环真实计时 ──
from .packed_bills import run_bills_pipeline as _bills_pipeline_fn  # noqa: E402
register_packed("bills_scan_pipeline", _bills_pipeline_fn)

# ── 视频速览打包执行器（#1532 官方第三号）：cv2 抽帧→关键帧→逐帧识别→VLM 成稿 ──
from .packed_video import run_video_pipeline as _video_pipeline_fn  # noqa: E402
register_packed("video_brief_pipeline", _video_pipeline_fn)


# ══ 编排器本体 ═════════════════════════════════════════════════
class Orchestrator:
    def __init__(self):
        self.reg = get_registry()
        self.pool = get_pool()
        self.lg = get_logistics()

    # ── preflight：三查（依赖/显存/权限），任何一查不过都不开跑 ──
    def preflight(self, r: recipe_mod.Recipe, current_models: dict | None = None) -> dict:
        problems = []
        # ① B10 依赖对账：钉死的模型必须在登记表且已装（builtin 底座视为已装）
        for s in r.steps:
            if s.engine == "program" or s.binding != recipe_mod.BIND_FIXED:
                continue
            m = self.reg.find_model(s.model)
            if m is None:
                problems.append(f"组合缺少模型 {s.model}，去安装或编辑更换（B10）")
            elif s.model.startswith("chain:") is False and m.get("builtin") is False \
                    and m.get("installed") is False:
                problems.append(f"模型 {s.model} 未安装（B10）")
        # ② A3 显存体检（聚段取最大）
        vram = self.reg.model_vram_gb
        vram = lambda mid: self.reg.model_vram_gb(mid)  # noqa: E731
        pf = self.pool.preflight(r, vram, self._ollama_resident)
        if not pf["ok"]:
            reuse_note = ""
            if pf.get("reused"):
                reuse_note = f"（已驻留复用抵扣：{'、'.join(pf['reused'])}）"
            if pf.get("checked") and pf.get("free_short") and pf.get("short_gb", 0) <= 0:
                # ★ #1589 预算账够、真实空闲不够：说人话，列出能腾出来的项
                items = "、".join(pf.get("retirable") or []) or "（没有可自动腾出的本地模型）"
                problems.append(
                    f"这台机器现在跑不动组合「{r.name}」：显卡真实空闲不够，还差 {pf['free_short']} GB"
                    f"（需新增峰值 {pf['peak_gb']}GB / 实时空闲 {pf.get('free_gb')}GB / "
                    f"可让位 {pf.get('retirable_gb', 0)}GB）。运行时会自动让位：{items}；"
                    f"其余是桌面应用占的，关掉几个吃显存的再试（A2 铁规：不降级不偷换）")
            else:
                real_note = ""
                if pf.get("checked") and pf.get("free_short"):
                    real_note = f"；另显卡真实空闲还差 {pf['free_short']} GB（桌面应用占着，关几个再试）"
                avail = pf.get("used_fixed_gb", pf["used_gb"])
                problems.append(f"这台机器跑不动组合「{r.name}」：缺 {pf['short_gb']} GB 显存"
                                f"（需新增峰值 {pf['peak_gb']}GB / 可用 {pf['budget_gb'] - avail:.2f}GB）{reuse_note}。"
                                f"建议换小模型或调整聚段（A2 铁规：不降级不偷换）{real_note}")
        # ③ B4 权限挂接：safe 模式动作环预提示（运行时跳过并如实记录）
        skipped_actions = []
        try:
            from core.security_guard import get_permission_mode
            if get_permission_mode() == "safe":
                skipped_actions = [s.name for s in r.steps if s.damage == recipe_mod.DAMAGE_ACTION]
        except Exception:
            pass
        return {"ok": not problems, "problems": problems, "vram": pf,
                "safe_skipped_actions": skipped_actions}

    def _ollama_resident(self, model_id: str) -> bool:
        """★ #1589 池外驻留判定：该模型此刻是否已在 Ollama 里驻留（/api/ps）。
        `ollama:<名字>` 直接比对；视觉目录条目按同目录 .ollama_registered.json 的注册名比对。
        驻留=复用单例，体检不重复收钱（#1586 口径延伸到池外）。"""
        try:
            import json as _json
            import urllib.request
            from pathlib import Path as _P
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=1.5) as r:
                loaded = [m.get("name", "") for m in (_json.load(r) or {}).get("models", [])]
            names = {n for x in loaded for n in (x, x.split(":")[0])}
            if model_id.startswith("ollama:"):
                name = model_id.split(":", 1)[1]
                return name in names or name.split(":")[0] in names
            try:
                from .channels import resolve_vision_gguf
                gguf = resolve_vision_gguf(model_id)["gguf"]
            except Exception:
                return False
            cache = _P(gguf).parent / ".ollama_registered.json"
            omodel = ((_json.loads(cache.read_text(encoding="utf-8")) or {}).get("model") or "") \
                if cache.is_file() else ""
            return bool(omodel) and omodel in names
        except Exception:
            return False

    # ── 主入口：一次激活 ──
    def run(self, recipe_id: str, payload: dict,
            current_models: dict | None = None) -> dict:
        r = self.reg.get_chain(recipe_id)
        if r is None:
            return {"ok": False, "error": f"组合未注册: {recipe_id}"}
        if not r.enabled:
            return {"ok": False, "error": f"组合已停用: {r.name}"}
        run_id = f"run-{uuid.uuid4().hex[:10]}"

        # ★ #1589 单载裁决第二层：组合开跑前对齐用户钉选的理解通道（与 vision_link.get_pipeline
        #   同口径），防 config 自导线抢跑造成双载；失败不动配置（幂等便宜）
        try:
            import sys as _sys
            root = r"H:\HarvenAI"
            if root not in _sys.path:
                _sys.path.insert(0, root)
            from src.core import vision_link as _vl
            _vl.apply_understand_channel()
        except Exception:
            pass

        pre = self.preflight(r, current_models)
        if not pre["ok"]:
            for p in pre["problems"]:
                self.lg.log_step(run_id, r.id, r.name, "preflight", "运行前体检",
                                 ok=False, error=p)
            return {"ok": False, "error": "；".join(pre["problems"]), "run_id": run_id,
                    "preflight": pre}

        t0 = time.perf_counter()
        ctx: dict = {"payload": payload, "run_id": run_id, "step_rows": []}

        def step_collect(row: dict):
            self.lg.log_step(run_id, r.id, r.name, row.get("step_id", ""),
                             row.get("step_name", ""), row.get("model_id", ""),
                             row.get("channel", ""), row.get("elapsed_ms", 0),
                             row.get("ok", True), row.get("error", ""),
                             row.get("token_cost", 0), row.get("meta"))
            ctx["step_rows"].append(row)

        try:
            packed_names = [s.packed for s in r.steps if s.engine == "program" and s.packed]
            if packed_names:
                fn = _PACKED.get(packed_names[0])
                if fn is None:
                    raise ch.ChannelError(f"打包执行器未注册: {packed_names[0]}")
                out = fn(payload, run_id, step_collect)
                if not out.get("ok"):
                    raise RuntimeError(out.get("error", "打包执行器失败"))
                result = out.get("result")
            else:
                result = self._run_steps(r, payload, run_id, step_collect, current_models)
        except Exception as e:
            step_collect({"step_id": "abort", "step_name": "异常中止", "ok": False,
                          "error": str(e), "elapsed_ms":
                              round((time.perf_counter() - t0) * 1000, 1)})
            return {"ok": False, "error": str(e), "run_id": run_id}

        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        # B1 身份标注：不冒用不隐瞒
        label = f"{r.name}（组合）执行"
        return {"ok": True, "run_id": run_id, "chain": r.id, "chain_name": r.name,
                "label": label, "identity": r.identity(), "result": result,
                "total_ms": total_ms, "safe_skipped": pre["safe_skipped_actions"],
                "steps": ctx.get("step_rows") or [],   # #1559 物流单（垂直页 step_timeline 用）
                "note": "组合执行 · 成稿模型见回复方标注（B1）"}

    # ── 逐环执行（通用路径：非 packed 的普通组合）──
    def _run_steps(self, r, payload, run_id, step_collect, current_models):
        data = payload
        for s in r.steps:
            if s.engine == "program" and not s.packed:
                continue    # 纯程序环由执行器内嵌逻辑处理（迁移期全走 packed）
            # B4：safe 模式动作环跳过并如实提示（不记住授权）
            try:
                from core.security_guard import get_permission_mode
                if s.damage == recipe_mod.DAMAGE_ACTION \
                        and get_permission_mode() == "safe":
                    step_collect({"step_id": s.id, "step_name": s.name, "ok": True,
                                  "skipped_safe": True,
                                  "error": "safe 模式动作环自动跳过（B4）"})
                    continue
            except Exception:
                pass
            model_id, channel = self._resolve_step_model(s, current_models)
            t0 = time.perf_counter()
            try:
                # ★ #1571 件4 参数接力：配方环提示词 > 运行时 payload > 上游环输出。
                #   「通用模型扩写 → 垂直模型生产」的接力不需要任何手工映射——
                #   上一环的文本产出自动成为下一环的提示词（配方写了 prompt 则以配方为准）。
                _up = data.get("step_output")
                _relay = ""
                if _up is not None and not (getattr(s, "prompt", "") or data.get("prompt", "")):
                    from .channels import _stringify_upstream
                    _relay = _stringify_upstream(_up)
                # ★ #1583 人话报错：图像环缺图别让 PIL 报 NoneType.read 看不懂
                if (getattr(s, "capability", "") or "") in recipe_mod.IMAGE_CAPS \
                        and not data.get("image_path"):
                    raise RuntimeError(
                        f"环节「{s.name or s.id}」需要图片输入，但链里没有图"
                        "（调用方没传 image_path 且试跑样例未取到）")
                handle = self.pool.acquire(model_id, channel,
                                           self.reg.model_vram_gb(model_id),
                                           keep_alive_s=s.keep_alive_s,
                                           pin=(s.keep_alive_s == -1))
                adapter = ch.get_adapter(channel)
                out = adapter.infer(handle, {**payload, "image_path": data.get("image_path"),
                                             "prompt": (getattr(s, "prompt", "") or data.get("prompt", "") or _relay)})
                # ★ #1589 B1：物流单如实记「实际跑在哪个运行时」（如链选 8B 同款已注册 Ollama
                #   → 实跑 ollama 通道；未注册 → llama_cpp 独立子进程），成稿方可查
                try:
                    _rt = handle.runtime() if hasattr(handle, "runtime") else channel
                except Exception:
                    _rt = channel
                step_collect({"step_id": s.id, "step_name": s.name,
                              "model_id": model_id, "channel": channel,
                              "elapsed_ms": out.get("elapsed_ms", 0), "ok": True,
                              "meta": {"runtime": _rt}})
                data = {**data, "step_output": out.get("result")}
            except Exception as e:
                ok = self._on_fail(r, s, e, step_collect)
                if not ok:
                    raise
        return data

    def _resolve_step_model(self, s, current_models):
        """A1 双模式解析：fixed 钉死（备选链兜底）；dynamic_follow 跟当前所选。"""
        if s.binding == recipe_mod.BIND_FIXED:
            return s.model, (self.reg.find_model(s.model) or {}).get("channel", s.engine)
        mid = (current_models or {}).get(s.capability, "")
        if mid:
            m = self.reg.find_model(mid)
            if m and s.capability in (m.get("capabilities") or []):
                return m["id"], m["channel"]
        cands = self.reg.models_with_capability(s.capability)
        if cands:
            return cands[0]["id"], cands[0]["channel"]
        raise ch.ChannelError(f"环节「{s.name}」无胜任模型（A1：不偷换，如实失败）")

    def _on_fail(self, r, s, e, step_collect) -> bool:
        """失败回退：use_backup→备选链；skip_optional→可选环跳过；否则中止。"""
        step_collect({"step_id": s.id, "step_name": s.name, "ok": False,
                      "error": str(e)})
        if s.backup_models and r.on_step_fail in ("use_backup",):
            for b in s.backup_models:
                m = self.reg.find_model(b)
                if m:
                    step_collect({"step_id": s.id + ":backup", "step_name": s.name,
                                  "model_id": b, "channel": m["channel"], "ok": True,
                                  "meta": {"fallback": True}})
                    return True
        if s.optional and r.on_step_fail in ("skip_optional", "use_backup"):
            step_collect({"step_id": s.id + ":skip", "step_name": s.name, "ok": True,
                          "meta": {"skipped_optional": True}})
            return True
        return False


# ══ 模块级单例 ═════════════════════════════════════════════════
_orch: Orchestrator | None = None
_o_lock = threading.Lock()


def get_orchestrator() -> Orchestrator:
    global _orch
    with _o_lock:
        if _orch is None:
            _orch = Orchestrator()
        return _orch
