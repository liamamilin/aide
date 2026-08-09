"""Lazy-loaded MLX text-to-speech worker."""
import gc
import logging
import queue
import threading
import time
from dataclasses import dataclass

from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    voice: str
    language: str


class SpeechWorker(QThread):
    """Keep one MLX model on a background thread and play requests in order."""

    status_changed = pyqtSignal(str)
    speech_finished = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(self, model_id: str, idle_timeout_seconds: float = 60, parent=None):
        super().__init__(parent)
        self._model_id = model_id
        self._idle_timeout_seconds = idle_timeout_seconds
        self._requests: queue.Queue[SpeechRequest | None] = queue.Queue()
        self._cancelled = threading.Event()
        self._stopping = threading.Event()
        self._stream_lock = threading.Lock()
        self._stream = None
        self._model = None

    def speak(self, text: str, voice: str, language: str) -> None:
        text = text.strip().replace("\u2029", "\n")
        if not text or self._stopping.is_set():
            return
        self.stop_speaking()
        self._discard_pending_requests()
        self._requests.put(SpeechRequest(text, voice, language))
        if not self.isRunning():
            self.start()

    def stop_speaking(self) -> None:
        """Cancel generation and abort playback; safe to call from the UI thread."""
        self._cancelled.set()
        with self._stream_lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                logger.debug("Failed to abort audio stream", exc_info=True)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self._stopping.set()
        self.stop_speaking()
        self._discard_pending_requests()
        self._requests.put(None)
        return not self.isRunning() or self.wait(timeout_ms)

    def _discard_pending_requests(self) -> None:
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                return

    def run(self) -> None:
        try:
            while not self._stopping.is_set():
                timeout = self._idle_timeout_seconds if self._model is not None else None
                try:
                    request = self._requests.get(timeout=timeout)
                except queue.Empty:
                    self._unload_model()
                    continue
                if request is None:
                    break
                self._cancelled.clear()
                ok = self._process(request)
                self.speech_finished.emit(ok)
        finally:
            self._unload_model()

    def _unload_model(self) -> None:
        if self._model is None:
            return
        self._model = None
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:
            logger.debug("Failed to clear MLX cache", exc_info=True)
        logger.info("Speech model unloaded after idle timeout")

    def _process(self, request: SpeechRequest) -> bool:
        stream = None
        try:
            if self._model is None:
                self.status_changed.emit("首次使用，正在下载并加载语音模型…")
                from mlx_audio.tts.utils import load_model

                self._model = load_model(self._model_id)
            if self._cancelled.is_set():
                return False

            import numpy as np
            import sounddevice as sd

            self.status_changed.emit("正在生成语音…")
            generation_started = time.monotonic()
            results = self._model.generate(
                text=request.text,
                voice=request.voice,
                lang_code=request.language,
                stream=False,
            )
            audio_chunks = []
            for result in results:
                if self._cancelled.is_set():
                    return False
                audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                if audio.size:
                    audio_chunks.append(audio)
            if not audio_chunks or self._cancelled.is_set():
                return False

            audio = np.concatenate(audio_chunks).reshape(-1, 1)
            generation_seconds = time.monotonic() - generation_started
            audio_seconds = audio.shape[0] / self._model.sample_rate
            logger.info(
                "Speech generated: text_len=%d generation=%.2fs audio=%.2fs rtf=%.2f",
                len(request.text),
                generation_seconds,
                audio_seconds,
                generation_seconds / audio_seconds if audio_seconds else 0,
            )

            stream = sd.OutputStream(
                samplerate=self._model.sample_rate,
                channels=1,
                dtype="float32",
            )
            with self._stream_lock:
                self._stream = stream
            stream.start()
            self.status_changed.emit("正在朗读…")
            stream.write(audio)
            return not self._cancelled.is_set()
        except Exception as exc:
            if self._cancelled.is_set():
                return False
            logger.exception("Text-to-speech failed")
            self.error.emit(self._friendly_error(exc))
            return False
        finally:
            with self._stream_lock:
                self._stream = None
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        message = str(exc).strip()
        if isinstance(exc, ImportError):
            return "语音组件未安装，请重新安装或更新应用。"
        if "No space left" in message:
            return "磁盘空间不足，无法下载语音模型。"
        if "401" in message or "403" in message:
            return "无法访问语音模型，请检查 Hugging Face 网络连接。"
        return f"朗读失败：{message or exc.__class__.__name__}"
