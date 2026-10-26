"""Executors for the desktop tools; they return metadata only and never read file contents."""
import json

from ai_desktop.desktop import opener
from ai_desktop.desktop.runner import Runner, run_command
from ai_desktop.desktop.search import find_apps, find_files
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

    def executors(self):
        return {"find_apps": self.find_apps, "find_files": self.find_files, "open_path": self.open_path}
