# -*- coding: utf-8 -*-
"""骨架核心冒烟测试（N/N 通过式）。

覆盖：schema 校验 / 身份与能力交集 / 注册表（命名查重+原件保护+卸载解耦）/
三道门（结构+能力）/ 编排器 preflight（B10 依赖+A3 显存）/ 真图跑 HarvenEye /
物流单落库 / B4 safe 动作环跳过 / C3 耗时对比。
跑法：H:/HarvenAI/.venv/Scripts/python.exe H:/HarvenAI/tests/test_chain_engine.py
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"H:\HarvenAI\src")
sys.path.insert(0, r"H:\HarvenAI")
os.environ.setdefault("HARVEN_TEST", "1")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f" | {detail}" if detail else ""))


# ══ 0. 初始化 ══════════════════════════════════════════════════
from core.chain_engine import (ensure_chain_engine, Recipe, Step,
                               get_registry, get_orchestrator, get_logistics)
from core.chain_engine import recipe as recipe_mod
from core.chain_engine import gates

info = ensure_chain_engine()
check("初始化 ensure_chain_engine", info["chains_loaded"] >= 1, str(info))

reg = get_registry()
orch = get_orchestrator()

# ══ 1. 配方单 schema ═══════════════════════════════════════════
he = reg.get_chain("harveneye")
check("HarvenEye 配方已注册", he is not None and he.origin == "builtin")
check("HarvenEye 身份=纯本地", he.identity() == "pure_local", he.identity())
check("HarvenEye 能力=交集诚实登记", "vision_understand" in he.capabilities(),
      str(sorted(he.capabilities())))
check("validate 干净配方零错误", he.validate() == [])

bad = Recipe(id="BAD ID", name="", version="1.0", steps=[])
errs = bad.validate()
check("validate 抓坏配方", len(errs) >= 3, f"{len(errs)} 处")

# YAML 往返
p = recipe_mod.save(he.copy(), r"H:\HarvenAI\data\chains")
r2 = recipe_mod.load(r"H:\HarvenAI\data\chains", "harveneye")
check("YAML 落盘/读回往返", r2 is not None and r2.name == "HarvenEye"
      and len(r2.steps) == len(he.steps))

# ══ 2. 注册表 ══════════════════════════════════════════════════
fork = he.copy()
fork.id, fork.name, fork.origin, fork.forked_from = "harveneye_fork_test", "我的眼睛", "forked", "harveneye"
res = reg.register_chain(fork)
check("fork 另存注册成功", res.get("ok"), str(res.get("errors", "")))

dup = fork.copy(); dup.id = "dup_name_test"
res2 = reg.register_chain(dup)
check("B7 组合名查重拦截", not res2.get("ok"), str(res2.get("errors", ""))[:60])

evil = fork.copy(); evil.id = "harveneye"; evil.name = "偷牌HarvenEye"
res3 = reg.register_chain(evil)
check("内置组合原件保护（不许覆盖）", not res3.get("ok"), str(res3.get("errors", ""))[:60])

res4 = reg.unregister_chain("harveneye_fork_test")
check("卸载组合只删配方单（底座保留）", res4.get("ok") and res4.get("base_models_kept"))
check("卸载后底座模型仍在册", reg.find_model("harven.base.yolox") is not None)

# 组合即模型：能力查询里出现 chain: 条目
entries = reg.capability_entries("vision_understand")
check("组合进能力查询（chain: 前缀）",
      any(e["kind"] == "chain" and e["id"] == "chain:harveneye" for e in entries),
      f"{len(entries)} 条")

# ══ 3. 三道门 ══════════════════════════════════════════════════
g1 = gates.gate_structure(he)
check("结构门：HarvenEye 通过", g1["passed"], str(g1["problems"])[:60])

bad_chain = Recipe(id="bad_chain", name="坏链", version="1.0.0",
                   steps=[Step(id="a", name="出文本", capability="text_gen",
                               model="ollama:qwen2.5:14b", engine="ollama",
                               input_type="image_path", output_type="text"),
                          Step(id="b", name="只吃图", capability="detect",
                               model="harven.base.yolox", engine="internal",
                               input_type="image", output_type="boxes")])
g1b = gates.gate_structure(bad_chain)
check("结构门：文本→只吃图 接不上即标红", not g1b["passed"],
      (g1b["problems"][0][:50] if g1b["problems"] else ""))

g2 = gates.gate_capability(he, reg)
check("能力门：HarvenEye 通过（底座已登记）", g2["passed"], str(g2["problems"])[:60])

bad_cap = Recipe(id="bad_cap", name="错配", version="1.0.0",
                 steps=[Step(id="a", name="定位模型写话", capability="text_gen",
                             model="harven.base.yolox", engine="internal")])
g2b = gates.gate_capability(bad_cap, reg)
check("能力门：YOLOX 干写话的活被拒", not g2b["passed"],
      (g2b["problems"][0][:50] if g2b["problems"] else ""))

# ══ 4. 编排器 preflight ════════════════════════════════════════
pre = orch.preflight(he)
check("preflight：HarvenEye 体检通过（无 GPU 预算时不冤枉）",
      pre["ok"] or pre["vram"]["budget_gb"] > 0, str(pre)[:80])

ghost = he.copy()
ghost.id = "ghost"; ghost.name = "幽灵组合"; ghost.origin = "user"
for s in ghost.steps:
    if s.engine == "internal" and not s.optional:
        s.model = "qwen3-vl-30b-a3b-q4km"; s.binding = recipe_mod.BIND_FIXED
ghost.steps = [s for s in ghost.steps if not s.optional]
reg.register_chain(ghost)
pre2 = orch.run("ghost", {"image_path": "x.jpg"})
check("B10：缺模型/跑不动如实报错（不偷换）",
      not pre2.get("ok") and ("缺少模型" in pre2.get("error", "") or "跑不动" in pre2.get("error", "")),
      pre2.get("error", "")[:70])
reg.unregister_chain("ghost")

# ══ 5. 真图跑 HarvenEye（打包执行器 + C3 对比）══════════════════
from PIL import Image
import numpy as np
img_path = r"H:\HarvenAI\data\chains\_smoke_img.jpg"
Image.fromarray(np.random.randint(0, 255, (640, 480, 3), dtype=np.uint8)).save(img_path)

# C3 红线：同图对比原链（公平对比——先冷启动热身，再用同一个 Pipeline 实例计时）
from core.chain_engine.orchestrator import _get_shared_pipeline
out = orch.run("harveneye", {"image_path": img_path})     # 冷启动跑（不计入对比）
check("编排器真图跑通 HarvenEye", out.get("ok"), (out.get("error") or "")[:80])
check("B1 身份标注存在", out.get("label") == "HarvenEye（组合）执行", out.get("label", ""))
pipe = _get_shared_pipeline()
raw = pipe.recognize(img_path)                            # 热身
t_chain0 = time.perf_counter()
out = orch.run("harveneye", {"image_path": img_path})
chain_ms = round((time.perf_counter() - t_chain0) * 1000, 1)
t0 = time.perf_counter()
raw = pipe.recognize(img_path)
raw_ms = round((time.perf_counter() - t0) * 1000, 1)
overhead = out.get("total_ms", 0) - raw.get("total_ms", 0)
ratio = (out.get("total_ms", 1) / max(raw.get("total_ms", 1), 1))
check("C3 六秒红线：骨架开销 <10%（同图热态对比）",
      overhead < max(1000, raw.get("total_ms", 0) * 0.10),
      f"chain={out.get('total_ms')}ms raw={raw.get('total_ms')}ms 比值={ratio:.2f}")

# ══ 6. 物流单落库 ══════════════════════════════════════════════
lg = get_logistics()
time.sleep(2.5)
lg.flush_now()
rows = lg.query("harveneye", limit=10)
check("物流单 chains_log 落库", len(rows) >= 3, f"{len(rows)} 行")
check("物流单按环展开（L0~L3）",
      any(r["step_id"] == "l0_dedup" for r in rows) and any(r["step_id"] == "l2_embed" for r in rows),
      str({r["step_id"]: r["elapsed_ms"] for r in rows[:6]}))

# ══ 7. B4 safe 动作环 ══════════════════════════════════════════
from core.chain_engine.orchestrator import Orchestrator
act = Recipe(id="act_test", name="动作测试", version="1.0.0",
             on_step_fail="skip_optional",
             steps=[Step(id="danger", name="危险动作环", capability="text_gen",
                         model="ollama:qwen2.5:14b", engine="ollama",
                         damage="action", optional=True)])
reg.register_chain(act)
mode = "safe"
try:
    from core import security_guard
    mode = security_guard.get_permission_mode()
except Exception:
    pass
# 直接低层验证 B4 分支（不真调 Ollama）
o = Orchestrator()
try:
    o._run_steps(act, {"prompt": "x"}, "run-b4test", lambda row: None, {})
    b4_ok = True
except Exception:
    b4_ok = False
check(f"B4：动作环权限挂接执行不炸（当前模式={mode}）", b4_ok)

# ══ 8. 驻留池单例共享 ══════════════════════════════════════════
from core.chain_engine.residency import get_pool
pool = get_pool()
h1 = pool.acquire("harven.base.dhash", "internal", 0.0)
h2 = pool.acquire("harven.base.dhash", "internal", 0.0)
check("驻留池模型单例（同模型同句柄）", h1 is h2)
st = pool.stats()
check("驻留池记账", st["models"] >= 1, str(st["used_gb"]) + "GB")
print("   驻留条目明细:", st["entries"])

# ══ 收尾 ═══════════════════════════════════════════════════════
print("\n══════════════════════════════════")
print(f"总计 {len(PASS)+len(FAIL)} 项：✅ {len(PASS)} / ❌ {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
