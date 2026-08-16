# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
本地知识库模块
使用 FAISS 向量检索实现语义搜索
纯本地运行，无HTTP服务
"""
import os
import sys
import json
import time
import threading
import traceback
import numpy as np
from pathlib import Path
from contextlib import contextmanager
import logging

# embedding 缓存：加速频繁的相似查询（LRU 简单实现）
_EMBED_CACHE = {}
_EMBED_CACHE_MAX = 100

# 独立性模块：Ollama 本地 API 调用绕过出站检查
try:
    from src.core.independence import bypass_independence
except ImportError:
    @contextmanager
    def bypass_independence():
        yield
logger = logging.getLogger(__name__)

# 项目根目录下的数据路径
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
FAISS_DIR = os.path.join(DATA_DIR, "faiss")
KNOWLEDGE_DIR = os.path.join(DATA_DIR, "knowledge")


class KnowledgeBase:
    """本地知识库：FAISS 向量检索"""

    def __init__(self):
        self.index = None
        self.documents = []  # [{id, text, source, metadata}]
        self._embedding_model = None
        self._dimension = 384  # 默认 embedding 维度
        self._load_index()

    def _load_index(self):
        """加载已有的 FAISS 索引（支持分片）"""
        import faiss
        index_path = os.path.join(FAISS_DIR, "knowledge.index")

        # 使用 MetaSharder 加载（优先分片，回退传统文件）
        try:
            from src.core.meta_sharder import MetaSharder
            sharder = MetaSharder(FAISS_DIR)
            self.documents = sharder.load_all()
        except Exception as e:
            logger.error(f"[KnowledgeBase] MetaSharder 加载失败，回退传统加载: {e}")
            meta_path = os.path.join(FAISS_DIR, "knowledge_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        self.documents = json.load(f)
                except Exception:
                    self.documents = []
            else:
                self.documents = []

        if os.path.exists(index_path):
            try:
                # 自适应加载：SSD 用 mmap（快+省内存），HDD 用 mmap（避免全量读取）
                try:
                    self.index = faiss.read_index(index_path, faiss.IO_FLAG_MMAP)
                except TypeError:
                    # 老版本 FAISS 不支持 IO_FLAG_MMAP
                    self.index = faiss.read_index(index_path)
                self._dimension = self.index.d
                return True
            except Exception as e:
                logger.error(f"[KnowledgeBase] 加载 FAISS 索引失败: {e}")
                # ★ 重要：仅清空索引，不清空元数据 documents
                # 之前的代码会把已从 MetaSharder 成功加载的 documents 也清空，造成数据脱节
                self.index = None
                # self.documents = []  ← 已移除此行，documents 保留已加载的元数据
        return False

    def _save_index(self):
        """保存 FAISS 索引 + 元数据（兼容旧调用）"""
        if self.index is None:
            return
        os.makedirs(FAISS_DIR, exist_ok=True)
        index_path = os.path.join(FAISS_DIR, "knowledge.index")
        try:
            import faiss
            faiss.write_index(self.index, index_path)
            self._save_meta()
        except Exception as e:
            logger.error(f"[KnowledgeBase] 保存索引失败: {e}")

    def _get_ollama_base_url(self):
        """获取 Ollama 地址"""
        try:
            import yaml
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.yaml")
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("ollama", {}).get("base_url", "http://localhost:11434")
        except Exception:
            return "http://localhost:11434"

    def _get_embedding_model(self):
        """延迟加载 embedding 模型 — 优先用 Ollama 本地 API"""
        if self._embedding_model is not None:
            return self._embedding_model

        # 方式1：Ollama embedding API（推荐，纯本地，无需下载）
        try:
            import requests
            base_url = self._get_ollama_base_url()
            resp = requests.get(f"{base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                # 检查是否有 embedding 模型（nomic-embed-text 或 bge-m3）
                models = [m["name"] for m in resp.json().get("models", [])]
                embed_models = [m for m in models if "embed" in m or "bge" in m]
                if embed_models:
                    self._embedding_model = ("ollama", base_url, embed_models[0])
                    # Ollama embedding 维度取决于模型，默认 768
                    self._dimension = 768
                    logger.debug(f"[KnowledgeBase] 使用 Ollama embedding: {embed_models[0]}")
                    return self._embedding_model
                # Ollama 在线但没有 embed 模型，尝试直接调用（Ollama 会自动下载默认的）
                self._embedding_model = ("ollama", base_url, "nomic-embed-text")
                self._dimension = 768
                logger.debug(f"[KnowledgeBase] 使用 Ollama embedding (自动下载 nomic-embed-text)")
                return self._embedding_model
        except Exception as e:
            logger.debug(f"[KnowledgeBase] Ollama embedding 不可用: {e}")

        # 方式2：sentence-transformers 本地缓存（离线回退）
        try:
            from sentence_transformers import SentenceTransformer
            model_name = "paraphrase-multilingual-MiniLM-L12-v2"
            logger.info(f"[KnowledgeBase] 尝试离线加载 embedding 模型: {model_name}")
            self._embedding_model = SentenceTransformer(model_name)
            self._dimension = self._embedding_model.get_sentence_embedding_dimension()
            logger.info(f"[KnowledgeBase] 模型加载完成, 维度: {self._dimension}")
            return self._embedding_model
        except Exception as e:
            logger.error(f"[KnowledgeBase] sentence-transformers 也失败: {e}")

        return None

    def _embed_texts(self, texts):
        """文本转向量（带缓存）"""
        # 单文本查询走缓存
        if len(texts) == 1:
            cache_key = texts[0][:200]  # 截断避免缓存过大
            cached = _EMBED_CACHE.get(cache_key)
            if cached is not None:
                return np.array([cached])

        model = self._get_embedding_model()
        if model is None:
            return None

        # Ollama API embedding
        if isinstance(model, tuple) and model[0] == "ollama":
            import requests
            _, base_url, model_name = model
            try:
                embeddings = []
                # Ollama v0.1.44+ 支持批量 embedding
                # 发送所有文本在一个请求中，大幅提升处理速度
                with bypass_independence():
                    resp = requests.post(
                        f"{base_url}/api/embed",
                        json={"model": model_name, "input": texts},
                        timeout=120
                    )
                if resp.status_code == 200:
                    result_data = resp.json()
                    if "embeddings" in result_data:
                        embeddings = result_data["embeddings"]
                    elif "embedding" in result_data:
                        # 旧的 /api/embeddings 端点返回单条 embedding
                        # 退化为逐条处理
                        pass
                
                # 如果批量失败，退化为逐条处理
                if not embeddings:
                    for text in texts:
                        with bypass_independence():
                            resp = requests.post(
                                f"{base_url}/api/embeddings",
                                json={"model": model_name, "prompt": text},
                                timeout=30
                            )
                        if resp.status_code == 200:
                            embeddings.append(resp.json()["embedding"])
                        else:
                            return None
                
                result = np.array(embeddings, dtype=np.float32)
                # 更新实际维度
                self._dimension = result.shape[1]
                return result
            except Exception as e:
                logger.error(f"[KnowledgeBase] Ollama embedding 失败: {e}")
                return None

        # sentence-transformers fallback
        embeddings = model.encode(texts, show_progress_bar=False)
        # 缓存单条 embedding 结果
        if len(texts) == 1 and len(embeddings) == 1:
            cache_val = embeddings[0]
            if len(_EMBED_CACHE) < _EMBED_CACHE_MAX:
                _EMBED_CACHE[cache_key] = cache_val
        return np.array(embeddings, dtype=np.float32)

    def add_document(self, text, source="manual", metadata=None):
        """添加一条知识到知识库"""
        import uuid
        doc_id = uuid.uuid4().hex[:16]
        doc = {
            "id": doc_id,
            "text": text,
            "source": source,
            "metadata": metadata or {}
        }
        self.documents.append(doc)

        # 重建索引（简单实现，大规模需增量索引）
        self._rebuild_index()
        return doc_id

    def add_documents_batch(self, texts, source="manual", metadata=None):
        """批量添加知识"""
        import uuid
        new_docs = []
        for text in texts:
            if not text.strip():
                continue
            doc_id = uuid.uuid4().hex[:16]
            doc = {
                "id": doc_id,
                "text": text.strip(),
                "source": source,
                "metadata": metadata or {}
            }
            new_docs.append(doc)
            self.documents.append(doc)

        if new_docs:
            self._rebuild_index()
        return [d["id"] for d in new_docs]

    def add_text(self, title, content):
        """
        手动添加文本到知识库
        Args:
            title: 文本标题（用作 source）
            content: 文本内容
        Returns:
            doc_id: 添加的文档ID
        """
        if not content or not content.strip():
            return None
        
        # 保存原始文本到 data/knowledge/ 目录（方便管理）
        import re
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)[:50]
        if not safe_title:
            safe_title = "untitled"
        
        txt_path = Path(KNOWLEDGE_DIR) / f"{safe_title}_{int(time.time())}.txt"
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n{content}")
        except Exception as e:
            logger.error(f"[KnowledgeBase] 保存文本文件失败: {e}")
        
        # 添加到知识库
        metadata = {"title": title, "type": "manual_text"}
        doc_id = self.add_document(content, source=title, metadata=metadata)
        
        logger.debug(f"[KnowledgeBase] 已添加文本: {title} (ID: {doc_id[:8]}...)")
        return doc_id

    # ==================== 格式支持定义 ====================

    # 完全支持的格式（有专用解析器）
    FORMAT_FULL = {
        ".txt": "纯文本文件",
        ".md": "Markdown 文档",
        ".json": "JSON 数据文件",
        ".jsonl": "JSON Lines 数据",
        ".csv": "CSV 表格文件",
        ".pdf": "PDF 文档",
        ".docx": "Word 文档 (.docx)",
        ".xlsx": "Excel 表格 (.xlsx)",
        ".pptx": "PowerPoint 演示文稿",
        ".html": "HTML 网页文件",
        ".htm": "HTML 网页文件",
        ".xml": "XML 数据文件",
        ".yaml": "YAML 配置文件",
        ".yml": "YAML 配置文件",
        ".toml": "TOML 配置文件",
        ".log": "日志文件",
        ".rtf": "富文本文件",
        ".epub": "EPUB 电子书",
        ".ini": "INI 配置文件",
        ".cfg": "配置文件",
        ".conf": "配置文件",
        ".py": "Python 脚本",
        ".js": "JavaScript 脚本",
        ".java": "Java 源码",
        ".c": "C 源码",
        ".cpp": "C++ 源码",
        ".h": "C/C++ 头文件",
        ".css": "CSS 样式表",
        ".sql": "SQL 脚本",
        ".sh": "Shell 脚本",
        ".bat": "批处理脚本",
        ".ps1": "PowerShell 脚本",
        ".r": "R 语言脚本",
        ".go": "Go 源码",
        ".rs": "Rust 源码",
        ".ts": "TypeScript 脚本",
        ".vue": "Vue 组件",
        ".jsx": "React JSX",
        ".tsx": "React TSX",
        ".srt": "字幕文件",
        ".lrc": "歌词文件",
        ".tex": "LaTeX 文档",
        ".bib": "BibTeX 参考文献",
        ".rst": "reStructuredText",
    }

    # 纯二进制格式（明确不支持，给出提示）
    FORMAT_UNSUPPORTED = {
        ".exe": "可执行文件，非文本格式",
        ".dll": "动态链接库，非文本格式",
        ".sys": "系统文件，非文本格式",
        ".zip": "压缩包，请先解压后再导入",
        ".rar": "压缩包，请先解压后再导入",
        ".7z": "压缩包，请先解压后再导入",
        ".tar": "压缩包，请先解压后再导入",
        ".gz": "压缩文件，请先解压后再导入",
        ".bz2": "压缩文件，请先解压后再导入",
        # ★ 2026-07-13: 音频/图片已支持（Whisper+minicpm-v），不再标记为不支持
        ".mp4": "视频文件，暂不支持视频识别导入",
        ".avi": "视频文件，暂不支持视频识别导入",
        ".mkv": "视频文件，暂不支持视频识别导入",
        ".mov": "视频文件，暂不支持视频识别导入",
        # ★ 2026-07-13: png/bmp/webp已支持（minicpm-v识别）
        ".gif": "GIF图片，暂不支持",
        ".svg": "SVG 矢量图，暂不支持",
        ".ico": "图标文件，非文本格式",
        ".psd": "Photoshop 文件，非文本格式",
        ".tiff": "TIFF 图片，暂不支持",
        ".doc": "旧版 Word 文档(.doc)，请转换为 .docx 格式",
        ".xls": "旧版 Excel 文档(.xls)，请转换为 .xlsx 格式",
        ".ppt": "旧版 PowerPoint 文档(.ppt)，请转换为 .pptx 格式",
        ".mobi": "Mobi 电子书，暂不支持，请转换为 .epub 或 .pdf",
        ".azw": "Kindle 电子书，暂不支持，请转换为 .epub 或 .pdf",
        ".obj": "3D 模型文件，非文本格式",
        ".stl": "3D 模型文件，非文本格式",
        ".db": "数据库文件，非文本格式",
        ".sqlite": "数据库文件，非文本格式",
        ".sqlite3": "数据库文件，非文本格式",
    }

    def import_file(self, file_path, source=None):
        """
        从文件导入知识（增强版：返回结构化结果，全面格式检测）

        Returns:
            dict: {
                "success": True/False,
                "filename": str,
                "format": str,           # 识别到的格式类型
                "chunks": int,           # 导入的段落数
                "size_mb": float,        # 文件大小(MB)
                "message": str,          # 成功/失败描述
                "error": str|None,       # 失败原因
                "warning": str|None,     # 警告（如部分解析失败）
            }
        """
        path = Path(file_path)
        if not path.exists():
            return {
                "success": False, "filename": path.name,
                "format": "unknown", "chunks": 0, "size_mb": 0,
                "message": f"文件不存在: {path.name}",
                "error": f"文件不存在: {file_path}", "warning": None
            }

        source = source or path.name
        suffix = path.suffix.lower()
        file_size_mb = path.stat().st_size / (1024 * 1024)

        # ---------- 1. 检查是否为明确不支持的二进制格式 ----------
        if suffix in self.FORMAT_UNSUPPORTED:
            reason = self.FORMAT_UNSUPPORTED[suffix]
            return {
                "success": False, "filename": path.name,
                "format": suffix, "chunks": 0, "size_mb": round(file_size_mb, 2),
                "message": f"不支持 {suffix} 格式: {reason}",
                "error": reason, "warning": None
            }

        # ---------- 2. 跳过空文件和过小文件 ----------
        if path.stat().st_size == 0:
            return {
                "success": False, "filename": path.name,
                "format": suffix or "unknown", "chunks": 0, "size_mb": 0,
                "message": f"文件为空: {path.name}",
                "error": "文件大小为 0 字节", "warning": None
            }
        if path.stat().st_size < 50:
            return {
                "success": False, "filename": path.name,
                "format": suffix or "unknown", "chunks": 0, "size_mb": round(file_size_mb, 4),
                "message": f"文件过小({path.stat().st_size}字节)，可能不是有效文档",
                "error": f"文件仅 {path.stat().st_size} 字节", "warning": None
            }

        # ---------- 3. 按格式解析文本 ----------
        texts = []
        parse_warning = None

        try:
            # === 纯文本类 ===
            if suffix in (".txt", ".md", ".log", ".ini", ".cfg", ".conf",
                          ".rst", ".srt", ".lrc", ".bib"):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 20]
                texts = paragraphs[:500]

            # === 代码类（作为单文档导入） ===
            elif suffix in (".py", ".js", ".java", ".c", ".cpp", ".h", ".css",
                            ".sql", ".sh", ".bat", ".ps1", ".r", ".go", ".rs",
                            ".ts", ".vue", ".jsx", ".tsx", ".tex"):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if len(content.strip()) > 30:
                    # 代码文件按函数/类/段落分块
                    import re
                    blocks = re.split(r'\n\n+|\n(?=def |class |function |const |let |var |import |export |# )', content)
                    texts = [b.strip() for b in blocks if len(b.strip()) > 30][:300]

            # === JSON / JSONL ===
            elif suffix in (".json", ".jsonl"):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    try:
                        data = json.load(f)
                        if isinstance(data, list):
                            texts = [json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
                                     for item in data if len(str(item)) > 20]
                        elif isinstance(data, dict):
                            # 对字典，把键值对转成可读文本
                            texts = [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in data.items()
                                     if len(f"{k}: {v}") > 20]
                        else:
                            texts = [json.dumps(data, ensure_ascii=False)]
                    except json.JSONDecodeError:
                        f.seek(0)
                        texts = [line.strip() for line in f if len(line.strip()) > 20][:500]

            # === CSV ===
            elif suffix == ".csv":
                import csv
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.reader(f)
                    rows = list(reader)
                    if rows:
                        header = rows[0] if rows else []
                        texts = []
                        for row in rows[1:]:
                            row_text = " | ".join(f"{header[i]}={row[i]}" if i < len(header) else str(c)
                                                  for i, c in enumerate(row) if c)
                            if len(row_text) > 20:
                                texts.append(row_text)
                        texts = texts[:500]

            # === YAML / TOML ===
            elif suffix in (".yaml", ".yml"):
                try:
                    import yaml as _yaml
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        data = _yaml.safe_load(f)
                    if isinstance(data, dict):
                        texts = [f"{k}: {v}" for k, v in data.items() if len(f"{k}: {v}") > 20]
                    elif isinstance(data, list):
                        texts = [str(item) for item in data if len(str(item)) > 20]
                    else:
                        texts = [str(data)] if data else []
                except Exception:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    texts = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 20][:500]

            elif suffix == ".toml":
                try:
                    import tomllib
                    with open(path, "rb") as f:
                        data = tomllib.load(f)
                    if isinstance(data, dict):
                        texts = [f"{k}: {v}" for k, v in data.items() if len(f"{k}: {v}") > 20]
                except Exception:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    texts = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 20][:500]

            # === HTML ===
            elif suffix in (".html", ".htm"):
                try:
                    # 去除标签提取纯文本
                    import re
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    # 去除 script/style
                    content = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content, flags=re.DOTALL | re.IGNORECASE)
                    # 去除 HTML 标签
                    content = re.sub(r'<[^>]+>', ' ', content)
                    # 去除多余空白
                    content = re.sub(r'\s+', ' ', content).strip()
                    # 按段落分块
                    paragraphs = [p.strip() for p in content.split('  ') if len(p.strip()) > 20]
                    texts = paragraphs[:500]
                except Exception as e:
                    parse_warning = f"HTML 解析异常: {e}"

            # === XML ===
            elif suffix == ".xml":
                import re
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                # 提取标签之间的文本内容
                texts_list = re.findall(r'>([^<]+)<', content)
                texts = [t.strip() for t in texts_list if len(t.strip()) > 20][:500]

            # === RTF ===
            elif suffix == ".rtf":
                try:
                    # 简单 RTF 解析：去除控制字符
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    import re
                    # 去除 RTF 控制词 {\rtf1 ... }
                    content = re.sub(r'\{[^{}]*\}', '', content)
                    content = re.sub(r'\\[a-z]+\d* ?', '', content)
                    content = re.sub(r'[^\\]\{', '', content)
                    content = re.sub(r'\}', '', content)
                    content = re.sub(r'\s+', ' ', content).strip()
                    paragraphs = [p.strip() for p in content.split('.') if len(p.strip()) > 20]
                    texts = paragraphs[:300]
                except Exception as e:
                    parse_warning = f"RTF 解析不完整: {e}，建议转换为 .docx 格式"

            # === EPUB ===
            elif suffix == ".epub":
                try:
                    import zipfile
                    import xml.etree.ElementTree as ET
                    with zipfile.ZipFile(str(path), 'r') as z:
                        # 找到 content.opf 获取章节文件列表
                        chapter_texts = []
                        # 简单方法：遍历所有 HTML/XHTML 文件
                        for name in z.namelist():
                            if name.endswith(('.html', '.xhtml', '.htm')):
                                try:
                                    html_content = z.read(name).decode('utf-8', errors='replace')
                                    import re as _re
                                    html_content = _re.sub(r'<[^>]+>', ' ', html_content)
                                    html_content = _re.sub(r'\s+', ' ', html_content).strip()
                                    if len(html_content) > 50:
                                        chapter_texts.append(html_content)
                                except Exception:
                                    continue
                        if not chapter_texts:
                            # 尝试从 META-INF/container.xml 找根文件
                            parse_warning = "EPUB 章节未找到，可能结构特殊"
                        # 合并并分段
                        full_text = "\n\n".join(chapter_texts)
                        paragraphs = [p.strip() for p in full_text.split("\n\n") if len(p.strip()) > 20]
                        texts = paragraphs[:500]
                except Exception as e:
                    parse_warning = f"EPUB 解析失败: {e}，建议转换为 .pdf 格式"

            # === PDF（pypdf 优先 + pdfplumber 回退，MIT/Apache 2.0 完全合规） ===
            elif suffix == ".pdf":
                pdf_text = ""
                pdf_method = None
                pdf_errors = []  # 收集各引擎的错误信息

                # 预检：是否是空文件或损坏文件
                if path.stat().st_size < 100:
                    return {
                        "success": False, "filename": path.name,
                        "format": "pdf", "chunks": 0, "size_mb": round(file_size_mb, 4),
                        "message": f"PDF 文件过小({path.stat().st_size}字节)，可能是损坏文件",
                        "error": "PDF 文件损坏或为空", "warning": None
                    }

                # 引擎1：pypdf（MIT）— 主引擎，纯 Python，无 GPL 依赖
                try:
                    from pypdf import PdfReader as _PypdfReader
                    reader = _PypdfReader(str(path))
                    if reader.is_encrypted:
                        return {
                            "success": False, "filename": path.name,
                            "format": "pdf", "chunks": 0, "size_mb": round(file_size_mb, 2),
                            "message": "PDF 文件已加密，请先解除密码保护后再导入",
                            "error": "PDF 文件有密码保护", "warning": None
                        }
                    if len(reader.pages) == 0:
                        return {
                            "success": False, "filename": path.name,
                            "format": "pdf", "chunks": 0, "size_mb": round(file_size_mb, 2),
                            "message": "PDF 文件无页面",
                            "error": "PDF 无页面内容", "warning": None
                        }
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            pdf_text += page_text + "\n"
                    pdf_method = "pypdf"
                except ImportError:
                    pdf_errors.append("pypdf: 未安装")
                except Exception as e:
                    pdf_errors.append(f"pypdf: {e}")

                # 引擎2：pdfplumber（MIT）— 回退，表格型 PDF 效果好
                if not pdf_text.strip():
                    try:
                        import pdfplumber
                        with pdfplumber.open(str(path)) as pdf:
                            # 先检查是否加密（pdfplumber 打开加密文件会抛异常）
                            for page in pdf.pages:
                                page_text = page.extract_text()
                                if page_text:
                                    pdf_text += page_text + "\n"
                        pdf_method = "pdfplumber"
                    except ImportError:
                        pdf_errors.append("pdfplumber: 未安装")
                    except Exception as e:
                        _emsg = str(e)
                        if "password" in _emsg.lower() or "encrypt" in _emsg.lower():
                            return {
                                "success": False, "filename": path.name,
                                "format": "pdf", "chunks": 0, "size_mb": round(file_size_mb, 2),
                                "message": "PDF 文件已加密，请先解除密码保护后再导入",
                                "error": "PDF 文件有密码保护", "warning": None
                            }
                        pdf_errors.append(f"pdfplumber: {e}")

                # 所有引擎都失败
                if not pdf_text.strip():
                    # 判断是否缺少所有解析库
                    all_missing = all("未安装" in e for e in pdf_errors)
                    if all_missing:
                        return {
                            "success": False, "filename": path.name,
                            "format": "pdf", "chunks": 0, "size_mb": round(file_size_mb, 2),
                            "message": "PDF 解析库缺失，请安装: pip install pypdf pdfplumber",
                            "error": "缺少 PDF 解析库 (pypdf/pdfplumber)", "warning": None
                        }
                    # 有库但解析失败 — 可能是扫描件
                    error_detail = "; ".join(pdf_errors) if pdf_errors else "所有引擎均无法提取文字"
                    return {
                        "success": False, "filename": path.name,
                        "format": "pdf", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": f"PDF 解析失败: {error_detail}（可能是扫描图片，需要 OCR）",
                        "error": error_detail, "warning": None
                    }

                # ★ 智能段落分割（兼容单/双换行的 PDF）
                # 先尝试双换行分割（标准段落），如果分块太少则用单换行
                raw_paragraphs = pdf_text.split("\n\n")
                paragraphs = [p.strip() for p in raw_paragraphs if len(p.strip()) > 15]

                if len(paragraphs) < 3:
                    # 单换行 PDF：用连续非空行合并成段落
                    all_lines = [l.strip() for l in pdf_text.split("\n") if l.strip()]
                    merged = []
                    current = ""
                    for line in all_lines:
                        # 检测段落开始标记（序号、标题模式）
                        is_new_para = (
                            line.endswith((".", "。", "!", "！", "?", "？", ":", "：")) and len(current) > 80
                        ) or (
                            len(line) < 50 and (line[0].isdigit() or line.startswith(("第", "一", "二", "三")))
                        )
                        if is_new_para and current:
                            merged.append(current.strip())
                            current = line
                        else:
                            current += " " + line if current else line
                    if current.strip():
                        merged.append(current.strip())
                    paragraphs = merged if merged else paragraphs

                # 将段落分割成 500 字符以内的 chunks
                for para_text in paragraphs:
                    if len(para_text) <= 500:
                        texts.append(para_text)
                    else:
                        # 按句号/分号智能切分
                        import re as _re
                        sentences = _re.split(r'(?<=[。！？；\n])', para_text)
                        chunk = ""
                        for s in sentences:
                            if len(chunk) + len(s) > 500 and chunk:
                                texts.append(chunk.strip())
                                chunk = s
                            else:
                                chunk += s
                        if chunk.strip():
                            texts.append(chunk.strip())

                texts = texts[:500]

            # === DOCX ===
            elif suffix == ".docx":
                try:
                    import docx
                    doc = docx.Document(str(path))
                    paragraphs = [p.text.strip() for p in doc.paragraphs if len(p.text.strip()) > 20]
                    texts = paragraphs[:500]
                    if not paragraphs and not texts:
                        parse_warning = "DOCX 文件为空或无可提取文字"
                except ImportError:
                    return {
                        "success": False, "filename": path.name,
                        "format": "docx", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": "DOCX 解析库缺失: 请安装 python-docx (pip install python-docx)",
                        "error": "缺少 python-docx 库", "warning": None
                    }
                except Exception as e:
                    return {
                        "success": False, "filename": path.name,
                        "format": "docx", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": f"DOCX 解析失败: {e}",
                        "error": str(e), "warning": None
                    }

            # === XLSX ===
            elif suffix == ".xlsx":
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(str(path), read_only=True)
                    texts = []
                    for sheet_name in wb.sheetnames:
                        sheet = wb[sheet_name]
                        rows_data = []
                        for row in sheet.iter_rows(max_row=200, values_only=True):
                            row_text = " | ".join(str(c) if c is not None else "" for c in row)
                            if len(row_text) > 15:
                                rows_data.append(row_text)
                        if rows_data:
                            texts.extend(rows_data[:300])
                    texts = texts[:500]
                    if not texts:
                        parse_warning = "Excel 文件为空或无数据"
                except ImportError:
                    return {
                        "success": False, "filename": path.name,
                        "format": "xlsx", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": "Excel 解析库缺失: 请安装 openpyxl (pip install openpyxl)",
                        "error": "缺少 openpyxl 库", "warning": None
                    }
                except Exception as e:
                    return {
                        "success": False, "filename": path.name,
                        "format": "xlsx", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": f"Excel 解析失败: {e}",
                        "error": str(e), "warning": None
                    }

            # === PPTX ===
            elif suffix == ".pptx":
                try:
                    from pptx import Presentation
                    prs = Presentation(str(path))
                    for slide in prs.slides:
                        for shape in slide.shapes:
                            if shape.has_text_frame:
                                for para in shape.text_frame.paragraphs:
                                    text = para.text.strip()
                                    if len(text) > 20:
                                        texts.append(text)
                    texts = texts[:500]
                    if not texts:
                        parse_warning = "PPTX 无可提取文字（可能只有图片）"
                except ImportError:
                    return {
                        "success": False, "filename": path.name,
                        "format": "pptx", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": "PPTX 解析库缺失: 请安装 python-pptx (pip install python-pptx)",
                        "error": "缺少 python-pptx 库", "warning": None
                    }
                except Exception as e:
                    return {
                        "success": False, "filename": path.name,
                        "format": "pptx", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": f"PPTX 解析失败: {e}",
                        "error": str(e), "warning": None
                    }

            # === ★ 2026-07-13: 音频文件（Whisper转文字） ===
            elif suffix in (".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"):
                try:
                    import subprocess as _sp
                    # 用Ollama的Whisper或faster-whisper
                    # 先试faster-whisper
                    try:
                        from faster_whisper import WhisperModel
                        model_w = WhisperModel("base", device="cpu", compute_type="int8")
                        segments, info = model_w.transcribe(str(path))
                        texts = [seg.text.strip() for seg in segments if len(seg.text.strip()) > 20][:500]
                        if not texts:
                            parse_warning = "音频转文字为空"
                    except ImportError:
                        # 回退：用Whisper命令行
                        try:
                            result = _sp.run(
                                ['python', '-m', 'whisper', str(path), '--model', 'base', '--output_format', 'txt'],
                                capture_output=True, text=True, timeout=300
                            )
                            if result.returncode == 0:
                                content = result.stdout
                                texts = [p.strip() for p in content.split('\n') if len(p.strip()) > 20][:500]
                            else:
                                parse_warning = "音频转文字需要安装faster-whisper: pip install faster-whisper"
                        except Exception as we:
                            parse_warning = f"音频转文字失败: {we}，请安装faster-whisper"
                except Exception as e:
                    parse_warning = f"音频处理异常: {e}"

            # === ★ 2026-07-13: 图片文件（Ollama minicpm-v识别） ===
            elif suffix in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                try:
                    import base64 as _b64
                    import requests as _req
                    # 用Ollama的minicpm-v:8b识别图片
                    with open(path, 'rb') as img_f:
                        img_b64 = _b64.b64encode(img_f.read()).decode('utf-8')
                    r = _req.post(
                        'http://localhost:11434/api/chat',
                        json={
                            'model': 'minicpm-v:8b',
                            'messages': [{
                                'role': 'user',
                                'content': '请详细描述这张图片的内容，包括场景、物体、文字、颜色等信息。',
                                'images': [img_b64]
                            }],
                            'stream': False,
                            'options': {'temperature': 0.3, 'num_predict': 500}
                        },
                        timeout=60
                    )
                    if r.status_code == 200:
                        desc = r.json().get('message', {}).get('content', '')
                        if desc:
                            texts = [desc.strip()]
                            if len(desc) > 500:
                                import re as _re
                                sentences = _re.split(r'(?<=[。！？\n])', desc)
                                texts = [s.strip() for s in sentences if len(s.strip()) > 20][:10]
                        else:
                            parse_warning = "图片识别返回空内容"
                    else:
                        parse_warning = f"图片识别失败: Ollama返回{r.status_code}，确保minicpm-v:8b已安装"
                except Exception as e:
                    parse_warning = f"图片识别异常: {e}，确保Ollama和minicpm-v:8b可用"

            # === 其他已知文本格式（通用回退） ===
            elif suffix in self.FORMAT_FULL:
                # 格式在列表中但上面没特殊处理，用通用文本读取
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    if len(content.strip()) > 20:
                        paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 20]
                        texts = paragraphs[:500]
                        if not paragraphs and len(content) > 20:
                            texts = [content[:2000]]
                except Exception as e:
                    parse_warning = f"文本读取异常: {e}"

            # === 完全未知的格式 ===
            else:
                # 尝试以文本方式读取（可能是重命名的文本文件）
                try:
                    with open(path, "rb") as f:
                        raw = f.read(4096)
                    # 检测是否为二进制文件（包含大量 null 字节）
                    null_count = raw.count(b'\x00')
                    if null_count > len(raw) * 0.05:
                        return {
                            "success": False, "filename": path.name,
                            "format": suffix or "unknown", "chunks": 0, "size_mb": round(file_size_mb, 2),
                            "message": f"未知格式 .{suffix}: 文件为二进制文件，不支持导入",
                            "error": f"二进制文件 (.{suffix})，不是文本格式", "warning": None
                        }
                    # 看起来是文本文件，尝试读取
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(50000)
                    if len(content.strip()) > 20:
                        paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 20]
                        texts = paragraphs[:500]
                        if texts:
                            parse_warning = f".{suffix} 不是标准格式，已尝试以文本方式读取"
                        else:
                            return {
                                "success": False, "filename": path.name,
                                "format": suffix or "unknown", "chunks": 0, "size_mb": round(file_size_mb, 2),
                                "message": f".{suffix} 格式文本内容过短或为空",
                                "error": f".{suffix} 无有效文本内容", "warning": None
                            }
                    else:
                        return {
                            "success": False, "filename": path.name,
                            "format": suffix or "unknown", "chunks": 0, "size_mb": round(file_size_mb, 2),
                            "message": f".{suffix} 格式: 文件内容为空或不可读",
                            "error": f".{suffix} 无有效内容", "warning": None
                        }
                except Exception as e:
                    return {
                        "success": False, "filename": path.name,
                        "format": suffix or "unknown", "chunks": 0, "size_mb": round(file_size_mb, 2),
                        "message": f"无法读取 .{suffix} 文件: {e}",
                        "error": str(e), "warning": None
                    }

        except Exception as e:
            return {
                "success": False, "filename": path.name,
                "format": suffix or "unknown", "chunks": 0, "size_mb": round(file_size_mb, 2),
                "message": f"文件解析异常: {e}",
                "error": str(e), "warning": None
            }

        # ---------- 4. 检查解析结果 ----------
        if not texts:
            msg = parse_warning or f".{suffix} 文件解析后无有效文本内容"
            return {
                "success": False, "filename": path.name,
                "format": suffix, "chunks": 0, "size_mb": round(file_size_mb, 2),
                "message": msg,
                "error": "解析后无有效内容", "warning": parse_warning
            }

        # ---------- 5. 导入到知识库 ----------
        try:
            self.add_documents_batch(texts, source=source)
        except Exception as e:
            return {
                "success": False, "filename": path.name,
                "format": suffix, "chunks": len(texts), "size_mb": round(file_size_mb, 2),
                "message": f"解析成功({len(texts)}段) 但导入知识库失败: {e}",
                "error": f"知识库导入失败: {e}", "warning": parse_warning
            }

        return {
            "success": True, "filename": path.name,
            "format": suffix, "chunks": len(texts), "size_mb": round(file_size_mb, 2),
            "message": f"成功导入 {path.name}: {len(texts)} 段",
            "error": None, "warning": parse_warning
        }

    def _save_meta(self):
        """仅保存文档元数据（不依赖 FAISS 索引）"""
        os.makedirs(FAISS_DIR, exist_ok=True)
        try:
            from src.core.meta_sharder import MetaSharder
            sharder = MetaSharder(FAISS_DIR)
            sharder.save_all(self.documents)
        except Exception as e:
            logger.error(f"[KnowledgeBase] 保存元数据失败: {e}")
            # 回退传统保存
            meta_path = os.path.join(FAISS_DIR, "knowledge_meta.json")
            try:
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(self.documents, f, ensure_ascii=False, indent=2)
            except Exception as _e: logger.debug(f"[knowledge_base] 忽略: {_e}")

    def _rebuild_index(self):
        """重建 FAISS 索引（先持久化元数据，再建向量索引）"""
        if not self.documents:
            return

        # ★ 先持久化文档元数据（防止 embedding 失败导致新增文档丢失）
        self._save_meta()

        texts = [d["text"] for d in self.documents]
        embeddings = self._embed_texts(texts)
        if embeddings is None:
            # 元数据已保存，索引下次可以重建
            logger.error("[KnowledgeBase] Embedding 失败，元数据已保存但 FAISS 索引未更新")
            return

        import faiss
        d = embeddings.shape[1]
        self._dimension = d

        # 使用 L2 距离的 IndexFlatIP（内积，余弦相似度归一化后可用）
        # 先归一化
        faiss.normalize_L2(embeddings)
        self.index = faiss.IndexFlatIP(d)
        self.index.add(embeddings)

        # 保存 FAISS 索引
        os.makedirs(FAISS_DIR, exist_ok=True)
        index_path = os.path.join(FAISS_DIR, "knowledge.index")
        try:
            faiss.write_index(self.index, index_path)
        except Exception as e:
            logger.error(f"[KnowledgeBase] 保存 FAISS 索引失败: {e}")

    def search(self, query, top_k=5, threshold=0.3, rerank=True):
        """
        语义搜索知识库（支持混合 Rerank 重排序）

        Args:
            query: 查询文本
            top_k: 返回前K条结果
            threshold: 相似度阈值
            rerank: 是否启用混合 Rerank（向量 + BM25 关键词）

        Returns:
            list: [{"text": str, "source": str, "score": float, "metadata": dict}]
        """
        if self.index is None or not self.documents:
            return []

        query_embedding = self._embed_texts([query])
        if query_embedding is None:
            return []

        # 归一化查询向量
        faiss_mod = __import__("faiss")
        faiss_mod.normalize_L2(query_embedding)

        # 扩大候选池给 Rerank 用
        fetch_k = min(top_k * 3 if rerank else top_k, len(self.documents))
        scores, indices = self.index.search(query_embedding, fetch_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.documents):
                continue
            if score < threshold:
                continue
            doc = self.documents[idx]
            results.append({
                "text": doc["text"],
                "source": doc["source"],
                "score": float(score),
                "metadata": doc.get("metadata", {}),
                "_doc_idx": idx,
            })

        if not rerank or len(results) <= 1:
            for r in results[:top_k]:
                r.pop("_doc_idx", None)
            return results[:top_k]

        # ── 混合 Rerank：向量分数 + BM25 关键词分数 ──
        query_tokens = self._tokenize(query)
        for r in results:
            idx = r["_doc_idx"]
            doc_text = self.documents[idx]["text"]
            bm25_score = self._bm25_score(query_tokens, doc_text)
            # 向量相似度 60% + BM25 40%
            r["score"] = round(r["score"] * 0.6 + bm25_score * 0.4, 4)

        results.sort(key=lambda x: x["score"], reverse=True)
        for r in results[:top_k]:
            r.pop("_doc_idx", None)
        return results[:top_k]

    @staticmethod
    def _tokenize(text):
        """简单分词：小写 + 按空格/标点切分"""
        import re
        # 中文按字切，英文按词切
        tokens = []
        for char in text.lower():
            if char.strip() and (char.isalnum() or '\u4e00' <= char <= '\u9fff'):
                tokens.append(char)
        #  also extract word-level tokens
        words = re.findall(r'[a-z0-9]+', text.lower())
        tokens.extend(words)
        return tokens

    @staticmethod
    def _bm25_score(query_tokens, doc_text, k1=1.5, b=0.75):
        """
        简化 BM25 分数计算
        返回 0~1 之间的归一化分数
        """
        if not query_tokens:
            return 0.0
        doc_tokens = []
        import re
        for char in doc_text.lower()[:2000]:  # 只取前2000字符
            if char.strip() and (char.isalnum() or '\u4e00' <= char <= '\u9fff'):
                doc_tokens.append(char)
        doc_tokens.extend(re.findall(r'[a-z0-9]+', doc_text.lower()[:2000]))
        if not doc_tokens:
            return 0.0
        doc_len = len(doc_tokens)
        avgdl = 200  # 假设平均文档长度
        # 计算词频
        from collections import Counter
        doc_counter = Counter(doc_tokens)
        score = 0.0
        for qt in set(query_tokens):
            tf = doc_counter.get(qt, 0)
            if tf == 0:
                continue
            idf = 1.0  # 简化：假设所有词都有相同 IDF
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * doc_len / avgdl)
            score += idf * numerator / denominator
        # 归一化到 0~1
        return min(score / max(len(query_tokens), 1), 1.0)

    def get_stats(self):
        """获取知识库统计信息"""
        return {
            "total_documents": len(self.documents),
            "sources": list(set(d["source"] for d in self.documents)),
            "index_loaded": self.index is not None,
            "dimension": self._dimension,
        }

    def clear(self):
        """清空知识库"""
        self.documents = []
        self.index = None
        # 删除文件
        for f in os.listdir(FAISS_DIR):
            os.remove(os.path.join(FAISS_DIR, f))


# ==================== 自动导入系统 ====================

IMPORTED_FILES_PATH = os.path.join(DATA_DIR, "imported_files.json")

# 支持的文件类型（合并 KnowledgeBase 类定义，供模块级使用）
SUPPORTED_EXTENSIONS = set(KnowledgeBase.FORMAT_FULL.keys())

# 自动导入状态（供前端查询）
_auto_import_status = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_file": "",
    "results": [],  # [{filename, chunks, status, error?, message?}]
    "started_at": None,
    "finished_at": None,
}
_auto_import_lock = threading.Lock()


def _load_imported_files():
    """加载已导入文件记录"""
    if os.path.exists(IMPORTED_FILES_PATH):
        try:
            with open(IMPORTED_FILES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as _e: logger.debug(f"[knowledge_base] 忽略: {_e}")
    return {}


def _save_imported_files(records):
    """保存已导入文件记录"""
    os.makedirs(os.path.dirname(IMPORTED_FILES_PATH), exist_ok=True)
    with open(IMPORTED_FILES_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def _file_hash(filepath):
    """快速计算文件哈希（只取前 64KB + 文件大小 + 修改时间）"""
    import hashlib
    stat = os.stat(filepath)
    h = hashlib.md5()
    h.update(str(stat.st_size).encode())
    h.update(str(stat.st_mtime).encode())
    # 再取文件头部 64KB
    try:
        with open(filepath, "rb") as f:
            h.update(f.read(65536))
    except Exception as _e: logger.debug(f"[knowledge_base] 忽略: {_e}")
    return h.hexdigest()


def auto_import_new_files(kb=None, callback=None):
    """
    自动检测并导入新文件（后台线程调用）

    逻辑：
    1. 扫描 data/knowledge/ 目录所有文件
    2. 与 imported_files.json 比对
    3. 新文件或哈希变化的文件 → 导入
    4. 更新 imported_files.json
    """
    import threading
    import time as _time

    # 预扫描目录，提前获取文件列表（避免 running=True 后 total 还是 0 的窗口期）
    all_files = []
    if os.path.isdir(KNOWLEDGE_DIR):
        for fname in os.listdir(KNOWLEDGE_DIR):
            fpath = os.path.join(KNOWLEDGE_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            all_files.append(fpath)

    with _auto_import_lock:
        if _auto_import_status["running"]:
            return None  # 已在导入中
        _auto_import_status["running"] = True
        _auto_import_status["total"] = len(all_files)
        _auto_import_status["done"] = 0
        _auto_import_status["results"] = []
        _auto_import_status["started_at"] = _time.time()
        _auto_import_status["finished_at"] = None

    def _do_import():
        if kb is None:
            _kb = get_knowledge_base()
        else:
            _kb = kb

        records = _load_imported_files()
        files_to_import = []

        # 目录不存在则直接结束
        if not all_files:
            with _auto_import_lock:
                _auto_import_status["running"] = False
                _auto_import_status["finished_at"] = _time.time()
            if callback:
                callback(0, [])
            return

        # 找出新文件/变更文件
        for fpath in all_files:
            fname = os.path.basename(fpath)
            fhash = _file_hash(fpath)
            old_record = records.get(fname, {})

            if old_record.get("hash") == fhash:
                continue  # 未变化，跳过
            # 跳过只有 .pdf 后缀但实际是图片的假 PDF（< 1KB）
            if fpath.lower().endswith(".pdf") and os.path.getsize(fpath) < 1024:
                records[fname] = {"hash": fhash, "chunks": 0, "status": "skipped", "error": "文件过小(<1KB)，可能是假PDF"}
                with _auto_import_lock:
                    _auto_import_status["results"].append({
                        "filename": fname, "chunks": 0,
                        "status": "skipped", "error": "文件过小，可能是假PDF"
                    })
                continue

            # 检查是否为明确不支持的二进制格式
            ext = os.path.splitext(fname)[1].lower()
            _kb = _kb if _kb else get_knowledge_base()
            if ext in _kb.FORMAT_UNSUPPORTED:
                reason = _kb.FORMAT_UNSUPPORTED[ext]
                records[fname] = {"hash": fhash, "chunks": 0, "status": "unsupported", "error": reason}
                with _auto_import_lock:
                    _auto_import_status["results"].append({
                        "filename": fname, "chunks": 0,
                        "status": "unsupported", "error": f"不支持 {ext}: {reason}"
                    })
                continue

            files_to_import.append((fpath, fname, fhash))

        # 没有新文件需要导入，直接结束，不让 running 卡住
        if not files_to_import:
            with _auto_import_lock:
                _auto_import_status["running"] = False
                _auto_import_status["finished_at"] = _time.time()
            if callback:
                callback(0, _auto_import_status["results"])
            return

        # 逐个导入
        for fpath, fname, fhash in files_to_import:
            with _auto_import_lock:
                _auto_import_status["current_file"] = fname
                _auto_import_status["done"] += 1

            try:
                result = _kb.import_file(fpath)
                if result["success"]:
                    records[fname] = {
                        "hash": fhash,
                        "chunks": result["chunks"],
                        "status": "ok",
                        "format": result.get("format", ""),
                        "size_mb": result.get("size_mb", 0),
                        "imported_at": _time.time(),
                    }
                    with _auto_import_lock:
                        _auto_import_status["results"].append({
                            "filename": fname,
                            "chunks": result["chunks"],
                            "status": "ok",
                            "message": result.get("message", ""),
                        })
                    logger.debug(f"[KnowledgeBase] 自动导入: {fname} → {result['chunks']} 段")
                else:
                    error_msg = result.get("error") or result.get("message", "未知错误")
                    records[fname] = {
                        "hash": fhash,
                        "chunks": 0,
                        "status": "error",
                        "error": error_msg,
                    }
                    with _auto_import_lock:
                        _auto_import_status["results"].append({
                            "filename": fname,
                            "chunks": 0,
                            "status": "error",
                            "error": error_msg,
                            "message": result.get("message", ""),
                        })
                    logger.error(f"[KnowledgeBase] 导入失败: {fname} → {error_msg}")
            except Exception as e:
                records[fname] = {
                    "hash": fhash,
                    "chunks": 0,
                    "status": "error",
                    "error": str(e),
                }
                with _auto_import_lock:
                    _auto_import_status["results"].append({
                        "filename": fname, "chunks": 0,
                        "status": "error", "error": str(e)
                    })
                logger.debug(f"[KnowledgeBase] 导入异常: {fname} → {e}")

        # 保存记录
        _save_imported_files(records)

        # 清理已删除文件的记录
        existing_names = set(os.listdir(KNOWLEDGE_DIR)) if os.path.isdir(KNOWLEDGE_DIR) else set()
        for old_name in list(records.keys()):
            if old_name not in existing_names:
                del records[old_name]
        _save_imported_files(records)

        with _auto_import_lock:
            _auto_import_status["running"] = False
            _auto_import_status["current_file"] = ""
            _auto_import_status["finished_at"] = _time.time()

        if callback:
            callback(len(files_to_import), _auto_import_status["results"])

        if files_to_import:
            logger.info(f"[KnowledgeBase] 自动导入完成: {len(files_to_import)} 个文件")

    # 启动后台线程
    t = threading.Thread(target=_do_import, daemon=True, name="kb-auto-import")
    t.start()
    return t


def get_auto_import_status():
    """获取自动导入状态（供 API 调用）"""
    with _auto_import_lock:
        return dict(_auto_import_status)


def get_imported_files_list():
    """获取已导入文件列表"""
    records = _load_imported_files()
    result = []
    for fname, info in records.items():
        result.append({
            "filename": fname,
            "chunks": info.get("chunks", 0),
            "status": info.get("status", "unknown"),
            "format": info.get("format", ""),
            "size_mb": info.get("size_mb", 0),
            "error": info.get("error", ""),
            "imported_at": info.get("imported_at"),
        })
    return result


def get_supported_formats_info():
    """获取支持的格式信息（供前端展示）"""
    return {
        "supported": {ext: desc for ext, desc in KnowledgeBase.FORMAT_FULL.items()},
        "unsupported": {ext: desc for ext, desc in KnowledgeBase.FORMAT_UNSUPPORTED.items()},
        "total_supported": len(KnowledgeBase.FORMAT_FULL),
        "total_unsupported": len(KnowledgeBase.FORMAT_UNSUPPORTED),
    }


# 全局单例
_knowledge_base = None

def get_knowledge_base():
    """获取知识库单例"""
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBase()
    return _knowledge_base
