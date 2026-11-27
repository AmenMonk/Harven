# -*- coding: utf-8 -*-
"""驻留池（骨架核心 · 组合的里子）。

- 模型单例共享：同一模型全系统只装一份，多组合共用（共存定版）；
- 常驻形态：pin 常驻 + keep_alive TTL 过期释放（低频模型让位）；
- A3 显存体检：单组合门槛=聚段取各段峰值最大；多组合同时激活才叠加；
- 驻留抵扣（#1586）：峰值只算「不在池内」模型的增量，已驻留同款复用单例不重复收钱；
- A2 铁规：体检不够 → 如实报错「缺 X GB」，绝不降级、绝不偷换、绝不偷偷改协作跑。
"""
from __future__ import annotations

import threading
import time

from . import channels as ch


class VramShortage(Exception):
    """A2/A3：显存不够，如实报错（缺多少说多少）。"""


class ResidencyPool:
    def __init__(self, vram_budget_gb: float | None = None):
        self._entries: dict[str, dict] = {}     # model_id -> {handle, channel, vram_gb, pinned, last_used, keep_alive_s}
        self._lock = threading.RLock()
        self._budget = vram_budget_gb           # None=运行时实测（硬件实测先行）

    # ══ 预算（硬件实测先行，v1.1 铁律）══════════════════════════
    def refresh_budget(self) -> float:
        """复用硬件自检模块实测显存，划定常驻池预算（留 1.5GB 系统余量）。"""
        try:
            import sys
            if r"H:\HarvenAI\src" not in sys.path:
                sys.path.insert(0, r"H:\HarvenAI\src")
            from core.hardware_detector import detect_hardware
            spec = detect_hardware()
            gpu = getattr(spec, "gpu_vram_gb", None) or getattr(spec, "vram_gb", 0) or 0
            self._budget = max(0.0, float(gpu) - 1.5) if gpu else 0.0
        except Exception:
            self._budget = self._budget or 0.0
        return self._budget

    def budget(self) -> float:
        if self._budget is None:
            self.refresh_budget()
        return self._budget or 0.0

    # ══ 记账 ═════════════════════════════════════════════════════
    def used_gb(self) -> float:
        with self._lock:
            return sum(e["vram_gb"] for e in self._entries.values())

    # ══ A3 体检：单组合门槛（聚段取最大，驻留抵扣 #1586）═════════
    def _is_resident(self, model_id: str) -> bool:
        with self._lock:
            return model_id in self._entries

    def segment_vram_peak_incremental_gb(self, recipe, vram_of, resident_of=None) -> tuple:
        """同 recipe 峰值口径，但已驻留模型按 0 增量计（单例复用）。
        resident_of(mid)：池外驻留判定回调（如 Ollama /api/ps 已在列）——同为复用不重复收钱。
        返回 (增量峰值, 已驻留复用的模型 id 列表)。"""
        peak, cur, cur_model = 0.0, 0.0, None
        reused: list[str] = []
        for s in recipe.steps:
            if s.engine == "program":
                continue
            m = s.model or "dynamic"
            if self._is_resident(m) or (resident_of and m not in reused and resident_of(m)):
                if m not in reused:
                    reused.append(m)
                inc = 0.0
            else:
                inc = float(vram_of(m) or 0)
            if m == cur_model:
                cur += inc
            else:
                peak = max(peak, cur)
                cur, cur_model = inc, m
        return max(peak, cur), reused

    def used_fixed_gb(self) -> float:
        """不可让位的已占预算（GB）：本地大模型通道（llama_cpp/ollama）单载可让位，
        不按「常驻占满」计——否则上一次组合用过的模型会一直压着预算误报（#1589）。"""
        with self._lock:
            return sum(e["vram_gb"] for e in self._entries.values()
                       if e.get("channel") not in ("llama_cpp", "ollama"))

    def real_free_check(self, peak: float, resident_of=None) -> dict:
        """★ #1589 真实空闲复核（账本之外也看）：预算口径只算「总显存−1.5−池内已占」，
        看不见桌面应用/QQ/Edge 吃掉的显存，也看不见池外驻留——所以再量一次真空闲：
          可用 = 实时空闲 + 可让位（进程内视觉 worker + Ollama 驻留）− 0.5GB 余量
          可用 < 需新增峰值 → 如实报「真实空闲不够」，并列出可腾出的项。
        无 torch/无显卡 → checked=False（不误伤 CPU 机）。"""
        out = {"checked": False, "free_gb": None, "retirable_gb": 0.0,
               "retirable": [], "free_short": 0.0}
        try:
            import torch
            if not torch.cuda.is_available():
                return out
            free, _total = torch.cuda.mem_get_info(0)
            free = free / 1024 ** 3
        except Exception:
            return out
        retirable, ret_gb = [], 0.0
        try:
            from src.vision.engines import vlm_engine as ve
            for e in ve._live_cpp_engines():
                g = e.retirable_gb()
                if g > 0:
                    ret_gb += g
                    retirable.append(f"视觉 worker「{e.model_label}」≈{g:.1f}GB")
        except Exception:
            pass
        try:
            import urllib.request, json as _json
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=1.5) as r:
                for m in (json.load(r) or {}).get("models", []):
                    g = float(m.get("size_vram") or 0) / 1024 ** 3
                    if g > 0.05:
                        ret_gb += g
                        retirable.append(f"Ollama 驻留「{m.get('name')}」≈{g:.1f}GB")
        except Exception:
            pass
        eff = free + ret_gb - 0.5
        out.update({"checked": True, "free_gb": round(free, 2),
                    "retirable_gb": round(ret_gb, 2), "retirable": retirable,
                    "eff_free_gb": round(eff, 2),
                    "free_short": round(max(0.0, peak - eff), 2)})
        return out

    def preflight(self, recipe, vram_of, resident_of=None) -> dict:
        peak, reused = self.segment_vram_peak_incremental_gb(recipe, vram_of, resident_of)
        budget = self.budget()
        avail = budget - self.used_fixed_gb()
        ok = (peak <= avail + 1e-6) or (budget <= 0)   # 无 GPU（预算 0）不拦，纯 CPU 链放行
        out = {"ok": ok, "peak_gb": round(peak, 2),
               "budget_gb": round(budget, 2), "used_gb": round(self.used_gb(), 2),
               "short_gb": round(max(0.0, peak - avail), 2),
               "used_fixed_gb": round(self.used_fixed_gb(), 2),
               "reused": reused}
        if ok:
            real = self.real_free_check(peak, resident_of)
            out.update(real)
            if real.get("checked") and real.get("free_short"):
                out["ok"] = False
        return out

    # ══ 取用（单例 + keep_alive + LRU 换出）══════════════════════
    def acquire(self, model_id: str, channel: str, vram_gb: float = 0.0,
                keep_alive_s: int = 0, pin: bool = False):
        with self._lock:
            e = self._entries.get(model_id)
            if e:
                e["last_used"] = time.time()
                if keep_alive_s:
                    e["keep_alive_s"] = keep_alive_s
                return e["handle"]
        # 不在池：先按需换出（LRU，非 pin 且过期者先走）
        self._evict_for(vram_gb)
        adapter = ch.get_adapter(channel)
        handle = adapter.load(model_id)
        with self._lock:
            self._entries[model_id] = {
                "handle": handle, "channel": channel, "vram_gb": float(vram_gb or 0),
                "pinned": pin, "last_used": time.time(), "keep_alive_s": keep_alive_s,
            }
        return handle

    def _evict_for(self, need_gb: float):
        """预算不足时按「最近使用」换出非 pin 条目（v1.5 第 3 条）。"""
        if need_gb <= 0 or self.budget() <= 0:
            return
        with self._lock:
            while self.used_gb() + need_gb > self.budget():
                cands = [(v["last_used"], k) for k, v in self._entries.items()
                         if not v["pinned"]]
                if not cands:
                    return          # 全是 pin：让 preflight 的报错说话，不硬换
                cands.sort()
                _, victim = cands[0]
                del self._entries[victim]

    def sweep(self) -> int:
        """keep_alive 过期清理（管家心跳可挂）。"""
        n, now = 0, time.time()
        with self._lock:
            for k in [k for k, v in self._entries.items()
                      if not v["pinned"] and v["keep_alive_s"]
                      and now - v["last_used"] > v["keep_alive_s"]]:
                del self._entries[k]
                n += 1
        return n

    def stats(self) -> dict:
        with self._lock:
            return {"models": len(self._entries), "used_gb": round(self.used_gb(), 2),
                    "budget_gb": round(self.budget(), 2),
                    "entries": [{"model_id": k, "channel": v["channel"],
                                 "vram_gb": v["vram_gb"], "pinned": v["pinned"],
                                 "idle_s": round(time.time() - v["last_used"], 1)}
                                for k, v in self._entries.items()]}


# ══ 模块级单例 ═════════════════════════════════════════════════
_pool: ResidencyPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> ResidencyPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ResidencyPool()
        return _pool
