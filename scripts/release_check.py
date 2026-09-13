#!/usr/bin/env python3
"""Read-only release and bundle validation for AI Desktop Assistant."""

from __future__ import annotations

import argparse
import plistlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "ai_desktop" / "version.py"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[ab]|rc)?[0-9]*$")


def source_version() -> str:
    namespace: dict[str, str] = {}
    exec(VERSION_FILE.read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def validate_source(
    expected: str | None = None,
    *,
    require_clean: bool = False,
    require_tag: bool = False,
    require_new_tag: bool = False,
) -> tuple[str, list[str]]:
    version = source_version()
    errors: list[str] = []
    if not SEMVER.fullmatch(version):
        errors.append(f"source version is not supported SemVer: {version!r}")
    normalized_expected = expected.removeprefix("v") if expected else None
    if normalized_expected and normalized_expected != version:
        errors.append(
            f"requested version {normalized_expected!r} does not match source {version!r}"
        )

    tag = f"v{version}"
    if require_clean:
        status = _git("status", "--porcelain=v1", "--untracked-files=all")
        if status.returncode != 0:
            errors.append(status.stderr.strip() or "cannot inspect Git worktree")
        elif status.stdout.strip():
            errors.append("Git worktree is not clean")
    if require_tag:
        points_at = _git("tag", "--points-at", "HEAD")
        tags = set(points_at.stdout.split()) if points_at.returncode == 0 else set()
        if tag not in tags:
            errors.append(f"HEAD is not tagged {tag}")
    if require_new_tag:
        exists = _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
        if exists.returncode == 0:
            errors.append(f"tag {tag} already exists")
    return version, errors


def validate_bundle(bundle: Path, version: str) -> list[str]:
    errors: list[str] = []
    contents = bundle / "Contents"
    plist_path = contents / "Info.plist"
    if not plist_path.is_file():
        return [f"missing bundle Info.plist: {plist_path}"]
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)

    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if info.get(key) != version:
            errors.append(f"{key}={info.get(key)!r}, expected {version!r}")
    executable = contents / "MacOS" / str(info.get("CFBundleExecutable", "AI桌面助手"))
    if not executable.is_file():
        errors.append(f"missing bundle executable: {executable}")

    resources = contents / "Resources" / "ai_desktop"
    for name in ("图标.icns", "图标.png", "桌面宠物.png"):
        if not (resources / name).is_file():
            errors.append(f"missing bundled resource: ai_desktop/{name}")

    frameworks = contents / "Frameworks"
    framework_names = (
        {path.name for path in frameworks.iterdir()} if frameworks.is_dir() else set()
    )
    if "PyQt5" not in framework_names:
        errors.append("missing bundled PyQt5 frameworks")
    qt_network = frameworks / "PyQt5" / "Qt5" / "lib" / "QtNetwork.framework"
    if not qt_network.exists():
        errors.append("missing bundled QtNetwork.framework")
    for module in ("AppKit", "Foundation", "CoreFoundation", "HIServices", "objc", "Quartz"):
        if module not in framework_names:
            errors.append(f"missing bundled PyObjC module: {module}")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate source/bundle release metadata without modifying Git.",
    )
    parser.add_argument("--version", help="Expected version, with or without a leading v")
    parser.add_argument("--bundle", type=Path, help="Validate a built .app bundle")
    parser.add_argument("--require-clean", action="store_true")
    tag_group = parser.add_mutually_exclusive_group()
    tag_group.add_argument("--require-tag", action="store_true")
    tag_group.add_argument("--require-new-tag", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    version, errors = validate_source(
        args.version,
        require_clean=args.require_clean,
        require_tag=args.require_tag,
        require_new_tag=args.require_new_tag,
    )
    if args.bundle:
        errors.extend(validate_bundle(args.bundle.resolve(), version))
    if errors:
        print("Release preflight failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    scope = f" and bundle {args.bundle}" if args.bundle else ""
    print(f"Release preflight passed for {version}{scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
