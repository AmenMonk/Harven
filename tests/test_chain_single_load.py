# -*- coding: utf-8 -*-
"""★ #1589/#1590 回归（N/N 通过式）：组合通道按链选模型 + 视觉单载 + 体检真实空闲 +
协作弹窗与模型中心对齐 + P2/P3 四处真 bug。

覆盖：
  A. resolve_vision_gguf 三步解析 / 缺模型如实报错
  B. LlamaCppAdapter 按链选模型（8B→Ollama 同款、4B→独立 worker，不被全局槽偷换）
  C. 全局理解槽引擎单例 / build_understand_engine_for / 单载让位 retire
  D. preflight：池外驻留抵扣、本地通道不压预算、真实空闲复核结构
  E. worker notes / shutdown 端点 / 日志路径话术
  F. 协作站：站名修复、chain: 站校验、participants 分组与门禁、local-all 专项卡
  G. P2/P3：engine.BASE_DIR / evolution 非破坏备份 / agent 目录 / QProgressBar
跑法：H:/HarvenAI/.venv/Scripts/python.exe H:/HarvenAI/tests/test_chain_single_load.py
"""
import io
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"H:\HarvenAI\src")
sys.path.insert(0, r"H:\HarvenAI")
os.environ.setdefault("HARVEN_TEST", "1")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f" | {detail}" if detail else ""))


ROOT = r"H:\HarvenAI"

# ══ A. resolve_vision_gguf ═══════════════════════════════════
from core.chain_engine.channels import (LlamaCppAdapter, resolve_vision_gguf,
                                        ChannelError, _SingletonSlot)
r8 = resolve_vision_gguf("qwen3-vl-8b-q4km")
r8q6 = resolve_vision_gguf("qwen3-vl-8b-q6k")
r4 = resolve_vision_gguf("qwen3-vl-4b-q4km")
check("A1 目录条目 8B-Q4 → qwen3-vl-8b 主模型", r8["gguf"].endswith("Qwen3VL-8B-Instruct-Q4_K_M.gguf")
      and "mmproj" in r8["mmproj"].lower(), r8["gguf"])
check("A2 目录条目 8B-Q6 → qwen3-vl-8b-q6k", "qwen3-vl-8b-q6k" in r8q6["gguf"], r8q6["gguf"])
check("A3 目录条目 4B → qwen3-vl-4b", "qwen3-vl-4b" in r4["gguf"], r4["gguf"])
try:
    resolve_vision_gguf("qwen3-vl-30b-a3b-q4km")
    check("A4 未安装的旗舰档如实报错", False)
except ChannelError as e:
    check("A4 未安装的旗舰档如实报错", "未安装" in str(e) and "不偷换" in str(e), str(e)[:60])
try:
    resolve_vision_gguf("no-such-model-xyz")
    check("A5 查无此模型如实报错（不回落全局槽）", False)
except ChannelError as e:
    check("A5 查无此模型如实报错（不回落全局槽）", "no-such-model-xyz" in str(e), str(e)[:60])

# ══ B. LlamaCppAdapter 按链选模型 ═════════════════════════════
from src.vision.engines import vlm_engine as ve
ad = LlamaCppAdapter()
h8 = ad.load("qwen3-vl-8b-q4km")
e8 = h8.raw()
check("B1 链选 8B-Q4：同款已注册 Ollama → OllamaVlmEngine（同一份模型非代选）",
      isinstance(e8, ve.OllamaVlmEngine) and (e8.model or "").startswith("hv-pin-67d1659b"),
      f"{type(e8).__name__} model={getattr(e8, 'model', '')}")
h8q6 = ad.load("qwen3-vl-8b-q6k")
e8q6 = h8q6.raw()
check("B2 链选 8B-Q6：Ollama 覆盖名=2f963d09b4c7（与 8B-Q4 是两份不同模型，不串）",
      isinstance(e8q6, ve.OllamaVlmEngine) and (e8q6.model or "").startswith("hv-pin-2f963d09"),
      f"{type(e8q6).__name__} model={getattr(e8q6, 'model', '')}")
check("B3 两份模型槽 key 互不覆盖", h8._key != h8q6._key, f"{h8._key} / {h8q6._key}")
h4 = ad.load("qwen3-vl-4b-q4km")
e4 = h4.raw()
check("B4 链选 4B：未注册 Ollama → LlamaCppVlmEngine 且 override=4B 路径（不被全局槽/钉选偷换）",
      isinstance(e4, ve.LlamaCppVlmEngine) and "qwen3-vl-4b" in (e4._gguf_override or ""),
      f"{type(e4).__name__} override={getattr(e4, '_gguf_override', '')}")
