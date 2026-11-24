# -*- coding: utf-8 -*-
"""#1537b 验证：bills_chain 免样例试跑（test client 新代码）。跑完即删。"""
import sys
sys.path.insert(0, r"H:\HarvenAI")
import os
os.chdir(r"H:\HarvenAI")

from src.core.chain_engine.routes import bp as chains_bp
from flask import Flask

app = Flask(__name__)
app.register_blueprint(chains_bp)
c = app.test_client()

r = c.post("/api/chains/bills_chain/test", json={})
d = r.get_json()
steps = [s.get("step_id") for s in (d.get("steps") or [])]
print("bills_chain ok:", d.get("ok"), "| note:", d.get("sample_note"))
print("steps:", steps)
print("error:", d.get("error"))
ok = d.get("ok") is True and len(steps) >= 3
print("\n=== #1537b 修复验证：" + ("PASS" if ok else "FAIL") + " ===")
sys.exit(0 if ok else 1)
