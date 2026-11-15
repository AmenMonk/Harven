"""
Harven AI — 知识蒸馏引擎（#797 一期 MVP, 2026-09-19）

已定案要点：
  • 入口只在知识库；面板/台账结构化数据永不蒸馏
  • 上传默认不蒸（普通入库），每条资料有「蒸馏」按钮手动触发
  • 三道门：门1 资格检查 → 门2 用户点头选引擎 → 门3 预览审核后才入库
  • 引擎本地/外脑用户自选，默认本地（省 token 第一原则）
  • 双层存储：蒸馏层（提炼产物）+ 原文层（原文件不动），来源标注
  • 全局分类目录（省 token 第二支柱）
  • 断点续传：逐块处理、失败可续

蒸馏产物 4 类：摘要 / 章节要点 / 问答对 / 术语表
检索策略：蒸馏层优先（打分加权 + 排前），来源自动标注「蒸馏《文件名》」
"""

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
KB_DIR = PROJECT_ROOT / "data" / "knowledge"
DB_PATH = KB_DIR / "distill.db"

_job_lock = threading.Lock()
_running_thread = None

# ────────────────────────── 数据库 ──────────────────────────

def _conn():
    KB_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS kb_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS kb_distill_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            category_id INTEGER DEFAULT 0,
            engine TEXT DEFAULT 'local',
            status TEXT DEFAULT 'pending',      -- pending|running|done|failed
            total_chunks INTEGER DEFAULT 0,
            done_chunks INTEGER DEFAULT 0,
            chunk_offset INTEGER DEFAULT 0,     -- 断点续传：已处理块数
            error TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS kb_distill_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            chunk_idx INTEGER DEFAULT 0,
            kind TEXT NOT NULL,                 -- summary|outline|qa|term
            question TEXT DEFAULT '',
            answer TEXT DEFAULT '',
            content TEXT DEFAULT '',
            approved INTEGER DEFAULT 0,         -- 0待审 1已入库 2已拒绝
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS kb_distill_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT OR IGNORE INTO kb_distill_settings(key, value) VALUES
            ('distill_allow_cloud', 'false'),        -- 蒸馏允许走外脑（默认纯本地）
            ('auto_distill_on_upload', 'false');     -- 上传自动蒸馏（默认关，手动按钮）
        """)


_init_db()

# ────────────────────────── 设置 ──────────────────────────

def get_distill_settings():
    with _conn() as c:
        return {r["key"]: r["value"] for r in
                c.execute("SELECT key, value FROM kb_distill_settings")}


def set_distill_setting(key, value):
    if key not in ("distill_allow_cloud", "auto_distill_on_upload"):
        raise ValueError(f"未知设置项: {key}")
    value = "true" if str(value).lower() in ("true", "1", "on") else "false"
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO kb_distill_settings(key, value) VALUES (?, ?)",
                  (key, value))
    return value


# ────────────────────────── 分类目录（全局，省 token 第二支柱） ──────────────────────────

def list_categories():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, name FROM kb_categories ORDER BY id")]


def create_category(name):
    name = (name or "").strip()[:30]
    if not name:
        raise ValueError("分类名不能为空")
    with _conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO kb_categories(name) VALUES (?)", (name,))
        c.commit()
        row = c.execute("SELECT id, name FROM kb_categories WHERE name=?", (name,)).fetchone()
        return dict(row)


def delete_category(cat_id):
    with _conn() as c:
        c.execute("DELETE FROM kb_categories WHERE id=?", (cat_id,))
        c.execute("UPDATE kb_distill_jobs SET category_id=0 WHERE category_id=?", (cat_id,))


# ────────────────────────── 门1：资格检查 ──────────────────────────

def gate_check(source_name):
    """门1 — 蒸馏资格检查。返回 (ok, 原因, 全文)"""
    path = (KB_DIR / source_name).resolve()
    if not str(path).startswith(str(KB_DIR.resolve())):
        return False, "路径越权", ""
    if not path.exists():
        return False, "文件不存在（可能已被删除）", ""
    if path.suffix.lower() not in (".txt", ".md", ".markdown"):
        return False, "一期蒸馏支持 txt / md 文本类资料（pdf 等 / 音视频暂不支持）", ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"读取失败: {e}", ""
    if len(text.strip()) < 300:
        return False, "内容太短（不足 300 字），不值得蒸馏", ""
    return True, "", text


# ────────────────────────── 分块 ──────────────────────────

def chunk_text(text, size=1200, overlap=120):
    """按段落聚合成 ~1200 字块，块间 120 字重叠保上下文"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        paras = [text]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 1 <= size:
            buf = (buf + "\n" + p).strip()
        else:
            if buf:
                chunks.append(buf)
            # 单段超长直接硬切
            while len(p) > size:
                chunks.append(p[:size])
                p = p[size - overlap:]
            buf = p
    if buf:
        chunks.append(buf)
    # 加重叠：每块末尾带上前一块的尾部（首块除外）
    out = []
    for i, ch in enumerate(chunks):
        if i > 0 and overlap > 0:
            tail = chunks[i - 1][-overlap:]
            ch = tail + "\n" + ch
        out.append(ch)
    return out


