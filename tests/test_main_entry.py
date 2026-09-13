"""Tests for the lightweight command-line entry point."""

import subprocess
import sys
from pathlib import Path

from ai_desktop import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_main_module_reports_version_without_loading_gui():
    result = subprocess.run(
        [sys.executable, "-m", "ai_desktop", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == __version__
    assert result.stderr == ""
