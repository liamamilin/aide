"""Plain-language text for native tool cards, instead of raw arguments and JSON."""
import json
import os

_LAYOUTS = {"left_half": "左半屏", "right_half": "右半屏", "maximize": "最大化", "center": "居中"}
NATIVE_TOOLS = frozenset({
    "find_apps", "find_files", "open_path", "list_windows", "arrange_window", "create_note", "create_reminder",
    "list_notes", "list_reminders", "complete_reminder", "delete_reminder", "append_note", "delete_note",
})
AUTOMATION_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def _name(path) -> str:
    return os.path.basename(str(path).rstrip("/")) or str(path)


def describe_call(tool_name: str, args: dict) -> str:
    """What the tool is about to do, in one line."""
    get = args.get
    if tool_name == "find_apps":
        return f"查找应用：{get('query', '')}"
    if tool_name == "find_files":
        days = f"（最近 {get('modified_within_days')} 天）" if get("modified_within_days") else ""
        return f"查找文件：{get('query', '')}{days}"
    if tool_name == "open_path":
        with_app = f"，用 {_name(get('app')).removesuffix('.app')}" if get("app") else ""
        return f"打开：{_name(get('path', ''))}{with_app}"
    if tool_name == "list_windows":
        return "查看当前打开的窗口"
    if tool_name == "arrange_window":
        return f"{get('app', '')} → {_LAYOUTS.get(get('layout'), get('layout', ''))}"
    if tool_name in {"create_note", "create_reminder"}:
        return f"{'新建笔记' if tool_name == 'create_note' else '新建提醒'}：{get('title', '')}"
    if tool_name == "list_notes":
        return f"列出备忘录{'：' + get('query') if get('query') else ''}"
    if tool_name == "list_reminders":
        return f"列出未完成的提醒{'：' + get('query') if get('query') else ''}"
    if tool_name == "complete_reminder":
        return "把这条提醒标记为完成"
    if tool_name == "delete_reminder":
        return "永久删除这条提醒"
    if tool_name == "append_note":
        return f"追加到笔记末尾：{get('text', '')[:60]}"
    if tool_name == "delete_note":
        return "删除这条笔记"
    return ""


def _json(text):
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def describe_result(tool_name: str, text: str, error: bool) -> str:
    """A short status for the card header once the tool has finished."""
    if error:
        return "未完成"
    record = _json(text) or {}
    if tool_name in {"find_apps", "find_files"}:
        count = len(record.get("results", []))
        return f"找到 {count} 项" if count else "没有找到"
    if tool_name == "open_path":
        return "已在访达中显示" if record.get("action") == "reveal" else "已打开"
    if tool_name == "list_windows":
        return f"{len(record.get('windows', []))} 个窗口"
    if tool_name == "arrange_window":
        return "已调整"
    if tool_name in {"create_note", "create_reminder"}:
        return "已创建"
    if tool_name == "list_notes":
        return f"共 {record.get('total_notes', 0)} 条"
    if tool_name == "list_reminders":
        return f"{record.get('total_open_reminders', 0)} 条未完成"
    if tool_name == "complete_reminder":
        return f"已完成：{record.get('title', '')}"[:40]
    if tool_name in {"delete_reminder", "delete_note"}:
        return f"已删除：{record.get('title', '')}"[:40]
    if tool_name == "append_note":
        return "已追加"
    return "已完成"


def permission_pane(text: str) -> str | None:
    """The System Settings pane that fixes a permission failure reported by a tool, if any."""
    if "辅助功能" in text:
        return ACCESSIBILITY_PANE
    if "允许控制" in text or "-1743" in text:
        return AUTOMATION_PANE
    return None
