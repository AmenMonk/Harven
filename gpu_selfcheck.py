# -*- coding: ascii -*-
"""#1449 (2026-11-05 r50) post-install vision GPU self-check + runtime self-heal.

Two silent-CPU-fallback causes found tonight (desktop + laptop both hit):
  1. The llama-cpp-python cu124 wheel ships WITHOUT the CUDA runtime DLLs
     (cudart64_12 / cublas64_12 / cublasLt64_12). ggml-cuda.dll cannot load
     its dependencies -> CUDA backend silently unavailable -> CPU only.
     FIX: copy the three DLLs from torch\\lib next to ggml-cuda.dll.
  2. Old NVIDIA drivers (<551.61) cannot drive cu124 builds at all
     (laptop RTX 3060 + driver 511.81). FIX: upgrade the driver.

NOTE: llama_cpp.llama_supports_gpu_offload() reports FALSE negatives when
called outside a real model load (verified 2026-11-05) - do NOT use it as
the verdict. The reliable probe is ctypes ggml_backend_cuda_init(0).

Exit codes:
  0 = ok, or nothing to check (no NVIDIA GPU -> CPU is expected)
  2 = warning printed (caller should pause so the user can read it)
"""
import ctypes
import os
import shutil
import subprocess
import sys


def heal_cuda_runtime():
    """Copy CUDA runtime DLLs from torch\\lib next to ggml-cuda.dll (idempotent)."""
    site = os.path.join(sys.prefix, "Lib", "site-packages")
    ll = os.path.join(site, "llama_cpp", "lib")
    tl = os.path.join(site, "torch", "lib")
    if not os.path.isfile(os.path.join(ll, "ggml-cuda.dll")):
        return False
    for name in ("cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"):
        dst = os.path.join(ll, name)
        src = os.path.join(tl, name)
        if not os.path.isfile(dst) and os.path.isfile(src):
            try:
                shutil.copy2(src, dst)
                print("       self-heal: copied " + name)
            except Exception:
                pass
    return True


def probe_cuda_backend():
    """True/False = CUDA backend init verdict; None = llama_cpp not installed."""
    site = os.path.join(sys.prefix, "Lib", "site-packages")
    ll = os.path.join(site, "llama_cpp", "lib")
    tl = os.path.join(site, "torch", "lib")
    cuda_dll = os.path.join(ll, "ggml-cuda.dll")
    if not os.path.isfile(cuda_dll):
        return None
    try:
        os.add_dll_directory(ll)
        if os.path.isdir(tl):
            os.add_dll_directory(tl)
        ext = ctypes.CDLL(cuda_dll)
    except OSError:
        return False
    try:
        ext.ggml_backend_cuda_init.restype = ctypes.c_void_p
        ext.ggml_backend_cuda_init.argtypes = [ctypes.c_size_t]
        return bool(ext.ggml_backend_cuda_init(0))
    except Exception:
        return False


def main():
    drv = ""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8)
        lines = (out.stdout or "").strip().splitlines()
        if lines:
            drv = lines[0].strip()
    except Exception:
        pass

    major = 0
    if drv:
        try:
            major = int(drv.split(".")[0])
        except Exception:
            major = 0

    has_llama = heal_cuda_runtime()
    cuda_ok = probe_cuda_backend() if has_llama else None

    torch_cuda = None
    try:
        import torch
        torch_cuda = torch.cuda.is_available()
    except Exception:
        torch_cuda = None

    if cuda_ok is not None:
        print("       vision engine CUDA backend: " + ("OK" if cuda_ok else "FAILED"))
    if torch_cuda is not None:
        print("       torch CUDA available: " + ("YES" if torch_cuda else "NO"))

    if not drv:
        print("       no NVIDIA GPU detected - CPU mode is expected here.")
        return 0
    print("       NVIDIA driver: " + drv)

    if cuda_ok is False or torch_cuda is False:
        print("  " + "=" * 60)
        if major and major < 551:
            print("   WARNING: NVIDIA driver " + drv + " is too OLD for the")
            print("   CUDA build (needs 551.61 or newer). AI vision is")
            print("   running on CPU and will be VERY SLOW.")
            print("   FIX: upgrade your NVIDIA graphics driver,")
            print("   restart the computer, then start Harven AI again.")
        else:
            print("   WARNING: driver is new enough but CUDA is not active.")
            print("   Vision runs on CPU (slower). If this machine has an")
            print("   NVIDIA GPU, run this installer again to repair.")
        print("  " + "=" * 60)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
