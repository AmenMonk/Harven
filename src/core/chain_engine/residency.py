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

    def segment_vram_peak_incremental_gb(self, recipe, vram_of) -> tuple:
        """同 recipe 峰值口径，但已驻留模型按 0 增量计（单例复用）。
        返回 (增量峰值, 已驻留复用的模型 id 列表)。"""
        peak, cur, cur_model = 0.0, 0.0, None
        reused: list[str] = []
        for s in recipe.steps:
            if s.engine == "program":
                continue
            m = s.model or "dynamic"
            if self._is_resident(m):
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

    def preflight(self, recipe, vram_of) -> dict:
        peak, reused = self.segment_vram_peak_incremental_gb(recipe, vram_of)
        avail = self.budget() - self.used_gb()
        ok = (peak <= avail + 1e-6) or (self.budget() <= 0)   # 无 GPU（预算 0）不拦，纯 CPU 链放行
        return {"ok": ok, "peak_gb": round(peak, 2),
                "budget_gb": round(self.budget(), 2), "used_gb": round(self.used_gb(), 2),
                "short_gb": round(max(0.0, peak - avail), 2),
                "reused": reused}

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
