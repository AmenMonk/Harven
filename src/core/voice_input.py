# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
语音输入模块
按住说话 → 录音 → 语音转文字 → 填入输入框
"""
import os
import tempfile
import numpy as np
from pathlib import Path
import logging
logger = logging.getLogger(__name__)


class VoiceInput:
    """语音输入器"""

    # 本地 Whisper 模型路径（通过 ModelScope 预下载，不依赖 HuggingFace）
    LOCAL_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "whisper-base"

    def __init__(self):
        self._is_recording = False
        self._sample_rate = 16000
        self._audio_data = None
        self._stream = None
        self._whisper_model = None  # 缓存Whisper模型，避免每次加载
        self._model_loaded = False  # 标记模型是否已尝试加载

    @property
    def is_available(self):
        """检查语音输入是否可用"""
        try:
            import sounddevice as sd
            return True
        except ImportError:
            return False

    def start_recording(self):
        """开始录音"""
        try:
            import sounddevice as sd
            self._audio_data = []
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                callback=self._audio_callback
            )
            self._stream.start()
            self._is_recording = True
            return True
        except Exception as e:
            logger.error(f"[VoiceInput] 启动录音失败: {e}")
            return False

    def stop_recording(self):
        """停止录音"""
        self._is_recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._audio_data:
            return None

        # 保存为临时 WAV 文件
        try:
            import soundfile as sf
            audio = np.concatenate(self._audio_data).astype(np.float32) / 32768.0
            tmp_path = os.path.join(tempfile.gettempdir(), "lingzhi_voice.wav")
            sf.write(tmp_path, audio, self._sample_rate)
            return tmp_path
        except Exception as e:
            logger.error(f"[VoiceInput] 保存音频失败: {e}")
            return None

    def _audio_callback(self, indata, frames, time_info, status):
        """音频回调"""
        if status:
            logger.debug(f"[VoiceInput] 录音状态: {status}")
        self._audio_data.append(indata.copy())

    def _get_model_path(self):
        """获取模型路径：本地优先，否则回退 HuggingFace 在线下载"""
        if self.LOCAL_MODEL_DIR.exists() and (self.LOCAL_MODEL_DIR / "model.bin").exists():
            return str(self.LOCAL_MODEL_DIR), True
        return "base", False

    def _try_load_model(self, device="cuda", compute_type="int8_float16"):
        """尝试加载 Whisper 模型，返回 (model, error_or_none)"""
        try:
            from faster_whisper import WhisperModel
            model_path, is_local = self._get_model_path()
            if is_local:
                logger.info(f"[VoiceInput] Loading local Whisper from {model_path} (device={device})")
            else:
                logger.info(f"[VoiceInput] Local model missing, downloading base from HuggingFace (device={device})")
            model = WhisperModel(model_path, device=device, compute_type=compute_type)
            logger.info(f"[VoiceInput] Whisper loaded OK on {device}")
            return model, None
        except ImportError:
            return None, "faster-whisper not installed"
        except Exception as e:
            return None, str(e)

    def transcribe(self, audio_path, language=None):
        """
        语音转文字 — CUDA → CPU 自动回退

        Args:
            audio_path: 音频文件路径
            language: 语言代码 (zh/en/ja/...)；None=跟随界面语言（★ 2026-08-16 i18n）

        Returns:
            str: 识别出的文字
        """
        # ★ 2026-08-16: 未指定语言时跟随界面语言（zh_CN→zh, en_US→en）
        if not language:
            try:
                from src.utils.i18n import get_stt_language
                language = get_stt_language()
            except Exception:
                language = "zh"
        # 加载模型（带 CUDA→CPU 自动降级）
        if self._whisper_model is None and not self._model_loaded:
            self._model_loaded = True
            # 先尝试 CUDA
            self._whisper_model, err = self._try_load_model(device="cuda", compute_type="int8_float16")
            if self._whisper_model is None:
                logger.error(f"[VoiceInput] CUDA load failed ({err}), retrying on CPU...")
                self._whisper_model, err = self._try_load_model(device="cpu", compute_type="int8")
                if self._whisper_model is None:
                    logger.error(f"[VoiceInput] CPU load also failed: {err}")

        if self._whisper_model:
            try:
                segments, _ = self._whisper_model.transcribe(audio_path, language=language)
                text = "".join(seg.text for seg in segments).strip()
                return text
            except Exception as e:
                err_str = str(e).lower()
                logger.error(f"[VoiceInput] Transcribe failed: {e}")
                # CUDA 运行时库缺失 → 重载为 CPU 再试一次
                if "cublas" in err_str or "cuda" in err_str or "cudnn" in err_str:
                    logger.info("[VoiceInput] CUDA runtime missing, reloading model on CPU...")
                    self._whisper_model, err2 = self._try_load_model(device="cpu", compute_type="int8")
                    if self._whisper_model:
                        try:
                            segments, _ = self._whisper_model.transcribe(audio_path, language=language)
                            text = "".join(seg.text for seg in segments).strip()
                            logger.info(f"[VoiceInput] CPU transcribe OK: {text[:80]}...")
                            return text
                        except Exception as e3:
                            logger.error(f"[VoiceInput] CPU retry also failed: {e3}")

        # 备用：speech_recognition（需联网，Google API）
        try:
            import speech_recognition as sr
            recognizer = sr.Recognizer()
            with sr.AudioFile(audio_path) as source:
                audio = recognizer.record(source)
            text = recognizer.recognize_google(audio, language=language)
            return text
        except ImportError as _e:
            logger.debug(f"[voice_input] 忽略: {_e}")
        except Exception as e:
            logger.error(f"[VoiceInput] speech_recognition fallback failed: {e}")

        return ""


# 全局单例
_voice_input = None

def get_voice_input():
    global _voice_input
    if _voice_input is None:
        _voice_input = VoiceInput()
    return _voice_input
