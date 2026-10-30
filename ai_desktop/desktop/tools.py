"""Executors for the desktop tools; they return metadata only and never read file contents."""
import json
import time
from contextlib import nullcontext
from datetime import timedelta

from ai_desktop.desktop import opener, windows
from ai_desktop.desktop.runner import Runner, cancellable, run_command
from ai_desktop.desktop.search import find_apps, find_files
from ai_desktop.integrations import apple, calendar_app, clipboard
from ai_desktop.llm.run_types import ToolOutput
from ai_desktop.llm.tool_registry import ToolRisk, get_definition
from ai_desktop.services.command_confirmation import CommandConfirmation, ConfirmationBroker, ConfirmationOutcome

_FAILED = ToolOutput("本机查找或打开失败，请换个说法或向用户说明。", error=True)
_NOT_CONFIRMED = {
    ConfirmationOutcome.DENIED: "用户拒绝了此操作，未执行。不要再次尝试，向用户说明即可。",
    ConfirmationOutcome.EXPIRED: "确认已过期，未执行。",
    ConfirmationOutcome.CANCELLED: "已取消。",
    ConfirmationOutcome.UNAVAILABLE: "无法显示确认，未执行。",
}


class DesktopToolbox:
    def __init__(self, runner: Runner = run_command, confirmations: ConfirmationBroker | None = None):
        self._runner = runner
        self._confirmations = confirmations

    def _confirm(self, context, tool_name, preview, reason):
        """Ask the user to approve exactly this action; None means approved, else the refusal to return."""
        if self._confirmations is None:
            return ToolOutput(_NOT_CONFIRMED[ConfirmationOutcome.UNAVAILABLE], error=True)
        request = CommandConfirmation.for_action(context, tool_name, preview, reason,
                                                 clock=self._confirmations.clock)
        if context.on_state is not None:
            context.on_state("waiting_confirmation")
        pause = context.budget.pause() if context.budget is not None else nullcontext()
        with pause:
            outcome = self._confirmations.wait(request, context.cancelled)
        if context.on_state is not None and outcome == ConfirmationOutcome.APPROVED:
            context.on_state("executing")
        if outcome == ConfirmationOutcome.APPROVED:
            return None
        return ToolOutput(_NOT_CONFIRMED[outcome], error=True)

    def _search(self, finder, args, context, **options):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            found = finder(args["query"], runner=self._runner, **options)
        except (OSError, ValueError):
            return _FAILED
        results = []
        for item in found:
            record = {"kind": item.kind, "name": item.name, "path": item.path}
            if item.modified:
                record["modified"] = time.strftime("%Y-%m-%d", time.localtime(item.modified))
            results.append(record)
        return ToolOutput(json.dumps({"results": results}, ensure_ascii=False))

    def find_apps(self, args, context):
        return self._search(find_apps, args, context)

    def find_files(self, args, context):
        days = args.get("modified_within_days")
        return self._search(find_files, args, context, **({"modified_within_days": days} if days else {}))

    def open_path(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            action = opener.open_path(args["path"], app=args.get("app"), runner=self._runner)
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError):
                return ToolOutput("路径无效或不存在。", error=True)
            return _FAILED
        return ToolOutput(json.dumps({"action": action}))

    def list_windows(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            found = windows.list_windows(runner=self._runner)
        except OSError:
            return _FAILED
        record = {"windows": [{"app": w.app, "title": w.title} for w in found]}
        if not found:
            record["note"] = "没有找到窗口；若应用确有窗口，需要在系统设置中授予辅助功能权限。"
        return ToolOutput(json.dumps(record, ensure_ascii=False))

    def arrange_window(self, args, context):
        return self._act(lambda: windows.arrange_window(args["app"], args["layout"], runner=self._runner),
                         context, "已调整窗口。",
                         "无法调整窗口；请确认应用已打开，并授予辅助功能权限。")

    def create_note(self, args, context):
        return self._created(lambda: apple.create_note(args["title"], args.get("body", ""), runner=self._runner),
                             context, args["title"], "已在备忘录中新建笔记。", "无法创建笔记；请允许控制备忘录。")

    def create_reminder(self, args, context):
        return self._created(lambda: apple.create_reminder(args["title"], args.get("notes", ""),
                                                           runner=self._runner),
                             context, args["title"], "已在提醒事项中新建提醒。",
                             "无法创建提醒；请允许控制提醒事项。")

    def list_notes(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            notes = apple.list_notes(runner=self._runner)
        except (OSError, ValueError):
            return ToolOutput("无法读取备忘录；请允许控制备忘录。", error=True)
        query = args.get("query", "").strip().casefold()
        matching = [note for note in notes if query in note.title.casefold()]
        shown = matching[:args.get("limit", 20)]
        return ToolOutput(json.dumps({
            "total_notes": len(notes), "matching": len(matching),
            "notes": [{"id": n.id, "title": n.title, "age_seconds": n.age_seconds} for n in shown],
        }, ensure_ascii=False))

    def list_reminders(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            reminders = apple.list_reminders(runner=self._runner)
        except (OSError, ValueError):
            return ToolOutput("无法读取提醒事项；请允许控制提醒事项。", error=True)
        query = args.get("query", "").strip().casefold()
        matching = [item for item in reminders if query in item.title.casefold()]
        return ToolOutput(json.dumps({
            "total_open_reminders": len(reminders), "matching": len(matching),
            "reminders": [{"id": r.id, "title": r.title} for r in matching[:args.get("limit", 20)]],
        }, ensure_ascii=False))

    def complete_reminder(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            title = apple.reminder_title(args["id"], runner=self._runner)
            apple.complete_reminder(args["id"], runner=self._runner)
        except (OSError, ValueError):
            return ToolOutput("无法完成这条提醒；请先用 list_reminders 查看，并允许控制提醒事项。", error=True)
        return ToolOutput(json.dumps({"result": "已标记为完成。", "title": title}, ensure_ascii=False))

    def delete_reminder(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            title = apple.reminder_title(args["id"], runner=self._runner)
        except (OSError, ValueError):
            return ToolOutput("找不到这条提醒，请先用 list_reminders 查看。", error=True)
        refused = self._confirm(context, "delete_reminder", f"永久删除提醒：{title}",
                                "提醒事项没有“最近删除”，删除后无法恢复。如只是做完了，请选择拒绝并改为标记完成。")
        if refused is not None:
            return refused
        return self._act(lambda: apple.delete_reminder(args["id"], runner=self._runner), context,
                         json.dumps({"result": "已删除这条提醒。", "title": title}, ensure_ascii=False),
                         "无法删除提醒。")

    def list_events(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            events = calendar_app.list_events(args.get("days_ahead", 7), runner=self._runner)
        except (OSError, ValueError):
            return ToolOutput("无法读取日历；请允许控制日历。", error=True)
        return ToolOutput(json.dumps({
            "total_events": len(events),
            "events": [{"title": e.title, "start": e.start.strftime("%Y-%m-%d %H:%M"), "minutes": e.minutes,
                        "calendar": e.calendar} for e in events[:50]],
        }, ensure_ascii=False))

    def create_event(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            start = calendar_app.parse_start(args["start"])
            minutes = args.get("duration_minutes", 60)
            ends = start + timedelta(minutes=minutes)
        except (ValueError, OverflowError):
            return ToolOutput("开始时间格式应为 2026-11-02 15:00。", error=True)
        try:
            calendar_app.validate_event(args["title"], start, minutes)
        except ValueError:
            return ToolOutput("开始时间超出范围（只支持昨天起五年内），请核对日期。", error=True)
        weekday = "一二三四五六日"[start.weekday()]
        preview = (f"新建日历事件：{args['title']}\n{start:%Y-%m-%d} 周{weekday} {start:%H:%M}–{ends:%H:%M}"
                   f"（{minutes} 分钟）")
        refused = self._confirm(context, "create_event", preview, "请核对日期和时间；确认后才会写入日历。")
        if refused is not None:
            return refused
        return self._created(lambda: calendar_app.create_event(args["title"], start, minutes,
                                                               runner=self._runner),
                             context, args["title"], "已在日历中新建事件。", "无法创建事件；请允许控制日历。")

    def read_clipboard(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            text = clipboard.read_text(runner=self._runner)
        except OSError:
            return ToolOutput("剪贴板里没有可读取的文字。", error=True)
        shown = text[:4000]
        return ToolOutput(json.dumps({"text": shown, "truncated": len(text) > len(shown)}, ensure_ascii=False))

    def append_note(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            title = apple.note_title(args["id"], runner=self._runner)
        except (OSError, ValueError):
            return ToolOutput("找不到这条笔记，请先用 list_notes 查看。", error=True)
        preview = f"在备忘录《{title}》末尾追加：\n{args['text']}"
        refused = self._confirm(context, "append_note", preview,
                                "只在末尾追加，不删除已有内容；含附件或手写的笔记，请先在备忘录里确认。")
        if refused is not None:
            return refused
        return self._act(lambda: apple.append_to_note(args["id"], args["text"], runner=self._runner), context,
                         json.dumps({"result": "已追加到笔记末尾。", "title": title}, ensure_ascii=False),
                         "无法追加到笔记。")

    def delete_note(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            title = apple.note_title(args["id"], runner=self._runner)
        except (OSError, ValueError):
            return ToolOutput("找不到这条笔记，请先用 list_notes 查看。", error=True)
        refused = self._confirm(context, "delete_note", f"删除备忘录：{title}",
                                "这条笔记会移到“最近删除”，30 天内可在备忘录中恢复。")
        if refused is not None:
            return refused
        return self._act(lambda: apple.delete_note(args["id"], runner=self._runner), context,
                         json.dumps({"result": "已把这条笔记移到备忘录的“最近删除”。", "title": title},
                                    ensure_ascii=False), "无法删除笔记。")

    @staticmethod
    def _created(action, context, title, done, failed):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            created_id = action()
        except (OSError, ValueError):
            return ToolOutput(failed, error=True)
        return ToolOutput(json.dumps({"result": done, "id": created_id, "title": title}, ensure_ascii=False))

    @staticmethod
    def _act(action, context, done, failed):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            action()
        except (OSError, ValueError):
            return ToolOutput(failed, error=True)
        return ToolOutput(done)

    def executors(self):
        tools = {"find_apps": self.find_apps, "find_files": self.find_files, "open_path": self.open_path,
                 "list_windows": self.list_windows, "arrange_window": self.arrange_window,
                 "create_note": self.create_note, "create_reminder": self.create_reminder,
                 "list_notes": self.list_notes, "list_reminders": self.list_reminders,
                 "complete_reminder": self.complete_reminder, "delete_reminder": self.delete_reminder,
                 "append_note": self.append_note, "delete_note": self.delete_note,
                 "list_events": self.list_events, "create_event": self.create_event,
                 "read_clipboard": self.read_clipboard}
        return {name: self._guarded(name, execute) for name, execute in tools.items()}

    def _guarded(self, name, execute):
        """A destructive tool is only offered when the user can be asked; it fails closed otherwise."""
        definition = get_definition(name)
        if definition is not None and definition.risk == ToolRisk.DESTRUCTIVE and self._confirmations is None:
            return lambda args, context: ToolOutput(_NOT_CONFIRMED[ConfirmationOutcome.UNAVAILABLE], error=True)

        def run(args, context):
            with cancellable(context.cancelled):  # stop kills a slow osascript/mdfind instead of waiting it out
                return execute(args, context)
        return run