check("B5 运行时标注：8B=ollama、4B=llama_cpp", h8.runtime() == "ollama" and h4.runtime() == "llama_cpp",
      f"{h8.runtime()} / {h4.runtime()}")
# 全局理解槽此刻指向钉选的 8B-Q6（或自导线），链选 4B 的引擎不得等于全局槽引擎
g_eng = ve.build_understand_engine()
check("B6 全局理解槽与链选引擎各管各（链选不被全局槽顶替）",
      e4 is not g_eng and (getattr(e4, "_gguf_override", "") != ""), f"global={type(g_eng).__name__}")

# ══ C. 全局单例 / 按需工厂 / 单载让位 ═════════════════════════
g2 = ve.build_understand_engine()
check("C1 全局理解槽引擎单例（同配置两次同一实例，防同模型双 worker）", g2 is g_eng)
f4 = ve.build_understand_engine_for(r4["gguf"], r4["mmproj"])
check("C2 build_understand_engine_for(4B) → LlamaCpp + override", isinstance(f4, ve.LlamaCppVlmEngine)
      and f4._gguf_override == r4["gguf"])


class _FakeProc:
    def poll(self):
        return None


class _Fake(ve.LlamaCppVlmEngine):
    def __init__(self, gguf):
        super().__init__(gguf_override=gguf, mmproj_override="x")
        self._proc = _FakeProc()
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1
        self._proc = None


ve._LIVE_CPP.clear()
fa, fb = _Fake(r8["gguf"]), _Fake(r4["gguf"])
ve._register_live(fa)
ve._register_live(fb)
ret = ve.retire_inprocess_workers("单测", keep_gguf=r8["gguf"])
check("C3 单载让位：只退「其他模型」的 worker，保留同款", fb.unloaded == 1 and fa.unloaded == 0
      and len(ret) == 1, f"retired={ret}")
check("C4 让位后登记表自清理", ve._live_cpp_engines() == [] or all(e._alive() for e in ve._live_cpp_engines()))
ve._LIVE_CPP.clear()
check("C5 可让位显存估算：没活 worker = 0", ve.retirable_gb() == 0.0)

# ══ D. preflight ═══════════════════════════════════════════════
from core.chain_engine import get_registry, get_orchestrator, recipe as recipe_mod
from core.chain_engine.residency import ResidencyPool
reg = get_registry()
orch = get_orchestrator()
pool = ResidencyPool(vram_budget_gb=12.0)
pool._entries["ollama:some"] = {"handle": None, "channel": "ollama", "vram_gb": 9.0,
                                "pinned": False, "last_used": 0, "keep_alive_s": 0}
check("D1 本地大模型通道不按「常驻占满」压预算", pool.used_fixed_gb() == 0.0 and pool.used_gb() == 9.0)
fridge = reg.get_chain("umwyu5g72911") or reg.get_chain("harveneye")
calls = []


def vram_of(mid):
    return 6.0 if mid == fridge.steps[-1].model else 0.5


def resident_of(mid):
    calls.append(mid)
    return mid == fridge.steps[-1].model


peak, reused = pool.segment_vram_peak_incremental_gb(fridge, vram_of, resident_of)
check("D2 池外驻留抵扣：已在 Ollama 驻留的模型按 0 增量（只剩 0.5 的分类环）",
      fridge.steps[-1].model in reused and abs(peak - 0.5) < 1e-6,
      f"peak={peak} reused={reused}")
peak2, _ = pool.segment_vram_peak_incremental_gb(fridge, vram_of, None)
check("D2b 不在驻留时按全量 6.0 计", abs(peak2 - 6.0) < 1e-6, f"peak={peak2}")
check("D2c 抵扣判定逐模型回调（非模型池内写死）", fridge.steps[-1].model in calls)
pf = pool.preflight(fridge, vram_of, lambda m: False)
check("D3 体检结果带真实空闲复核字段", {"checked", "free_gb", "retirable", "free_short", "used_fixed_gb"} <= set(pf),
      str({k: pf.get(k) for k in ("ok", "checked", "free_gb", "free_short")}))
if pf.get("checked"):
    check("D4 真实空闲不够才拦：free_short>0 时 ok 必为 False", (pf["free_short"] > 0) == (not pf["ok"]))
else:
    check("D4 无显卡/无 torch 时不误伤（checked=False 不拦）", pf["ok"])
