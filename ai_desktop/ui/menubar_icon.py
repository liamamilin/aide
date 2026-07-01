"""
macOS 菜单栏图标
"""
import os

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QPainter, QPainterPath, QPixmap
from PyQt5.QtWidgets import QAction, QMenu, QSystemTrayIcon

from ai_desktop.config import Agent
from ai_desktop.ui import styles
from ai_desktop.utils.paths import resource_path

_ICON_PATH = next(
    (resource_path("ai_desktop", f) for f in ("图标.icns", "图标.png")
     if os.path.exists(resource_path("ai_desktop", f))),
    resource_path("ai_desktop", "图标.png"),
)
_SIZE = 18


def _make_tray_icon() -> QIcon:
    """加载图标并缩放为菜单栏尺寸的圆角图标"""
    if not os.path.exists(_ICON_PATH):
        return QIcon()
    src = QPixmap(_ICON_PATH).scaled(_SIZE, _SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    w, h = src.width(), src.height()
    side = min(w, h)
    x = (w - side) // 2
    y = (h - side) // 2
    cropped = src.copy(x, y, side, side)

    result = QPixmap(side, side)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, side, side))
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()
    return QIcon(result)


class MenuBarIcon(QSystemTrayIcon):
    dialog_toggle = pyqtSignal()
    agent_selected = pyqtSignal(Agent)
    settings_clicked = pyqtSignal()
    about_clicked = pyqtSignal()
    exit_clicked = pyqtSignal()

    def __init__(self, agents: list[Agent], active_agent: Agent, parent=None):
        super().__init__(parent)
        self._agents = agents
        self._active_agent = active_agent
        self._icon = _make_tray_icon()
        if not self._icon.isNull():
            self.setIcon(self._icon)
        else:
            # 无图标文件时显示一个彩色圆点
            px = QPixmap(_SIZE, _SIZE)
            px.fill(Qt.transparent)
            p = QPainter(px)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(Qt.blue)
            p.setPen(Qt.NoPen)
            p.drawEllipse(2, 2, _SIZE - 4, _SIZE - 4)
            p.end()
            self.setIcon(QIcon(px))
        self.setToolTip("AI 桌面助手")
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()
        menu.setStyleSheet(styles.MENU)

        show_action = menu.addAction("打开对话")
        show_action.triggered.connect(self.dialog_toggle.emit)

        menu.addSeparator()

        # Agent 快速切换
        self._agent_actions: list[QAction] = []
        for ag in self._agents:
            act = menu.addAction(f"{ag.icon}  {ag.name}")
            act.setCheckable(True)
            act.setData(ag.id)
            if ag.id == self._active_agent.id:
                act.setChecked(True)
            act.triggered.connect(lambda checked, a=ag: self.agent_selected.emit(a))
            self._agent_actions.append(act)

        menu.addSeparator()

        settings_action = menu.addAction("设置…")
        settings_action.triggered.connect(self.settings_clicked.emit)

        about_action = menu.addAction("关于")
        about_action.triggered.connect(self.about_clicked.emit)

        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.exit_clicked.emit)

        self.setContextMenu(menu)

    def set_active_agent(self, agent: Agent) -> None:
        self._active_agent = agent
        for act in self._agent_actions:
            act.setChecked(act.data() == agent.id)

    def refresh_agents(self, agents: list[Agent]) -> None:
        """自定义 Agent 变更后重建菜单"""
        self._agents = agents
        self._build_menu()

    def _on_activated(self, reason: int) -> None:
        # macOS 左键点击 = QSystemTrayIcon.Trigger
        if reason == QSystemTrayIcon.Trigger:
            self.dialog_toggle.emit()
