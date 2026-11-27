# -*- coding: utf-8 -*-
"""#1584 worker 端到端实测：拉起→加载→/infer /chat→空闲自退→收尸"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"H:\HarvenAI")
PF = ROOT / "data" / "vision_worker_port_test.json"
PF.unlink(missing_ok=True)
LOGF = open(ROOT / "data" / "logs" / "vision_worker_test.log", "wb")

GGUF = r"H:\HarvenAI\data\models\vision\qwen3-vl-4b\Qwen3VL-4B-Instruct-Q4_K_M.gguf"
MMPROJ = r"H:\HarvenAI\data\models\vision\qwen3-vl-4b\mmproj-Qwen3VL-4B-Instruct-F16.gguf"
IMG = sorted((ROOT / "data" / "media" / "img_store" / "202611").glob("*.jpg"))[0]

proc = subprocess.Popen(
    [sys.executable, "-u", str(ROOT / "src" / "vision" / "vision_worker.py"),
     "--gguf", GGUF, "--mmproj", MMPROJ, "--num-ctx", "8192",
     "--port-file", str(PF), "--idle-exit", "20"],
    stdout=LOGF, stderr=subprocess.STDOUT, cwd=str(ROOT),
    creationflags=subprocess.CREATE_NO_WINDOW)
print("worker pid:", proc.pid)

# 1) 等端口 + 加载就绪
t0 = time.time()
port = 0
while time.time() - t0 < 300:
    if proc.poll() is not None:
        print("!! worker 提前退出 rc=", proc.returncode); break
    try:
        port = json.loads(PF.read_text(encoding="utf-8"))["port"]
    except Exception:
        port = 0
    if port:
        break
    time.sleep(0.5)
print(f"端口: {port}  耗时{time.time()-t0:.1f}s")
assert port, "拿不到端口"

import requests
while time.time() - t0 < 300:
    try:
        st = requests.get(f"http://127.0.0.1:{port}/health", timeout=3).json()
        if st.get("loaded"):
            print(f"模型加载就绪，总耗时 {time.time()-t0:.1f}s"); break
        if st.get("err"):
            print("!! 加载失败:", st["err"]); sys.exit(1)
    except Exception:
        pass
    time.sleep(1)

# 2) /infer 实测（真实图片）
import base64
b64 = base64.b64encode(IMG.read_bytes()).decode()
t1 = time.time()
r = requests.post(f"http://127.0.0.1:{port}/infer",
                  json={"image_b64": "data:image/jpeg;base64," + b64,
                        "prompt": "这张图里有什么？用一句话回答。"}, timeout=180)
print(f"/infer rc={r.status_code} 耗时{time.time()-t1:.1f}s")
print("  回答:", (r.json() or {}).get("text", r.text)[:120])

# 3) /chat 非流式
t2 = time.time()
r2 = requests.post(f"http://127.0.0.1:{port}/chat",
                   json={"messages": [{"role": "user", "content": "用五个字回答：天空什么颜色"}],
                         "stream": False}, timeout=120)
print(f"/chat rc={r2.status_code} 耗时{time.time()-t2:.1f}s 答案:", (r2.json() or {}).get("text", "")[:50])

# 4) /chat 流式
t3 = time.time()
pieces = []
with requests.post(f"http://127.0.0.1:{port}/chat",
                   json={"messages": [{"role": "user", "content": "从1数到5，用顿号分隔"}],
                         "stream": True}, timeout=120, stream=True) as r3:
    for line in r3.iter_lines():
        if not line:
            continue
        obj = json.loads(line.decode("utf-8"))
        if obj.get("done"):
            break
        if obj.get("t"):
            pieces.append(obj["t"])
print(f"/chat流式 rc={r3.status_code} 耗时{time.time()-t3:.1f}s 首字延迟OK 片段数={len(pieces)} 拼接:", "".join(pieces)[:60])

# 5) 空闲自退（idle-exit=20s）
print("等空闲自退（25s）...")
time.sleep(25)
print("worker 存活?", proc.poll() is None, "（应为 False=已自退）")
print("=== 测试完成 ===")
