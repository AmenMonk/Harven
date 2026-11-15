# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Harven AI — 硬件自检 & 模型推荐模块
启动时自动检测 CPU / RAM / GPU-VRAM，推荐最适合的本地模型。
"""

import re
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 1. 硬件规格数据结构
# ─────────────────────────────────────────────
class HardwareSpec:
    """检测到的本机硬件规格"""
    def __init__(self):
        self.cpu_name: str = "Unknown"
        self.cpu_cores: int = 0
        self.cpu_threads: int = 0
        self.ram_total_gb: float = 0.0
        self.ram_available_gb: float = 0.0
        self.gpu_name: str = ""
        self.gpu_vram_gb: float = 0.0          # 独显显存（0 = 无独显）
        self.gpu_is_nvidia: bool = False
        self.gpu_is_amd: bool = False
        self.gpu_is_intel: bool = False          # Intel Arc
        self.has_dedicated_gpu: bool = False
        self.os_name: str = "Unknown"
        self.os_arch: str = "Unknown"            # AMD64 / ARM64
        self.ollama_installed: bool = False
        self.ollama_models: List[str] = []
        self.disk_type: str = "Unknown"           # SSD / HDD
        self.disk_model: str = ""                 # 硬盘型号
        self.has_ssd: bool = False

    def summary(self) -> str:
        lines = [
            f"CPU : {self.cpu_name} ({self.cpu_cores}C/{self.cpu_threads}T)",
            f"RAM : {self.ram_total_gb:.1f} GB 总计 / {self.ram_available_gb:.1f} GB 可用",
        ]
        if self.has_dedicated_gpu:
            lines.append(f"GPU : {self.gpu_name}  {self.gpu_vram_gb:.1f} GB VRAM")
        else:
            lines.append("GPU : 无独立显卡（将使用 CPU 推理）")
        lines.append(f"OS  : {self.os_name} ({self.os_arch})")
        lines.append(f"Ollama : {'已安装' if self.ollama_installed else '未安装'}")
        if self.ollama_models:
            lines.append(f"  已安装模型 : {', '.join(self.ollama_models)}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# 2. 硬件检测（跨平台）
# ─────────────────────────────────────────────
def _detect_windows() -> HardwareSpec:
    spec = HardwareSpec()
    spec.os_name = "Windows"
    try:
        import platform
        spec.os_arch = platform.machine().upper()
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # CPU — PowerShell（wmic 已在 Win10 1803+/Win11 弃用）
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]
            if data:
                cpu = data[0]
                spec.cpu_name = cpu.get("Name", "Unknown").strip()
                try:
                    spec.cpu_cores = int(cpu.get("NumberOfCores", 0))
                except (ValueError, TypeError):
                    pass
                try:
                    spec.cpu_threads = int(cpu.get("NumberOfLogicalProcessors", 0))
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.debug(f"[HW] CPU 检测失败 (PowerShell): {e}")
        # Fallback: platform.processor()
        try:
            import platform
            spec.cpu_name = platform.processor() or "Unknown"
        except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # RAM — PowerShell（wmic 已在 Win10 1803+/Win11 弃用）
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if not isinstance(data, list):
                data = [data]
            if data:
                total_bytes = int(data[0].get("TotalPhysicalMemory", 0))
                if total_bytes > 0:
                    spec.ram_total_gb = total_bytes / (1024 ** 3)
    except Exception as e:
        logger.debug(f"[HW] RAM 检测失败 (PowerShell): {e}")
        # Fallback: psutil
        try:
            import psutil
            spec.ram_total_gb = psutil.virtual_memory().total / (1024 ** 3)
        except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # 可用内存（使用 psutil）
    try:
        import psutil
        spec.ram_available_gb = psutil.virtual_memory().available / (1024 ** 3)
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # GPU — nvidia-smi
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            shell=False, text=True, stderr=subprocess.DEVNULL, timeout=10
        ).strip()
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split(",", 1)
            spec.gpu_name = parts[0].strip()
            try:
                spec.gpu_vram_gb = float(parts[1].strip()) / 1024  # MiB → GB
            except ValueError as _e:
                logger.debug(f"[hardware_detector] 忽略: {_e}")
            spec.gpu_is_nvidia = True
            spec.has_dedicated_gpu = True
            break
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # GPU — PowerShell Get-CimInstance（AMD / Intel / 集显）
    if not spec.has_dedicated_gpu:
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Csv -NoTypeInformation"],
                shell=False, text=True, stderr=subprocess.DEVNULL, timeout=15
            ).strip()
            for line in out.splitlines():
                if not line or line.startswith('"Name"'):
                    continue
                m = re.search(r'"([^"]+)",(\d+)', line)
                if m:
                    name = m.group(1)
                    ram_bytes = int(m.group(2))
                    # 只取第一个独显（AdapterRAM > 1GB 认为是独显）
                    if ram_bytes > 1_000_000_000:
                        spec.gpu_name = name
                        spec.gpu_vram_gb = ram_bytes / (1024 ** 3)
                        spec.has_dedicated_gpu = True
                        if "amd" in name.lower() or "radeon" in name.lower():
                            spec.gpu_is_amd = True
                        if "intel" in name.lower() or "arc" in name.lower():
                            spec.gpu_is_intel = True
                        break
        except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # Ollama
    try:
        subprocess.check_call(
            ["ollama", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, timeout=5
        )
        spec.ollama_installed = True
        out = subprocess.check_output(
            ["ollama", "list"],
            shell=False, text=True, stderr=subprocess.DEVNULL, timeout=30
        ).strip()
        for line in out.splitlines()[1:]:
            if line.strip():
                spec.ollama_models.append(line.split()[0])
    except Exception:
        spec.ollama_installed = False

    # 磁盘类型检测（SSD vs HDD）
    try:
        out = subprocess.check_output(
            ["powershell", "-Command",
             "Get-PhysicalDisk | Select-Object MediaType, Model | ConvertTo-Json -Compress"],
            shell=False, text=True, timeout=10
        ).strip()
        import json as _json
        disks = _json.loads(out)
        if isinstance(disks, dict): disks = [disks]
        for d in disks:
            mt = d.get("MediaType", "").upper()
            if "SSD" in mt:
                spec.disk_type = "SSD"; spec.has_ssd = True
                spec.disk_model = d.get("Model", "")
                break
            elif "HDD" in mt:
                spec.disk_type = "HDD"
                spec.disk_model = d.get("Model", "")
                break
        if spec.disk_type == "Unknown" and disks:
            spec.disk_type = "SSD"; spec.has_ssd = True
    except Exception:
        spec.disk_type = "SSD"; spec.has_ssd = True

    return spec


def _detect_linux() -> HardwareSpec:
    spec = HardwareSpec()
    spec.os_name = "Linux"
    try:
        import platform
        spec.os_arch = platform.machine().upper()
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # CPU
    try:
        with open("/proc/cpuinfo", errors="replace") as f:
            for line in f:
                if line.startswith("model name"):
                    spec.cpu_name = line.split(":", 1)[1].strip()
                if line.startswith("cpu cores"):
                    try:
                        spec.cpu_cores = int(line.split(":", 1)[1].strip())
                    except ValueError as _e:
                        logger.debug(f"[hardware_detector] 忽略: {_e}")
        spec.cpu_threads = spec.cpu_cores * 2 if spec.cpu_cores else 0
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # RAM
    try:
        with open("/proc/meminfo", errors="replace") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    # KiB
                    spec.ram_total_gb = int(re.search(r"\d+", line).group()) / (1024 ** 2)
                    break
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    try:
        import psutil
        spec.ram_available_gb = psutil.virtual_memory().available / (1024 ** 3)
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # GPU — nvidia-smi
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,csv,noheader,nounits"],
            shell=False, text=True, stderr=subprocess.DEVNULL, timeout=10
        ).strip()
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split(",", 1)
            spec.gpu_name = parts[0].strip()
            try:
                spec.gpu_vram_gb = float(parts[1].strip()) / 1024
            except ValueError as _e:
                logger.debug(f"[hardware_detector] 忽略: {_e}")
            spec.gpu_is_nvidia = True
            spec.has_dedicated_gpu = True
            break
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # GPU — lspci（AMD / Intel）
    if not spec.has_dedicated_gpu:
        try:
            out = subprocess.check_output(
                ["lspci", "-v"],
                shell=False, text=True, stderr=subprocess.DEVNULL, timeout=10
            )
            for line in out.splitlines():
                if "VGA" in line or "3D" in line:
                    if "AMD" in line or "Radeon" in line:
                        spec.gpu_name = line.strip()
                        spec.gpu_is_amd = True
                        spec.has_dedicated_gpu = True
                        break
                    if "Intel" in line or "Arc" in line:
                        spec.gpu_name = line.strip()
                        spec.gpu_is_intel = True
                        spec.has_dedicated_gpu = True
                        break
        except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # Ollama
    try:
        subprocess.check_call(
            ["ollama", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, timeout=5
        )
        spec.ollama_installed = True
        out = subprocess.check_output(
            ["ollama", "list"],
            shell=False, text=True, stderr=subprocess.DEVNULL, timeout=30
        ).strip()
        for line in out.splitlines()[1:]:
            if line.strip():
                spec.ollama_models.append(line.split()[0])
    except Exception:
        spec.ollama_installed = False

    # 磁盘类型检测（SSD vs HDD）
    try:
        out = subprocess.check_output(
            ["powershell", "-Command",
             "Get-PhysicalDisk | Select-Object MediaType, Model | ConvertTo-Json -Compress"],
            shell=False, text=True, timeout=10
        ).strip()
        import json as _json
        disks = _json.loads(out)
        if isinstance(disks, dict): disks = [disks]
        for d in disks:
            mt = d.get("MediaType", "").upper()
            if "SSD" in mt:
                spec.disk_type = "SSD"; spec.has_ssd = True
                spec.disk_model = d.get("Model", "")
                break
            elif "HDD" in mt:
                spec.disk_type = "HDD"
                spec.disk_model = d.get("Model", "")
                break
        if spec.disk_type == "Unknown" and disks:
            spec.disk_type = "SSD"; spec.has_ssd = True
    except Exception:
        spec.disk_type = "SSD"; spec.has_ssd = True

    return spec


def _detect_macos() -> HardwareSpec:
    spec = HardwareSpec()
    spec.os_name = "macOS"
    try:
        import platform
        spec.os_arch = platform.machine().upper()
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # CPU + RAM — sysctl
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            shell=False, text=True, stderr=subprocess.DEVNULL, timeout=5
        ).strip()
        spec.cpu_name = out
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"],
            shell=False, text=True, stderr=subprocess.DEVNULL, timeout=5
        ).strip()
        spec.ram_total_gb = int(out) / (1024 ** 3)
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    try:
        import psutil
        spec.ram_available_gb = psutil.virtual_memory().available / (1024 ** 3)
    except Exception as _e: logger.debug(f"[hardware_detector] 忽略: {_e}")

    # GPU — 统一内存架构（不区分 VRAM）
    # Apple Silicon 使用统一内存，不需要单独显存
    spec.has_dedicated_gpu = False
    spec.gpu_name = "Apple Silicon 统一内存"

    # Ollama
    try:
        subprocess.check_call(
            ["ollama", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False, timeout=5
        )
        spec.ollama_installed = True
        out = subprocess.check_output(
            ["ollama", "list"],
            shell=False, text=True, stderr=subprocess.DEVNULL, timeout=30
        ).strip()
        for line in out.splitlines()[1:]:
            if line.strip():
                spec.ollama_models.append(line.split()[0])
    except Exception:
        spec.ollama_installed = False

    # 磁盘类型检测（SSD vs HDD）
    try:
        out = subprocess.check_output(
            ["powershell", "-Command",
             "Get-PhysicalDisk | Select-Object MediaType, Model | ConvertTo-Json -Compress"],
            shell=False, text=True, timeout=10
        ).strip()
        import json as _json
        disks = _json.loads(out)
        if isinstance(disks, dict): disks = [disks]
        for d in disks:
            mt = d.get("MediaType", "").upper()
            if "SSD" in mt:
                spec.disk_type = "SSD"; spec.has_ssd = True
                spec.disk_model = d.get("Model", "")
                break
            elif "HDD" in mt:
                spec.disk_type = "HDD"
                spec.disk_model = d.get("Model", "")
                break
        if spec.disk_type == "Unknown" and disks:
            spec.disk_type = "SSD"; spec.has_ssd = True
    except Exception:
        spec.disk_type = "SSD"; spec.has_ssd = True

    return spec


def detect_hardware() -> HardwareSpec:
    """自动检测本机硬件规格（跨平台）"""
    plat = sys.platform.lower()
    if plat.startswith("win"):
        return _detect_windows()
    elif plat.startswith("linux"):
        return _detect_linux()
    elif plat.startswith("darwin"):
        return _detect_macos()
    else:
        logger.warning(f"[HW] 未知平台: {plat}，使用默认值")
        return HardwareSpec()


# ─────────────────────────────────────────────
# 3. 模型数据库（含最低配置要求）
# ─────────────────────────────────────────────
MODEL_DB: List[Dict] = [
    # ─── 入门级（无独显也能跑）───
    {
        "model_id": "qwen2.5:3b",
        "name": "Qwen2.5-3B（入门保底）",
        "min_ram_gb": 8,
        "min_vram_gb": 0,       # 无独显可跑（CPU 模式）
        "recommended_vram_gb": 4,
        "size_mb": 2000,
        "speed_cpu": "5-10字/秒",
        "speed_gpu": "20-40字/秒",
        "quality": "⭐⭐⭐",
        "use_case": "日常对话、简单问答、低配电脑",
        "tier": "starter",
    },
    {
        "model_id": "qwen3.5:4b",
        "name": "Qwen3.5-4B（入门优选）",
        "min_ram_gb": 8,
        "min_vram_gb": 0,       # 无独显可跑（CPU 模式）
        "recommended_vram_gb": 6,
        "size_mb": 4200,
        "speed_cpu": "8-15字/秒",
        "speed_gpu": "30-60字/秒",
        "quality": "⭐⭐⭐⭐",
        "use_case": "日常对话、简单问答、自带视觉看图，替代 3B 老款",
        "tier": "starter",
    },
    # ─── 主流级（6-8GB 显存）───
    {
        "model_id": "qwen2.5:7b",
        "name": "Qwen2.5-7B（主流推荐）",
        "min_ram_gb": 12,
        "min_vram_gb": 6,
        "recommended_vram_gb": 8,
        "size_mb": 4500,
        "speed_cpu": "3-6字/秒",
        "speed_gpu": "15-30字/秒",
        "quality": "⭐⭐⭐⭐",
        "use_case": "日常对话、写作、代码、翻译",
        "tier": "mainstream",
    },
    {
        "model_id": "llama3.2:8b",
        "name": "Llama-3.2-8B",
        "min_ram_gb": 14,
        "min_vram_gb": 8,
        "recommended_vram_gb": 10,
        "size_mb": 5000,
        "speed_cpu": "2-5字/秒",
        "speed_gpu": "12-25字/秒",
        "quality": "⭐⭐⭐⭐",
        "use_case": "通用对话、英文能力好",
        "tier": "mainstream",
    },
    # ─── 高性能级（10-12GB 显存）───
    {
        "model_id": "qwen2.5:14b",
        "name": "Qwen2.5-14B（高性能）",
        "min_ram_gb": 16,
        "min_vram_gb": 10,
        "recommended_vram_gb": 12,
        "size_mb": 9000,
        "speed_cpu": "1-3字/秒",
        "speed_gpu": "10-20字/秒",
        "quality": "⭐⭐⭐⭐⭐",
        "use_case": "深度推理、复杂写作、代码生成",
        "tier": "high_performance",
    },
    {
        "model_id": "deepseek-r1:14b",
        "name": "DeepSeek-R1-Distill-14B（推理增强）",
        "min_ram_gb": 16,
        "min_vram_gb": 10,
        "recommended_vram_gb": 12,
        "size_mb": 9000,
        "speed_cpu": "1-3字/秒",
        "speed_gpu": "8-15字/秒",
        "quality": "⭐⭐⭐⭐⭐",
        "use_case": "数学推理、代码推理、逻辑分析",
        "tier": "high_performance",
    },
    # ─── 旗舰级（16GB+ 显存）───
    {
        "model_id": "qwen2.5:32b",
        "name": "Qwen2.5-32B（旗舰）",
        "min_ram_gb": 32,
        "min_vram_gb": 20,
        "recommended_vram_gb": 24,
        "size_mb": 20000,
        "speed_cpu": "极慢",
        "speed_gpu": "5-12字/秒",
        "quality": "⭐⭐⭐⭐⭐",
        "use_case": "专业研究、复杂任务、最高质量",
        "tier": "flagship",
    },
]


# ─────────────────────────────────────────────
# 4. 模型推荐引擎
# ─────────────────────────────────────────────
def recommend_models(spec: HardwareSpec) -> Tuple[List[Dict], Optional[Dict], List[Dict]]:
    """
    根据硬件规格推荐模型
    
    Returns:
        (recommended, best_match, all_compatible)
        - recommended: 推荐安装的模型列表（按优先级排序）
        - best_match: 最匹配的单个模型（可设为默认）
        - all_compatible: 所有可运行的模型
    """
    compatible = []
    VRAM_TOLERANCE = 1.05  # 5% 容差，避免 11.99GB 对 12GB 的边界问题
    for m in MODEL_DB:
        # 检查 RAM
        if spec.ram_total_gb < m["min_ram_gb"]:
            continue
        # 检查 VRAM（有独显时，加 5% 容差）
        if spec.has_dedicated_gpu:
            if spec.gpu_vram_gb * VRAM_TOLERANCE < m["min_vram_gb"]:
                continue
        # 无独显时，只允许 min_vram_gb == 0 的模型（CPU 模式）
        else:
            if m["min_vram_gb"] > 0:
                continue
        compatible.append(m)

    if not compatible:
        # 没有任何模型满足要求，返回最低配的 3B
        compatible = [MODEL_DB[0]]

    # 按 tier 排序（显示用：starter → mainstream → high_performance → flagship）
    tier_order = {"starter": 0, "mainstream": 1, "high_performance": 2, "flagship": 3}
    compatible.sort(key=lambda x: (tier_order.get(x["tier"], 99), x["min_vram_gb"]))

    # 最佳匹配：从高端往低端找，第一个满足 recommended_vram_gb 的（加 5% 容差）
    best_match = None
    for m in reversed(compatible):
        if spec.has_dedicated_gpu:
            if spec.gpu_vram_gb * VRAM_TOLERANCE >= m["recommended_vram_gb"]:
                best_match = m
                break
        else:
            best_match = m
            break
    if not best_match and compatible:
        best_match = compatible[-1]  # 最高端的一个

    # 推荐列表：最佳匹配 + 下一个备选（更低端保底）
    recommended = []
    if best_match:
        recommended.append(best_match)
        idx = next((i for i, m in enumerate(compatible) if m["model_id"] == best_match["model_id"]), -1)
        # 添加低一档的备选（如果存在且不同于最佳匹配）
        if idx > 0:
            alt = compatible[idx - 1]
            if alt["model_id"] != best_match["model_id"]:
                recommended.append(alt)

    return recommended, best_match, compatible


# ── 多模型并行内存估算 ──

# 常见模型的内存占用参考（GB）
_MODEL_MEMORY_ESTIMATES = {
    # Ollama 语言模型
    "qwen2.5:3b": 2.2, "qwen2.5:7b": 5.0, "qwen2.5:14b": 9.0, "qwen2.5:32b": 20.0,
    "deepseek-r1:1.5b": 1.2, "deepseek-r1:7b": 5.0, "deepseek-r1:8b": 5.5, "deepseek-r1:14b": 9.0,
    "llama3.2:3b": 2.0, "llama3.1:8b": 5.5, "llama3.1:70b": 42.0,
    "gemma3:4b": 2.8, "gemma3:12b": 8.0,
    "phi4-mini:3.8b": 2.6, "phi4:14b": 9.0,
    "mistral:7b": 5.0, "mixtral:8x7b": 24.0,
    "codellama:7b": 5.0, "codellama:13b": 8.5,
    # 垂直模型
    "musicgen-small": 1.8, "musicgen-medium": 3.5, "musicgen-large": 5.0,
    "bark": 2.0, "bark-small": 1.0,
    "stable-diffusion": 3.0, "sdxl": 6.0, "sd3-medium": 8.0,
    "whisper": 1.5, "whisper-large": 3.0,
}
# 系统 + Ollama server 基础开销
_SYSTEM_OVERHEAD_GB = 2.0
_OLLAMA_SERVER_OVERHEAD_GB = 1.0


def get_memory_warnings(spec: HardwareSpec, active_models: list = None) -> dict:
    """
    分析硬件是否支持多模型并行运行。

    Args:
        spec: 硬件规格
        active_models: 当前激活的模型列表（model_id 列表），用于精确估算

    Returns:
        {
            "ok": bool,           # 是否有足够内存并行
            "total_ram_gb": float,
            "available_ram_gb": float,
            "used_estimate_gb": float,
            "free_after_gb": float,
            "warnings": [str],    # 警告消息列表
            "recommendations": [str],
        }
    """
    result = {
        "ok": True,
        "total_ram_gb": round(spec.ram_total_gb, 1),
        "available_ram_gb": round(spec.ram_available_gb, 1),
        "used_estimate_gb": _SYSTEM_OVERHEAD_GB,
        "free_after_gb": 0,
        "warnings": [],
        "recommendations": [],
    }

    if active_models:
        for mid in active_models:
            # 尝试部分匹配
            est = 0
            for key, val in _MODEL_MEMORY_ESTIMATES.items():
                if key in mid.lower() or mid.lower() in key:
                    est = max(est, val)
            if est == 0:
                est = 3.0  # 未知模型默认估算 3GB
            result["used_estimate_gb"] += est
            # 非 Ollama 模型不需要 Ollama server
            if any(kw in mid.lower() for kw in ['ollama', 'qwen', 'llama', 'deepseek', 'gemma', 'phi', 'mistral', 'mixtral', 'codellama']):
                result["used_estimate_gb"] += 0  # Ollama server overhead counted once later
    else:
        # 无具体模型列表，估算常见场景
        result["used_estimate_gb"] += _OLLAMA_SERVER_OVERHEAD_GB  # Ollama server
        result["used_estimate_gb"] += 5.0  # 平均一个语言模型

    # 判断
    result["free_after_gb"] = round(spec.ram_available_gb - result["used_estimate_gb"], 1)

    if spec.ram_total_gb < 16:
        result["ok"] = False
        result["warnings"].append(
            f"⚠️ 系统内存仅 {spec.ram_total_gb:.0f}GB，不推荐同时运行多个本地模型。"
        )
        result["recommendations"].append("升级到 16GB+ 内存以获得更好的多模型体验")

    if result["free_after_gb"] < 2:
        result["ok"] = False
        result["warnings"].append(
            f"⚠️ 预估剩余内存仅 {result['free_after_gb']:.1f}GB，运行多模型可能导致系统卡顿或 OOM"
        )
        result["recommendations"].append("关闭不需要的模型以释放内存")

    if spec.gpu_vram_gb < 6 and spec.has_dedicated_gpu:
        result["warnings"].append(
            f"💡 独显显存 {spec.gpu_vram_gb:.0f}GB，大模型可能回退到 CPU，内存压力更大"
        )

    return result


def estimate_model_memory(model_id: str) -> float:
    """估算单个模型的内存占用"""
    for key, val in _MODEL_MEMORY_ESTIMATES.items():
        if key in model_id.lower() or model_id.lower() in key:
            return val
    return 3.0  # 默认 3GB
    """获取入门保底模型（内置，无需下载）"""
    return MODEL_DB[0].copy()  # qwen2.5:3b


def format_recommendation(spec: HardwareSpec) -> str:
    """生成人类可读的推荐报告"""
    recommended, best, compatible = recommend_models(spec)
    lines = [
        "═══════════════════════════════════════",
        "       Harven AI 硬件自检报告",
        "═══════════════════════════════════════",
        "",
        "【本机配置】",
        spec.summary(),
        "",
        "【推荐模型】",
    ]
    if recommended:
        for i, m in enumerate(recommended, 1):
            lines.append(f"  {i}. {m['name']}")
            lines.append(f"     用途：{m['use_case']}")
            lines.append(f"     质量：{m['quality']}  大小：{m['size_mb']} MB")
            lines.append(f"     GPU速度：{m['speed_gpu']}  CPU速度：{m['speed_cpu']}")
            lines.append("")
    else:
        lines.append("  ❌ 未找到兼容模型，建议使用云端 API 模式")

    if best:
        lines.append(f"【建议默认模型】{best['name']}")
        lines.append("")

    lines.append("【所有可运行模型】")
    for m in compatible:
        marker = " ✅ 推荐" if m["model_id"] == (best["model_id"] if best else "") else ""
        lines.append(f"  • {m['name']}{marker}")
    lines.append("")
    lines.append("═══════════════════════════════════════")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# 5. 模型自动安装
# ─────────────────────────────────────────────
def pull_model(model_id: str, callback=None) -> bool:
    """
    使用 ollama pull 下载模型
    callback(progress_str) 用于报告进度
    """
    if not Path("ollama").exists():
        if callback:
            callback("❌ Ollama 未安装，请先安装 Ollama")
        return False

    try:
        if callback:
            callback(f"开始下载 {model_id} ...")
        proc = subprocess.Popen(
            ["ollama", "pull", model_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        for line in proc.stdout:
            if callback:
                callback(line.strip())
        proc.wait()
        if proc.returncode == 0:
            if callback:
                callback(f"✅ {model_id} 下载完成！")
            return True
        else:
            if callback:
                callback(f"❌ 下载失败（退出码 {proc.returncode}）")
            return False
    except Exception as e:
        if callback:
            callback(f"❌ 下载出错：{e}")
        return False


# ─────────────────────────────────────────────
# 6. 入口（命令行测试）
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("检测硬件中...")
    spec = detect_hardware()
    print()
    print(spec.summary())
    print()
    recommended, best, compatible = recommend_models(spec)
    print("推荐模型：")
    for m in recommended:
        print(f"  - {m['name']}")
    print()
    print("最佳匹配：", best["name"] if best else "无")
    print()
    print(format_recommendation(spec))