# ────────────────────────── 蒸馏调用（本地默认 / 外脑可选） ──────────────────────────

_DISTILL_SYSTEM = (
    "你是知识蒸馏助手。从原文片段提炼可检索的知识，只输出 JSON（不要解释、不要代码块标记）：\n"
    '{"summary":"不超过120字的要点摘要",'
    '"outline":["要点1","要点2"],'
    '"qa":[{"q":"用户可能会问的问题","a":"准确简短的答案"}],'
    '"terms":[{"term":"术语","def":"解释"}]}\n'
    "规则：outline 0-6条；qa 0-5个，问题要像用户真实会问的；terms 0-8个，只收片段里的专业术语；"
    "片段信息太少时对应数组留空；绝对不要编造片段里没有的内容。"
)


def _call_engine(system, user, engine):
    """门2 已选引擎。返回 (text, err)"""
    if engine == "cloud":
        s = get_distill_settings()
        if s.get("distill_allow_cloud") != "true":
            return None, "蒸馏未允许走外脑（设置里先打开「蒸馏允许外脑」）"
        try:
            from src.core.panel_assist import _llm_call
            txt, _label, err = _llm_call(system, user, max_tokens=900, temperature=0.1, prefer="cloud")
            if err or not txt:
                return None, f"外脑调用失败: {err}"
            return txt, ""
        except Exception as e:
            return None, f"外脑调用失败: {e}"
    # 默认本地（统一本地推理入口：进程内GGUF→Ollama→按需GGUF→明确报错）
    try:
        from src.core.local_infer import chat_local
        txt, _backend, err = chat_local(user, system=system, timeout=600, temperature=0.1, max_tokens=900)
        if txt is None:
            return None, err
        return txt, ""
    except Exception as e:
        return None, f"本地推理失败: {e}"


def _parse_distill_json(raw):
    """容错解析模型输出 JSON"""
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return None
    out = {"summary": "", "outline": [], "qa": [], "terms": []}
    if isinstance(data.get("summary"), str):
        out["summary"] = data["summary"].strip()[:400]
    for arr, key in (("outline", "outline"), ("qa", "qa"), ("terms", "terms")):
        v = data.get(arr)
        if isinstance(v, list):
            out[key] = v[:8] if key != "qa" else v[:6]
    return out


# ────────────────────────── 门3：审核入库（双层存储） ──────────────────────────

def _items_to_docs(items, source_name, category_name=""):
    """把审核通过的蒸馏产物转成知识库文档（蒸馏层）"""
    docs = []
    tag = f"（{category_name}）" if category_name else ""
    for it in items:
        kind, q, a, content = it["kind"], it.get("question", ""), it.get("answer", ""), it.get("content", "")
        if kind == "summary" and content:
            text = f"【摘要】{content}"
        elif kind == "outline" and content:
            text = f"【要点】{content}"
        elif kind == "qa" and q and a:
            text = f"问：{q}\n答：{a}"
        elif kind == "term" and content:
            text = f"【术语】{content}"
        else:
            continue
        docs.append({
            "text": text,
            "source": f"蒸馏《{source_name}》{tag}",   # 来源标注
            "metadata": {
                "layer": "distilled",                 # 蒸馏层标记（检索加权用）
                "origin": source_name,
                "kind": kind,
                "category": category_name,
            },
        })
    return docs


