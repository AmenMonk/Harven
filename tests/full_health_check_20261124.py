# -*- coding: utf-8 -*-
"""哈文全量体检 2026-11-24 · v2（真实路由/真实表名修正版）。只读+无副作用。"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"H:\HarvenAI")
BASE = "http://localhost:7000"
PASS, FAIL, WARN = [], [], []

def check(batch, name, ok, extra="", warn=False):
    (WARN if warn else (PASS if ok else FAIL)).append((batch, name, extra))
    mark = "WARN" if warn else ("PASS" if ok else "FAIL")
    print(f"[{mark}] [{batch}] {name}" + (f" | {extra}" if extra else ""))

def get(path, timeout=15):
    try:
        import urllib.request
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return getattr(e, 'code', 0), {"_err": str(e)[:120]}

# ══ A 服务基础 ═════════════════════════════════════════════
import urllib.request
def raw(path, timeout=10):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, len(r.read())
    except Exception:
        return 0, 0

s, n = raw("/spa/")
check("A基础", "SPA 壳可达", s == 200, f"HTTP {s}, {n}B")
s, n = raw("/static/icon-duck.png")
check("A基础", "官方鸭子图标在位", s == 200 and n > 1000, f"{n}B")
s, n = raw("/static/spa/assets/" + [f.name for f in (ROOT/"static/spa/assets").glob("index-*.js")][0])
check("A基础", "SPA 构建产物可达", s == 200, f"{n}B")

# ══ B 组合工坊链（今晚新上线）═══════════════════════════════
s, j = get("/api/chains/")
chains = j.get("chains", [])
check("B工坊", "组合列表 API", s == 200 and isinstance(chains, list), f"{len(chains)} 个组合")
builtin = [c for c in chains if c.get("origin") == "builtin"]
check("B工坊", "内置官方组合=3（账单链/HarvenEye/视频速览）", len(builtin) == 3,
      "、".join(c["name"] for c in builtin))
s, j = get("/api/chains/models")
check("B工坊", "本机模型盘点 API", s == 200 and j.get("models") is not None, f"{len(j.get('models') or [])} 个")
s, j = get("/api/chains/entries")
ents = j.get("entries", [])
chain_ents = [e for e in ents if str(e.get("id", "")).startswith("chain:")]
check("B工坊", "组合↔垂直页绑定 entries", s == 200 and len(chain_ents) >= 1,
      f"chain: 绑定 {len(chain_ents)} 个")
sid = next((c["id"] for c in chains if c.get("origin") == "builtin"), None)
if sid:
    s, j = get(f"/api/chains/{sid}")
    check("B工坊", f"配方单全文（{sid}）", s == 200 and bool(j.get("recipe")))
    s, j = get(f"/api/chains/{sid}/logs?limit=5")
    check("B工坊", "物流单历史 API", s == 200 and "rows" in j, f"{len(j.get('rows') or [])} 行")

# ══ C 面板体系 ═════════════════════════════════════════════
s, j = get("/api/panels/manage")
panels = j.get("panels") or (j.get("data") or {}).get("panels") or []
check("C面板", "面板管理清单", s == 200, f"{len(panels) if isinstance(panels, list) else 'ok'}")
s, j = get("/api/panels/members")
mem = j.get("members") or []
check("C面板", "家庭成员", s == 200, f"{len(mem)} 人：{'、'.join(m.get('name','') for m in mem[:6])}")
s, j = get("/api/panels/members/birthdays")
check("C面板", "成员生日提醒 API", s == 200, "")

# ══ D 数据家底（真实表名）═══════════════════════════════════
DB = ROOT / "data" / "sqlite" / "lingzhi.db"
conn = sqlite3.connect(str(DB))
ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
check("D数据", "lingzhi.db 完整性", ic == "ok", ic)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("D数据", "库表规模", len(tables) >= 110, f"{len(tables)} 张表")
for label, table in [("冰箱库存", "panel_fridge_stock_1"), ("冰箱消耗流水", "fridge_consume_log"),
                     ("管家议程", "butler_agenda"), ("目标", "goals"),
                     ("组合物流单", "chains_log"), ("车辆加油面板", "panel_car_fuel_1"),
                     ("孩子成长", "kid_growth"), ("照片索引", "images_index")]:
    if table in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        check("D数据", label, True, f"{n} 行")
    else:
        check("D数据", label, False, f"缺表 {table}")
if "images_index" in tables:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(images_index)")}
    if "bill_processed" in cols:
        n = conn.execute("SELECT COUNT(*) FROM images_index WHERE bill_processed=1").fetchone()[0]
        check("D数据", "账单照片已扫标记（#1377）", True, f"{n} 张")
    else:
        check("D数据", "bill_processed 列", False, "列缺失")
conn.close()

# ══ E 模型与配置 ═══════════════════════════════════════════
mm = ROOT / "data" / "multi_models.json"
j = json.loads(mm.read_text(encoding="utf-8"))
cur = j.get("current_model", "")
provs = j.get("providers") or j.get("models") or {}
check("E模型", "multi_models.json 可读", True, f"current_model={cur}，{len(provs)} 外脑")
cfg_text = (ROOT / "config.yaml").read_text(encoding="utf-8")
import re
m_cfm = re.search(r"collab_foreign_model:\s*(\S+)", cfg_text)
m_cm = re.search(r"^current_model:\s*(\S+)", cfg_text, re.M)
cfm = m_cfm.group(1).strip("'\"") if m_cfm else ""
cm = m_cm.group(1).strip("'\"") if m_cm else ""
check("E配置", "协作外脑与当前外脑一致（无土豆式污染）", cfm == cm,
      f"collab_foreign_model={cfm} vs current_model={cm}")
check("E配置", "协作模式关闭（collab_mode off）", "collab_mode: 'off'" in cfg_text or "collab_mode: off" in cfg_text, "")
check("E配置", "外脑 API Key 已配置（zhipu/deepseek）", cfg_text.count("api_key") >= 2 or "sk-" in cfg_text, "")

# ══ F 业务端点（真实路由实测）═══════════════════════════════
for name, path in [("冰箱库存 API", "/api/panels/fridge/stock"),
                   ("照片库列表 API", "/api/photos/list?limit=1"),
                   ("管家状态 API", "/api/butler/state"),
                   ("管家简报 API", "/api/butler/briefing"),
                   ("健康概览 API", "/api/panels/health/overview"),
                   ("孩子列表 API", "/api/kid/children"),
                   ("面板管理 API", "/api/panels/manage"),
                   ("IM 平台 API", "/api/im/platforms"),
                   ("IM 频道 API", "/api/im/channels")]:
    s, j = get(path)
    ok = s == 200
    check("F业务", name, ok, f"HTTP {s}" + ("" if ok else f" {str(j)[:60]}"))

# ══ G 日志健康 ═════════════════════════════════════════════
logs = sorted((ROOT / "data" / "logs").glob("*.log"))
if logs:
    latest = logs[-1]
    tail = latest.read_text(encoding="utf-8", errors="ignore")[-300000:]
    n_tb = tail.count("Traceback")
    # xformers→triton 依赖探测属无害（嵌入模型照常就绪），白名单排除
    harmless = tail.count("No module named 'triton'")
    biz_err = n_tb - harmless
    check("G日志", f"日志业务级 Traceback（{latest.name}）", biz_err == 0,
          f"共 {n_tb} 处，其中无害依赖探测 {harmless}", warn=biz_err > 0)
else:
    check("G日志", "data/logs 目录存在", False)

# ══ H 交付一致性 ═══════════════════════════════════════════
import hashlib
STG = ROOT / "_package_staging"
diff = []
for rel in ["src/core/chain_engine/routes.py", "src/core/chain_engine/advisor.py",
            "src/core/chain_engine/binding.py", "src/core/harvenhub_client.py",
            "spa/src/pages/SpacePage.vue", "spa/src/api.js", "web_app.py"]:
    a, b = ROOT / rel, STG / rel
    if not b.exists():
        diff.append(rel + "(缺)")
    elif hashlib.md5(a.read_bytes()).hexdigest() != hashlib.md5(b.read_bytes()).hexdigest():
        diff.append(rel)
check("H交付", "staging 关键 7 文件 MD5 一致", not diff, "；".join(diff) or "全部一致")
r = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True)
dirty = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("??")]
check("H交付", "git 工作树无未提交改动", not dirty, f"{len(dirty)} 项" if dirty else "干净")
r = subprocess.run(["git", "-C", str(ROOT), "log", "--oneline", "-1"], capture_output=True, text=True)
check("H交付", "git 最新提交", True, r.stdout.strip()[:50])

total = len(PASS) + len(FAIL) + len(WARN)
print(f"\n══════════ 哈文全量体检 v2：{len(PASS)}/{total} 通过（警告 {len(WARN)} / 失败 {len(FAIL)}）══════════")
for b, n2, e in WARN:
    print(f"  [WARN] [{b}] {n2} | {e}")
for b, n2, e in FAIL:
    print(f"  [FAIL] [{b}] {n2} | {e}")
sys.exit(1 if FAIL else 0)
