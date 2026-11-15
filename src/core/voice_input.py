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

    # 模型目录（自动择优：large-v3-turbo → medium → small → base → tiny，取第一个存在的）
    # ★ 2026-09-21 (#825) 加入 small/tiny，与设置页语音模型管理五档对齐
    # ★ 2026-09-29 (#929-A1) 支持常用档位：config.yaml → voice.preferred_model（默认 small，
    #   短指令识别质量与 turbo 无差别、加载/转写快约 10 倍）；未装时自动回退下方择优顺序
    MODEL_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "models"
    MODEL_PREFERENCE = ("whisper-large-v3-turbo", "whisper-medium", "whisper-small", "whisper-base", "whisper-tiny")
    DEFAULT_PREFERRED = "small"
    _CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"

    @classmethod
    def get_preferred_model(cls):
        """读 config.yaml → voice.preferred_model；缺省返回 small，配置坏了也返回 small"""
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(cls._CONFIG_PATH.read_text(encoding="utf-8")) or {}
            v = (cfg.get("voice") or {}).get("preferred_model") or cls.DEFAULT_PREFERRED
            return str(v).strip()
        except Exception as e:
            logger.debug(f"[VoiceInput] 读常用档位失败（用默认 {cls.DEFAULT_PREFERRED}）: {e}")
            return cls.DEFAULT_PREFERRED

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
        """获取模型路径：常用档位优先（config），否则自动择优（turbo→medium→small→base→tiny），
        都没有再回退 HuggingFace 在线下载"""
        # ★ #929-A1：常用档位优先
        pref = self.get_preferred_model()
        if pref:
            d = self.MODEL_ROOT / f"whisper-{pref}"
            if (d / "model.bin").exists():
                return str(d), True
            logger.info(f"[VoiceInput] 常用档位 {pref} 未安装，回退自动择优")
        for name in self.MODEL_PREFERENCE:
            d = self.MODEL_ROOT / name
            if (d / "model.bin").exists():
                return str(d), True
        # 兼容旧字段
        if self.MODEL_ROOT.exists():
            for child in sorted(self.MODEL_ROOT.iterdir()):
                if child.name.startswith("whisper") and (child / "model.bin").exists():
                    return str(child), True
        return "base", False

    @staticmethod
    def _register_gpu_dll_dirs():
        """把 cublas/cudnn DLL 目录注册进进程搜索路径（venv Scripts + pip nvidia 包 + Ollama）。
        不注册的话，一旦先加载了其他音频库，ctranslate2 会找不到 GPU 库而回落 CPU。
        ★ 2026-09-23 修：新增 Ollama 自带 CUDA 12 库目录（实测实锤：venv 没装 nvidia pip 包
        时 GPU 推理缺 cublas64_12.dll；Ollama 装机即有全套，ctranslate2 4.5+ 不需要 cuDNN）。"""
        import os
        root = Path(__file__).resolve().parent.parent.parent
        candidates = [
            root / ".venv" / "Scripts",   # fix_gpu_dlls.py 拷贝的 DLL
            root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
            root / ".venv" / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
            # Ollama 官方安装自带的 CUDA 12 运行库（cublas/cublasLt/cudart 齐全）
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "lib" / "ollama" / "cuda_v12",
        ]
        for d in candidates:
            try:
                if d.exists():
                    os.add_dll_directory(str(d))
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass

    def _try_load_model(self, device="cuda", compute_type="float16"):
        """尝试加载 Whisper 模型，返回 (model, error_or_none)"""
        try:
            from faster_whisper import WhisperModel
            self._register_gpu_dll_dirs()
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

    def _rescue_reencode(self, audio_path):
        """★ #973：截断音频抢救——手机 MediaRecorder 丢尾包的 webm 严格解码必报 AVERROR_EOF，
        用 ffmpeg 宽容转码成 16k 单声道 wav（能解出前面完整的簇），救回大半截录音。成功返回 wav 路径。"""
        import subprocess, tempfile
        out = Path(tempfile.mktemp(suffix='.wav'))
        try:
            r = subprocess.run(
                ['ffmpeg', '-y', '-err_detect', 'ignore_err', '-i', str(audio_path),
                 '-ar', '16000', '-ac', '1', str(out)],
                capture_output=True, timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
                return str(out)
        except Exception as e:
            logger.debug(f"[VoiceInput] ffmpeg 抢救失败: {e}")
        try:
            out.unlink(missing_ok=True)
        except Exception:
            pass
        return None

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
        # ★ 2026-09-27 防线①：能量预检——静音/底噪输入直接短路返回（杜绝 whisper 幻觉，
        # 实锤：开发者 09-27 凌晨按住说话，麦克风没录进声，whisper 幻觉出「字幕志愿者 杨茜茜」×6）
        rms = self._audio_energy(audio_path)
        if 0 <= rms < 0.0015:
            logger.info(f"[VoiceInput] 音频近静音(rms={rms:.5f})，不送识别（防幻觉）")
            return ''

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
                return self._do_transcribe(audio_path, language)
            except Exception as e:
                err_str = str(e).lower()
                logger.error(f"[VoiceInput] Transcribe failed: {e}")
                # CUDA 运行时库缺失 → 重载为 CPU 再试一次
                if "cublas" in err_str or "cuda" in err_str or "cudnn" in err_str:
                    logger.info("[VoiceInput] CUDA runtime missing, reloading model on CPU...")
                    self._whisper_model, err2 = self._try_load_model(device="cpu", compute_type="int8")
                    if self._whisper_model:
                        try:
                            text = self._do_transcribe(audio_path, language)
                            logger.info(f"[VoiceInput] CPU transcribe OK: {text[:80]}...")
                            return text
                        except Exception as e3:
                            logger.error(f"[VoiceInput] CPU retry also failed: {e3}")
                # ★ #973 截断音频抢救：手机 webm 丢尾包 → AVERROR_EOF，ffmpeg 宽容转码后再试
                if ('end of file' in err_str or 'eof' in err_str
                        or '541478725' in err_str or 'invalid data' in err_str):
                    rescued = self._rescue_reencode(audio_path)
                    if rescued:
                        try:
                            text = self._do_transcribe(rescued, language)
                            if text:
                                logger.info(f"[VoiceInput] ffmpeg 抢救转码后识别成功: {text[:60]}")
                                return text
                            logger.info("[VoiceInput] 抢救转码后识别为空（截断段无有效语音）")
                        except Exception as e2:
                            logger.error(f"[VoiceInput] 抢救转码后仍失败: {e2}")
                        finally:
                            try:
                                Path(rescued).unlink(missing_ok=True)
                            except Exception:
                                pass

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

    # ═══ ★ 2026-09-27 幻觉三层防线（实锤：静音输入幻觉出「字幕志愿者 杨茜茜」×6） ═══

    # faster-whisper 对无语音/底噪段会编造训练语料里的字幕组署名与引导语，
    # 这些短语在正常语音里出现即视为识别已废（中文场景误杀率≈0）
    _HALLUCINATION_PHRASES = (
        '字幕志愿者', '字幕制作', '字幕由', '字幕提供', '字幕组', '谢谢观看', '谢谢收看',
        '请不吝点赞', '点赞订阅', '订阅转发', '请订阅', '订阅频道', '留言评论',
        'amara.org', 'subtitles by', '请不吝',
    )

    def _audio_energy(self, audio_path):
        """返回音频 RMS 能量；测不了返回 -1（放行走原流程）。正常说话 0.01~0.1+，静音/底噪 <0.002。"""
        try:
            import numpy as _np
            from faster_whisper.audio import decode_audio
            arr = decode_audio(str(audio_path), sampling_rate=16000)
            if arr is None or not getattr(arr, 'size', 0):
                return 0.0
            return float(_np.sqrt(_np.mean(arr.astype(_np.float64) ** 2)))
        except Exception as e:
            logger.debug(f"[VoiceInput] 能量预检跳过: {e}")
            return -1.0

    def _do_transcribe(self, audio_path, language):
        """② VAD 过滤静音段 + 不沿用上文（防幻觉滚雪球重复）；③ 结果过幻觉黑名单。"""
        try:
            segments, _ = self._whisper_model.transcribe(
                str(audio_path), language=language, beam_size=3,
                vad_filter=True, condition_on_previous_text=False)
        except Exception as vad_e:
            logger.debug(f"[VoiceInput] VAD 参数不可用，退回裸调用: {vad_e}")
            segments, _ = self._whisper_model.transcribe(str(audio_path), language=language)
        text = "".join(seg.text for seg in segments).strip()
        if text:
            low = text.lower()
            hit = next((p for p in self._HALLUCINATION_PHRASES if p in low), None)
            if hit:
                logger.info(f"[VoiceInput] 命中幻觉黑名单({hit})，丢弃: {text[:60]!r}")
                return ''
        return text


# 全局单例
_voice_input = None

def get_voice_input():
    global _voice_input
    if _voice_input is None:
        _voice_input = VoiceInput()
    return _voice_input
