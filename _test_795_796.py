# -*- coding: utf-8 -*-
"""离线验证 #795 关键词兜底 / #796 统一本地推理入口"""
import sys
sys.path.insert(0, r"H:\HarvenAI")
_out = open(r"C:\Users\usera\_dev_test796_out.txt", "w", encoding="utf-8")

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    _out.write(("✅ " if cond else "❌ ") + name + (" " + detail if detail else "") + "\n")

# ── 1. local_infer 单元逻辑 ──
from src.core.local_infer import _pick_ollama_model, chat_local

models = ["nomic-embed-text:latest", "qwen2.5:14b", "minicpm-v:8b", "huatuo:latest"]
check("挑模型-偏好命中", _pick_ollama_model("qwen2.5", models) == "qwen2.5:14b")
check("挑模型-跳过embed/视觉", _pick_ollama_model(None, models) == "qwen2.5:14b")
check("挑模型-空列表", _pick_ollama_model(None, []) is None)
check("挑模型-只有embed时兜底返回", _pick_ollama_model(None, ["nomic-embed-text:latest"]) == "nomic-embed-text:latest")

# prefer 不存在的模型 → Ollama 仍可用（挑第一个非embed模型），验证返回格式
text, backend, err = chat_local("只回复：好", allow_load_gguf=False, prefer_model="zzz-no-such-model")
_out.write(f"   chat_local -> backend={backend} text={str(text)[:40]!r} err={err[:80]}\n")
check("chat_local 返回三元组格式", isinstance(text, (str, type(None))) and isinstance(err, str))
# 本机 Ollama 在跑（qwen2.5:14b），若可达应成功；不可达应明确报错
check("chat_local 要么成功要么明确报错", (text and err == "") or (text is None and "不可用" in err))

# ── 2. knowledge_base 关键词兜底 ──
from src.core.knowledge_base import KnowledgeBase
kb = KnowledgeBase()
kb.documents = [
    {"id": "a", "text": "大六壬是中国古代的占卜术，起课方法讲究月将加时。", "source": "test", "metadata": {}},
    {"id": "b", "text": "红烧肉的做法：五花肉焯水后加酱油冰糖小火炖四十分钟。", "source": "test", "metadata": {}},
    {"id": "c", "text": "公司报销流程：先填报销单，主管审批后交财务。", "source": "test", "metadata": {}},
]
r = kb._keyword_search("大六壬怎么起课", top_k=2)
check("关键词兜底-命中", len(r) == 1 and "大六壬" in r[0]["text"])
check("关键词兜底-标注fallback", bool(r) and r[0]["metadata"].get("keyword_fallback") is True)
r2 = kb._keyword_search("报销", top_k=3)
check("关键词兜底-命中流程", len(r2) >= 1 and "报销" in r2[0]["text"])
check("关键词兜底-无关查询不乱配", kb._keyword_search("量子物理", top_k=3) == [])

_out.write(f"\n结果: {len(PASS)} 通过 / {len(FAIL)} 失败\n")
_out.write("RESULT_ALL_PASS\n" if not FAIL else "RESULT_FAIL: " + ", ".join(FAIL) + "\n")
_out.close()
sys.exit(1 if FAIL else 0)
