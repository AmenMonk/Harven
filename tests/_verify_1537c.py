# -*- coding: utf-8 -*-
"""#1537c 验证：_humanize 挖 trace + 绑定收紧（user_facing）+ 说明文案。"""
import sys, os
sys.path.insert(0, r"H:\HarvenAI")
os.chdir(r"H:\HarvenAI")

PASS = 0
def check(name, cond, detail=""):
    global PASS
    print(("PASS" if cond else "FAIL"), name, ("| " + str(detail) if detail else ""))
    if cond:
        PASS += 1

# ── 1. _humanize 单元验证（用真跑拿到的 trace 结构）──
from src.core.chain_engine.binding import _humanize

real_trace_result = {  # 刚才真跑 harveneye 的结果（脱样）
    "image": "x.jpg", "preset": "balanced", "final_layer": "L3",
    "note": "VLM 兜底完成（L1置信0.00/相似0.23低于门控）",
    "trace": [
        {"source_layer": "L1", "confidence": 0.0, "detections": []},
        {"source_layer": "L2", "confidence": 1.0, "similar_count": 3,
         "similar": [{"id": 66, "tags": "", "similarity": 0.2332}]},
        {"source_layer": "L3", "confidence": 0.9, "elapsed_ms": 2714.2,
         "description": "这张图片包含三个简单的几何图形，背景为浅米色。", "model": "llama-cpp-vlm"},
    ],
    "item": None, "total_ms": 2880.7,
}
out = _humanize(real_trace_result)
check("L3成稿端出", "几何图形" in out and "工坊试跑页" not in out, out[:50])

det_result = {"trace": [
    {"source_layer": "L1", "detections": [{"name": "person"}, {"name": "dog"}]},
    {"source_layer": "L2", "similar": []},
]}
out = _humanize(det_result)
check("对象清单兜底", "person" in out and "dog" in out, out[:40])

sim_result = {"trace": [
    {"source_layer": "L1", "detections": []},
    {"source_layer": "L2", "similar": [{"id": 9, "similarity": 0.86}]},
]}
out = _humanize(sim_result)
check("相似图线索兜底", "相似照片" in out and "86%" in out, out[:40])

empty_result = {"trace": [{"source_layer": "L1", "detections": []}],
                "note": "未检测到目标"}
out = _humanize(empty_result)
check("空结果友好话术", "识别完成" in out and "工坊试跑页" not in out, out[:40])

# summary 类组合不受影响
check("summary优先", _humanize({"summary": "视频讲了…"}) == "视频讲了…")
rec_result = {"records": [{"项目": "咖啡", "金额": "136", "分类": "餐饮", "日期": "11-24"}]}
out = _humanize(rec_result)
check("账单records不受影响", "咖啡" in out and "136" in out, out[:40])

# ── 2. 绑定收紧：跑新同步逻辑后 _dynamic_models 只剩用户侧组合 ──
from src.core.chain_engine.binding import sync_vertical_binding
print("sync:", sync_vertical_binding())
from src.core import vertical_api as va
chains = [mid for mid in va._dynamic_models if mid.startswith("chain:")]
check("垂直页只剩2个组合", sorted(chains) == ["chain:harveneye", "chain:video_brief"], chains)
desc = va._dynamic_models.get("chain:harveneye", {}).get("description", "")
check("说明文案在", "流水线" in desc and "自动调用" in desc, desc[:40])
check("账单链已退垂直页", "chain:bills_chain" not in chains)

# ── 3. 账单链本体还在（工坊可编辑/试跑，后台照常）──
from src.core.chain_engine.registry import get_registry
reg = get_registry()
c = reg.get_chain("bills_chain")
check("账单链本体还在", c is not None and c.enabled)

print(f"\n结果: {PASS}/9")
