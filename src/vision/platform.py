# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""跨平台动力适配（v1.3 节点⑧，2026-10-29）。

GGUF 格式本身全平台通用（相当于 MP4），差异只在推理引擎的「动力后端」：
  Win/Win+N卡   → llama-cpp-python CUDA wheel（JamePeng 预编译，含 Qwen3VL 支持）
  macOS (M系列) → 官方 pip 源码安装（自动 Metal 后端，苹果一等公民）
  Linux+N卡     → CUDA wheel（同 Win+N 卡源）
  A卡/Intel/无卡 → pip 源码安装（CPU 后端兜底；A 卡用户可自装 Vulkan 版）

原则：检测只做「建议与展示」，绝不静默替用户装东西（铁律 0 同源）。
"""
from __future__ import annotations

import platform as _pf
import shutil
import subprocess


def detect() -> dict:
    """检测当前平台与可用动力后端。返回 {os, machine, gpu, backend, wheel_hint}。"""
    system = _pf.system()          # Windows / Darwin / Linux
    machine = _pf.machine()        # AMD64 / arm64 / x86_64
    gpu, backend = "none", "cpu"

    if system == "Darwin":
        gpu, backend = "apple_silicon" if machine == "arm64" else "intel_mac", "metal"
    elif shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=5)
            name = (out.stdout or "").strip().splitlines()[0] if out.stdout.strip() else "NVIDIA"
            gpu, backend = name, "cuda"
        except Exception:
            gpu, backend = "NVIDIA", "cuda"
    elif system == "Windows" and shutil.which("npu-smi") is None:
        # 无 N 卡：A 卡/Intel 核显走 Vulkan 或 CPU（不强行探测，如实标注）
        backend = "cpu_or_vulkan"

    return {"os": system, "machine": machine, "gpu": gpu, "backend": backend,
            "wheel_hint": _wheel_hint(system, machine, backend)}


def _wheel_hint(system: str, machine: str, backend: str) -> str:
    """llama-cpp-python 安装建议（面向小白的一句话）。"""
    py = f"{_pf.python_version_tuple()[0]}.{_pf.python_version_tuple()[1]}"
    if backend == "cuda":
        return (f"检测到 N 卡：安装 CUDA 加速版 llama-cpp-python（cp{py.replace('.', '')} wheel，"
                "来源 JamePeng/llama-cpp-python releases，装完即可进程内跑本地视觉模型）")
    if backend == "metal":
        return "Mac 自动启用 Metal 加速：pip install llama-cpp-python 源码编译即可（首次约几分钟）"
    if backend == "cpu_or_vulkan":
        return ("未检测到 N 卡：默认 CPU 推理（能跑但较慢）；A 卡/Intel 显卡用户可自行安装 "
                "Vulkan 版 llama-cpp-python 提速；或直接钉选外脑视觉模型（零硬件要求）")
    return "pip install llama-cpp-python"


if __name__ == "__main__":
    import json
    print(json.dumps(detect(), ensure_ascii=False, indent=2))
