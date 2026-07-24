"""
AI 桌面助手 —— 主入口

单模式：悬浮按钮 + 全局快捷键 → Agent 多轮对话
  选中文字 → ⌘⌃L → 自动打开对话窗口并粘贴选中文字
"""
import functools
import json
import logging
import signal
import sys
from typing import Optional

import requests
from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from ai_desktop import config
from ai_desktop.agent_manager import AgentManager
from ai_desktop.capture.clipboard_monitor import read_selection
from ai_desktop.config import Agent
from ai_desktop.llm.chat_client import ChatClient, list_models
from ai_desktop.settings_manager import SettingsManager
from ai_desktop.ui.agent_editor import AgentDef, AgentEditor
from ai_desktop.ui.chat_dialog import ChatDialog
from ai_desktop.ui.float_button import FloatButton, pin_to_all_spaces
from ai_desktop.ui.history_dialog import HistoryDialog
from ai_desktop.ui.menubar_icon import MenuBarIcon
from ai_desktop.ui.settings_dialog import SettingsDialog
from ai_desktop.utils import logging as log_util
from ai_desktop.utils.storage import (
    Message,
    create_conversation,
    get_conversation,
    get_setting,
    init_db,
    list_conversations,
    save_message,
    save_setting,
)

logger = logging.getLogger(__name__)


