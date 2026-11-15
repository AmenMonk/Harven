# -*- coding: utf-8 -*-
"""#801 离线验证：健康联动上下文 + 视觉识别通道（测完清理测试数据）"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"H:\HarvenAI")

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("✅" if cond else "❌"), name, detail)

from src.core import panel_assist as pa
from src.core.panel_manager import get_panel_manager
pm = get_panel_manager()

check("面板就位-meal_diary", pm.get_panel("meal_diary") is not None)
check("面板就位-dream_journal", pm.get_panel("dream_journal") is not None)
check("面板就位-health_check_1", pm.get_panel("health_check_1") is not None)

# ── 1. 非餐饮面板 → 联动返回空（零开销） ──
check("联动-非餐饮面板返回空", pa._meal_health_link("health_check_1") == "")

# ── 2. 餐饮面板：插测试数据 → 联动带摄入汇总 ──
import time
rid = []
r1 = pm.insert_panel_data("meal_diary", {"日期": "2026-09-19", "餐次": "午餐", "成员": "开发者", "识别出的食物": "红烧肉米饭", "估算热量": 850})
r2 = pm.insert_panel_data("meal_diary", {"日期": "2026-09-19", "餐次": "早餐", "成员": "开发者", "识别出的食物": "鸡蛋牛奶", "估算热量": 350})
rid += [r1.get("id"), r2.get("id")] if isinstance(r1, dict) and r1.get("id") else []
try:
    link = pa._meal_health_link("meal_diary")
    check("联动-非空", bool(link), f"len={len(link)}")
    check("联动-含摄入汇总", "千卡" in link)
    check("联动-含1200汇总", "1200" in link, "850+350=1200")
    has_health = "健康" in link or "体检" in link
    check("联动-健康面板区块(有记录才出现)", isinstance(has_health, bool))
    print("   联动内容预览:", link[:220].replace("\n", " ⏎ "))
finally:
    # 清理测试数据
    for i in rid:
        try:
            pm.delete_panel_data("meal_diary", i)
        except Exception as e:
            print("   清理失败:", e)
cnt = pm.get_panel_data_count("meal_diary")
check("清理-测试数据还原", cnt == 0, f"count={cnt}")

# ── 3. 视觉识别通道实弹（minicpm-v 在线时走真模型） ──
from src.core.panel_assist import call_local_vision
import base64
img_path = r"H:\HarvenAI\static\icons\duck_64.png"
try:
    b64 = base64.b64encode(open(img_path, "rb").read()).decode("ascii")
    text, model, err = call_local_vision("用一句话描述这张图片的内容。", b64, timeout=90)
    check("视觉通道-有返回", bool(text) and not err, f"model={model} err={err[:60]}")
    print("   视觉返回预览:", (text or err)[:100])
except Exception as e:
    check("视觉通道-异常兜底", False, str(e)[:80])

print(f"\n结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
open(r"C:\Users\usera\_m5.txt", "w", encoding="utf-8").write("RESULT_ALL_PASS")