pre = orch.preflight(fridge)
check("D5 编排器体检通过（本机跑得动冰箱链）", pre["ok"], "；".join(pre["problems"])[:120])
check("D6 体检话术分两版：预算口径 / 真实空闲口径都在代码里",
      "显卡真实空闲不够" in open(os.path.join(ROOT, "src", "core", "chain_engine", "orchestrator.py"),
                                 encoding="utf-8").read())

# ══ E. worker notes / shutdown / 日志路径 ═════════════════════
from src.vision import vision_worker as vw
vw._STATE["notes"].clear()
vw._note("显存不足，已卸载 Ollama 常驻模型 2 个")
vw._note("显存不足，已卸载 Ollama 常驻模型 2 个")
check("E1 worker 自愈动作 notes 记录且去重", vw._STATE["notes"] == ["显存不足，已卸载 Ollama 常驻模型 2 个"])
src = open(os.path.join(ROOT, "src", "vision", "vision_worker.py"), encoding="utf-8").read()
check("E2 /health 带 notes/gguf/gpu_layers，/shutdown 端点存在",
      '"notes"' in src and '"gguf": _STATE.get("gguf"' in src and '"/shutdown"' in src)
hint = ve._worker_log_hint()
check("E3 报错话术里的日志路径=真实落点 data/models/logs/（不再是错的 data/logs/）",
      "data/models/logs/vision_worker.log" in hint and "data/logs/" not in hint.replace("data/models/logs/", ""), hint)
eng4 = ve.LlamaCppVlmEngine(gguf_override=r4["gguf"], mmproj_override=r4["mmproj"])
eng4._absorb_note("显存仍不足，部分层改用 CPU 跑")
check("E4 主进程吸收 worker 自愈动作（引擎可见，不静默）", eng4.notes and "CPU" in eng4._note_text())

# ══ F. 协作站 ══════════════════════════════════════════════════
from src.core.engine import ConversationEngine


class _Dummy:
    def _pipeline_local_selection_full(self):
        return ("harveneye", "vision", "本地视觉引擎（看图大脑：测试）", True, "")


nm = lambda pid: ConversationEngine._pipeline_participant_name(_Dummy(), pid)  # noqa: E731
check("F1 站名：local 带当前选中真身", "当前选中的：本地视觉引擎" in nm("local"), nm("local"))
check("F2 站名：ollama: 站显示本地（旧版 UnboundLocalError / 冒充 ☁️ 外脑）",
      nm("ollama:qwen3:8b").startswith("🏠 本地"), nm("ollama:qwen3:8b"))
check("F3 站名：视觉引擎站", nm("harveneye") == "🏠 本地视觉引擎", nm("harveneye"))
check("F4 站名：chain: 组合站", nm("chain:harveneye").startswith("🧩 本地组合「"), nm("chain:harveneye"))
check("F5 站名：外脑站仍是 ☁️", nm("deepseek").startswith("☁️"), nm("deepseek"))

from src.core.collab_routes import _validate_pipeline, local_current_selection
cl, err = _validate_pipeline([{"type": "chat", "participant": "chain:harveneye"},
                              {"type": "chat", "participant": "local"}])
check("F6 chain: 对话型组合可当站", err is None and cl and cl[0]["participant"] == "chain:harveneye", str(err))
_cl, err2 = _validate_pipeline([{"type": "chat", "participant": "chain:nope"}, {"type": "chat", "participant": "local"}])
check("F7 不存在的组合站被拦", err2 and "不存在" in err2, str(err2))
_cl, err3 = _validate_pipeline([{"type": "chat", "participant": "chain:bills_chain"},
                                {"type": "chat", "participant": "local"}])
check("F8 管道型组合不能当对话站", err3 and ("管道" in err3 or "不存在" in err3), str(err3))
sel = local_current_selection()
check("F9 本地槽真身解析五元组", len(sel) == 5 and sel[1] in ("vision", "chain", "ollama", "gguf_dir", ""),
      str(sel)[:100])
check("F10 钉选视觉大脑显示 catalog 标准名（不是目录名）",
      "q6k" not in sel[2] and ("Q6_K" in sel[2] or sel[1] != "vision"), sel[2])

