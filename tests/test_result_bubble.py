"""Desktop-pet result bubble behavior and placement regressions."""

from unittest.mock import patch

import pytest
from PyQt5.QtCore import QPoint, QRect, Qt

from ai_desktop.ui.result_bubble import ResultBubble


@pytest.fixture
def bubble(qtbot):
    widget = ResultBubble()
    qtbot.addWidget(widget)
    return widget


def test_result_bubble_prefers_left_and_falls_back_to_right(bubble):
    screen = QRect(0, 0, 1000, 700)
    with patch.object(ResultBubble, "_screen_geometry", return_value=screen):
        bubble.position_near(QRect(700, 300, 116, 122))
        assert bubble._anchor_side == "left"
        assert bubble.x() < 700
        assert screen.contains(bubble.geometry())

        bubble.position_near(QRect(10, 20, 116, 122))
        assert bubble._anchor_side == "right"
        assert bubble.x() > 10
        assert screen.contains(bubble.geometry())


def test_click_opens_full_result_and_close_only_dismisses(qtbot, bubble):
    with patch.object(ResultBubble, "_screen_geometry", return_value=QRect(0, 0, 1000, 700)):
        bubble.show_result("success", "任务完成", "点击查看完整内容", QRect(700, 300, 116, 122))
    with qtbot.waitSignal(bubble.activated, timeout=1000):
        qtbot.mouseClick(bubble._content, Qt.LeftButton, pos=QPoint(120, 45))
    assert not bubble.isVisible()

    with patch.object(ResultBubble, "_screen_geometry", return_value=QRect(0, 0, 1000, 700)):
        bubble.show_result("error", "生成失败", "点击查看错误详情", QRect(700, 300, 116, 122))
    activated = []
    bubble.activated.connect(lambda: activated.append(True))
    with qtbot.waitSignal(bubble.dismissed, timeout=1000):
        bubble._close.click()
    assert activated == []
    assert not bubble.isVisible()


def test_result_bubble_timeout_and_summary_bound(qtbot, bubble):
    assert ResultBubble.summarize("  多行\n内容  ", "空") == "多行 内容"
    assert len(ResultBubble.summarize("字" * 120, "空")) == 97

    with patch.object(ResultBubble, "_screen_geometry", return_value=QRect(0, 0, 1000, 700)):
        bubble.show_result("action", "需要处理", "请检查服务连接", QRect(700, 300, 116, 122))
    assert bubble.result_kind == "action"
    assert bubble._hide_timer.isActive()
    bubble._hide_timer.start(1)
    qtbot.waitUntil(lambda: not bubble.isVisible(), timeout=100)


def test_progress_bubble_dismisses_without_opening_chat(qtbot, bubble):
    activated = []
    bubble.activated.connect(lambda: activated.append(True))
    with patch.object(ResultBubble, "_screen_geometry", return_value=QRect(0, 0, 1000, 700)):
        bubble.show_result(
            "progress",
            "准备朗读",
            "正在加载英语朗读模型…",
            QRect(700, 300, 116, 122),
            activate_on_click=False,
        )

    with qtbot.waitSignal(bubble.dismissed, timeout=1000):
        qtbot.mouseClick(bubble._content, Qt.LeftButton, pos=QPoint(120, 45))
    assert activated == []
    assert not bubble.isVisible()


def test_result_bubble_rejects_unknown_kind(bubble):
    with pytest.raises(ValueError):
        bubble.show_result("unknown", "", "", QRect())