def approve_items(job_id, approve_ids, reject_ids):
    """门3 — 用户审核：通过的写入知识库蒸馏层，拒绝的标记丢弃"""
    from src.core.knowledge_base import get_knowledge_base
    kb = get_knowledge_base()
    cats = {c["id"]: c["name"] for c in list_categories()}
    added = 0
    with _conn() as c:
        job = c.execute("SELECT * FROM kb_distill_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("任务不存在")
        cat_name = cats.get(job["category_id"], "")
        if approve_ids:
            rows = c.execute(
                f"SELECT * FROM kb_distill_items WHERE job_id=? AND id IN ({','.join('?' * len(approve_ids))}) AND approved=0",
                [job_id] + list(approve_ids)).fetchall()
            docs = _items_to_docs([dict(r) for r in rows], job["source_name"], cat_name)
            if docs:
                source = docs[0]["source"]
                # 按 kind 分组批量入库（每组一次重建索引，避免逐条重建卡顿）
                by_kind = {}
                for d in docs:
                    by_kind.setdefault(d["metadata"]["kind"], []).append(d)
                for kind, group in by_kind.items():
                    kb.add_documents_batch(
                        [d["text"] for d in group], source=source,
                        metadata={"layer": "distilled", "origin": job["source_name"],
                                  "kind": kind, "category": cat_name})
                added = len(docs)
        if approve_ids:
            c.execute(f"UPDATE kb_distill_items SET approved=1 WHERE id IN ({','.join('?' * len(approve_ids))})",
                      list(approve_ids))
        if reject_ids:
            c.execute(f"UPDATE kb_distill_items SET approved=2 WHERE id IN ({','.join('?' * len(reject_ids))})",
                      list(reject_ids))
        c.commit()
    return added


# ────────────────────────── 任务执行（断点续传） ──────────────────────────

def _run_job(job_id):
    """后台线程：逐块蒸馏 → 产物写 items 表（approved=0 待审）"""
    try:
        with _conn() as c:
            job = c.execute("SELECT * FROM kb_distill_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return
        ok, reason, text = gate_check(job["source_name"])
        if not ok:
            with _conn() as c:
                c.execute("UPDATE kb_distill_jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                          (reason, datetime.now().isoformat(), job_id))
            return
        chunks = chunk_text(text)
        offset = job["chunk_offset"]
        with _conn() as c:
            c.execute("UPDATE kb_distill_jobs SET status='running', total_chunks=?, updated_at=? WHERE id=?",
                      (len(chunks), datetime.now().isoformat(), job_id))
        for idx in range(offset, len(chunks)):
            user = f"原文片段（{idx + 1}/{len(chunks)}）：\n{chunks[idx]}"
            raw, err = _call_engine(_DISTILL_SYSTEM, user, job["engine"])
            if raw is None:
                # 记录进度后失败——下次可从 offset 续跑
                with _conn() as c:
                    c.execute("UPDATE kb_distill_jobs SET status='failed', error=?, chunk_offset=?, done_chunks=?, updated_at=? WHERE id=?",
                              (err, idx, idx, datetime.now().isoformat(), job_id))
                return
            data = _parse_distill_json(raw)
            with _conn() as c:
                if data:
                    if data["summary"]:
                        c.execute("INSERT INTO kb_distill_items(job_id, chunk_idx, kind, content) VALUES (?,?,?,?)",
                                  (job_id, idx, "summary", data["summary"]))
                    for o in data["outline"]:
                        if isinstance(o, str) and o.strip():
                            c.execute("INSERT INTO kb_distill_items(job_id, chunk_idx, kind, content) VALUES (?,?,?,?)",
                                      (job_id, idx, "outline", o.strip()[:200]))
                    for qa in data["qa"]:
                        if isinstance(qa, dict) and qa.get("q") and qa.get("a"):
                            c.execute("INSERT INTO kb_distill_items(job_id, chunk_idx, kind, question, answer) VALUES (?,?,?,?,?)",
                                      (job_id, idx, "qa", str(qa["q"])[:200], str(qa["a"])[:800]))
                    for t in data["terms"]:
                        if isinstance(t, dict) and t.get("term") and t.get("def"):
                            c.execute("INSERT INTO kb_distill_items(job_id, chunk_idx, kind, content) VALUES (?,?,?,?)",
                                      (job_id, idx, "term", f"{t['term']}：{t['def']}"[:300]))
                c.execute("UPDATE kb_distill_jobs SET chunk_offset=?, done_chunks=?, updated_at=? WHERE id=?",
                          (idx + 1, idx + 1, datetime.now().isoformat(), job_id))
                c.commit()
        with _conn() as c:
            c.execute("UPDATE kb_distill_jobs SET status='done', error='', updated_at=? WHERE id=?",
                      (datetime.now().isoformat(), job_id))
        logger.info(f"[Distiller] 任务 #{job_id} 蒸馏完成（{len(chunks)} 块），等待用户审核入库")
    except Exception as e:
        logger.error(f"[Distiller] 任务 #{job_id} 异常: {e}")
        with _conn() as c:
            c.execute("UPDATE kb_distill_jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                      (str(e)[:300], datetime.now().isoformat(), job_id))


def start_job(source_name, engine="local", category_id=0, resume=False):
    """门2 — 用户点头触发。创建或续跑任务，返回 job_id"""
    global _running_thread
    ok, reason, text = gate_check(source_name)
    if not ok:
        raise ValueError(reason)
    engine = engine if engine in ("local", "cloud") else "local"

    with _conn() as c:
        old = c.execute(
            "SELECT * FROM kb_distill_jobs WHERE source_name=? ORDER BY id DESC LIMIT 1",
            (source_name,)).fetchone()
        if old and resume and old["status"] in ("failed", "running"):
            # 断点续传：复用旧任务，从 offset 继续
            job_id = old["id"]
            c.execute("UPDATE kb_distill_jobs SET status='pending', engine=?, error='', updated_at=? WHERE id=?",
                      (engine, datetime.now().isoformat(), job_id))
        elif old and old["status"] in ("running", "pending"):
            raise ValueError("该资料已有蒸馏任务在进行中")
        else:
            cur = c.execute(
                "INSERT INTO kb_distill_jobs(source_name, category_id, engine, total_chunks) VALUES (?,?,?,?)",
                (source_name, int(category_id or 0), engine, len(chunk_text(text))))
            job_id = cur.lastrowid

    def _worker():
        global _running_thread
        try:
            _run_job(job_id)
        finally:
            with _job_lock:
                _running_thread = None

    with _job_lock:
        if _running_thread is None:
            _running_thread = threading.Thread(target=_worker, daemon=True, name=f"distill-{job_id}")
            _running_thread.start()
    return job_id


def list_jobs():
    with _conn() as c:
        rows = c.execute("""
            SELECT j.*, (SELECT COUNT(*) FROM kb_distill_items i
                         WHERE i.job_id=j.id AND i.approved=0) AS pending_items
            FROM kb_distill_jobs j ORDER BY j.id DESC LIMIT 50""").fetchall()
        return [dict(r) for r in rows]


def job_items(job_id, pending_only=True):
    with _conn() as c:
        sql = "SELECT * FROM kb_distill_items WHERE job_id=?"
        if pending_only:
            sql += " AND approved=0"
        sql += " ORDER BY chunk_idx, id"
        return [dict(r) for r in c.execute(sql, (job_id,))]


def delete_job(job_id):
    with _conn() as c:
        c.execute("DELETE FROM kb_distill_items WHERE job_id=?", (job_id,))
        c.execute("DELETE FROM kb_distill_jobs WHERE id=?", (job_id,))


def list_sources():
    """门1 预检列表：data/knowledge 下的文本类资料 + 蒸馏状态"""
    jobs = {j["source_name"]: j for j in list_jobs()}
    sources = []
    if KB_DIR.exists():
        for f in sorted(KB_DIR.iterdir()):
            if not f.is_file() or f.suffix.lower() not in (".txt", ".md", ".markdown"):
                continue
            ok, reason, text = gate_check(f.name)
            j = jobs.get(f.name)
            sources.append({
                "filename": f.name,
                "size": f.stat().st_size,
                "eligible": ok,
                "reason": reason,
                "job_status": j["status"] if j else "",
                "job_id": j["id"] if j else None,
                "pending_items": j["pending_items"] if j else 0,
            })
    return sources
