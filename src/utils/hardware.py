"""
硬件检测模块
检测 NVIDIA 显存、内存，判断匹配版本
支持 GPU 加速 / CPU-only 双模式
"""
import logging
logger = logging.getLogger(__name__)
import subprocess
import sys


def get_gpu_info():
    """获取 NVIDIA GPU 显存信息（MB），跨平台兼容"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            gpus = []
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpus.append({
                        "name": parts[0],
                        "vram_total_mb": int(float(parts[1])),
                        "vram_free_mb": int(float(parts[2])),
                    })
            return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return []


def get_ram_gb():
    """获取系统内存（GB），跨平台兼容"""
    # Windows: 使用 kernel32 API
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(mem)
            kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return round(mem.ullTotalPhys / (1024 ** 3), 1)
        except Exception as _e: logger.debug(f"[hardware] 忽略: {_e}")

    # Linux: 读取 /proc/meminfo
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 * 1024), 1)
    except Exception as _e: logger.debug(f"[hardware] 忽略: {_e}")

    # macOS / BSD: 使用 sysctl
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return round(int(result.stdout.strip()) / (1024 ** 3), 1)
    except Exception as _e: logger.debug(f"[hardware] 忽略: {_e}")

    # psutil fallback
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception as _e: logger.debug(f"[hardware] 忽略: {_e}")

    return 0


def detect_hardware():
    """检测硬件信息"""
    gpus = get_gpu_info()
    ram_gb = get_ram_gb()

    if gpus:
        gpu = max(gpus, key=lambda g: g["vram_total_mb"])
        vram_gb = round(gpu["vram_total_mb"] / 1024, 1)
        gpu_name = gpu["name"]
    else:
        gpu_name = ""
        vram_gb = 0

    return {
        "gpu_name": gpu_name,
        "vram_gb": vram_gb,
        "ram_gb": ram_gb,
        "has_nvidia": len(gpus) > 0,
    }


def check_requirements(hw_info, min_vram=6, min_ram=16, allow_cpu=False):
    """检查硬件是否满足最低要求

    Args:
        hw_info: detect_hardware() 返回值
        min_vram: 最低显存（GB），CPU 模式下忽略
        min_ram: 最低内存（GB）
        allow_cpu: 是否允许纯 CPU 运行（无 NVIDIA 显卡）

    Returns:
        (ok: bool, issues: list[str], mode: str)
        mode: 'gpu' | 'cpu' | 'insufficient'
    """
    issues = []
    mode = "gpu"

    # 内存检查（必需）
    if hw_info["ram_gb"] < min_ram:
        issues.append(f"内存 {hw_info['ram_gb']}GB，需要至少 {min_ram}GB")

    # 显卡检查
    if not hw_info["has_nvidia"]:
        if allow_cpu:
            mode = "cpu"
            issues.append("未检测到 NVIDIA 显卡，将使用 CPU 模式（速度较慢）")
        else:
            issues.append("未检测到 NVIDIA 独立显卡")
    elif hw_info["vram_gb"] < min_vram:
        issues.append(f"显存 {hw_info['vram_gb']}GB，需要至少 {min_vram}GB")

    # 有 serious issues（非 advisory）时才算失败
    serious = [i for i in issues if "CPU 模式" not in i]
    hard_ok = len(serious) == 0

    return hard_ok, issues, mode


def recommend_version(hw_info, advanced_vram=12, normal_vram=6):
    """根据硬件推荐版本"""
    if hw_info["vram_gb"] >= advanced_vram:
        return "advanced"
    elif hw_info["vram_gb"] >= normal_vram:
        return "normal"
    if hw_info["ram_gb"] >= 8:
        return "cpu"  # CPU-only fallback
    return "insufficient"
