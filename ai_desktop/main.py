"""
AI 桌面助手 —— 主入口

单模式：悬浮按钮 + 全局快捷键 → Agent 多轮对话
  选中文字 → ⌘⇧J → 自动打开对话窗口并粘贴选中文字
"""
import logging
import signal
import sys
from typing import Optional

import requests
from PyQt5.QtCore import QObject, QTimer, QThread, pyqtSignal
from PyQt5.QtWidgets import QApplication, QMessageBox

from ai_desktop import config
from ai_desktop.capture.hotkey_listener import HotkeyListener
from ai_desktop.capture.clipboard_monitor import read_selection
from ai_desktop.config import Agent, AGENTS, DEFAULT_AGENT_INDEX
from ai_desktop.llm.chat_client import ChatClient, list_models
from ai_desktop.ui.float_button import FloatButton, pin_to_all_spaces
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.utils import logging as log_util
from ai_desktop.utils.storage import (
    init_db,
    create_conversation,
    save_message,
    save_setting,
    get_setting,
    Message,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# LLM Worker
# ═══════════════════════════════════════════════════════

class StreamingChatWorker(QThread):
    """流式聊天 Worker：分别发射 thinking / response chunk，完成后发射 done"""
    thinking_chunk = pyqtSignal(str)  # 思考过程 token
    chunk = pyqtSignal(str)           # 回复 token
    done = pyqtSignal(str, bool)      # 完整回复文本（成功）或错误信息（失败）

    def __init__(self, messages: list[Message], system_prompt: str, model: str = "", parent: QObject | None = None):
        super().__init__(parent)
        self.messages = messages
        self.system_prompt = system_prompt
        self._model = model

    def run(self) -> None:
        client = ChatClient(model=self._model)
        stream = client.chat_stream(self.messages, self.system_prompt)
        full_response = ""
        for kind, token in stream:
            if self.isInterruptionRequested():
                break
            if kind == "thinking":
                self.thinking_chunk.emit(token)
            elif kind == "response":
                full_response += token
                self.chunk.emit(token)
            elif kind == "error":
                self.chunk.emit(token)
                full_response = token
                break
        is_error = full_response.startswith("HTTP ") or full_response.startswith("无法") or full_response.startswith("响应超时")
        self.done.emit(full_response, not is_error)


# ═══════════════════════════════════════════════════════
# ChatController —— 悬浮按钮 + 快捷键 → 多轮对话
# ═══════════════════════════════════════════════════════

class ChatController(QObject):
    """快捷键触发 → 读选中文字 → 打开对话窗口粘贴 → 用户按 Enter 发送"""

    # pynput 回调在后台线程，通过信号桥接到主线程
    _hotkey_triggered = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # 读取上次使用的 Agent 和模型
        saved_agent_id = get_setting("last_agent_id")
        saved_model = get_setting("last_model")

        self._active_agent: Agent = AGENTS[DEFAULT_AGENT_INDEX]
        if saved_agent_id:
            for ag in AGENTS:
                if ag.id == saved_agent_id:
                    self._active_agent = ag
                    break

        self._model: str = saved_model or config.OLLAMA_MODEL
        self._auto_hide: bool = get_setting("auto_hide") == "true"  # 默认不收起
        self._convo_id: int = 0
        self._messages: list[Message] = []
        self._worker: Optional[StreamingChatWorker] = None
        self._dialog: Optional[ChatDialog] = None

        # 悬浮按钮
        self.float_btn = FloatButton()
        self.float_btn.clicked.connect(self._toggle_dialog)
        self.float_btn.exit_requested.connect(self._on_exit)
        self.float_btn.hide_requested.connect(self.float_btn.hide)
        self.float_btn.about_requested.connect(self._show_about)
        self.float_btn.auto_hide_toggled.connect(self._on_auto_hide_toggled)
        self.float_btn.set_auto_hide_state(self._auto_hide)

        # 全局快捷键（pynput 后台线程 → 信号桥接到主线程）
        self.hotkey = HotkeyListener()
        self.hotkey.register(config.HOTKEY, self._on_global_hotkey)
        self._hotkey_triggered.connect(self._on_hotkey_triggered)

    def start(self) -> None:
        init_db()
        self.hotkey.start()
        self.float_btn.show()
        pin_to_all_spaces(self.float_btn)
        logger.info("ChatController 已就绪（快捷键 %s）", config.HOTKEY)

    def stop(self) -> None:
        self.hotkey.stop()
        self.float_btn.hide()
        if self._dialog:
            self._dialog.hide()
            self._dialog.deleteLater()
            self._dialog = None
        logger.info("ChatController 已退出")

    # ── 快捷键 ─────────────────────────────────────────

    def _on_global_hotkey(self) -> None:
        """pynput 后台线程回调：只发射信号，不做任何 I/O（避免 Controller 与 Listener 冲突）"""
        self._hotkey_triggered.emit("")

    def _on_hotkey_triggered(self, _text: str) -> None:
        """主线程：延迟读取选中文字 → 打开对话窗口

        延迟 200ms 确保 pynput Listener 的 event tap 完全空闲后再调用
        read_selection()，避免 Controller 模拟的 ⌘C 事件与热键事件冲突。
        """
        # 延迟到 pynput 事件处理完毕后再读取选中文字
        QTimer.singleShot(200, self._do_capture_and_show)

    def _do_capture_and_show(self) -> None:
        """在 Qt 主线程执行：读取选中文字并显示对话窗口"""
        text = read_selection() or ""
        logger.info("Hotkey triggered, text length=%d", len(text))
        if not self._dialog or not self._dialog.isVisible():
            self._show_dialog()
        if text and self._dialog:
            self._dialog.show()
            self._dialog.activateWindow()
            self._dialog.raise_()
            self._dialog.set_input_text(text)
        elif not text:
            logger.info("No text captured — dialog shown without paste")

    # ── 对话框开关 ─────────────────────────────────────

    def _toggle_dialog(self) -> None:
        if self._dialog and self._dialog.isVisible():
            self._dialog.hide()
            return
        self._show_dialog()

    def _show_dialog(self) -> None:
        if self._dialog is None:
            models = list_models()
            self._dialog = ChatDialog(AGENTS, self._active_agent, models, self._model,
                                     auto_hide=self._auto_hide)
            self._dialog.message_sent.connect(self._on_user_message)
            self._dialog.new_convo_requested.connect(self._new_conversation)
            self._dialog.agent_changed.connect(self._on_agent_changed)
            self._dialog.model_changed.connect(self._on_model_changed)
        # 如果悬浮球被隐藏了，重新显示
        if self.float_btn.isHidden():
            self.float_btn.show()
            pin_to_all_spaces(self.float_btn)
        self._dialog.show_near(
            self.float_btn.mapToGlobal(self.float_btn.rect().topLeft())
        )

    def _on_auto_hide_toggled(self, checked: bool) -> None:
        self._auto_hide = checked
        save_setting("auto_hide", "true" if checked else "false")
        self.float_btn.set_auto_hide_state(checked)
        if self._dialog:
            self._dialog.set_auto_hide(checked)
        logger.info("Auto-hide %s", "enabled" if checked else "disabled")

    # ── 退出 / 关于 ───────────────────────────────────

    def _on_exit(self) -> None:
        self.stop()
        QApplication.instance().quit()

    def _show_about(self) -> None:
        QMessageBox.about(
            None,
            "关于 AI 桌面助手",
            "<b>AI 桌面助手</b> v1.0<br><br>"
            "macOS 常驻 AI 助手<br>"
            "选中文字 → ⌘⌃L → 一键提问<br><br>"
            "基于 Ollama 本地 LLM，数据不上传。",
        )

    def _on_auto_hide_toggled(self, checked: bool) -> None:
        self._auto_hide = checked
        save_setting("auto_hide", "true" if checked else "false")
        self.float_btn.set_auto_hide_state(checked)
        if self._dialog:
            self._dialog.set_auto_hide(checked)
        logger.info("Auto-hide %s", "enabled" if checked else "disabled")

    # ── Agent 切换 ─────────────────────────────────────

    def _on_agent_changed(self, agent: Agent) -> None:
        self._active_agent = agent
        save_setting("last_agent_id", agent.id)
        logger.info("Agent switched: %s", agent.name)

    def _on_model_changed(self, model: str) -> None:
        self._model = model
        save_setting("last_model", model)
        logger.info("Model switched: %s", model)

    # ── 新建对话 ───────────────────────────────────────

    def _new_conversation(self) -> None:
        self._convo_id = 0
        self._messages = []
        if self._dialog:
            self._dialog.clear_messages()
        logger.info("New conversation started (agent=%s)", self._active_agent.name)

    # ── 发送消息 ───────────────────────────────────────

    def _on_user_message(self, text: str) -> None:
        if self._worker and self._worker.isRunning():
            return

        if self._convo_id == 0:
            conv = create_conversation(self._active_agent.id)
            self._convo_id = conv.id

        user_msg = save_message(self._convo_id, "user", text)
        self._messages.append(user_msg)

        if self._dialog:
            self._dialog.add_user_message(text)
            self._dialog.begin_assistant_stream()
            self._dialog.set_thinking(True)

        self._worker = StreamingChatWorker(list(self._messages), self._active_agent.system_prompt, self._model, self)
        self._worker.thinking_chunk.connect(self._on_thinking_chunk)
        self._worker.chunk.connect(self._on_stream_chunk)
        self._worker.done.connect(self._on_stream_done)
        self._worker.start()

    def _on_thinking_chunk(self, token: str) -> None:
        if self._dialog:
            self._dialog.append_thinking_chunk(token)

    def _on_stream_chunk(self, token: str) -> None:
        if self._dialog:
            self._dialog.append_stream_chunk(token)

    def _on_stream_done(self, text: str, ok: bool) -> None:
        if self._dialog:
            self._dialog.set_thinking(False)

        if ok and self._convo_id > 0 and text:
            assistant_msg = save_message(self._convo_id, "assistant", text)
            self._messages.append(assistant_msg)

        if self._dialog:
            self._dialog.finalize_assistant_stream(text, ok)

        self._worker = None


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════

def main() -> None:
    log_util.setup()

    app = QApplication(sys.argv)
    app.setApplicationName("AI 桌面助手")
    app.setQuitOnLastWindowClosed(False)

    _quit_flag = False

    def _on_sigint(*_) -> None:
        nonlocal _quit_flag
        _quit_flag = True
        print("\n正在退出...")

    signal.signal(signal.SIGINT, _on_sigint)

    controller = ChatController()

    def _poll_quit() -> None:
        if _quit_flag:
            poll_timer.stop()
            controller.stop()
            app.quit()

    poll_timer = QTimer()
    poll_timer.timeout.connect(_poll_quit)
    poll_timer.start(200)

    controller.start()

    def _check_ollama() -> None:
        try:
            r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
            if r.status_code == 200:
                logger.info("Ollama 连接正常 (model=%s)", config.OLLAMA_MODEL)
            else:
                logger.warning("⚠️ 无法连接 Ollama，请确认服务已启动")
        except Exception:
            logger.warning("⚠️ 无法连接 Ollama，请确认服务已启动")

    QTimer.singleShot(1000, _check_ollama)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
