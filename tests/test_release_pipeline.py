"""Version and release-pipeline contract tests."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

from ai_desktop import __version__
from ai_desktop import version as version_module
from ai_desktop.utils import images, storage
from scripts import release_check

ROOT = Path(__file__).resolve().parents[1]


def test_source_and_package_metadata_share_version():
    result = subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == __version__ == version_module.__version__
    assert release_check.source_version() == __version__


def test_release_check_rejects_requested_version_mismatch():
    version, errors = release_check.validate_source("v99.0.0")

    assert version == __version__
    assert errors == [f"requested version '99.0.0' does not match source {__version__!r}"]


def test_bundle_validator_checks_version_dependencies_and_resources(tmp_path):
    bundle = tmp_path / "AI桌面助手.app"
    contents = bundle / "Contents"
    executable = contents / "MacOS" / "AI桌面助手"
    executable.parent.mkdir(parents=True)
    executable.touch()
    resources = contents / "Resources" / "ai_desktop"
    resources.mkdir(parents=True)
    for name in ("图标.icns", "图标.png", "桌面宠物.png"):
        (resources / name).touch()
    frameworks = contents / "Frameworks"
    for name in (
        "AppKit",
        "Foundation",
        "CoreFoundation",
        "CoreML",
        "HIServices",
        "objc",
        "Quartz",
        "Vision",
    ):
        (frameworks / name).mkdir(parents=True)
    (frameworks / "PyQt5" / "Qt5" / "lib" / "QtNetwork.framework").mkdir(
        parents=True
    )
    info = {
        "CFBundleExecutable": "AI桌面助手",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
    }
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)

    assert release_check.validate_bundle(bundle, __version__) == []
    mismatch = release_check.validate_bundle(bundle, "99.0.0")
    assert mismatch == [
        f"CFBundleShortVersionString={__version__!r}, expected '99.0.0'",
        f"CFBundleVersion={__version__!r}, expected '99.0.0'",
    ]


def test_smoke_paths_can_be_isolated_from_user_data(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AIDE_DATA_DIR", str(data_dir))

    assert storage._resolve_db_path() == data_dir / "chat_history.db"
    assert images._app_support_dir() == data_dir


def test_ci_and_local_build_use_one_definition():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    build_script = (ROOT / "scripts" / "build.sh").read_text(encoding="utf-8")
    smoke_script = (ROOT / "scripts" / "smoke_test.sh").read_text(encoding="utf-8")
    upgrade_smoke_script = (ROOT / "scripts" / "upgrade_smoke_test.sh").read_text(encoding="utf-8")

    assert "bash scripts/build.sh --smoke --dmg" in workflow
    assert "pyinstaller --windowed" not in workflow.lower()
    assert "scripts/aide.spec" in build_script
    assert "Contents/MacOS/AI桌面助手" in smoke_script
    assert "PRAGMA user_version" in smoke_script
    assert "SCHEMA_VERSION" in smoke_script
    assert "pgrep" not in smoke_script
    assert "AIDE_DATA_DIR" in upgrade_smoke_script
    assert "PRAGMA user_version" in upgrade_smoke_script
    assert "backups" in upgrade_smoke_script
