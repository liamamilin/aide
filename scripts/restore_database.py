#!/usr/bin/env python3
"""Restore an AI Desktop Assistant SQLite backup while the app is stopped."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore a schema backup. Stop AI Desktop Assistant first.",
    )
    parser.add_argument("backup", type=Path, help="Backup .sqlite3 file to restore")
    parser.add_argument(
        "--database",
        type=Path,
        help="Destination database (defaults to the app data database)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from ai_desktop.utils import storage

    args = build_parser().parse_args(argv)
    if args.database is not None:
        storage.close_db()
        storage.DB_PATH = args.database.expanduser().resolve()
    safety = storage.restore_database_backup(args.backup)
    print(f"Restored {args.backup.resolve()} to {storage.DB_PATH.resolve()}")
    if safety is not None:
        print(f"Current database safety backup: {safety}")
    print("Use the application version that matches the restored schema before starting it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
