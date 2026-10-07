"""Native conversation teardown regression; synthetic messages/events, no services."""


def acceptance(dialog, wait):
    from PyQt5 import sip
    from PyQt5.QtCore import QCoreApplication, QEvent
    from PyQt5.QtWidgets import QApplication

    cycles = 20
    for index in range(cycles):
        dialog.add_user_message(f'生命周期验收 {index}: 中文 English')
        dialog.add_assistant_message('## 回答\n\n- 临时测试内容\n- 不调用模型或搜索')
        bubble = dialog._msg_layout.itemAt(1).widget()
        button = bubble._edit_button
        QApplication.sendEvent(bubble, QEvent(QEvent.Enter))
        assert not button.isHidden()
        dialog.clear_messages()
        assert bubble.isHidden() and bubble._edit_button is None
        QApplication.sendEvent(bubble, QEvent(QEvent.Leave))
        QApplication.sendEvent(bubble, QEvent(QEvent.Enter))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert sip.isdeleted(bubble) and sip.isdeleted(button)
        wait(lambda: dialog._msg_layout.count() == 2)
    dialog.add_user_message('新对话输入仍可用')
    dialog.set_input_text('恢复输入 中文 English')
    assert dialog._input.toPlainText() == '恢复输入 中文 English'
    return {'clear_and_recreate_cycles': cycles, 'hover_enter_leave_verified': True,
            'retired_controls_not_accessed': True, 'deferred_widgets_deleted': True,
            'input_after_recreation': True, 'synthetic_qt_events': True,
            'physical_screenshot_cancel_verified': False,
            'model_or_tools_executed': False, 'credential_access': False,
            'user_clipboard_accessed': False}
