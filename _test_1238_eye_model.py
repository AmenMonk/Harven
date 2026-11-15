# -*- coding: utf-8 -*-
"""#1238 测试：哈文 multi_models 接入 harveneye（本地模型阵营）+ 分流回退。

脚本式 check() 风格。测试项（10）：
  T1  eye_client.models_ready：Eye 在线 → ready=True（qwen3-vl:8b）
  T2  ModelManager(harveneye) 引擎类型 = 'harveneye'
  T3  非流式对话（走 Eye /chat）
  T4  流式对话（走 Eye SSE，多 chunk）
  T5  /api/models/local-all 注入 harveneye（Eye 在线 → 门禁开）
  T6  /api/models/eye-ready 端点
  T7  Eye 停机 → local-all 门禁关（不显示 harveneye）
  T8  Eye 停机 → 流式回退 Ollama（带诚实标注）
  T9  Eye 停机 → models_ready ready=False
  T10 _route_model_for_query：外脑模式选 harveneye → 友好指引不误报

流程：起 Eye(:7001) → T1~T6 → 停 Eye → T7~T10。
哈文侧走 web_app test_client（不依赖运行实例 7000）。
"""
import subprocess
import sys
import time
from pathlib import Path

EYE_ROOT = Path(r"H:\HarvenEye")
EYE_PY = EYE_ROOT / ".venv" / "Scripts" / "python.exe"

PASS, FAIL = 0, 0
_eye_proc = None


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def start_eye():
    global _eye_proc
    code = (f"import sys; sys.path.insert(0, r'{str(EYE_ROOT)}');"
            f"from eye.api import main; main(port=7001)")
    _eye_proc = subprocess.Popen([str(EYE_PY), "-c", code], cwd=str(EYE_ROOT),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < 120:
        if _eye_proc.poll() is not None:
            raise RuntimeError(f"Eye 进程提前退出 code={_eye_proc.returncode}")
        try:
            with urllib.request.urlopen("http://127.0.0.1:7001/status", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("Eye 120s 未就绪")


def stop_eye():
    global _eye_proc
    if _eye_proc:
        _eye_proc.terminate()
        time.sleep(2)
        if _eye_proc.poll() is None:
            _eye_proc.kill()
        _eye_proc = None
    # 等端口释放
    time.sleep(2)


def main():
    print("=" * 60)
    print("#1238 哈文接入 HarvenEye 本地模型 — 测试开始")
    print("=" * 60)

    sys.path.insert(0, r"H:\HarvenAI")
    from src.core import eye_client
    from src.core.model_manager import ModelManager, EYE_MODEL_ID

    print("\n[启动] HarvenEye :7001 …")
    start_eye()
    print("[启动] 就绪\n")

    # T1 门禁开
    r = eye_client.models_ready()
    check("T1 eye_client.models_ready 门禁开", r.get("ready") is True and r.get("model") == "qwen3-vl:8b",
          f"{r}")

    # T2 引擎类型
    mm = ModelManager("http://localhost:11434", EYE_MODEL_ID)
    check("T2 ModelManager 引擎类型=harveneye", mm._engine_type == "harveneye", f"type={mm._engine_type}")

    # T3 非流式
    ok, content = eye_client.chat([{"role": "user", "content": "1+1等于几？只答数字。"}], timeout=120)
    check("T3 非流式对话", ok and "2" in content, f"content={content[:50]!r}")

    # T4 流式
    pieces = list(eye_client.chat_stream([{"role": "user", "content": "说「你好哈文」四个字。"}]))
    check("T4 流式对话", len(pieces) >= 1 and "".join(pieces).strip() != "",
          f"chunks={len(pieces)} text={''.join(pieces)[:40]!r}")

    # T5/T6 test_client（web_app import 较慢）
    print("\n[加载] web_app test_client …")
    import web_app
    client = web_app.app.test_client()

    resp = client.get("/api/models/local-all")
    d = resp.get_json() or {}
    eye_entry = next((m for m in d.get("models", []) if m.get("id") == "harveneye"), None)
    check("T5 local-all 注入 harveneye（门禁开）", resp.status_code == 200 and eye_entry is not None
          and eye_entry.get("type") == "llm",
          f"entry={eye_entry}" if eye_entry else f"models={[m.get('id') for m in d.get('models', [])][:12]}")

    resp = client.get("/api/models/eye-ready")
    d = resp.get_json() or {}
    check("T6 /api/models/eye-ready", resp.status_code == 200 and d.get("ready") is True, f"{d}")

    # 停 Eye → 门禁关 + 回退
    print("\n[停止] HarvenEye → 测门禁关 + 回退 …")
    stop_eye()

    r = eye_client.models_ready()
    check("T9 models_ready 门禁关", r.get("ready") is False, f"{r}")

    resp = client.get("/api/models/local-all")
    d = resp.get_json() or {}
    eye_entry = next((m for m in d.get("models", []) if m.get("id") == "harveneye"), None)
    check("T7 local-all 门禁关（不显示）", resp.status_code == 200 and eye_entry is None,
          f"entry={eye_entry}")

    # T8 流式回退：Eye 挂 → Ollama 顶上带标注
    mm2 = ModelManager("http://localhost:11434", EYE_MODEL_ID)
    out = "".join(mm2.chat([{"role": "user", "content": "回复两个字：收到"}], stream=True))
    check("T8 流式回退 Ollama（带标注）",
          out.startswith("（⚠️ HarvenEye") and len(out) > 20,
          f"out={out[:80]!r}")

    # T10 外脑模式选 harveneye → 友好指引（先临时设 current_model，测完还原）
    from src.core.engine import ConversationEngine
    from src.core.multi_model_manager import get_multi_model_manager
    _mmm = get_multi_model_manager()
    _saved_cm = _mmm.current_model
    _mmm.current_model = "harveneye"
    try:
        eng = ConversationEngine.__new__(ConversationEngine)  # 免初始化，只调路由方法
        use_ext, label, mid, err = ConversationEngine._route_model_for_query(eng, "direct")
        check("T10 外脑模式选 harveneye→友好指引",
              err is not None and "本地模式" in err and mid == "harveneye",
              f"err={str(err)[:80]!r}")
    finally:
        _mmm.current_model = _saved_cm


if __name__ == "__main__":
    code = 1
    try:
        main()
        code = 0
    finally:
        stop_eye()
        print("\n" + "=" * 60)
        print(f"结果：{PASS} PASS / {FAIL} FAIL（共 {PASS + FAIL}）")
        print("=" * 60)
    sys.exit(0 if (code == 0 and FAIL == 0) else 1)
