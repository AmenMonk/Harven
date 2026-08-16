"""
Harven AI — ModelScope 模型下载器
从魔搭社区下载 GGUF 量化模型，无需 Ollama
"""

import os
import time
import threading
import logging
from pathlib import Path
from typing import Optional, Callable, Dict, List

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent.parent.parent / "data" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# ── 模型列表：从 model_catalog.json 加载 ──
def _load_catalog_models() -> List[Dict]:
    """从目录加载可下载的模型"""
    try:
        from src.core.model_catalog import get_all_models
        models = get_all_models()
        result = []
        for m in models:
            dl = m.get("download", {})
            if dl.get("modelscope") or dl.get("huggingface"):
                result.append({
                    "id": m["id"],
                    "name": m["name"],
                    "size_gb": m["size_gb"],
                    "vram_gb": m["vram_gb"],
                    "modelscope_id": dl.get("modelscope", ""),
                    "filename": dl.get("filename", ""),
                    "description": m["description"],
                    "tags": m.get("tags", []),
                    "category": m.get("category", "llm"),
                })
        return result
    except Exception as e:
        logger.error(f"加载模型目录失败: {e}")
        return []

AVAILABLE_MODELS = _load_catalog_models()



# ── 下载状态追踪 ──
_downloads: Dict[str, dict] = {}
_lock = threading.Lock()


def list_models(gpu_vram_gb: Optional[float] = None) -> List[Dict]:
    """列出可用模型，可选按显存过滤"""
    models = []
    for m in AVAILABLE_MODELS:
        info = {
            "id": m["id"],
            "name": m["name"],
            "size_gb": m["size_gb"],
            "vram_gb": m["vram_gb"],
            "description": m["description"],
            "installed": _check_installed(m["id"]),
            "downloadable": not _check_installed(m["id"]),
        }
        if gpu_vram_gb and m["vram_gb"] > gpu_vram_gb:
            info["compatible"] = False
        else:
            info["compatible"] = True
        models.append(info)
    return models


def get_model_info(model_id: str) -> Optional[Dict]:
    for m in AVAILABLE_MODELS:
        if m["id"] == model_id:
            return m
    return None


def _check_installed(model_id: str) -> bool:
    """检查模型是否已下载"""
    info = get_model_info(model_id)
    if not info:
        return False
    path = MODELS_DIR / info["filename"]
    return path.exists()


def get_installed_model_path() -> Optional[Path]:
    """获取第一个已安装模型的路径"""
    for m in AVAILABLE_MODELS:
        path = MODELS_DIR / m["filename"]
        if path.exists():
            return path
    return None


def download_model(model_id: str, progress_callback: Optional[Callable] = None) -> Optional[str]:
    """
    从 ModelScope 下载模型 GGUF 文件
    返回模型文件路径，失败返回 None
    """
    info = get_model_info(model_id)
    if not info:
        logger.error(f"[ModelDownloader] 未知模型: {model_id}")
        return None

    target_path = MODELS_DIR / info["filename"]
    if target_path.exists():
        logger.info(f"[ModelDownloader] 模型已存在: {target_path}")
        return str(target_path)

    # 更新下载状态
    with _lock:
        _downloads[model_id] = {
            "status": "downloading",
            "progress": 0,
            "size_gb": info["size_gb"],
            "started_at": time.time(),
        }

    try:
        from modelscope.hub.snapshot_download import snapshot_download
        import shutil

        logger.info(f"[ModelDownloader] 开始从 ModelScope 下载: {info['modelscope_id']}")

        # 下载到临时目录
        cache_dir = MODELS_DIR / "_cache"
        cache_dir.mkdir(exist_ok=True)

        # 使用 snapshot_download 下载整个仓库
        local_dir = snapshot_download(
            info["modelscope_id"],
            cache_dir=str(cache_dir),
            revision="master",
        )

        # 找到目标 GGUF 文件
        local_path = Path(local_dir)
        gguf_files = list(local_path.glob("*.gguf"))

        if not gguf_files:
            logger.error(f"[ModelDownloader] 未找到 GGUF 文件在 {local_dir}")
            with _lock:
                _downloads[model_id]["status"] = "failed"
                _downloads[model_id]["error"] = "未找到 GGUF 文件"
            return None

        # 优先匹配指定文件名，否则取第一个
        matched = None
        for gf in gguf_files:
            if info["filename"] in gf.name or gf.name == info["filename"]:
                matched = gf
                break
        if not matched:
            matched = max(gguf_files, key=lambda f: f.stat().st_size)

        # 移动到目标位置
        shutil.move(str(matched), str(target_path))
        logger.info(f"[ModelDownloader] 下载完成: {target_path} ({target_path.stat().st_size / 1e9:.1f}GB)")

        with _lock:
            _downloads[model_id]["status"] = "completed"
            _downloads[model_id]["progress"] = 100

        return str(target_path)

    except ImportError:
        logger.error("[ModelDownloader] modelscope 未安装")
        # 回退方案：尝试用 wget/curl
        return _download_fallback(info, target_path)
    except Exception as e:
        logger.error(f"[ModelDownloader] 下载失败: {e}")
        with _lock:
            _downloads[model_id]["status"] = "failed"
            _downloads[model_id]["error"] = str(e)
        return None


def _download_fallback(info: Dict, target_path: Path) -> Optional[str]:
    """回退下载方案：使用 urllib 从 hf-mirror 下载"""
    try:
        import urllib.request

        url = f"https://hf-mirror.com/{info['modelscope_id'].replace('/', '/resolve/main/')}/{info['filename']}"
        logger.info(f"[ModelDownloader] 回退下载: {url}")

        def report(block_num, block_size, total_size):
            if total_size > 0:
                pct = min(int(block_num * block_size / total_size * 100), 100)
                with _lock:
                    if info["id"] in _downloads:
                        _downloads[info["id"]]["progress"] = pct

        urllib.request.urlretrieve(url, str(target_path), reporthook=report)
        logger.info(f"[ModelDownloader] 回退下载完成: {target_path}")
        return str(target_path)
    except Exception as e:
        logger.error(f"[ModelDownloader] 回退下载失败: {e}")
        return None


def get_download_status(model_id: str) -> Optional[dict]:
    with _lock:
        return _downloads.get(model_id)


def start_download_async(model_id: str) -> str:
    """异步开始下载，返回任务 ID"""
    def _run():
        download_model(model_id)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return model_id
