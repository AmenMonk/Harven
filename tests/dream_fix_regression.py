# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
梦境任务偷跑 token 修复 — 回归测试（2026-09-12）
背景：dream_light 定时任务把"执行 Memory Dreaming Light Dream"当普通消息发给外脑，
2026-06-25~08-20 偷跑 1068 次完整 AI 对话，纯烧 token 零产出。
修复：①DB 禁用 → ②web_app 拦截 dream_ 前缀走本地 dream_if_needed() → ③scheduler 原子占位防重
"""
import sys
import json
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS, FAIL = 0, 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")

print("=" * 60)
print("[1] scheduler 防重窗口函数")
print("=" * 60)
from src.core.scheduler import _dedup_window_seconds

check("cron 每小时 → 54分钟窗口", _dedup_window_seconds("cron", '{"hour": "*", "minute": "0"}') == 54 * 60)
check("cron 每天3点 → 20小时窗口", _dedup_window_seconds("cron", '{"hour": "3", "minute": "0"}') == 20 * 3600)
check("cron 每3天凌晨4点 → 20小时窗口", _dedup_window_seconds("cron", '{"hour": "4", "minute": "0", "day_of_week": "*/3"}') == 20 * 3600)
check("cron 每15分钟 → 13.5分钟窗口", _dedup_window_seconds("cron", '{"hour": "*", "minute": "*/15"}') == max(60.0, 15 * 60 * 0.9))
check("interval 60分钟 → 54分钟窗口", _dedup_window_seconds("interval", '{"minutes": 60}') == 54 * 60)
check("once → None 不防重", _dedup_window_seconds("once", '{"run_at": "..."}') is None)
check("脏配置 → 回退保守默认窗口（54分钟，fail-safe）", _dedup_window_seconds("cron", "not-json") == 54 * 60)

print()
print("=" * 60)
print("[2] 原子占位 SQL 逻辑（临时表模拟，不碰真实表）")
print("=" * 60)
conn = sqlite3.connect(":memory:")
conn.execute("""CREATE TABLE t (task_id TEXT PRIMARY KEY, last_run TEXT)""")
conn.execute("INSERT INTO t VALUES ('dream_light', NULL)")

now = datetime.now()
def claim(last_run):
    cutoff = (now - timedelta(seconds=54 * 60)).isoformat()
    cur = conn.execute(
        "UPDATE t SET last_run=? WHERE task_id=? AND (last_run IS NULL OR last_run < ?)",
        (now.isoformat(), "dream_light", cutoff)
    )
    conn.commit()
    return cur.rowcount

check("首次触发（last_run 为空）→ 占位成功", claim(now.isoformat()) == 1)
check("1 秒后重复触发 → 被拦截", claim(now.isoformat()) == 0)
conn.execute("UPDATE t SET last_run=?", ((now - timedelta(hours=2)).isoformat(),)); conn.commit()
check("2 小时后正常触发 → 放行", claim(now.isoformat()) == 1)
conn.close()

print()
print("=" * 60)
print("[3] web_app 梦境拦截代码在位")
print("=" * 60)
webapp_src = Path(r"H:\HarvenAI\web_app.py").read_text(encoding="utf-8")
check("拦截分支存在", 'task_id.startswith("dream_")' in webapp_src)
check("走本地 dream_if_needed", "dream_if_needed()" in webapp_src)
check("不再把梦境 prompt 发给 engine.send（拦截分支先 return）",
      webapp_src.find('task_id.startswith("dream_")') < webapp_src.find("engine.send(prompt"))

print()
print("=" * 60)
print("[4] DB 状态与真实数据")
print("=" * 60)
db = sqlite3.connect(r"H:\HarvenAI\data\sqlite\lingzhi.db")
rows = dict(db.execute("SELECT task_id, enabled FROM scheduled_tasks").fetchall())
check("dream_light 已重新启用（走本地，零 API 费）", rows.get("dream_light") == 1)
check("dream_deep 已重新启用（走本地，零 API 费）", rows.get("dream_deep") == 1)
check("dream_rem 已重新启用（走本地，零 API 费）", rows.get("dream_rem") == 1)
total_runs = db.execute("SELECT COUNT(*) FROM scheduled_task_runs").fetchone()[0]
print(f"  ℹ️  历史偷跑记录共 {total_runs} 条（证据保留，不删除）")
db.close()

print()
print("=" * 60)
print("[5] 梦境引擎本地可跑（无 LLM 依赖时安全降级）")
print("=" * 60)
try:
    from src.core.memory_dreaming import get_dream_engine
    eng = get_dream_engine()
    check("梦境引擎可实例化", eng is not None)
    s = eng.dream_if_needed()
    print(f"  ℹ️  dream_if_needed 返回: {s.dream_type if s else None}（本地执行，零 API 费）")
    check("本地执行不抛异常", True)
except Exception as e:
    check(f"梦境引擎异常: {e}", False)

print()
print("=" * 60)
print(f"结果: {PASS} PASS / {FAIL} FAIL")
print("=" * 60)
sys.exit(1 if FAIL else 0)
