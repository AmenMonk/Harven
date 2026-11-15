# -*- coding: utf-8 -*-
"""#797 蒸馏一期离线验证（引擎调用打桩，不真跑模型；测试数据用完即清）"""
import sys, json, time
sys.path.insert(0, r"H:\HarvenAI")
_out = open(r"C:\Users\usera\_dev_test797_out.txt", "w", encoding="utf-8")
PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    _out.write(("✅ " if cond else "❌ ") + name + (" " + detail if detail else "") + "\n")

from src.core import distiller

# ── 1. 门1 资格检查 ──
ok, reason, _ = distiller.gate_check("../etc/passwd")
check("门1-路径越权拒绝", not ok)
ok, reason, _ = distiller.gate_check("不存在的文件.txt")
check("门1-不存在拒绝", not ok)
# 造测试源文件
src = distiller.KB_DIR / "_distill_test.txt"
src.write_text("大六壬是中国古代最高级别的占卜术之一，与太乙、奇门并称三式。\n\n" +
               "起课方法：月将加时辰，天地盘上下对照，四课三传。月将即太阳所在之宫。\n\n" +
               "发三传的方法有贼克、比用、涉害、遥克、昴星、别责、八专、伏吟、返吟九宗门。\n\n" +
               "判断时先看日干与三传的生克关系，再看遁干旬空。课体有元首课、重审课等六十四种。\n\n" *
               4 +
               "历代名著有《六壬大全》《大六壬指南》《课经》等，邵彦和的断案尤为经典，" +
               "以案例飞伏见长，学者宜先熟读课体，再学断法，循序渐进。\n\n" +
               "实践中要注意：占事先定类神，课传既立，则看类神落宫旺衰，与日干生克定吉凶成败。" * 2,
               encoding="utf-8")
ok, reason, text = distiller.gate_check("_distill_test.txt")
check("门1-txt通过", ok and len(text) > 300)
# 短文件拒绝
short = distiller.KB_DIR / "_distill_short.txt"
short.write_text("太短了", encoding="utf-8")
ok, reason, _ = distiller.gate_check("_distill_short.txt")
check("门1-短文拒绝", not ok and "300" in reason)

# ── 2. 分块 ──
chunks = distiller.chunk_text(text, size=200, overlap=20)
check("分块-多块", len(chunks) >= 2)
check("分块-块长合规", all(len(c) <= 260 for c in chunks), f"max={max(len(c) for c in chunks)}")

# ── 3. JSON 容错解析 ──
good = distiller._parse_distill_json('```json\n{"summary":"摘要","outline":["a","b"],"qa":[{"q":"q1","a":"a1"}],"terms":[{"term":"t","def":"d"}]}\n```')
check("解析-带代码围栏", good and good["summary"] == "摘要" and len(good["qa"]) == 1)
bad = distiller._parse_distill_json("这不是JSON")
check("解析-垃圾输入返回None", bad is None)
empty = distiller._parse_distill_json('{"summary":""}')
check("解析-空结构不炸", empty and empty["outline"] == [])

# ── 4. 分类目录 ──
cat = distiller.create_category("测试蒸馏分类")
check("分类-创建", cat and cat["id"] > 0)
cats = distiller.list_categories()
check("分类-列表含新类", any(c["name"] == "测试蒸馏分类" for c in cats))

# ── 5. 打桩跑任务（门2→执行→断点字段） ──
CANNED = json.dumps({"summary": "大六壬是古代占卜术，月将加时起课。",
                     "outline": ["起课：月将加时", "九宗门发三传"],
                     "qa": [{"q": "大六壬怎么起课？", "a": "月将加时辰，天地盘对照成四课三传。"},
                            {"q": "发三传有几种方法？", "a": "九宗门。"}],
                     "terms": [{"term": "月将", "def": "太阳所在之宫"}]})
distiller._call_engine = lambda s, u, engine: (CANNED, "")
job_id = distiller.start_job("_distill_test.txt", engine="local", category_id=cat["id"])
# 等后台线程完成
for _ in range(40):
    jobs = {j["id"]: j for j in distiller.list_jobs()}
    if jobs[job_id]["status"] in ("done", "failed"):
        break
    time.sleep(0.25)
j = {x["id"]: x for x in distiller.list_jobs()}[job_id]
check("任务-完成", j["status"] == "done", f"status={j['status']} err={j['error']}")
chunks_default = distiller.chunk_text(text)  # 任务用默认 size=1200
check("任务-分块计数", j["total_chunks"] == len(chunks_default) and j["done_chunks"] == j["total_chunks"],
      f"job={j['total_chunks']} vs {len(chunks_default)}")
items = distiller.job_items(job_id)
kinds = {i["kind"] for i in items}
check("产物-四类齐全", {"summary", "outline", "qa", "term"} <= kinds, str(kinds))
check("产物-全部待审", all(i["approved"] == 0 for i in items))

# ── 6. 门3 审核入库（双层存储+来源标注） ──
approve_ids = [i["id"] for i in items if i["kind"] in ("summary", "qa")]
added = distiller.approve_items(job_id, approve_ids, [])
check("审核-入库数量", added == len(approve_ids))
from src.core.knowledge_base import get_knowledge_base
kb = get_knowledge_base()
distilled = [d for d in kb.documents if (d.get("metadata") or {}).get("layer") == "distilled"]
check("入库-蒸馏层标记", len(distilled) >= 2)
check("入库-来源标注", all(d["source"].startswith("蒸馏《_distill_test.txt》") for d in distilled))
check("入库-分类注入", all((d.get("metadata") or {}).get("category") == "测试蒸馏分类" for d in distilled))

# ── 7. 检索蒸馏层优先 ──
res = kb.search("大六壬怎么起课", top_k=5, rerank=True)
if res:
    check("检索-蒸馏层排前", res[0].get("distilled") is True and "大六壬" in res[0]["text"])
    check("检索-来源可见", "蒸馏《" in res[0].get("source", ""))
else:
    check("检索-有结果", False, "search 返回空")

# ── 8. 清理测试数据（还原现场） ──
distiller.delete_job(job_id)
distiller.delete_category(cat["id"])
kb.documents = [d for d in kb.documents
                if (d.get("metadata") or {}).get("layer") != "distilled"
                or (d.get("metadata") or {}).get("origin") != "_distill_test.txt"]
kb._rebuild_index()
kb._save_index()
src.unlink(missing_ok=True)
short.unlink(missing_ok=True)
check("清理-还原现场", not src.exists() and not any(
    (d.get("metadata") or {}).get("origin") == "_distill_test.txt" for d in kb.documents))

_out.write(f"\n结果: {len(PASS)} 通过 / {len(FAIL)} 失败\n")
_out.write("RESULT_ALL_PASS\n" if not FAIL else "RESULT_FAIL: " + ", ".join(FAIL) + "\n")
_out.close()
sys.exit(1 if FAIL else 0)
