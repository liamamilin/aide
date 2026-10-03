"""Fluent menu action/data compatibility and selection readability."""

from PyQt5.QtCore import QPoint
from qfluentwidgets import RoundMenu, Theme, qconfig

from ai_desktop.ui.fluent import ComboBox, Menu, initialize


def test_menu_actions_preserve_callback_and_enabled_state(qtbot):
    menu = Menu()
    qtbot.addWidget(menu)
    seen = []
    action = menu.addAction("翻译", lambda: seen.append(True))
    action.trigger()
    assert seen == [True]
    action.setEnabled(False)
    assert not menu.actions()[0].isEnabled()
    assert isinstance(menu, RoundMenu)
    assert "MenuActionListWidget" in menu.styleSheet()


def test_submenu_uses_library_and_preserves_text(qtbot):
    menu = Menu()
    qtbot.addWidget(menu)
    sub = menu.addMenu("窗口大小")
    sub.addAction("恢复默认")
    assert isinstance(sub, RoundMenu)
    assert sub.actions()[0].text() == "恢复默认"


def test_menu_open_is_nonblocking_and_retains_actions(qtbot):
    menu = Menu()
    qtbot.addWidget(menu)
    action = menu.addAction("复制")
    menu.exec_(QPoint(10, 10), ani=False)
    assert menu.isVisible()
    assert action in menu.actions()
    menu.close()


def test_combo_preserves_ids_tuples_none_and_selection_signal(qtbot):
    combo = ComboBox()
    qtbot.addWidget(combo)
    combo.addItem("全局", None)
    combo.addItem("宠物", ("petdex", "boba"))
    combo.addItem("模型", "focused")
    with qtbot.waitSignal(combo.currentIndexChanged) as signal:
        combo.setCurrentIndex(combo.findData(("petdex", "boba")))
    assert signal.args == [1]
    assert combo.currentData() == ("petdex", "boba")
    combo.setCurrentIndex(0)
    assert combo.currentData() is None


def test_light_theme_uses_library_menu_selection_style(qapp, qtbot):
    initialize()
    menu = Menu()
    qtbot.addWidget(menu)
    assert qconfig.theme == Theme.LIGHT
    assert "QMenu::item" not in menu.styleSheet()
    assert "MenuActionListWidget" in menu.styleSheet()