# participants / local-all 两个接口实测（与模型中心同一门禁）
try:
    import web_app as _wa
    _c = _wa.app.test_client()
    _pj = _c.get("/api/collab/participants").get_json()["data"]
    _chat = _pj["chat"]
    _by = {p["id"]: p for p in _chat}
    _ext = [p for p in _chat if p["kind"] == "external"]
    check("F11 participants：本地站带当前选中真身与就绪状态",
          "current_local" in _by["local"] and _by["local"]["ready"] is True and "当前选中的" in _by["local"]["name"],
          _by["local"]["name"])
    check("F12 participants：对话型组合进站（与模型中心同一清单）",
          any(p["kind"] == "chain" for p in _chat), str([p["id"] for p in _chat if p["kind"] == "chain"]))
    check("F13 participants：外脑按 core/more 折叠，has_key 与模型中心 configured 同口径",
          all(p["group"] in ("core", "more") for p in _ext)
          and all(p["group"] == "core" for p in _ext if p["has_key"])
          and all(p["group"] == "more" for p in _ext if not p["has_key"] and p["id"] not in
                  ('deepseek', 'doubao', 'qwen', 'kimi', 'zhipu', 'yi', 'baichuan', 'stepfun')
                  and not p.get("is_custom")),
          f"core={sum(p['group'] == 'core' for p in _ext)} more={sum(p['group'] == 'more' for p in _ext)}")
    _mm = _wa.get_multi_model_manager() if hasattr(_wa, "get_multi_model_manager") else None
    try:
        from src.core.multi_model_manager import get_multi_model_manager as _gmm
        _cfg = {m["id"]: m["configured"] for m in _gmm().get_all_models()}
        check("F14 participants has_key == 模型中心 configured（逐家一致）",
              all(p["has_key"] == bool(_cfg.get(p["id"], False)) for p in _ext if p["id"] in _cfg),
              str([(p["id"], p["has_key"], _cfg.get(p["id"])) for p in _ext if p["id"] in _cfg and p["has_key"] != bool(_cfg.get(p["id"]))]))
    except Exception as _e:
        check("F14 participants has_key == 模型中心 configured（逐家一致）", False, str(_e)[:100])
    _la = _c.get("/api/models/local-all").get_json()["models"]
    _hv = next((m for m in _la if "huatuo" in m["id"].lower()), None)
    check("F15 local-all：专项模型卡（HuatuoGPT）标 settable=false（不再是指了必报错的死按钮）",
          _hv is not None and _hv.get("settable") is False and _hv.get("vertical_only") is True,
          str({k: _hv.get(k) for k in ("id", "settable", "vertical_only")} if _hv else None))
    check("F16 local-all：通用卡 settable=true", all(m.get("settable", True) for m in _la if not m.get("vertical_only")))
except Exception as _e:
    check("F11-F16 participants/local-all 接口实测", False, str(_e)[:160])

# ══ G. P2/P3 ════════════════════════════════════════════════════
from src.core import engine as _eng
check("G1 engine.BASE_DIR 已定义（「下载了什么」分支不再 NameError）",
      hasattr(_eng, "BASE_DIR") and os.path.normpath(str(_eng.BASE_DIR)) == os.path.normpath(ROOT), str(_eng.BASE_DIR))
from src.core import evolution_manager as _ev, agent_service as _ag
check("G2 evolution_manager 已 import subprocess", _ev.subprocess is subprocess)
before = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
n_stash_before = subprocess.run(["git", "stash", "list"], cwd=ROOT, capture_output=True, text=True).stdout.count("\n")
ok = _ev.EvolutionManager._git_backup(_ev.EvolutionManager.__new__(_ev.EvolutionManager))
after = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
check("G3 _git_backup 非破坏：工作区未提交改动原样保留", ok and before == after and len(after) > 0,
      f"ok={ok} before={len(before)} after={len(after)}")
n_stash_after = subprocess.run(["git", "stash", "list"], cwd=ROOT, capture_output=True, text=True).stdout.count("\n")
check("G4 _git_backup 留下 stash 快照（可找回）", n_stash_after == n_stash_before + 1, f"{n_stash_before}->{n_stash_after}")
if n_stash_after > n_stash_before:
    subprocess.run(["git", "stash", "drop", "stash@{0}"], cwd=ROOT, capture_output=True)   # 单测不留痕
_svc_dir = _ag.AgentService.__new__(_ag.AgentService)
import inspect  # noqa: E402
_src = inspect.getsource(_ag.AgentService.__init__)
check("G5 agent 审计目录=哈文根 data/agent（不再落 src/data/agent）",
      _src.count("os.path.dirname(") == 3 and not os.path.isdir(os.path.join(ROOT, "src", "data", "agent")), "")
_mw = open(os.path.join(ROOT, "src", "ui", "main_window.py"), encoding="utf-8").read()
check("G6 main_window 已 import QProgressBar", "QProgressBar," in _mw.split(")")[0])

print(f"\n{'=' * 60}\n通过 {len(PASS)}/{len(PASS) + len(FAIL)}" + (f"  ❌ 失败: {FAIL}" if FAIL else "  全绿 ✅"))
sys.exit(1 if FAIL else 0)
