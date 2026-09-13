"""Explicit SQLite schema migration, backup, and rollback tests."""

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from ai_desktop.utils import storage


def _use_database(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(storage, "DB_PATH", path)
    monkeypatch.setattr(storage, "_local", threading.local())


def _legacy_database(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE conversations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT '', "
        "agent_id TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    db.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, "
        "role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO conversations VALUES (1, '旧对话', 'custom-agent', 10.0)")
    db.execute("INSERT INTO messages VALUES (1, 1, 'user', '旧正文', 11.0)")
    db.execute(
        "INSERT INTO settings VALUES ('custom_agents', ?)",
        (json.dumps([{"id": "custom-agent", "name": "旧 Agent"}]),),
    )
    db.execute("INSERT INTO settings VALUES ('bad_setting', '{broken')")
    db.commit()
    return db


def test_legacy_database_gets_consistent_backup_and_idempotent_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "chat_history.db"
    writer = _legacy_database(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO messages VALUES (2, 1, 'assistant', 'WAL 中已提交', 12.0)")
    writer.commit()
    _use_database(monkeypatch, path)

    storage.init_db()
    assert storage._schema_version(storage._conn()) == storage.SCHEMA_VERSION
    conversation = storage.get_conversation(1)
    assert conversation is not None
    assert [message.content for message in conversation.messages] == ["旧正文", "WAL 中已提交"]
    assert storage.get_setting("bad_setting") == "{broken"
    assert json.loads(storage.get_setting("custom_agents"))[0]["name"] == "旧 Agent"

    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
    assert backup.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    backup.close()

    storage.init_db()
    assert list((tmp_path / "backups").glob("*.sqlite3")) == backups
    writer.close()


def test_migration_failure_rolls_back_schema_and_preserves_backup(tmp_path, monkeypatch):
    path = tmp_path / "chat_history.db"
    writer = _legacy_database(path)
    writer.close()
    _use_database(monkeypatch, path)

    def failing_migration(db):
        db.execute("ALTER TABLE messages ADD COLUMN transient TEXT")
        raise RuntimeError("injected migration failure")

    monkeypatch.setitem(storage._MIGRATIONS, 0, failing_migration)
    with pytest.raises(RuntimeError, match="injected"):
        storage.init_db()

    db = storage._conn()
    assert storage._schema_version(db) == 0
    columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    assert "transient" not in columns
    assert db.execute("SELECT content FROM messages").fetchone()[0] == "旧正文"
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1


def test_upgrade_preserves_existing_and_missing_legacy_images(tmp_path, monkeypatch):
    data_dir = tmp_path / "app-data"
    data_dir.mkdir()
    path = data_dir / "chat_history.db"
    writer = _legacy_database(path)
    writer.execute("ALTER TABLE messages ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
    legacy_dir = data_dir / "images"
    legacy_dir.mkdir()
    existing = legacy_dir / "existing.png"
    existing.write_bytes(b"legacy image bytes")
    missing = legacy_dir / "missing.png"
    writer.execute(
        "UPDATE messages SET images=? WHERE id=1",
        (json.dumps([str(existing), str(missing)]),),
    )
    writer.commit()
    writer.close()
    monkeypatch.setenv("AIDE_DATA_DIR", str(data_dir))
    _use_database(monkeypatch, path)

    storage.init_db()
    message = storage.get_conversation(1).messages[0]
    assert message.images == [str(existing)]
    assert message.missing_images == [str(missing)]
    records = storage.list_attachments()
    assert [record["relative_path"] for record in records] == [
        "images/existing.png",
        "images/missing.png",
    ]
    backup = sqlite3.connect(next((data_dir / "backups").glob("*.sqlite3")))
    assert json.loads(backup.execute("SELECT images FROM messages").fetchone()[0]) == [
        str(existing),
        str(missing),
    ]
    backup.close()


def test_newer_schema_is_rejected_without_modification(tmp_path, monkeypatch):
    path = tmp_path / "chat_history.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE future_data (value TEXT)")
    db.execute("INSERT INTO future_data VALUES ('keep')")
    db.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
    db.commit()
    db.close()
    _use_database(monkeypatch, path)

    with pytest.raises(storage.UnsupportedSchemaVersionError, match="高于"):
        storage.init_db()
    check = sqlite3.connect(path)
    assert check.execute("SELECT value FROM future_data").fetchone()[0] == "keep"
    assert not (tmp_path / "backups").exists()
    check.close()


def test_sqlite_copy_includes_committed_wal_rows(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE sample (value TEXT)")
    writer.execute("INSERT INTO sample VALUES ('committed')")
    writer.commit()

    storage._copy_sqlite_database(source, destination)
    copied = sqlite3.connect(destination)
    assert copied.execute("SELECT value FROM sample").fetchone()[0] == "committed"
    assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copied.close()
    writer.close()


def test_restore_uses_selected_backup_and_keeps_current_safety_copy(tmp_path, monkeypatch):
    path = tmp_path / "chat_history.db"
    writer = _legacy_database(path)
    writer.close()
    _use_database(monkeypatch, path)
    storage.init_db()
    original_backup = next((tmp_path / "backups").glob("*.sqlite3"))
    storage.create_conversation("new-agent", "升级后的记录")

    safety = storage.restore_database_backup(original_backup)
    assert safety is not None and safety.is_file()
    restored = sqlite3.connect(path)
    assert restored.execute("PRAGMA user_version").fetchone()[0] == 0
    assert restored.execute("SELECT title FROM conversations").fetchall() == [("旧对话",)]
    restored.close()
    preserved = sqlite3.connect(safety)
    assert preserved.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
    assert preserved.execute(
        "SELECT COUNT(*) FROM conversations WHERE title='升级后的记录'"
    ).fetchone()[0] == 1
    preserved.close()
