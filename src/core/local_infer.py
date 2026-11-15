"""
Harven AI — 统一本地推理入口（#796, 2026-09-19）

作用：解除各模块对 Ollama 的静默硬依赖。
原来 panel_assist / kid_archive / memory 各自裸调 http://127.0.0.1:11434，
Ollama 没装的用户直接瘫。现在全部收口到这里，按四级优先自动选择：

  ① 进程内已加载的 GGUF（llama_engine 单例）→ 直接用，零启动成本
  ② Ollama 可达 → 用（兼容老用户，可指定偏好模型）
  ③ 本地 data/models/ 有 GGUF → 按需加载 llama-cpp 推理（首次加载有几十秒成本）
  ④ 全部不可用 → 明确报错（绝不静默返回空串）

调用约定（与旧 call_local_llm 保持一致）：
  text, backend, err = chat_local(prompt, system=..., ...)
  成功：text 非空字符串，err == ''
  失败：text == None，err 为中文原因说明
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_load_lock = threading.Lock()

# Ollama 探测缓存（15 秒），避免高频调用每次都打 /api/tags
_ollama_cache = {"ts": 0.0, "ok": False, "models": []}
_OLLAMA_CACHE_SECONDS = 15


def _ollama_base_url():
    """读 config.yaml 的 ollama.base_url（缺省 localhost:11434）"""
    try:
        import os
        import yaml
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config.yaml")
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("ollama", {}).get("base_url", "http://localhost:11434")
    except Exception:
        return "http://localhost:11434"


def _ollama_status(force=False):
    """探测 Ollama 是否可达及已装模型（带缓存）。
    返回 (ok, models_list)
    """
    c = _ollama_cache
    now = time.time()
    if not force and now - c["ts"] < _OLLAMA_CACHE_SECONDS:
        return c["ok"], c["models"]
    try:
        import requests
        resp = requests.get(f"{_ollama_base_url()}/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            c.update(ts=now, ok=True, models=models)
            return True, models
    except Exception as e:
        logger.debug(f"[LocalInfer] Ollama 探测失败: {e}")
    c.update(ts=now, ok=False, models=[])
    return False, []


def _pick_ollama_model(prefer, models):
    """从已装模型里挑一个对话模型：
    ① prefer 精确/前缀匹配 ② 第一个非 embed/非视觉模型 ③ 第一个模型
    """
    if not models:
        return None
    if prefer:
        for m in models:
            if m == prefer or m.startswith(prefer):
                return m
    for m in models:
        low = m.lower()
        if "embed" not in low and "bge" not in low and "minicpm-v" not in low:
            return m
    return models[0]


def _inproc_gguf_chat(prompt, system, temperature, max_tokens):
    """① 进程内已加载的 GGUF 单例。返回 (text, backend) 或 (None, 原因)"""
    try:
        from src.core.llama_engine import _engine_instance
        inst = _engine_instance
        if inst is not None and inst.is_loaded:
            text = inst.generate(prompt, system_prompt=system or "",
                                 temperature=temperature, max_tokens=max_tokens)
            if text and not text.startswith("❌"):
                return text, "gguf-inproc"
            return None, f"进程内 GGUF 推理失败: {text}"
    except Exception as e:
        logger.debug(f"[LocalInfer] 进程内 GGUF 不可用: {e}")
    return None, "进程内无已加载 GGUF"


def _ollama_chat(prompt, system, temperature, max_tokens, prefer_model, timeout):
    """② Ollama 后端。返回 (text, backend) 或 (None, 原因)"""
    ok, models = _ollama_status()
    if not ok:
        return None, "Ollama 不可达"
    model = _pick_ollama_model(prefer_model, models)
    if not model:
        return None, "Ollama 在线但无已装模型"
    try:
        import requests
        payload = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        r = requests.post(f"{_ollama_base_url()}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        text = r.json().get("message", {}).get("content", "").strip()
        if text:
            return text, f"ollama:{model}"
        return None, "Ollama 返回空内容"
    except Exception as e:
        return None, f"Ollama 调用失败: {e}"


def _load_gguf_chat(prompt, system, temperature, max_tokens, prefer_model):
    """③ 按需加载本地 data/models/ 下的 GGUF。返回 (text, backend) 或 (None, 原因)"""
    try:
        from src.core.llama_engine import find_local_gguf, get_llama_engine
        path = find_local_gguf(prefer_model)
        if not path:
            return None, "本地无 GGUF 模型文件"
        with _load_lock:
            engine = get_llama_engine(path)
            if not engine.is_loaded:
                return None, "GGUF 模型加载失败"
            logger.info(f"[LocalInfer] 按需加载 GGUF: {path}")
            text = engine.generate(prompt, system_prompt=system or "",
                                   temperature=temperature, max_tokens=max_tokens)
        if text and not text.startswith("❌"):
            return text, f"gguf-loaded:{path}"
        return None, f"GGUF 推理失败: {text}"
    except Exception as e:
        logger.debug(f"[LocalInfer] GGUF 直读不可用: {e}")
        return None, f"GGUF 直读不可用: {e}"


def chat_local(prompt, system="", timeout=300, temperature=0.4,
               max_tokens=1024, prefer_model=None, allow_load_gguf=True):
    """统一本地推理入口（四级优先自动选择）。

    Args:
        prompt: 用户内容
        system: 系统提示词（可空）
        timeout: 请求超时（秒）
        temperature: 采样温度
        max_tokens: 最大生成 token 数
        prefer_model: 偏好模型名（如 settings 的 default_model 或 'qwen2.5:14b'）
        allow_load_gguf: 前两级都不可用时，是否允许按需加载本地 GGUF（加载较慢）

    Returns:
        (text, backend, error)
        成功：text 非空、error == ''
        失败：text == None、error 为中文原因（绝不静默返回空）
    """
    errors = []

    # ① 进程内已加载 GGUF
    text, info = _inproc_gguf_chat(prompt, system, temperature, max_tokens)
    if text is not None:
        return text, info, ""
    errors.append(info)

    # ② Ollama（兼容老用户）
    text, info = _ollama_chat(prompt, system, temperature, max_tokens, prefer_model, timeout)
    if text is not None:
        return text, info, ""
    errors.append(info)

    # ③ 按需加载本地 GGUF
    if allow_load_gguf:
        text, info = _load_gguf_chat(prompt, system, temperature, max_tokens, prefer_model)
        if text is not None:
            return text, info, ""
        errors.append(info)

    # ④ 明确报错
    err = "本地推理不可用（" + "；".join(errors) + "）。请安装 Ollama 或下载 GGUF 模型到 data/models/"
    logger.warning(f"[LocalInfer] {err}")
    return None, "none", err


def local_infer_available():
    """快速探测当前是否有任一本地推理后端可用（供 UI/提示用，不触发加载）"""
    try:
        from src.core.llama_engine import _engine_instance
        if _engine_instance is not None and _engine_instance.is_loaded:
            return True, "gguf-inproc"
    except Exception:
        pass
    ok, models = _ollama_status()
    if ok:
        return True, "ollama"
    return False, "none"
