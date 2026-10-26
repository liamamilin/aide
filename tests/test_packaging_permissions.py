"""The signed build controls Notes, Reminders and System Events through Apple events."""
import plistlib
import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_hardened_runtime_allows_apple_events():
    entitlements = plistlib.loads((SCRIPTS / "entitlements.plist").read_bytes())
    assert entitlements["com.apple.security.automation.apple-events"] is True


def test_apple_events_usage_text_names_what_the_app_controls():
    spec = (SCRIPTS / "aide.spec").read_text(encoding="utf-8")
    text = re.search(r"'NSAppleEventsUsageDescription': '([^']+)'", spec).group(1)
    assert all(word in text for word in ("备忘录", "提醒事项", "窗口"))
