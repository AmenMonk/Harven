"""哈文视觉子系统配置 — 单一事实来源（v1.3 融合定版）

规则依据: 《HarvenEye制作规则说明 v1.3（2026-10-29 融合定版）》
  - 定位：HarvenEye 底座（检测/索引）融合为哈文内置子系统；
    理解槽无专属大脑，走用户在哈文选的模型
  - 第三章 四层瀑布管线（L0~L3）
  - 第四章 三槽位引擎适配：槽位接口永不变，槽内模型随便换
  - 旧独立进程形态（7001 / api.py / supervisor）按 v1.3 退役
"""
from __future__ import annotations

import os
from pathlib import Path

# ── 路径（哈文代码树内）─────────────────────────────────────────────
# src/vision/config.py → parents[2] = 哈文根（如 H:\HarvenAI）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 模型真家: data/models/vision/（data/models 整体被安装包排除，符合「装包不含模型」铁律）
# HARVENEYE_MODELS 环境变量可覆盖（多实例/验收测试隔离）
MODELS_DIR = Path(os.environ.get(
    "HARVENEYE_MODELS", str(PROJECT_ROOT / "data" / "models" / "vision")))
DATA_DIR = PROJECT_ROOT / "data" / "vision"
# HARVENEYE_DB / HARVENEYE_INBOX 环境变量可覆盖对应路径（多实例/验收测试隔离，默认走 data/ 真家）
DB_PATH = Path(os.environ.get("HARVENEYE_DB", str(DATA_DIR / "eye_catalog.db")))
THUMB_DIR = DATA_DIR / "thumbs"              # 缩略图（≤256px，~10KB/张）
INBOX_DIR = Path(os.environ.get("HARVENEYE_INBOX", str(DATA_DIR / "inbox")))
MAX_UPLOAD_MB = 20                           # 单张上传上限

# ── 三槽位引擎（槽位接口永不变，槽内模型随便换）────────────────────
ENGINES = {
    "detect": {                               # 槽位1: 检测 YOLOX (Apache 2.0)
        "nano": str(MODELS_DIR / "yolox" / "yolox_nano.onnx"),   # 416x416 边缘档
        "s":    str(MODELS_DIR / "yolox" / "yolox_s.onnx"),      # 640x640 桌面档
        "profile": "s",                       # 桌面基线档；AR/手机切 nano
    },
    "index": {                                # 槽位2: 索引 Chinese-CLIP
        "model_dir": str(MODELS_DIR / "chinese-clip"),
    },
    "understand": {                           # 槽位3: 理解 — 无专属大脑，provider 三通道
        # ★ v1.3 融合定版（2026-10-29）: 理解走用户选的模型（铁律 0）
        #   ollama    = 本机 Ollama REST（过渡兼容通道，模型管家时代退役）
        #   llama_cpp = 进程内直载 GGUF（主模型+mmproj，哈文内置播放器，最终形态）
        #   api       = 外脑视觉 API（用户选的外脑模型，如 GLM-4V）—— 接线在 ② 节点
        "provider": os.environ.get("HARVENEYE_VLM_PROVIDER", "ollama"),
        "base_url": os.environ.get("HARVENEYE_OLLAMA", "http://127.0.0.1:11434"),
        # ollama 通道过渡模型（#1257: -instruct 快速版，裸 tag 是 Thinking 变体易超时）
        "model": "qwen3-vl:8b-instruct",
        # llama_cpp 通道文件（模型管家下载后自动填充；空=未安装 → 降级报错带指引）
        "gguf": "",
        "mmproj": "",
        "num_ctx": 16384,                     # ★ #1270 同源教训：长批改 prompt+图需要 16K
    },
}

# ── ★ #1424 understand 槽自动接线（2026-11-04 完全体「有脑无接线」根治）──────
# 开发者笔记本实锤：完全体捆了 Qwen3-VL 权重，但 provider 默认 ollama 指向用户从没拉过的
# tag + gguf/mmproj 空字符串 + llama-cpp-python 没装 → 开箱必坏。此处启动时探测权重：
#   GGUF+mmproj 都在 → 自动切 llama_cpp 通道并填路径（完全体开箱即满血）
#   不在 → 维持 ollama 现状（标准版/轻量版行为不变，模型管家下载后重开哈文也自动接上）
# env HARVENEYE_VLM_PROVIDER 显式指定仍最高优先（不破坏现有调试通道），但路径照填（受益）
def _autowire_understand() -> None:
    qwen_dir = MODELS_DIR / "qwen3-vl-4b"
    if not qwen_dir.is_dir():
        return
    ggufs = list(qwen_dir.glob("*.gguf"))
    mmproj_files = [p for p in ggufs if "mmproj" in p.name.lower()]
    main_files = [p for p in ggufs if "mmproj" not in p.name.lower()]
    if not mmproj_files or not main_files:
        return
    # 主模型取最大的那个（量化档可能多版本共存）
    gguf = max(main_files, key=lambda p: p.stat().st_size)
    mmproj = max(mmproj_files, key=lambda p: p.stat().st_size)
    u = ENGINES["understand"]
    u["gguf"] = str(gguf)
    u["mmproj"] = str(mmproj)
    if not os.environ.get("HARVENEYE_VLM_PROVIDER"):
        u["provider"] = "llama_cpp"

_autowire_understand()

# ── 三档置信度门控（13.3）──────────────────────────────────────────
GATING_PRESETS = {
    "fast":     0.35,   # ⚡快：速度优先
    "balanced": 0.50,   # ⚖️平衡：默认
    "accurate": 0.65,   # 🎯准：准确优先
}
DEFAULT_PRESET = "balanced"

# ── 性能预算 P50/P95（毫秒，13.3）──────────────────────────────────
LATENCY_BUDGET_MS = {
    "L0_hash":     (2, 5),       # dhash 查重
    "L1_detect":   (30, 80),     # YOLOX
    "L2_index":    (60, 150),    # Chinese-CLIP
    "L3_vlm":      (3000, 15000) # VLM 兜底
}

# ── L3 降级（13.2 显存调度器）──────────────────────────────────────
VLM_TIMEOUT_S = 60          # ★ #1299（2026-10-29）20→60：旧值是给 CLIP 打标设的快超时，
                            #   Q6_K 读卷子（视觉预填充+长答）远超 20s → eye_ask 未传参时必炸；
                            #   重活链路已显式传 120s（#1298），此处只作未传参场景的保险垫
VLM_LOCK_TIMEOUT_S = 20

# ── 通用对话超时（理解槽对话通道）────────────────────────────────
CHAT_TIMEOUT_S = 120         # 非流式对话总超时（对话比看图长）
CHAT_STREAM_FIRST_S = 60     # 流式首包超时（冷加载模型可能要几十秒）
CHAT_KEEP_ALIVE = '30m'      # ollama 通道：常驻 30 分钟防闲置卸载（#1243 实锤教训）
CHAT_NUM_CTX = 16384         # ★ #1270（2026-10-29 批改 400 实锤）：4197>4096 超限；
                             #   16K 覆盖长批改 prompt+图+JSON 输出，KV 增量 ~1GB

# ── 视觉索引库（第五章 catalog 模式）──────────────────────────────
VECTOR_DIM = 512            # Chinese-CLIP vit-base 实际投影维度（M1-2 实测，非 768）
THUMB_SIZE = 256            # 缩略图最长边（px）
LOST_POINTER_TTL_DAYS = 30  # 失指图保留天数
# ⭐ 零原图入库铁律：库内只有 指纹+向量+标签+缩略图指针，原图真家=指针指向
