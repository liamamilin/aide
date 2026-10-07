"""Synthetic multi-step answer layout checks; no model, keychain or HTTP calls."""
import json
from dataclasses import replace

from ai_desktop.llm.run_types import RunEvent, RunEventKind, ToolOutput

CITATION_TEXT = ('Python 官方文档是浏览在线教程、指南和按类型与主题分类的参考资料，'
                 '旨在帮助用户学习 Python 并获取编程支持 [S1]。')
STRUCTURED_TEXT = ('## 文档用途\n\n'+CITATION_TEXT+'\n\n'
                   '- 学习语言与标准库。\n- 查阅参考资料和示例。\n\n'
                   '| 资料 | 用途 |\n| --- | --- |\n| 教程 | 学习语法 |\n| 参考 | 查阅 API |\n\n[S1]')


def streamed_fixture(dialog, text, settle):
    """Exercise real UI event/stream paths with an already scrolled conversation."""
    dialog.clear_messages()
    dialog.add_user_message('搜索官方文档并引用来源。')
    first = RunEvent('layout', 0, 'first', 'first-request', 1, RunEventKind.MODEL_STARTED)
    dialog.begin_tool_run(first.run_id)
    dialog.show_task_model_event(first)
    dialog.show_task_model_event(replace(first, kind=RunEventKind.MODEL_FINISHED, status='succeeded',
                                        payload_json=json.dumps({'output': {'content': '查阅官方资料。'}})))
    tool = replace(first, kind=RunEventKind.TOOL_STARTED, tool_name='web_search', local_call_id='search',
                   arguments_json='{"query":"fixture"}')
    dialog.show_tool_event(tool)
    record = {'provider': 'parallel', 'error_type': '', 'sources': [
        {'source_id': 'S1', 'title': 'Python Documentation', 'url': 'https://www.python.org/doc',
         'excerpt': 'Synthetic fixture, not a live search.'}]}
    dialog.show_tool_event(replace(tool, kind=RunEventKind.TOOL_FINISHED, status='succeeded',
                                  output=ToolOutput(json.dumps(record))))
    second = replace(first, step_id='second', request_id='second-request')
    dialog.show_task_model_event(second)
    label, card = dialog._stream_bubble, dialog._stream_container
    dialog.append_stream_chunk(text)
    dialog._flush_stream_buffer()
    settle(lambda: label.height() >= label.heightForWidth(label.width()))
    dialog.show_task_model_event(replace(second, kind=RunEventKind.MODEL_FINISHED, status='succeeded',
                                        payload_json=json.dumps({'output': {'content': text}})))
    dialog.finalize_assistant_stream(text, True)
    return label, card


def fits(label, card):
    """The rendered document fits, and following controls cannot overlap it."""
    return (label.width() > 0 and label.height() >= label.heightForWidth(label.width())
            and card.rect().contains(label.geometry())
            and card.status.y() > label.geometry().bottom())


def measure(label):
    return {'width': label.width(), 'height': label.height(),
            'required_height': label.heightForWidth(label.width())}


def restored_answer(dialog, text, previous_labels):
    # clear_messages schedules deleteLater; old labels remain children until
    # Qt processes deferred deletes. Only newly created labels prove restoration.
    from PyQt5.QtWidgets import QLabel

    return next((label for label in dialog.findChildren(QLabel)
                 if label not in previous_labels and getattr(label, '_markdown_source', '') == text), None)


def acceptance(dialog, settle, preview_dir=None):
    """Verify compact/wide/resized answers and a restored Markdown message."""
    checks = []
    for name, text in [('citation', CITATION_TEXT), ('structured', STRUCTURED_TEXT)]:
        dialog.resize(450, 570)
        label, card = streamed_fixture(dialog, text, settle)
        for width, height in [(450, 570), (400, 460), (900, 700), (450, 570)]:
            dialog.resize(width, height)
            settle(lambda: fits(label, card))
            checks.append({'fixture': name, 'window': [width, height], **measure(label)})
        if preview_dir:
            preview_dir.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(preview_dir/(name+'.png')))
    dialog.clear_messages()
    dialog.add_assistant_message(CITATION_TEXT)
    from PyQt5.QtWidgets import QLabel

    label = next(label for label in dialog.findChildren(QLabel)
                 if getattr(label, '_markdown_source', '') == CITATION_TEXT)
    settle(lambda: label.height() >= label.heightForWidth(label.width()))
    checks.append({'fixture': 'history', **measure(label)})
    return {'checks': checks, 'model_or_tools_executed': False, 'credential_access': False}
