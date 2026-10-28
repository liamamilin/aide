"""Local app/file candidates shown above the chat input, never sent to a model."""
import logging
import threading

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout

from ai_desktop.desktop import resolver
from ai_desktop.desktop.opener import open_action
from ai_desktop.ui.fluent import CaptionLabel, SimpleCardWidget

logger = logging.getLogger(__name__)

_KIND_LABEL = {"app": "应用", "project": "项目", "folder": "文件夹", "file": "文件"}
ROW_HEIGHT = 28
MAX_VISIBLE_ROWS = 6


class CandidateLookup(QObject):
    """Debounced lookup on a worker thread; stale answers are dropped by sequence number."""

    resolved = pyqtSignal(int, str, object)
    _finished = pyqtSignal(int, str, object)

    def __init__(self, resolve_fn=None, *, debounce_ms: int = 150, run_async: bool = True, parent=None):
        super().__init__(parent)
        self._resolve = resolve_fn or resolver.resolve
        self._run_async = run_async
        self._seq = 0
        self._pending = ""
        self._closed = False
        self._inflight = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self._start)
        self._finished.connect(self._on_finished)

    def request(self, text: str) -> None:
        """Ask for candidates; a sentence or empty text clears them immediately."""
        self._seq += 1
        self._pending = (text or "").strip()
        if self._closed or not resolver.is_short_query(self._pending):
            self._timer.stop()
            self.resolved.emit(self._seq, self._pending, [])
            return
        self._timer.start()

    def close(self) -> None:
        self._closed = True
        self._seq += 1
        self._timer.stop()

    def _start(self) -> None:
        seq, text = self._seq, self._pending
        if self._closed:
            return
        if not self._run_async:
            self._on_finished(seq, text, self._lookup(text))
            return
        if self._inflight:
            return  # the running lookup restarts for the latest text when it finishes
        self._inflight = True
        threading.Thread(target=self._work, args=(seq, text), daemon=True).start()

    def _lookup(self, text):
        try:
            return list(self._resolve(text))
        except Exception:
            logger.warning("candidate lookup failed", exc_info=True)
            return []

    def _work(self, seq: int, text: str) -> None:
        result = self._lookup(text)
        try:
            self._finished.emit(seq, text, result)
        except RuntimeError:
            pass  # the dialog was destroyed while the lookup was running

    def _on_finished(self, seq: int, text: str, candidates) -> None:
        self._inflight = False
        if self._closed:
            return
        if seq == self._seq:
            self.resolved.emit(seq, text, candidates)
        elif resolver.is_short_query(self._pending):
            self._start()  # typing moved on while this lookup ran


class CandidatePanel(SimpleCardWidget):
    """A short list the user must pick from explicitly before Enter opens anything."""

    activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._candidates = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(4)
        self._list = QListWidget()
        self._list.setFrameShape(QListWidget.NoFrame)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setAccessibleName("本地候选")
        self._list.itemClicked.connect(self._on_clicked)
        layout.addWidget(self._list)
        self._hint = CaptionLabel("↑↓ 选择 · Enter 打开 · Esc 关闭；不选择时 Enter 照常发送")
        layout.addWidget(self._hint)
        self.hide()

    def set_candidates(self, candidates) -> None:
        self._candidates = list(candidates)
        self._list.clear()
        self._hint.setText("↑↓ 选择 · Enter 打开 · Esc 关闭；不选择时 Enter 照常发送")
        for candidate in self._candidates:
            note = "（在 Finder 中显示）" if open_action(candidate.path) == "reveal" else ""
            item = QListWidgetItem(f"{_KIND_LABEL.get(candidate.kind, '')}  {candidate.name}{note}")
            item.setToolTip(candidate.path)
            self._list.addItem(item)
        self._list.setCurrentRow(-1)
        rows = min(len(self._candidates), MAX_VISIBLE_ROWS)
        self._list.setFixedHeight(rows * ROW_HEIGHT + 4)
        self.setVisible(bool(self._candidates))

    def has_selection(self) -> bool:
        return self.isVisible() and 0 <= self._list.currentRow() < len(self._candidates)

    def move_selection(self, delta: int) -> None:
        """Down from no selection picks the first row; Up from the first row clears the selection."""
        row = self._list.currentRow() + delta
        self._list.setCurrentRow(row if 0 <= row < len(self._candidates) else (-1 if row < 0 else row - delta))

    def activate_current(self) -> bool:
        if not self.has_selection():
            return False
        self.activated.emit(self._candidates[self._list.currentRow()].path)
        return True

    def show_error(self, text: str) -> None:
        self._hint.setText(text)

    def _on_clicked(self, item) -> None:
        row = self._list.row(item)
        if 0 <= row < len(self._candidates):
            self.activated.emit(self._candidates[row].path)
