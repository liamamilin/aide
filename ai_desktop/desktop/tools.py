"""Executors for the desktop tools; they return metadata only and never read file contents."""
import json

from ai_desktop.desktop import opener, windows
from ai_desktop.desktop.runner import Runner, run_command
from ai_desktop.desktop.search import find_apps, find_files
from ai_desktop.integrations import apple
from ai_desktop.llm.run_types import ToolOutput

_FAILED = ToolOutput("本机查找或打开失败，请换个说法或向用户说明。", error=True)


class DesktopToolbox:
    def __init__(self, runner: Runner = run_command):
        self._runner = runner

    def _search(self, finder, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            found = finder(args["query"], runner=self._runner)
        except (OSError, ValueError):
            return _FAILED
        return ToolOutput(json.dumps({"results": [{"kind": c.kind, "name": c.name, "path": c.path}
                                                    for c in found]}, ensure_ascii=False))

    def find_apps(self, args, context):
        return self._search(find_apps, args, context)

    def find_files(self, args, context):
        return self._search(find_files, args, context)

    def open_path(self, args, context):
        if context.cancelled.is_set():
            return ToolOutput("已取消。", error=True)
        try:
            action = opener.open_path(args["path"], runner=self._runner)
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
        return self._act(lambda: apple.create_note(args["title"], args.get("body", ""), runner=self._runner),
                         context, "已在备忘录中新建笔记。", "无法创建笔记；请允许控制备忘录。")

    def create_reminder(self, args, context):
        return self._act(lambda: apple.create_reminder(args["title"], args.get("notes", ""),
                                                       runner=self._runner),
                         context, "已在提醒事项中新建提醒。", "无法创建提醒；请允许控制提醒事项。")

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
        return {"find_apps": self.find_apps, "find_files": self.find_files, "open_path": self.open_path,
                "list_windows": self.list_windows, "arrange_window": self.arrange_window,
                "create_note": self.create_note, "create_reminder": self.create_reminder}
