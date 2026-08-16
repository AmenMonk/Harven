"""
Harven AI — 本地模型推理引擎（llama-cpp-python）
直接加载 GGUF 模型推理，无需 Ollama
"""

import logging
import threading
from pathlib import Path
from typing import Optional, Generator, Dict

logger = logging.getLogger(__name__)

_engine_instance = None
_engine_lock = threading.Lock()


class LlamaEngine:
    """llama-cpp-python 推理封装"""

    def __init__(self, model_path: Optional[str] = None):
        self._model_path = None
        self._llm = None
        self._loaded = False
        if model_path:
            self.load(model_path)

    def load(self, model_path: str) -> bool:
        """加载 GGUF 模型"""
        path = Path(model_path)
        if not path.exists():
            logger.error(f"[LlamaEngine] 模型文件不存在: {model_path}")
            return False

        try:
            from llama_cpp import Llama

            logger.info(f"[LlamaEngine] 加载模型: {model_path}")
            self._llm = Llama(
                model_path=str(path),
                n_ctx=4096,          # 上下文长度
                n_threads=8,         # CPU 线程
                n_gpu_layers=-1,     # 全部层放 GPU（自动检测 CUDA）
                verbose=False,
            )
            self._model_path = model_path
            self._loaded = True
            logger.info(f"[LlamaEngine] 模型加载成功")
            return True
        except ImportError:
            logger.error("[LlamaEngine] llama-cpp-python 未安装")
            return False
        except Exception as e:
            logger.error(f"[LlamaEngine] 模型加载失败: {e}")
            return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded and self._llm is not None

    @property
    def model_path(self) -> Optional[str]:
        return self._model_path

    def generate(self, prompt: str, system_prompt: str = "",
                 temperature: float = 0.7, max_tokens: int = 2048,
                 stop: Optional[list] = None) -> str:
        """非流式生成"""
        if not self.is_loaded:
            return "❌ 模型未加载"

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

        try:
            result = self._llm(
                full_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop or [],
                echo=False,
            )
            return result["choices"][0]["text"].strip()
        except Exception as e:
            logger.error(f"[LlamaEngine] 生成失败: {e}")
            return f"❌ 推理错误: {e}"

    def stream_generate(self, prompt: str, system_prompt: str = "",
                        temperature: float = 0.7, max_tokens: int = 2048,
                        stop: Optional[list] = None) -> Generator[str, None, None]:
        """流式生成"""
        if not self.is_loaded:
            yield "❌ 模型未加载"
            return

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

        try:
            stream = self._llm(
                full_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop or [],
                echo=False,
                stream=True,
            )
            for chunk in stream:
                text = chunk["choices"][0].get("text", "")
                if text:
                    yield text
        except Exception as e:
            logger.error(f"[LlamaEngine] 流式生成失败: {e}")
            yield f"❌ 推理错误: {e}"

    def unload(self):
        """卸载模型释放显存"""
        if self._llm:
            del self._llm
            self._llm = None
        self._loaded = False
        self._model_path = None


def get_llama_engine(model_path: Optional[str] = None) -> LlamaEngine:
    """获取全局单例"""
    global _engine_instance
    with _engine_lock:
        if _engine_instance is None:
            _engine_instance = LlamaEngine(model_path)
        elif model_path and model_path != _engine_instance.model_path:
            _engine_instance.unload()
            _engine_instance.load(model_path)
        return _engine_instance


def find_local_gguf(model_name: str = None) -> Optional[str]:
    """在本地 data/models 目录查找 GGUF 模型文件"""
    from pathlib import Path
    models_dir = Path(__file__).parent.parent.parent / "data" / "models"
    if not models_dir.exists():
        return None
    
    gguf_files = list(models_dir.glob("*.gguf"))
    if not gguf_files:
        return None
    
    if model_name:
        # 按模型名匹配
        for f in gguf_files:
            name_lower = f.stem.lower()
            if model_name.lower() in name_lower or model_name.lower().replace(":", "-") in name_lower:
                return str(f)
    
    # 返回找到的第一个 GGUF
    return str(gguf_files[0]) if gguf_files else None
