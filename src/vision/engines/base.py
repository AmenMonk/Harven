"""槽位基类 — 三槽位统一接口。

「引擎可换、规范不动」：任何模型想进 HarvenEye，实现对应槽位接口即可。
结果统一带溯源三件套：source_layer / confidence / elapsed_ms（第三章）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineResult:
    """引擎统一输出载荷（第六章接口与载荷细则）。"""
    source_layer: str                 # "L0" | "L1" | "L2" | "L3"
    confidence: float                 # 0~1
    elapsed_ms: float                 # 本层耗时
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_layer": self.source_layer,
            "confidence": round(self.confidence, 4),
            "elapsed_ms": round(self.elapsed_ms, 1),
            **self.data,
        }


class BaseEngine:
    """槽位引擎基类：load 一次、infer 多次；进程内单例。"""

    name: str = "base"
    slot: str = "detect"              # detect | index | understand
    loaded: bool = False

    def load(self) -> None:
        """加载模型到内存/显存。幂等。"""
        raise NotImplementedError

    def infer(self, image_path: str, **kwargs) -> EngineResult:
        """推理。返回统一 EngineResult。"""
        raise NotImplementedError

    def unload(self) -> None:
        """释放资源（13.2 显存调度器用）。"""
        self.loaded = False

    # ── ★ #1589 运行时标注（B1：成稿模型是谁、跑在哪，物流单如实记）──
    def runtime(self) -> str:
        """本引擎实际运行时（ollama / llama_cpp / api / internal…），缺省类名小写。"""
        return self.__class__.__name__.lower()

    def retirable_gb(self) -> float:
        """可让位显存（GB）：本引擎占着、但别的模型要用时能腾出来的量。缺省 0。"""
        return 0.0

    # ── 计时助手 ────────────────────────────────────────────────
    @staticmethod
    def _now_ms() -> float:
        return time.perf_counter() * 1000