def _safe_slot(fn):
    """Decorator: catch all exceptions in Qt slots to prevent qFatal abort"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("Unhandled exception in %s", fn.__name__)
    return wrapper


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
        is_error = (full_response.startswith("HTTP ")
                     or full_response.startswith("无法")
                     or full_response.startswith("响应超时"))
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
        init_db()  # 确保表结构存在，必须在任何 DB 查询之前

        # 加载持久化配置
        self._settings = SettingsManager()
        self._settings.load()

        # 加载 Agent 列表
        self._agent_mgr = AgentManager()
        self._all_agents = self._agent_mgr.all_agents
        self._active_agent = self._agent_mgr.active_agent
        self._custom_agents = self._agent_mgr.custom_agents

        # 读取上次使用的模型
        saved_model = get_setting("last_model")

        self._model: str = saved_model or config.OLLAMA_MODEL
        self._auto_hide: bool = get_setting("auto_hide") == "true"  # 默认不收起

        self._convo_id: int = 0
        self._messages: list[Message] = []
        self._restore_last: bool = True  # 首次打开自动恢复上次对话
        self._worker: Optional[StreamingChatWorker] = None
        self._stale_workers: list[StreamingChatWorker] = []
        self._dialog: Optional[ChatDialog] = None

        # 悬浮按钮
        self.float_btn = FloatButton()
        self.float_btn.clicked.connect(self._toggle_dialog)
        self.float_btn.exit_requested.connect(self._on_exit)
        self.float_btn.hide_requested.connect(self.float_btn.hide)
        self.float_btn.about_requested.connect(self._show_about)
        self.float_btn.settings_requested.connect(self._on_settings_requested)
        self.float_btn.auto_hide_toggled.connect(self._on_auto_hide_toggled)
        self.float_btn.set_auto_hide_state(self._auto_hide)

        # 菜单栏图标
        self._tray = MenuBarIcon(self._all_agents, self._active_agent)
        self._tray.dialog_toggle.connect(self._toggle_dialog)
        self._tray.agent_selected.connect(self._on_tray_agent)
        self._tray.settings_clicked.connect(self._on_settings_requested)
        self._tray.about_clicked.connect(self._show_about)
        self._tray.exit_clicked.connect(self._on_exit)

        # 全局快捷键
        if getattr(sys, "frozen", False):
            # 冻结模式（.app）：用 NSEvent 全局监听（主线程，无 dispatch 断言）
            from ai_desktop.capture.nsevent_monitor import NSEventMonitor
            self.hotkey = NSEventMonitor()
            self.hotkey.register(config.HOTKEY, self._on_global_hotkey)
            logger.info("Using NSEventMonitor hotkey backend")
        else:
            # 开发模式（aide）：用 pynput（终端已有 AX 权限）
            from ai_desktop.capture.hotkey_listener import HotkeyListener
            self.hotkey = HotkeyListener()
            self.hotkey.register(config.HOTKEY, self._on_global_hotkey)
            logger.info("Using pynput hotkey backend")
        self._hotkey_triggered.connect(self._on_hotkey_triggered)

    def start(self) -> None:
        init_db()
        try:
            self.hotkey.start()
        except Exception as e:
            logger.warning("Failed to start hotkey listener: %s", e)
        self.float_btn.show()
        pin_to_all_spaces(self.float_btn)
        self._tray.show()
        logger.info("ChatController 已就绪（快捷键 %s）", config.HOTKEY)

    def stop(self) -> None:
        self._tray.hide()
        self.hotkey.stop()
        self.float_btn.hide()
        if self._dialog:
            self._dialog.hide()
            self._dialog.deleteLater()
            self._dialog = None
        logger.info("ChatController 已退出")

    # ── 快捷键 ─────────────────────────────────────────

    def _on_global_hotkey(self) -> None:
        """热键回调：发射信号到主线程（pynput 后台线程 / NSEvent 主线程均适用）"""
        self._hotkey_triggered.emit("")

    def _on_hotkey_triggered(self, _text: str) -> None:
        """主线程：延迟读取选中文字 → 打开对话窗口

        延迟 100ms 等待热键修饰键释放后再调用 read_selection()，
        避免 Controller 模拟的 ⌘C 事件与仍按住的热键修饰键冲突。
        """
        QTimer.singleShot(100, self._do_capture_and_show)

    def _do_capture_and_show(self) -> None:
        """在 Qt 主线程执行：读取选中文字并显示对话窗口"""
        try:
            text = read_selection() or ""
        except Exception:
            logger.exception("Failed to read selection")
            text = ""
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
            self._dialog = ChatDialog(self._all_agents, self._active_agent, [], self._model,
                                     auto_hide=self._auto_hide)
            self._dialog.message_sent.connect(self._on_user_message)
            self._dialog.new_convo_requested.connect(self._new_conversation)
            self._dialog.history_requested.connect(self._on_history_requested)
            self._dialog.export_requested.connect(self._on_export_requested)
            self._dialog.manage_agents_requested.connect(self._on_manage_agents)
            self._dialog.stop_requested.connect(self._on_stop_requested)
            self._dialog.agent_changed.connect(self._on_agent_changed)
            self._dialog.model_changed.connect(self._on_model_changed)
            self._dialog.ollama_online.connect(self._refresh_model_list)
            # 首次打开自动恢复上次对话
            if self._restore_last:
                self._restore_last = False
                convs = list_conversations(limit=1)
                if convs:
                    prev_agent = self._active_agent
                    self._on_conversation_selected(convs[0].id)
                    # 不覆盖用户手动选择的 Agent
                    if self._active_agent != prev_agent:
                        self._active_agent = self._agent_mgr.switch(prev_agent)
                        self._dialog.set_active_agent(self._active_agent)
                        self._tray.set_active_agent(self._active_agent)
            # 首次构造后立刻刷新模型列表（dialog combo 当前为占位"加载中…"）
            self._refresh_model_list()
        else:
            # 复用现有 dialog，每次显示刷新模型列表
            self._refresh_model_list()
        # 如果悬浮球被隐藏了，重新显示
        if self.float_btn.isHidden():
            self.float_btn.show()
            pin_to_all_spaces(self.float_btn)
        self._dialog.show_near(
            self.float_btn.mapToGlobal(self.float_btn.rect().topLeft())
        )

    @_safe_slot
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

    @_safe_slot
    def _on_settings_requested(self) -> None:
        """打开设置面板"""
        current = {
            "base_url": config.OLLAMA_BASE_URL,
            "think": config.OLLAMA_THINK,
            "timeout": config.OLLAMA_TIMEOUT,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": config.OLLAMA_NUM_PREDICT,
            "temperature": config.OLLAMA_TEMPERATURE,
            "top_p": config.OLLAMA_TOP_P,
            "top_k": config.OLLAMA_TOP_K,
            "repeat_penalty": config.OLLAMA_REPEAT_PENALTY,
            "max_rounds": config.OLLAMA_MAX_ROUNDS,
            "hotkey": config.HOTKEY,
        }
        dlg = SettingsDialog(current, parent=self._dialog)
        dlg.settings_applied.connect(self._on_settings_applied)
        dlg.exec_()

    @_safe_slot
    def _on_settings_applied(self, data: dict) -> None:
        """应用设置变更"""
        changed = self._settings.apply(data)
        if "hotkey" in changed:
            try:
                self.hotkey.reregister(config.HOTKEY, self._on_global_hotkey)
                logger.info("Hotkey changed to %s", config.HOTKEY)
            except Exception as e:
                logger.warning("Failed to change hotkey: %s", e)
        if changed:
            logger.info("Settings applied")

    def _show_about(self) -> None:
        QMessageBox.about(
            None,
            "关于 AI 桌面助手",
            "<b>AI 桌面助手</b> v1.0<br><br>"
            "macOS 常驻 AI 助手<br>"
            "选中文字 → ⌘⌃L → 一键提问<br><br>"
            "基于 Ollama 本地 LLM，数据不上传。",
        )

    # ── Agent 切换 ─────────────────────────────────────

    @_safe_slot
    def _on_agent_changed(self, agent: Agent) -> None:
        self._active_agent = self._agent_mgr.switch(agent)
        self._tray.set_active_agent(self._active_agent)
        logger.info("Agent switched: %s", self._active_agent.name)

    @_safe_slot
    def _on_tray_agent(self, agent: Agent) -> None:
        """菜单栏切换 Agent"""
        self._active_agent = self._agent_mgr.switch(agent)
        if self._dialog:
            self._dialog.set_active_agent(self._active_agent)
        self._tray.set_active_agent(self._active_agent)
        logger.info("Agent switched via tray: %s", self._active_agent.name)

    def _on_model_changed(self, model: str) -> None:
        self._model = model
        save_setting("last_model", model)
        logger.info("Model switched: %s", model)

    @_safe_slot
    def _refresh_model_list(self) -> None:
        """从 Ollama 拉取模型列表刷新 dialog combo，失败时回退到 SQLite 缓存。

        缓存 key = 'cached_models'，存放 JSON 编码的模型名列表。
        """
        models = list_models()
        if not models:
            cached = get_setting("cached_models")
            if cached:
                try:
                    models = json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    models = []
        if models:
            if self._model not in models:
                self._model = models[0]
                save_setting("last_model", self._model)
            save_setting("cached_models", json.dumps(models, ensure_ascii=False))
            if self._dialog:
                self._dialog.refresh_models(models)

    # ── 新建对话 ───────────────────────────────────────

    @_safe_slot
    def _new_conversation(self) -> None:
        self._convo_id = 0
        self._messages = []
        if self._dialog:
            self._dialog.clear_messages()
        logger.info("New conversation started (agent=%s)", self._active_agent.name)

    # ── 工作线程管理 ───────────────────────────────────

    def _stop_worker(self) -> None:
        """安全停止当前流式 worker：发送中断信号 + 等待退出 + 清理"""
        if self._worker is None:
            return
        if not self._worker.isRunning():
            self._worker = None
            return
        w = self._worker
        w.requestInterruption()
        if w.wait(5000):
            self._worker = None
            return
        logger.warning("Worker did not stop within 5s, keeping reference for cleanup")
        try:
            w.thinking_chunk.disconnect()
            w.chunk.disconnect()
            w.done.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._worker = None
        self._stale_workers.append(w)
        w.finished.connect(lambda w=w: self._prune_worker(w))

    def _prune_worker(self, w: StreamingChatWorker) -> None:
        """从遗留队列中移除已完成的 worker"""
        if w in self._stale_workers:
            self._stale_workers.remove(w)

    # ── 对话历史 ───────────────────────────────────────

    @_safe_slot
    def _on_history_requested(self) -> None:
        dialog = HistoryDialog(parent=self._dialog)
        dialog.conversation_selected.connect(self._on_conversation_selected)
        if self._dialog:
            p = self._dialog.geometry().center()
            dialog.move(p.x() - dialog.width() // 2, p.y() - dialog.height() // 2)
        dialog.exec_()

    def _on_conversation_selected(self, convo_id: int) -> None:
        try:
            conv = get_conversation(convo_id)
            if conv is None:
                return
            # 停止当前 worker
            self._stop_worker()
            # 恢复对话状态
            self._convo_id = conv.id
            self._messages = conv.messages
            # 切换 Agent
            for ag in self._all_agents:
                if ag.id == conv.agent_id:
                    self._active_agent = self._agent_mgr.switch(ag)
                    if self._dialog:
                        self._dialog.set_active_agent(self._active_agent)
                    self._tray.set_active_agent(self._active_agent)
                    break
            # 渲染消息
            if self._dialog:
                self._dialog.clear_messages()
                for m in conv.messages:
                    if m.role == "user":
                        self._dialog.add_user_message(m.content)
                    else:
                        self._dialog.add_assistant_message(m.content)
            logger.info("Loaded conversation %d (%d messages)", convo_id, len(conv.messages))
        except Exception:
            logger.exception("Failed to load conversation %d", convo_id)

    @_safe_slot
    def _on_export_requested(self) -> None:
        """将当前对话格式化为 Markdown 并复制到剪贴板"""
        if not self._messages:
            return
        md = "# AI 桌面助手 · 对话记录\n\n"
        md += f"**{self._active_agent.icon} {self._active_agent.name}**\n\n"
        md += "---\n\n"
        for m in self._messages:
            role = "**用户**" if m.role == "user" else "**助手**"
            md += f"{role}: {m.content}\n\n"
        try:
            QApplication.clipboard().setText(md)
        except Exception:
            pass
        if self._dialog:
            self._dialog.flash_export_btn()
        logger.info("Exported %d messages to clipboard", len(self._messages))

    @_safe_slot
    def _on_manage_agents(self) -> None:
        """打开 Agent 管理对话框"""
        builtin = [
            AgentDef(id=ag.id, name=ag.name, icon=ag.icon,
                     system_prompt=ag.system_prompt, builtin=True)
            for ag in self._agent_mgr.builtin_agents
        ]
        editor = AgentEditor(builtin, self._custom_agents, parent=self._dialog)
        editor.agents_saved.connect(self._on_custom_agents_saved)
        if self._dialog:
            p = self._dialog.geometry().center()
            editor.move(p.x() - editor.width() // 2, p.y() - editor.height() // 2)
        editor.exec_()

    @_safe_slot
    def _on_custom_agents_saved(self, data: list[dict]) -> None:
        """自定义 Agent 保存后刷新"""
        self._agent_mgr.save_custom(data)
        self._all_agents = self._agent_mgr.all_agents
        self._custom_agents = self._agent_mgr.custom_agents
        self._active_agent = self._agent_mgr.active_agent
        if self._dialog:
            self._dialog.refresh_agents(self._all_agents)
        self._tray.refresh_agents(self._all_agents)
        self._tray.set_active_agent(self._active_agent)
        logger.info("Custom agents saved (%d custom)", len(data))

    # ── 发送消息 ───────────────────────────────────────

    def _on_stop_requested(self) -> None:
        """中断当前流式生成"""
        self._stop_worker()
        logger.info("Streaming interrupted by user")

    def _on_user_message(self, text: str) -> None:
        if self._worker and self._worker.isRunning():
            if self._dialog:
                self._dialog.set_input_text(text)
                self._dialog.flash_busy()
            return

        try:
            if self._convo_id == 0:
                conv = create_conversation(self._active_agent.id)
                self._convo_id = conv.id

            user_msg = save_message(self._convo_id, "user", text)
            self._messages.append(user_msg)

            if self._dialog:
                self._dialog.add_user_message(text)
                self._dialog.begin_assistant_stream()
                self._dialog.set_thinking(True)

            # 截断过长的历史，只保留最近 N 轮
            recent = list(self._messages)
            max_msgs = config.OLLAMA_MAX_ROUNDS * 2
            if len(recent) > max_msgs:
                recent = recent[-max_msgs:]

            self._worker = StreamingChatWorker(recent, self._active_agent.system_prompt, self._model, self)
            self._worker.thinking_chunk.connect(self._on_thinking_chunk)
            self._worker.chunk.connect(self._on_stream_chunk)
            self._worker.done.connect(self._on_stream_done)
            self._worker.start()
            self.float_btn.set_responding(True)
        except Exception:
            logger.exception("Failed to send message")
            if self._dialog:
                self._dialog.set_input_text(text)  # restore input so user can retry

    @_safe_slot
    def _on_thinking_chunk(self, token: str) -> None:
        if self._dialog:
            self._dialog.append_thinking_chunk(token)

    @_safe_slot
    def _on_stream_chunk(self, token: str) -> None:
        if self._dialog:
            self._dialog.append_stream_chunk(token)

    @_safe_slot
    def _on_stream_done(self, text: str, ok: bool) -> None:
        self.float_btn.set_responding(False)
        if self._dialog:
            self._dialog.set_thinking(False)

        if ok and self._convo_id > 0 and text:
            try:
                assistant_msg = save_message(self._convo_id, "assistant", text)
                self._messages.append(assistant_msg)
            except Exception:
                logger.exception("Failed to save assistant message")

            # 窗口在后台时发通知
            if self._dialog and not self._dialog.isActiveWindow():
                preview = text[:80].replace("\n", " ") + ("…" if len(text) > 80 else "")
                self._tray.showMessage(
                    f"{self._active_agent.icon} {self._active_agent.name}",
                    preview,
                    QSystemTrayIcon.Information,
                    3000,
                )

        if self._dialog:
            self._dialog.finalize_assistant_stream(text, ok)

        self._worker = None


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════


def _check_permissions():
    """检测辅助功能 + 输入监听权限"""
    from ai_desktop.utils.permissions import check_all
    return check_all()


def _request_permissions(ax: bool, im: bool) -> None:
    """触发 macOS 系统标准授权弹窗（已授权则静默返回）

    用 AXIsProcessTrustedWithOptions(prompt=True) 和 CGRequestListenEventAccess()
    替代自定义弹窗 —— 这是 macOS 推荐的标准 UX。
    """
    from ai_desktop.utils.permissions import request_accessibility, request_input_monitoring
    if not ax:
        logger.info("请求辅助功能权限（系统弹窗）")
        request_accessibility()
    if not im:
        logger.info("请求输入监听权限（系统弹窗）")
        request_input_monitoring()


def _open_accessibility_prefs() -> None:
    import subprocess
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])


def _open_input_monitoring_prefs() -> None:
    import subprocess
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"])


def main() -> None:
    log_util.setup()

    # 崩溃处理钩子（必须在任何异常可能发生之前安装）
    from ai_desktop.utils import crash_handler
    crash_handler.install()

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

    # 权限检查（辅助功能 + 输入监听）
    # 已授权 → 静默跳过；缺失 → 触发 macOS 系统标准授权弹窗
    perm = _check_permissions()
    logger.info("权限状态: AX=%s, InputMonitoring=%s", perm.accessibility, perm.input_monitoring)
    _perm_requested = False
    if not perm.all_granted:
        # 触发系统标准弹窗（非阻塞，用户在系统设置中授权后自动检测到）
        _request_permissions(perm.accessibility, perm.input_monitoring)
        _perm_requested = True

    # 权限重检定时器：用户在系统设置中授权后自动检测到，自动启动热键
    def _hotkey_running() -> bool:
        """检测热键是否已运行（兼容 NSEventMonitor / HotkeyListener）"""
        h = controller.hotkey
        if hasattr(h, "_monitor"):
            return h._monitor is not None
        if hasattr(h, "_listener"):
            return h._listener is not None
        return True

    _perm_recheck = QTimer()
    _recheck_count = 0

    def _recheck_permissions() -> None:
        nonlocal _perm_requested, _recheck_count
        _recheck_count += 1
        cur = _check_permissions()
        if cur.all_granted:
            if not _hotkey_running():
                # 权限刚授予，热键尚未启动 → 启动热键
                logger.info("权限已授予，启动热键监听")
                try:
                    controller.hotkey.start()
                except Exception as e:
                    logger.warning("热键启动失败: %s", e)
            _perm_recheck.stop()
            _perm_requested = False
        else:
            # 每 5 次（~15 秒）记录一次状态，避免日志刷屏
            if _recheck_count % 5 == 1:
                logger.info(
                    "等待授权中... (AX=%s, IM=%s, 第%d次检查)",
                    cur.accessibility, cur.input_monitoring, _recheck_count,
                )
            if not _perm_requested:
                # 权限被撤销或仍未授权 → 重新触发系统弹窗
                _request_permissions(cur.accessibility, cur.input_monitoring)
                _perm_requested = True

    _perm_recheck.timeout.connect(_recheck_permissions)
    _perm_recheck.start(3000)  # 每 3 秒重检一次

    controller.start()

    # ── 启动检查 ────────────────────────────────────────

    def _startup_check() -> None:
        """启动时检查：首次引导、Ollama 连通性、模型状态"""

        # 首次运行欢迎
        if not get_setting("startup_welcome_shown"):
            QMessageBox.information(
                None, "欢迎使用 AI 桌面助手",
                "<b>AI 桌面助手</b><br><br>"
                "三种打开方式：<br>"
                "1. 选中文字 → 按 <b>⌘⌃L</b> → 自动填入对话框<br>"
                "2. 点击屏幕右侧 <b>悬浮按钮</b><br>"
                "3. 点击菜单栏 <b>图标</b><br><br>"
                "需要 <b>Ollama</b> 本地模型服务，数据不上传。<br>"
                "首次使用请确保 Ollama 已启动。",
            )
            save_setting("startup_welcome_shown", "1")

        # Ollama 连通性检查
        ollama_ok = False
        try:
            r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
            if r.status_code == 200:
                ollama_ok = True
                logger.info("Ollama 连接正常 (model=%s)", config.OLLAMA_MODEL)
            else:
                logger.warning("Ollama 返回 %d", r.status_code)
        except Exception:
            logger.warning("无法连接 Ollama (%s)", config.OLLAMA_BASE_URL)

        if not ollama_ok:
            QMessageBox.warning(
                None, "Ollama 未运行",
                "未检测到 Ollama 服务。<br><br>"
                "请打开终端执行：<br>"
                "<tt>ollama serve</tt><br><br>"
                "安装地址：<a href='https://ollama.com'>https://ollama.com</a><br><br>"
                "启动后可点击右下角状态灯查看连接状态。",
            )
        else:
            # 检查是否有至少一个可用模型
            try:
                r = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
                if r.status_code == 200:
                    installed = {m["name"] for m in r.json().get("models", [])}
                    if not installed:
                        QMessageBox.warning(
                            None, "无可用模型",
                            "Ollama 已启动，但未安装任何模型。<br><br>"
                            "请打开终端执行：<br>"
                            "<tt>ollama pull qwen3:14b</tt><br><br>"
                            "更多模型：<a href='https://ollama.com/library'>https://ollama.com/library</a>",
                        )
            except Exception:
                pass

        # 版本更新检查（启动后 3s）
        from ai_desktop.utils.update_checker import check_for_update
        update = check_for_update()
        if update is not None:
            from PyQt5.QtWidgets import QSystemTrayIcon

            tray = QSystemTrayIcon()
            if tray.supportsMessages():
                tray.showMessage(
                    "AI 桌面助手 — 有更新",
                    f"新版本 {update.version} 可用\n{update.url}",
                    QSystemTrayIcon.Information,
                    5000,
                )

    QTimer.singleShot(1500, _startup_check)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
