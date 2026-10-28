"""List and arrange application windows through System Events (needs Accessibility permission)."""
from dataclasses import dataclass

from ai_desktop.desktop.runner import Runner, run_command, run_script

LAYOUTS = ("left_half", "right_half", "maximize", "center")
MAX_NAME = 200
MENU_BAR = 25

_LIST_SCRIPT = """tell application "System Events"
    set output to ""
    repeat with proc in (application processes whose background only is false)
        try
            repeat with win in windows of proc
                set output to output & (name of proc) & tab & (name of win) & linefeed
            end repeat
        end try
    end repeat
    return output
end tell"""

_ARRANGE_SCRIPT = """on run argv
    set appName to item 1 of argv
    set layoutName to item 2 of argv
    tell application "Finder" to set screenBounds to bounds of window of desktop
    set sw to item 3 of screenBounds
    set sh to (item 4 of screenBounds) - %(bar)d
    set top to %(bar)d
    if layoutName is "left_half" then
        set target to {0, top, sw div 2, sh}
    else if layoutName is "right_half" then
        set target to {sw div 2, top, sw div 2, sh}
    else if layoutName is "maximize" then
        set target to {0, top, sw, sh}
    else
        set target to {sw div 8, top + (sh div 8), (sw * 3) div 4, (sh * 3) div 4}
    end if
    tell application "System Events" to tell application process appName
        set position of window 1 to {item 1 of target, item 2 of target}
        set size of window 1 to {item 3 of target, item 4 of target}
    end tell
end run""" % {"bar": MENU_BAR}


@dataclass(frozen=True)
class WindowInfo:
    app: str
    title: str


def list_windows(*, runner: Runner = run_command, limit: int = 40) -> list[WindowInfo]:
    found = []
    for line in run_script(_LIST_SCRIPT, runner=runner).splitlines():
        app, _, title = line.partition("\t")
        if app:
            found.append(WindowInfo(app, title))
        if len(found) >= limit:
            break
    return found


def arrange_window(app: str, layout: str, *, runner: Runner = run_command) -> None:
    if not isinstance(app, str) or not app.strip() or len(app) > MAX_NAME or "\x00" in app:
        raise ValueError("Invalid application name")
    if layout not in LAYOUTS:
        raise ValueError("Invalid layout")
    run_script(_ARRANGE_SCRIPT, [app.strip(), layout], runner=runner)
