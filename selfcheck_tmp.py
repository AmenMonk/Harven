# -*- coding: utf-8 -*-
import os, json, yaml, urllib.request, sqlite3, sys
os.chdir(r"H:\HarvenAI")
c = yaml.safe_load(open("config.yaml", encoding="utf-8"))
out = {}
out["mode"] = c.get("mode")
out["current_model"] = c.get("current_model")
out["by_mode"] = c.get("current_model_by_mode")
out["collab_mode"] = c.get("collab_mode")
models = c.get("models") or {}
if isinstance(models, dict):
    out["models_keys"] = list(models.keys())
    out["models_detail"] = {k: {kk: (("SET" if vv else "EMPTY") if kk in ("api_key","key","token") else vv) for kk, vv in (v.items() if isinstance(v, dict) else [("value", v)])} for k, v in models.items()}
# 探测 7000 接口
for ep in ["/api/system/status", "/api/model/status", "/api/status"]:
    try:
        r = urllib.request.urlopen("http://127.0.0.1:7000" + ep, timeout=5)
        out[ep] = r.read()[:600].decode("utf-8", "ignore")
    except Exception as e:
        out[ep] = "ERR " + str(e)[:120]
# 数据库
db = "data/sqlite/lingzhi.db"
out["db_exists"] = os.path.exists(db)
if os.path.exists(db):
    try:
        con = sqlite3.connect(db)
        tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        out["db_tables"] = len(tabs)
        out["db_size_mb"] = round(os.path.getsize(db)/1048576, 2)
    except Exception as e:
        out["db_err"] = str(e)[:120]
# 目录
for d in ["data/knowledge", "data/skills", "data/panels", "skills", "templates", "static"]:
    out[d] = len(os.listdir(d)) if os.path.isdir(d) else "MISSING"
print(json.dumps(out, ensure_ascii=False, indent=1))
