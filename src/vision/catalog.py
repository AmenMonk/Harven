# -*- coding: utf-8 -*-
"""★ #1292（2026-10-29）HarvenEye 理解大脑精选目录（方案A①）。

原则（已定版）：
- 目录条目 = 官方源核实 / 开发者机器实测过的档位，不收录野生权重；
- SHA256 优先官方已验证值（旧 Eye MANIFEST MATCH）；第三方转档拿不到官方哈希的
  留空（下载器自动降级为体积校验 + 装后冒烟，如实标注「来源未验证」）；
- 铁律 0：目录只是推荐清单，装不装永远等用户点头（FC 工具 confirmed 门禁）。
"""

# 本地现成源（免下载）：旧独立 Eye 的模型目录（10-25 装的那对，SHA 已 MATCH）
_EYE_LOCAL = r"H:\HarvenEye\models\qwen3-vl-8b"

HF = "https://huggingface.co"
HM = "https://hf-mirror.com"
MS = "https://www.modelscope.cn"
# ★ #1292 下载源实测（10-29 晚）：hf-mirror 第三方仓库仅 ~70KB/s（6.3GB 要 25h），
#   魔搭 CDN 实测 9.86MB/s（快 140 倍）——国内源优先魔搭，hf-mirror 只当备用。
#   Q6_K 官方仓库没有（只有 F16/Q4_K_M/Q8_0），unsloth 魔搭搬运版是唯一快源。

CATALOG = [
    {
        "id": "qwen3-vl-8b-q4km",
        "dir_name": "qwen3-vl-8b",
        "label": "Qwen3-VL 8B Q4_K_M（均衡首选）",
        "tier": "balanced",
        "min_vram_gb": 8,
        "size_gb": 5.8,
        "enabled": True,
        "desc": "速度/质量/显存三平衡，RTX 4070 12GB 实测全进显存（热态 1.1s/图）",
        "verified": True,
        "local_src": {"gguf": _EYE_LOCAL + r"\Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                      "mmproj": _EYE_LOCAL + r"\mmproj-Qwen3VL-8B-Instruct-F16.gguf"},
        "files": [
            {"filename": "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "size": 5027784800,
             "sha256": "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2",
             "urls": [f"{MS}/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/master/Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                      f"{HM}/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                      f"{HF}/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf"]},
            {"filename": "mmproj-Qwen3VL-8B-Instruct-F16.gguf", "size": 1159029824,
             "sha256": "ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38",
             "urls": [f"{MS}/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/master/mmproj-Qwen3VL-8B-Instruct-F16.gguf",
                      f"{HM}/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-F16.gguf",
                      f"{HF}/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-F16.gguf"]},
        ],
    },
    {
        "id": "qwen3-vl-8b-q6k",
        "dir_name": "qwen3-vl-8b-q6k",
        "label": "Qwen3-VL 8B Q6_K（求准）",
        "tier": "quality",
        "min_vram_gb": 10,
        "size_gb": 7.3,
        "enabled": True,
        "desc": "近乎无损精度，看小字账单/票据更稳；比 Q4 多占 1.5GB 显存，速度略慢",
        "verified": False,   # 第三方转档（Comfy-Org 系），无官方哈希
        "files": [
            {"filename": "Qwen3-VL-8B-Instruct-Q6_K.gguf", "size": 6_260_000_000, "sha256": "",
             "urls": [f"{MS}/unsloth/Qwen3-VL-8B-Instruct-GGUF/resolve/master/Qwen3-VL-8B-Instruct-Q6_K.gguf",
                      f"{HM}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/qwen3vl-8b-Q6_K.gguf",
                      f"{HF}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/qwen3vl-8b-Q6_K.gguf"]},
            {"filename": "mmproj-F16.gguf", "size": 1_080_000_000, "sha256": "",
             "urls": [f"{MS}/unsloth/Qwen3-VL-8B-Instruct-GGUF/resolve/master/mmproj-F16.gguf",
                      f"{HM}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/mmproj-qwen3vl-8b-f16.gguf",
                      f"{HF}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/mmproj-qwen3vl-8b-f16.gguf"]},
        ],
    },
    {
        "id": "qwen3-vl-4b-q4km",
        "dir_name": "qwen3-vl-4b",
        "label": "Qwen3-VL 4B Q4_K_M（轻量快速）",
        "tier": "light",
        "min_vram_gb": 4,
        "size_gb": 3.1,
        "enabled": True,
        "desc": "小显存/老机器之选，日常看图够用；复杂图（密集文字/多物体）不如 8B",
        "verified": False,
        "files": [
            {"filename": "qwen3vl-4b-Q4_K_M.gguf", "size": 2_330_000_000, "sha256": "",
             "urls": [f"{HM}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/qwen3vl-4b-Q4_K_M.gguf",
                      f"{HF}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/qwen3vl-4b-Q4_K_M.gguf"]},
            {"filename": "mmproj-qwen3vl-4b-f16.gguf", "size": 780_000_000, "sha256": "",
             "urls": [f"{HM}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/mmproj-qwen3vl-4b-f16.gguf",
                      f"{HF}/Abiray/ComfyUI-Qwen3-VL-GGUF/resolve/main/mmproj-qwen3vl-4b-f16.gguf"]},
        ],
    },
    {
        "id": "qwen3-vl-30b-a3b-q4km",
        "dir_name": "qwen3-vl-30b-a3b",
        "label": "Qwen3-VL 30B-A3B Q4_K_M（旗舰·暂缓）",
        "tier": "flagship",
        "min_vram_gb": 24,
        "size_gb": 18.4,
        "enabled": False,   # ★ 引擎尚未暴露 MoE 专家层分载参数，硬装会 OOM（10-29 定论）
        "desc": "质量天花板（MoE 仅 3B 激活，4070+32GB 内存实测可 22~33 tok/s），等引擎加分载开关后启用",
        "verified": False,
        "files": [
            {"filename": "Qwen3VL-30B-A3B-Instruct-Q4_K_M.gguf", "size": 17_300_000_000, "sha256": "",
             "urls": [f"{HM}/Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF/resolve/main/Qwen3VL-30B-A3B-Instruct-Q4_K_M.gguf",
                      f"{HF}/Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF/resolve/main/Qwen3VL-30B-A3B-Instruct-Q4_K_M.gguf"]},
            {"filename": "mmproj-Qwen3VL-30B-A3B-Instruct-F16.gguf", "size": 1_100_000_000, "sha256": "",
             "urls": [f"{HM}/Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-30B-A3B-Instruct-F16.gguf",
                      f"{HF}/Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-30B-A3B-Instruct-F16.gguf"]},
        ],
    },
]


def get(model_id: str):
    """按 id 取目录条目。"""
    for e in CATALOG:
        if e["id"] == model_id:
            return e
    return None


def fit_list(vram_gb: float) -> list:
    """按显存给可装清单（enabled 且 min_vram_gb 达标，按体积降序=质量优先）。"""
    return [e for e in CATALOG
            if e.get("enabled") and vram_gb >= e["min_vram_gb"]]
