"""Optional English text-to-speech service backed by Kokoro.

The GUI only depends on this small asynchronous wrapper.  Kokoro and its
runtime are imported lazily so the base app can still start when the optional
speech package has not been installed yet.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)


class SpeechUnavailableError(RuntimeError):
    """Raised when optional speech dependencies or the audio player are absent."""


class SpeechWorker(QThread):
    """Generate one utterance and play it without blocking Qt's GUI thread."""

    completed = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    def __init__(self, service: "SpeechService", text: str, parent: QObject | None = None):
        super().__init__(parent)
        # Kokoro's Torch/NumPy path needs more native stack than Qt's small
        # default QThread stack on macOS.  Without this, BLAS can hit the
        # guard page during the first model load and terminate the app.
        self.setStackSize(16 * 1024 * 1024)
        self.service = service
        self.text = text
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        self.requestInterruption()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set() or self.isInterruptionRequested()

    def run(self) -> None:
        try:
            self.service._synthesize_and_play(self.text, self)
        except Exception as exc:
            if self.cancelled:
                self.completed.emit(False, "朗读已停止。")
            else:
                logger.warning("Speech request failed: %s", exc, exc_info=True)
                self.completed.emit(False, str(exc) or "朗读失败。")
        else:
            if self.cancelled:
                self.completed.emit(False, "朗读已停止。")
            else:
                self.completed.emit(True, "")


class SpeechService(QObject):
    """Manage one cancellable Kokoro utterance at a time."""

    completed = pyqtSignal(bool, str)
    progress = pyqtSignal(str)

    _pipeline = None
    _pipeline_lock = threading.Lock()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: SpeechWorker | None = None
        self._retired: list[SpeechWorker] = []

    @property
    def active(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def speak(self, text: str) -> bool:
        """Start speaking text; return False when the selection is empty."""
        text = " ".join(text.split()).strip()
        if not text:
            self.completed.emit(False, "没有读取到选中文字，请重新选择后再试。")
            return False
        # Keep accidental whole-page selections from creating an enormous job.
        if len(text) > 2000:
            text = text[:2000].rstrip() + "…"
        self.stop()
        worker = SpeechWorker(self, text, self)
        self._worker = worker
        worker.completed.connect(self.completed.emit)
        worker.progress.connect(self.progress.emit)
        worker.finished.connect(self._on_finished)
        worker.start()
        return True

    def stop(self) -> SpeechWorker | None:
        worker = self._worker
        if worker is None:
            return None
        self._worker = None
        worker.cancel()
        if worker not in self._retired:
            self._retired.append(worker)
        return worker

    def shutdown(self) -> list[SpeechWorker]:
        worker = self.stop()
        if worker is not None:
            return [worker]
        return []

    def _on_finished(self) -> None:
        worker = self.sender()
        if worker in self._retired:
            self._retired.remove(worker)
        elif worker is self._worker:
            self._worker = None
        worker.deleteLater()

    @classmethod
    def _get_pipeline(cls, progress):
        if cls._pipeline is not None:
            return cls._pipeline
        with cls._pipeline_lock:
            if cls._pipeline is not None:
                return cls._pipeline
            progress("正在加载英语朗读模型，首次使用需要一点时间…")
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise SpeechUnavailableError(
                    "未安装朗读依赖，请执行：python3 -m pip install -r requirements-tts.txt"
                ) from exc
            try:
                import torch

                # Keep first-use synthesis predictable on laptops and avoid
                # creating a large BLAS thread fan-out inside the GUI app.
                torch.set_num_threads(min(4, os.cpu_count() or 1))
                torch.set_num_interop_threads(1)
            except Exception:
                logger.debug("Unable to tune Torch thread counts", exc_info=True)
            # `a` is Kokoro's American English pipeline.  Keep this explicit:
            # the first release is intentionally English-first.
            cls._pipeline = KPipeline(lang_code="a")
        return cls._pipeline

    def _synthesize_and_play(self, text: str, worker: SpeechWorker) -> None:
        if shutil.which("afplay") is None:
            raise SpeechUnavailableError("当前 macOS 环境找不到 afplay，无法播放朗读音频。")
        import numpy as np
        import soundfile as sf

        pipeline = self._get_pipeline(self.progress.emit)
        worker.progress.emit("正在生成语音…")
        chunks = []
        # af_heart is a stable, natural English voice in the Kokoro model.
        for _, _, audio in pipeline(
            text,
            voice="af_heart",
            speed=0.95,
            split_pattern=r"\n+",
        ):
            if worker.cancelled:
                return
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            chunks.append(np.asarray(audio))
        if worker.cancelled:
            return
        if not chunks:
            raise SpeechUnavailableError("朗读模型没有生成可播放的音频。")

        fd, raw_path = tempfile.mkstemp(prefix="aide-speech-", suffix=".wav")
        os.close(fd)
        Path(raw_path).unlink(missing_ok=True)
        path = Path(raw_path)
        try:
            sf.write(path, np.concatenate(chunks), 24000)
            process = subprocess.Popen(
                ["afplay", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            while process.poll() is None:
                if worker.cancelled:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return
                time.sleep(0.05)
            if process.returncode:
                detail = (process.stderr.read() if process.stderr else "").strip()
                raise RuntimeError(detail or "音频播放失败。")
        finally:
            path.unlink(missing_ok=True)
