"""★ #1257 批5（2026-10-28 定版拍板「一步到位」）模型守卫 —— 挑/装/验/用的「挑+验」后端。

═══ 纪律（开发者铁律，不可违）═══
- **不代选**：守卫只「拦下+说清原因+引导改正」，选谁永远是用户的事
- **安装≠运行**：装是一次性动作，跑不跑由用户选中决定；守卫发生在「选模型装进视觉槽」时
- **实测不信任自报**：能不能看图不发问卷，发标准测试图当场验

三个入口（供设置页 API / FC 工具调用）：
  probe_vision(model)        安装实测：标准图+标准问 → 能否看图（真伪视觉模型一验便知）
  vram_guard(model_info)     显存守卫：✓流畅 / ⚠勉强（放行+警告）/ ✗跑不动（拦下+引导）
  capability_badges(model)   能力标记：👁看图 / 💬聊天（查 Ollama 已装模型 + 简测缓存）
"""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

_OLLAMA = "http://127.0.0.1:11434"

# ── 标准测试图：1x1 不行（模型会说图太糊），用一张明确的红球图（程序生成，不依赖外部文件）──
_TEST_PROMPT = "这张图片的主体是什么？只回答物体名称。"

_probe_cache: dict[str, tuple[bool, float]] = {}   # model → (vision_ok, ts)，缓存 10 分钟
_CACHE_TTL = 600.0


def _test_image_b64() -> str:
    """生成标准测试图（纯色大圆盘，任何视觉模型都该答「圆/球」类）。"""
    import base64
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (448, 448), (245, 245, 245))
    dr = ImageDraw.Draw(img)
    dr.ellipse((74, 74, 374, 374), fill=(200, 30, 30))          # 大红圆
    dr.ellipse((168, 168, 280, 280), fill=(255, 255, 255))      # 内白圆
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _ollama_chat_vision(model: str, prompt: str, image_b64: str, timeout: float = 60.0):
    """Ollama 视觉问答一次。返回 (ok, text, err)。"""
    try:
        r = requests.post(f"{_OLLAMA}/api/chat", json={
            "model": model, "stream": False,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
        }, timeout=timeout)
        if r.status_code != 200:
            return False, "", f"HTTP {r.status_code}: {r.text[:120]}"
        return True, (r.json().get("message") or {}).get("content", ""), ""
    except Exception as e:
        return False, "", str(e)


def probe_vision(model: str, force: bool = False) -> dict:
    """安装实测：这个模型真能看图吗？（发标准图，不信任 manifest 自报）
    判定：回答含圆/球/圆形/红 等关键词即通过——纯文本模型会答不出或胡说。"""
    key = f"{model}|v1"
    hit = _probe_cache.get(key)
    if hit and not force and (time.time() - hit[1]) < _CACHE_TTL:
        return {"ok": True, "vision": hit[0], "cached": True}
    img = _test_image_b64()
    ok, text, err = _ollama_chat_vision(model, _TEST_PROMPT, img)
    if not ok:
        return {"ok": False, "vision": False, "error": f"模型调用失败: {err}",
                "hint": "模型没装或 Ollama 没启动"}
    t = (text or "").strip()
    vision = any(k in t for k in ("圆", "球", "circle", "红", "red", "target"))
    _probe_cache[key] = (vision, time.time())
    return {"ok": True, "vision": vision, "answer": t[:80], "cached": False,
            "hint": "" if vision else "该模型答不出图片内容——是纯文本模型，进不了视觉槽（可以当聊天模型用）"}


def _gpu_vram_gb() -> float:
    """本机独显显存（GB）；无卡返回 0（走外脑路线，守卫只提示不拦）。"""
    try:
        import subprocess
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        return round(float(out[0].strip()) / 1024, 1)
    except Exception:
        return 0.0


def _model_disk_gb(model: str) -> float:
    """Ollama 模型磁盘体积（≈Q4 权重，加 ~30% 当 KV/视觉编码峰值余量）。
    ★ 2026-10-28 实锤：/api/show 新版无 size 键，改查 /api/tags（带尺寸清单）。"""
    try:
        r = requests.get(f"{_OLLAMA}/api/tags", timeout=5)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                if m.get("name", "").split(":")[0] == model.split(":")[0] and \
                   (":" in model and m.get("name") == model or ":" not in model):
                    return round(m.get("size", 0) / 1e9, 1)
    except Exception:
        pass
    return 0.0


def ollama_capabilities(model: str) -> list[str]:
    """模型自报能力（Ollama capabilities 字段，当 manifest 用，与实测探针双重验证）。"""
    try:
        r = requests.post(f"{_OLLAMA}/api/show", json={"model": model}, timeout=5)
        if r.status_code == 200:
            return list(r.json().get("capabilities") or [])
    except Exception:
        pass
    return []


# 常驻底账（GB）：L1 yolox + L2 CLIP ≈0.8；系统/浏览器占用按 0.7 预留
_RESERVED_GB = 1.5
_WARN_RATIO = 0.92          # 权重×1.3 超过可用显存的 92% 判「勉强」


def vram_guard(model: str, other_loaded: list[str] | None = None) -> dict:
    """显存守卫：✓流畅 / ⚠勉强（放行+警告）/ ✗跑不动（拦下+引导）。
    other_loaded：当前已驻留的其他大模型（互斥检测，如 deepseek-r1:14b）。"""
    need = _model_disk_gb(model)
    total = _gpu_vram_gb()
    if total <= 0:
        return {"verdict": "no-gpu", "level": "warn",
                "msg": "本机没检测到独立显卡——本地大模型跑不动，建议用外脑（云端模型），视觉识别仍可用（跑 CPU）"}
    if need <= 0:
        return {"verdict": "unknown", "level": "pass",
                "msg": "模型体积未知（Ollama 未返回），放行——装完实测为准"}
    need_peak = need * 1.3
    others = 0.0
    for m in (other_loaded or []):
        others += _model_disk_gb(m) * 1.1
    avail = total - _RESERVED_GB - others
    if need_peak <= avail * _WARN_RATIO:
        return {"verdict": "fit", "level": "pass",
                "msg": f"✓ 显存充裕：需要约 {need_peak:.1f}GB / 可用 {avail:.1f}GB（{total}GB 卡）"}
    if need_peak <= avail:
        return {"verdict": "tight", "level": "warn",
                "msg": f"⚠ 显存勉强：需要约 {need_peak:.1f}GB / 可用 {avail:.1f}GB——能跑，高分辨率图可能变慢，你自己决定"}
    hint = f"✗ 显存不够：需要约 {need_peak:.1f}GB / 可用仅 {avail:.1f}GB。"
    if others > 0:
        hint += "当前有其他大模型驻留占着显存，先在设置里切换走再装；"
    hint += "也可以换小一档的模型（如 qwen3-vl:4b-instruct）"
    return {"verdict": "overflow", "level": "block", "msg": hint,
            "need_gb": round(need_peak, 1), "avail_gb": round(avail, 1)}


def capability_badges(model: str) -> dict:
    """能力标记（设置页列表用）：👁看图 / 💬聊天。
    双重验证：① Ollama capabilities 自报（manifest 层）② probe_vision 实测（真伪层）。"""
    caps = ollama_capabilities(model)
    self_vision = 'vision' in caps
    key = f"{model}|v1"
    hit = _probe_cache.get(key)
    probed = bool(hit and hit[0]) if hit else None      # None=未实测
    return {"model": model, "vision_self": self_vision, "vision_probed": probed,
            "chat": 'completion' in caps or True}   # Ollama chat 模型天然能聊天
