"""Native selection fixture using a private pasteboard and application-local events.

No global listener, user clipboard, synthetic system keypress or model call.
This verifies native wiring, not physical keys or another application's copy path.
"""
import sys
import time
from ctypes import c_void_p


def acceptance(ctl, wait):
    import objc
    from AppKit import NSApplication, NSEvent, NSKeyDown, NSPasteboard, NSPasteboardItem
    from PyQt5.QtCore import QTimer

    from ai_desktop.capture.clipboard_monitor import NativePasteboard, SelectionCaptureTask
    from ai_desktop.capture.nsevent_monitor import NSEventMonitor

    entry = sys.modules[type(ctl).__module__]
    original_factory = entry.SelectionCaptureTask
    native_board = NSPasteboard.pasteboardWithUniqueName()
    board = object.__new__(NativePasteboard)
    board._pasteboard = native_board
    item = NSPasteboardItem.alloc().init()
    assert item.setString_forType_('Original 中文 clipboard', 'public.utf8-plain-text')
    assert item.setString_forType_('<b>Original 中文 clipboard</b>', 'public.html')
    native_board.clearContents()
    assert native_board.writeObjects_([item])
    original_items = board.snapshot().items
    selected = '中文选区：你好，世界。 English selection: Hello, world.'
    held = [True]
    captures, copies, fired, pulses = [], [], [], []

    def copy_fixture():
        copies.append(True)
        native_board.clearContents()
        return bool(native_board.setString_forType_(selected, 'public.utf8-plain-text'))

    def factory(parent):
        task = SelectionCaptureTask(parent, pasteboard=board, copy_action=copy_fixture,
                                    modifier_state=lambda: held[0])
        captures.append(task)
        return task

    def triggered():
        fired.append(True)
        ctl._on_global_hotkey()

    monitor = NSEventMonitor()
    monitor.register('<cmd>+<ctrl>+l', triggered)
    dialog = ctl._dialog
    view = objc.objc_object(c_void_p=c_void_p(int(dialog.winId())))
    window_number = view.window().windowNumber()
    flags = (1 << 20) | (1 << 18)

    make_event = getattr(NSEvent, 'keyEventWithType_location_modifierFlags_timestamp_'
                         'windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_')

    def dispatch(*, repeat=False, extra=0):
        event = make_event(
            NSKeyDown, (0, 0), flags | extra, time.monotonic(), window_number,
            None, 'l', 'l', repeat, 37)
        NSApplication.sharedApplication().sendEvent_(event)

    try:
        entry.SelectionCaptureTask = factory
        monitor.start(local_only=True)
        assert monitor._local_monitor is not None and monitor._monitor is None
        dispatch()
        wait(lambda: len(captures) == 1)
        QTimer.singleShot(180, lambda: pulses.append(True))
        dispatch(repeat=True)
        dispatch(extra=1 << 17)
        wait(lambda: bool(pulses))
        assert fired == [True] and not copies
        held[0] = False
        wait(lambda: ctl._selection_capture is None)
        assert dialog._input.toPlainText() == selected
        assert board.snapshot().items == original_items
        assert copies == [True]
        old_handler = monitor._handler
        monitor.stop()
        event = make_event(
            NSKeyDown, (0, 0), flags, time.monotonic(), window_number,
            None, 'l', 'l', False, 37)
        old_handler(event)
        assert fired == [True]
        return {'native_local_event_dispatch': True, 'repeat_and_extra_modifier_ignored': True,
                'held_modifiers_waited_over_180ms': True, 'qt_event_loop_responsive': True,
                'cjk_and_english_input_matches': True, 'private_clipboard_formats_restored': True,
                'stopped_callback_ignored': True, 'user_clipboard_accessed': False,
                'global_listener_started': False, 'physical_cross_app_copy_verified': False}
    finally:
        monitor.stop()
        entry.SelectionCaptureTask = original_factory
        for task in captures:
            if ctl._selection_capture is task:
                task.cancel()
        native_board.releaseGlobally()
