# -*- coding: utf-8 -*-
"""模型组合工坊 API 冒烟（2026-11-24 工坊页面开工）——N/N 通过式。

覆盖：列表/盘点/配方单读写/内置原件保护（PUT/DELETE 拒绝）/fork 另存/
B7 命名查重/三道门干跑/试跑真跑（HarvenEye 副本走 packed 执行器）/物流单。
"""
import sys

sys.path.insert(0, r"H:\HarvenAI")
sys.path.insert(0, r"H:\HarvenAI\src")

from flask import Flask  # noqa: E402

from src.core.chain_engine import bp, ensure_chain_engine  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}{(' —— ' + str(detail)) if (detail and not cond) else ''}")


def main():
    ensure_chain_engine()
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.testing = True
    c = app.test_client()

    # 1 列表
    r = c.get("/api/chains/")
    d = r.get_json()
    check("列表 200 + ok", r.status_code == 200 and d.get("ok"))
    ids = [x["id"] for x in d.get("chains", [])]
    check("列表含 harveneye", "harveneye" in ids, ids)
    he = next(x for x in d["chains"] if x["id"] == "harveneye")
    check("内置组合 builtin_protected=True", he.get("builtin_protected") is True)
    check("组合带身份标签", he.get("identity") in ("pure_local", "mixed"), he.get("identity"))

    # 2 模型盘点
    r = c.get("/api/chains/models")
    d = r.get_json()
    check("盘点 200 + 底座在列", r.status_code == 200
          and any(m["id"] == "harven.base.yolox" for m in d.get("models", [])))

    # 3 配方单全文
    r = c.get("/api/chains/harveneye")
    d = r.get_json()
    check("配方单读取 6 环", r.status_code == 200 and len(d["recipe"]["steps"]) == 6,
          len(d.get("recipe", {}).get("steps", [])))

    # 4 内置原件保护
    r = c.put("/api/chains/harveneye", json={"recipe": {"name": "改内置"}})
    check("PUT 内置 → 403 原件保护", r.status_code == 403)
    r = c.delete("/api/chains/harveneye")
    check("DELETE 内置 → 403", r.status_code == 403)

    # 5 B7 命名查重（fork 用内置真名「图片理解组合」应被拒；#1587 修正：原测试写错为 HarvenEye）
    r = c.post("/api/chains/harveneye/fork", json={"name": "图片理解组合"})
    check("fork 同名 → 拒（B7）", r.status_code == 400, r.get_json())

    # 6 fork 另存
    r = c.post("/api/chains/harveneye/fork", json={"id": "ws_fork_test", "name": "工坊测试副本"})
    d = r.get_json()
    check("fork 成功", r.status_code == 200 and d.get("ok") and d.get("recipe_id") == "ws_fork_test", d)
    r = c.get("/api/chains/ws_fork_test")
    d = r.get_json()
    check("副本 origin=forked + forked_from", d["recipe"]["origin"] == "forked"
          and d["recipe"]["forked_from"] == "harveneye")

    # 7 更新副本
    r = c.put("/api/chains/ws_fork_test", json={"recipe": {"name": "工坊测试副本",
                                                           "target": "工坊冒烟用，跑完即删", "steps": d["recipe"]["steps"]}})
    check("PUT 副本成功", r.status_code == 200 and r.get_json().get("ok"), r.get_json())

    # 8 三道门干跑
    r = c.post("/api/chains/ws_fork_test/check", json={})
    d = r.get_json()
    check("干跑 check 返回 preflight+显存峰值", r.status_code == 200
          and "preflight" in d and "vram_peak_gb" in d, d.get("error"))

    # 9 试跑真跑（packed 执行器）
    r = c.post("/api/chains/ws_fork_test/test", json={})
    d = r.get_json()
    check("试跑真跑 ok + run_id + 逐环 steps", r.status_code == 200 and d.get("ok")
          and d.get("run_id") and isinstance(d.get("steps"), list) and len(d["steps"]) >= 4,
          d.get("error") or f"steps={len(d.get('steps') or [])}")
    check("B1 身份标注（副本名+组合）", d.get("label") == "工坊测试副本（组合）执行", d.get("label"))

    # 10 物流单
    r = c.get("/api/chains/ws_fork_test/logs")
    d = r.get_json()
    check("物流单有行", r.status_code == 200 and len(d.get("rows", [])) >= 4)

    # 11 卸载副本（底座保留）
    r = c.delete("/api/chains/ws_fork_test")
    d = r.get_json()
    check("卸载副本 ok + 底座保留", r.status_code == 200 and d.get("base_models_kept") is True)

    print(f"\n═══ 工坊 API 冒烟：{len(PASS)}/{len(PASS) + len(FAIL)} 通过 ═══")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
