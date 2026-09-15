"""
SQLite 持久化存储：对话记录
"""
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4


class UnsupportedSchemaVersionError(RuntimeError):
    """Raised when a database was created by a newer application version."""


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    """Copy a SQLite database, including committed WAL content, through backup()."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{time.time_ns()}.tmp")
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    source_db = sqlite3.connect(source_uri, uri=True)
    target_db = sqlite3.connect(str(temporary))
    try:
        source_db.backup(target_db)
        if target_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("SQLite backup integrity check failed")
        target_db.close()
        source_db.close()
        os.replace(temporary, destination)
    except Exception:
        target_db.close()
        source_db.close()
        temporary.unlink(missing_ok=True)
        raise


def _resolve_db_path() -> Path:
    """Resolve database path: use Application Support dir, with dev-mode fallback"""
    data_override = os.environ.get("AIDE_DATA_DIR")
    if data_override:
        data_dir = Path(data_override).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "chat_history.db"

    dev_path = Path(__file__).resolve().parent.parent.parent / "chat_history.db"

    # Production: use ~/Library/Application Support/ai-desktop-assistant/
    if sys.platform == "darwin":
        app_support = Path.home() / "Library" / "Application Support" / "ai-desktop-assistant"
    else:
        app_support = Path.home() / ".local" / "share" / "ai-desktop-assistant"

    app_support.mkdir(parents=True, exist_ok=True)
    prod_path = app_support / "chat_history.db"

    # Migrate from old dev path if it exists and prod path doesn't yet
    if dev_path.exists() and not prod_path.exists():
        try:
            _copy_sqlite_database(dev_path, prod_path)
            logger.info("Migrated DB from %s to %s", dev_path, prod_path)
        except Exception:
            logger.exception("Failed to migrate DB, falling back to dev path")
            return dev_path

    # Use prod path if it exists (or was just migrated);
    # fall back to dev path only if prod doesn't exist and migration failed
    if prod_path.exists():
        return prod_path

    # Dev-mode fallback: neither prod nor dev DB exists yet
    # (first launch, no data to migrate — use prod path going forward)
    return prod_path


DB_PATH = _resolve_db_path()
_local = threading.local()
_attachment_lock = threading.RLock()
_active_attachment_uses: dict[str, int] = {}
MAX_CONVERSATION_TITLE_LENGTH = 80
ConversationCursor = tuple[float, int]


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def _schema_version(db: sqlite3.Connection) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def _has_existing_schema(db: sqlite3.Connection) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _schema_backup_path(source_version: int, target_version: int) -> Path:
    directory = DB_PATH.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return directory / (
        f"{DB_PATH.stem}.schema-{source_version}-to-{target_version}."
        f"{stamp}-{time.time_ns() % 1_000_000_000:09d}.sqlite3"
    )


def _create_schema_backup(
    db: sqlite3.Connection,
    source_version: int,
    target_version: int,
) -> Path:
    path = _schema_backup_path(source_version, target_version)
    destination = sqlite3.connect(str(path))
    try:
        db.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("SQLite schema backup integrity check failed")
    except Exception:
        destination.close()
        path.unlink(missing_ok=True)
        raise
    destination.close()
    logger.info("Created schema %d backup at %s", source_version, path)
    return path


def _database_version(path: Path) -> int:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError(f"数据库完整性检查失败：{path}")
        return _schema_version(db)
    finally:
        db.close()


def close_db() -> None:
    """Close this thread's cached connection before replacing its database file."""
    connection = getattr(_local, "conn", None)
    if connection is not None:
        connection.close()
        _local.conn = None


def restore_database_backup(backup_path: str | Path) -> Path | None:
    """Restore an explicit SQLite backup and retain a safety copy of the current DB.

    The application must be stopped before this function is called. A restored older
    schema is intended for its matching older application; starting this version again
    will migrate it forward.
    """
    source = Path(backup_path).expanduser().resolve()
    destination = DB_PATH.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"备份文件不存在：{source}")
    if source == destination:
        raise ValueError("备份文件不能与当前数据库相同。")
    backup_version = _database_version(source)
    if backup_version > SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"备份 schema {backup_version} 高于当前工具支持的 {SCHEMA_VERSION}。"
        )

    safety_backup = None
    if destination.is_file():
        directory = destination.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safety_backup = directory / (
            f"{destination.stem}.before-restore.{stamp}-"
            f"{time.time_ns() % 1_000_000_000:09d}.sqlite3"
        )
        _copy_sqlite_database(destination, safety_backup)

    close_db()
    for suffix in ("-wal", "-shm"):
        Path(f"{destination}{suffix}").unlink(missing_ok=True)
    _copy_sqlite_database(source, destination)
    logger.info("Restored schema %d backup %s to %s", backup_version, source, destination)
    return safety_backup


def _migrate_v0_to_v1(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL DEFAULT '',
            agent_id    TEXT    NOT NULL,
            created_at  REAL    NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role            TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
            content         TEXT    NOT NULL,
            created_at      REAL    NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
        """
    )
    # 图片理解：用户消息可附带图片（存储为应用数据目录下的绝对路径列表）
    _ensure_column(db, "messages", "images", "TEXT NOT NULL DEFAULT '[]'")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            relative_path   TEXT NOT NULL UNIQUE,
            inference_path  TEXT NOT NULL DEFAULT '',
            original_name   TEXT NOT NULL DEFAULT '',
            mime_type       TEXT NOT NULL DEFAULT '',
            byte_size       INTEGER NOT NULL DEFAULT 0,
            width           INTEGER NOT NULL DEFAULT 0,
            height          INTEGER NOT NULL DEFAULT 0,
            sha256          TEXT NOT NULL DEFAULT '',
            state           TEXT NOT NULL DEFAULT 'staged'
                            CHECK(state IN ('staged', 'referenced', 'pending_gc')),
            created_at      REAL NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS message_attachments (
            message_id      INTEGER NOT NULL,
            attachment_id   INTEGER NOT NULL,
            position        INTEGER NOT NULL,
            PRIMARY KEY (message_id, attachment_id),
            UNIQUE (message_id, position),
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (attachment_id) REFERENCES attachments(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conversations_created
            ON conversations(created_at DESC, id DESC)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, created_at)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_attachments_attachment
            ON message_attachments(attachment_id)
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    _migrate_legacy_attachments(db)


def _migrate_v1_to_v2(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS model_profiles (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            model       TEXT NOT NULL DEFAULT '',
            options     TEXT NOT NULL DEFAULT '{}',
            updated_at  REAL NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_profile_assignments (
            agent_id    TEXT PRIMARY KEY,
            profile_id  TEXT,
            FOREIGN KEY (profile_id) REFERENCES model_profiles(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_profile_assignments_profile "
        "ON agent_profile_assignments(profile_id)"
    )


def _migrate_v2_to_v3(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS actions (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            agent_id        TEXT NOT NULL,
            profile_id      TEXT,
            input_types     TEXT NOT NULL DEFAULT '["text"]',
            instruction     TEXT NOT NULL,
            pinned_order    INTEGER,
            enabled         INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            updated_at      REAL NOT NULL,
            FOREIGN KEY (profile_id) REFERENCES model_profiles(id) ON DELETE SET NULL,
            CHECK(pinned_order IS NULL OR pinned_order BETWEEN 0 AND 3)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_actions_pinned "
        "ON actions(enabled, pinned_order, id)"
    )


def _migrate_v3_to_v4(db: sqlite3.Connection) -> None:
    """Add answer generations while preserving existing assistant messages."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS generations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_message_id     INTEGER NOT NULL,
            assistant_message_id INTEGER,
            request_id          TEXT NOT NULL UNIQUE,
            config_snapshot     TEXT NOT NULL DEFAULT '{}',
            status              TEXT NOT NULL DEFAULT 'pending'
                                CHECK(status IN ('pending', 'streaming', 'succeeded', 'failed', 'cancelled')),
            answer              TEXT NOT NULL DEFAULT '',
            active              INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
            created_at          REAL NOT NULL,
            FOREIGN KEY (user_message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (assistant_message_id) REFERENCES messages(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_generations_user_message "
        "ON generations(user_message_id, created_at, id)"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_generations_one_active "
        "ON generations(user_message_id) WHERE active=1"
    )

    # Legacy conversations had one assistant row per user turn. Convert those
    # rows into successful default generations without changing message order.
    last_user_by_conversation: dict[int, int] = {}
    rows = db.execute(
        "SELECT id, conversation_id, role, content, created_at "
        "FROM messages ORDER BY conversation_id, id"
    ).fetchall()
    for row in rows:
        conversation_id = int(row["conversation_id"])
        if row["role"] == "user":
            last_user_by_conversation[conversation_id] = int(row["id"])
            continue
        if row["role"] != "assistant":
            continue
        user_message_id = last_user_by_conversation.get(conversation_id)
        if user_message_id is None:
            continue
        already = db.execute(
            "SELECT 1 FROM generations WHERE assistant_message_id=?",
            (row["id"],),
        ).fetchone()
        if already is not None:
            continue
        has_active = db.execute(
            "SELECT 1 FROM generations WHERE user_message_id=? AND active=1",
            (user_message_id,),
        ).fetchone() is not None
        db.execute(
            """
            INSERT INTO generations (
                user_message_id, assistant_message_id, request_id,
                config_snapshot, status, answer, active, created_at
            ) VALUES (?, ?, ?, '{}', 'succeeded', ?, ?, ?)
            """,
            (
                user_message_id,
                row["id"],
                f"legacy-message-{row['id']}",
                row["content"],
                0 if has_active else 1,
                row["created_at"],
            ),
        )


_MIGRATIONS = {
    0: _migrate_v0_to_v1,
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
}


def init_db() -> None:
    db = _conn()
    current = _schema_version(db)
    if current > SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"数据库 schema {current} 高于当前应用支持的 {SCHEMA_VERSION}；"
            "请使用匹配版本的应用，或恢复升级前备份。"
        )
    if current < SCHEMA_VERSION:
        db.commit()
        if _has_existing_schema(db):
            _create_schema_backup(db, current, SCHEMA_VERSION)
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                while current < SCHEMA_VERSION:
                    migration = _MIGRATIONS.get(current)
                    if migration is None:
                        raise RuntimeError(f"缺少从 schema {current} 开始的迁移。")
                    migration(db)
                    current += 1
                    db.execute(f"PRAGMA user_version={current}")
                db.commit()
            except Exception:
                db.rollback()
                raise
        except Exception:
            logger.exception("Database migration to schema %d failed", SCHEMA_VERSION)
            raise
    with db:
        _migrate_legacy_attachments(db)
    if _db_uses_app_data_root():
        collect_attachment_garbage()


def _ensure_column(db, table: str, column: str, definition: str) -> None:
    """若列不存在则 ALTER TABLE 添加（SQLite 迁移，幂等）。"""
    cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@dataclass
class Message:
    role: str  # user / assistant
    content: str
    id: int = 0
    created_at: float = 0.0
    images: List[str] = field(default_factory=list)
    missing_images: List[str] = field(default_factory=list)


@dataclass
class Generation:
    id: int
    user_message_id: int
    request_id: str
    config_snapshot: dict = field(default_factory=dict)
    status: str = "pending"
    answer: str = ""
    active: bool = False
    created_at: float = 0.0
    assistant_message_id: int | None = None


@dataclass
class Conversation:
    id: int
    agent_id: str
    title: str = ""
    created_at: float = 0.0
    messages: list[Message] = field(default_factory=list)


# ── CRUD ──────────────────────────────────────────────


def create_conversation(agent_id: str, title: str = "") -> Conversation:
    db = _conn()
    now = time.time()
    cur = db.execute(
        "INSERT INTO conversations (title, agent_id, created_at) VALUES (?, ?, ?)",
        (title, agent_id, now),
    )
    db.commit()
    return Conversation(id=cur.lastrowid, agent_id=agent_id, title=title, created_at=now)


def list_conversations(limit: int = 50) -> list[Conversation]:
    db = _conn()
    rows = db.execute(
        """
        SELECT id, title, agent_id, created_at
        FROM conversations
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        Conversation(id=r["id"], agent_id=r["agent_id"], title=r["title"], created_at=r["created_at"])
        for r in rows
    ]


def page_conversations(
    *,
    limit: int = 50,
    cursor: ConversationCursor | None = None,
    query: str = "",
) -> tuple[list[dict], ConversationCursor | None]:
    """Return one stable newest-first page using a (created_at, id) cursor."""
    if limit < 1 or limit > 100:
        raise ValueError("分页大小必须在 1 到 100 之间。")
    db = _conn()
    conditions: list[str] = []
    params: list[object] = []
    normalized_query = query.strip()
    if normalized_query:
        conditions.append(
            """
            (c.title LIKE ? OR EXISTS (
                SELECT 1 FROM messages sm
                WHERE sm.conversation_id = c.id AND sm.content LIKE ?
            ))
            """
        )
        pattern = f"%{normalized_query}%"
        params.extend((pattern, pattern))
    if cursor is not None:
        created_at, conversation_id = cursor
        conditions.append("(c.created_at < ? OR (c.created_at = ? AND c.id < ?))")
        params.extend((created_at, created_at, conversation_id))
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit + 1)
    rows = db.execute(
        f"""
        SELECT c.id, c.title, c.agent_id, c.created_at,
               (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS msg_count
        FROM conversations c
        {where}
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    has_more = len(rows) > limit
    page = [dict(row) for row in rows[:limit]]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = (last["created_at"], last["id"])
    return page, next_cursor


def list_conversations_with_counts(limit: int = 50) -> list[dict]:
    """Compatibility wrapper returning the first history page."""
    return page_conversations(limit=limit)[0]


def search_conversations(query: str, limit: int = 50) -> list[dict]:
    """Compatibility wrapper returning the first matching history page."""
    return page_conversations(limit=limit, query=query)[0]


def update_conversation_title(convo_id: int, title: str) -> str:
    """Validate and persist a user-edited history title."""
    normalized = str(title).strip()
    if not normalized:
        raise ValueError("对话标题不能为空。")
    if len(normalized) > MAX_CONVERSATION_TITLE_LENGTH:
        raise ValueError(
            f"对话标题不能超过 {MAX_CONVERSATION_TITLE_LENGTH} 个字符。"
        )
    db = _conn()
    cur = db.execute(
        "UPDATE conversations SET title=? WHERE id=?",
        (normalized, convo_id),
    )
    if cur.rowcount == 0:
        db.rollback()
        raise LookupError("对话不存在或已被删除。")
    db.commit()
    return normalized


def get_conversation(convo_id: int) -> Optional[Conversation]:
    db = _conn()
    row = db.execute("SELECT id, title, agent_id, created_at FROM conversations WHERE id=?", (convo_id,)).fetchone()
    if row is None:
        return None
    conv = Conversation(id=row["id"], agent_id=row["agent_id"], title=row["title"], created_at=row["created_at"])
    conv.messages = _load_messages(convo_id)
    return conv


def delete_conversation(convo_id: int) -> None:
    db = _conn()
    attachment_ids = [
        row[0]
        for row in db.execute(
            """
            SELECT DISTINCT ma.attachment_id
            FROM message_attachments ma
            JOIN messages m ON m.id = ma.message_id
            WHERE m.conversation_id=?
            """,
            (convo_id,),
        ).fetchall()
    ]
    with _attachment_lock:
        db.execute("DELETE FROM conversations WHERE id=?", (convo_id,))
        db.commit()
        _update_or_collect_attachments(db, attachment_ids)


def delete_message(message_id: int, *, preserve_attachments: bool = False) -> None:
    """Delete one message, used to roll back a submission that could not start."""
    db = _conn()
    attachment_ids = [
        row[0]
        for row in db.execute(
            "SELECT attachment_id FROM message_attachments WHERE message_id=?",
            (message_id,),
        ).fetchall()
    ]
    row = db.execute("SELECT conversation_id FROM messages WHERE id=?", (message_id,)).fetchone()
    with _attachment_lock:
        db.execute("DELETE FROM messages WHERE id=?", (message_id,))
        if row is not None:
            remaining = db.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                (row["conversation_id"],),
            ).fetchone()[0]
            if remaining == 0:
                db.execute(
                    "UPDATE conversations SET title='' WHERE id=?",
                    (row["conversation_id"],),
                )
        db.commit()
        _update_or_collect_attachments(
            db,
            attachment_ids,
            preserve=preserve_attachments,
        )


def save_message(convo_id: int, role: str, content: str, images: Optional[List[str]] = None) -> Message:
    db = _conn()
    now = time.time()
    image_paths = list(dict.fromkeys(str(path) for path in (images or []) if path))
    from ai_desktop.utils.images import MAX_ATTACHMENTS_PER_MESSAGE

    if len(image_paths) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError(f"每条消息最多添加 {MAX_ATTACHMENTS_PER_MESSAGE} 张图片。")

    images_json = json.dumps(image_paths, ensure_ascii=False)
    with _attachment_lock:
        try:
            cur = db.execute(
                "INSERT INTO messages (conversation_id, role, content, images, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (convo_id, role, content, images_json, now),
            )
            for position, path in enumerate(image_paths):
                attachment_id = _ensure_attachment(db, path, now)
                if attachment_id is None:
                    continue
                db.execute(
                    """
                    INSERT OR IGNORE INTO message_attachments
                        (message_id, attachment_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (cur.lastrowid, attachment_id, position),
                )
                db.execute(
                    "UPDATE attachments SET state='referenced' WHERE id=?",
                    (attachment_id,),
                )

            # 第一条用户消息作为对话标题
            if role == "user":
                count = db.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                    (convo_id,),
                ).fetchone()[0]
                if count == 1:
                    title = (content or "[图片]")[:40].replace("\n", " ")
                    db.execute("UPDATE conversations SET title=? WHERE id=?", (title, convo_id))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return Message(
        id=cur.lastrowid,
        role=role,
        content=content,
        created_at=now,
        images=image_paths,
    )


# ── 答案版本 ──────────────────────────────────────────

_GENERATION_STATUSES = frozenset(
    {"pending", "streaming", "succeeded", "failed", "cancelled"}
)


def _generation_from_row(row: sqlite3.Row) -> Generation:
    try:
        snapshot = json.loads(row["config_snapshot"] or "{}")
    except (TypeError, json.JSONDecodeError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return Generation(
        id=int(row["id"]),
        user_message_id=int(row["user_message_id"]),
        assistant_message_id=(
            int(row["assistant_message_id"])
            if row["assistant_message_id"] is not None
            else None
        ),
        request_id=str(row["request_id"]),
        config_snapshot=snapshot,
        status=str(row["status"]),
        answer=str(row["answer"]),
        active=bool(row["active"]),
        created_at=float(row["created_at"]),
    )


def save_generation(
    user_message_id: int,
    request_id: str,
    *,
    config_snapshot: dict | None = None,
    status: str = "pending",
    answer: str = "",
    assistant_message_id: int | None = None,
    active: bool = False,
    created_at: float | None = None,
) -> Generation:
    """Persist one answer attempt for a user message.

    Activating a generation is transactional and clears the previous active
    version first. Failed or cancelled attempts remain queryable but inactive.
    """
    normalized_request_id = str(request_id).strip()
    if not normalized_request_id:
        raise ValueError("generation request_id 不能为空。")
    normalized_status = str(status).strip().lower()
    if normalized_status not in _GENERATION_STATUSES:
        raise ValueError(f"无效的 generation 状态：{status}")
    if active and (normalized_status != "succeeded" or not str(answer)):
        raise ValueError("只有成功且有内容的答案版本可以设为当前版本。")
    snapshot = config_snapshot if isinstance(config_snapshot, dict) else {}
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    now = time.time() if created_at is None else float(created_at)
    db = _conn()
    try:
        db.execute("BEGIN IMMEDIATE")
        if active:
            db.execute(
                "UPDATE generations SET active=0 WHERE user_message_id=? AND active=1",
                (user_message_id,),
            )
        cur = db.execute(
            """
            INSERT INTO generations (
                user_message_id, assistant_message_id, request_id,
                config_snapshot, status, answer, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_message_id,
                assistant_message_id,
                normalized_request_id,
                snapshot_json,
                normalized_status,
                str(answer),
                int(bool(active)),
                now,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    row = db.execute("SELECT * FROM generations WHERE id=?", (cur.lastrowid,)).fetchone()
    return _generation_from_row(row)


def list_generations(user_message_id: int) -> list[Generation]:
    rows = _conn().execute(
        "SELECT * FROM generations WHERE user_message_id=? ORDER BY created_at, id",
        (user_message_id,),
    ).fetchall()
    return [_generation_from_row(row) for row in rows]


def get_active_generation(user_message_id: int) -> Generation | None:
    row = _conn().execute(
        "SELECT * FROM generations WHERE user_message_id=? AND active=1",
        (user_message_id,),
    ).fetchone()
    return _generation_from_row(row) if row is not None else None


def get_generation(generation_id: int) -> Generation:
    """Return one stored answer version without changing the active row."""
    row = _conn().execute(
        "SELECT * FROM generations WHERE id=?",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise LookupError("答案版本不存在或已被删除。")
    if row["status"] != "succeeded" or not str(row["answer"]):
        raise ValueError("只有成功且有内容的答案版本可以设为当前版本。")
    return _generation_from_row(row)


def set_active_generation(generation_id: int) -> Generation:
    """Select one successful answer version and return the selected row."""
    db = _conn()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM generations WHERE id=?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise LookupError("答案版本不存在或已被删除。")
        if row["status"] != "succeeded" or not str(row["answer"]):
            raise ValueError("只有成功且有内容的答案版本可以设为当前版本。")
        db.execute(
            "UPDATE generations SET active=0 WHERE user_message_id=?",
            (row["user_message_id"],),
        )
        db.execute("UPDATE generations SET active=1 WHERE id=?", (generation_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    selected = db.execute(
        "SELECT * FROM generations WHERE id=?", (generation_id,)
    ).fetchone()
    return _generation_from_row(selected)


def _load_messages(convo_id: int) -> list[Message]:
    db = _conn()
    rows = db.execute(
        "SELECT id, role, content, created_at, images FROM messages WHERE conversation_id=? ORDER BY id ASC",
        (convo_id,),
    ).fetchall()
    messages = []
    for row in rows:
        images, missing = _message_attachment_paths(
            row["id"],
            _parse_images(row["images"]),
        )
        messages.append(
            Message(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
                images=images,
                missing_images=missing,
            )
        )
    return messages


def _parse_images(raw) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(p) for p in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ── 附件生命周期 ──────────────────────────────────────


def _db_uses_app_data_root() -> bool:
    """Only scan files when this database belongs to the active data root."""
    from ai_desktop.utils import images as image_utils

    try:
        return DB_PATH.resolve().parent == image_utils._app_support_dir().resolve()
    except OSError:
        return False


def _attachment_metadata(path: str) -> dict:
    """Read metadata for migration while tolerating already-missing legacy files."""
    from ai_desktop.utils import images as image_utils

    try:
        info = image_utils.inspect_image(path, enforce_limits=False)
        return {
            "original_name": info.original_name,
            "mime_type": info.mime_type,
            "byte_size": info.byte_size,
            "width": info.width,
            "height": info.height,
            "sha256": info.sha256,
        }
    except Exception:
        logger.debug("Could not inspect legacy attachment %s", path, exc_info=True)
        return {
            "original_name": Path(path).name,
            "mime_type": "",
            "byte_size": 0,
            "width": 0,
            "height": 0,
            "sha256": "",
        }


def _ensure_attachment(db: sqlite3.Connection, path: str, created_at: float) -> int | None:
    """Return the record for a managed path; external user files stay untracked."""
    from ai_desktop.utils import images as image_utils

    relative_path = image_utils.managed_relative_path(path)
    if relative_path is None:
        return None
    row = db.execute(
        "SELECT id FROM attachments WHERE relative_path=?",
        (relative_path,),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    resolved = image_utils.resolve_managed_path(relative_path)
    metadata = _attachment_metadata(str(resolved or path))
    cur = db.execute(
        """
        INSERT INTO attachments (
            relative_path, original_name, mime_type, byte_size,
            width, height, sha256, state, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'staged', ?)
        """,
        (
            relative_path,
            metadata["original_name"],
            metadata["mime_type"],
            metadata["byte_size"],
            metadata["width"],
            metadata["height"],
            metadata["sha256"],
            created_at,
        ),
    )
    return int(cur.lastrowid)


def _migrate_legacy_attachments(db: sqlite3.Connection) -> None:
    """Backfill reference rows from messages.images without moving originals."""
    rows = db.execute("SELECT id, images, created_at FROM messages ORDER BY id").fetchall()
    for row in rows:
        for position, path in enumerate(dict.fromkeys(_parse_images(row["images"]))):
            attachment_id = _ensure_attachment(db, path, row["created_at"])
            if attachment_id is None:
                continue
            db.execute(
                """
                INSERT OR IGNORE INTO message_attachments
                    (message_id, attachment_id, position)
                VALUES (?, ?, ?)
                """,
                (row["id"], attachment_id, position),
            )
            db.execute(
                "UPDATE attachments SET state='referenced' WHERE id=?",
                (attachment_id,),
            )


def _message_attachment_paths(
    message_id: int, legacy_paths: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve managed references and classify unavailable history images."""
    from ai_desktop.utils import images as image_utils

    db = _conn()
    rows = db.execute(
        """
        SELECT a.relative_path
        FROM message_attachments ma
        JOIN attachments a ON a.id = ma.attachment_id
        WHERE ma.message_id=?
        ORDER BY ma.position
        """,
        (message_id,),
    ).fetchall()
    existing: list[str] = []
    missing: list[str] = []
    represented: set[str] = set()

    def add(path: str) -> None:
        target = Path(path)
        collection = existing if target.is_file() else missing
        if path not in existing and path not in missing:
            collection.append(path)

    for row in rows:
        relative_path = row["relative_path"]
        represented.add(relative_path)
        resolved = image_utils.resolve_managed_path(relative_path)
        add(str(resolved) if resolved is not None else relative_path)

    for path in legacy_paths:
        relative_path = image_utils.managed_relative_path(path)
        if relative_path is not None and relative_path in represented:
            continue
        add(path)
    return existing, missing


def _delete_attachment_record(db: sqlite3.Connection, attachment_id: int) -> bool:
    """Delete an attachment only after checking that no message still owns it."""
    from ai_desktop.utils import images as image_utils

    count = db.execute(
        "SELECT COUNT(*) FROM message_attachments WHERE attachment_id=?",
        (attachment_id,),
    ).fetchone()[0]
    if count:
        db.execute(
            "UPDATE attachments SET state='referenced' WHERE id=?",
            (attachment_id,),
        )
        db.commit()
        return False

    row = db.execute(
        "SELECT relative_path, inference_path FROM attachments WHERE id=?",
        (attachment_id,),
    ).fetchone()
    if row is None:
        return True
    if _active_attachment_uses.get(row["relative_path"], 0):
        db.execute(
            "UPDATE attachments SET state='pending_gc' WHERE id=?",
            (attachment_id,),
        )
        db.commit()
        return False
    db.execute(
        "UPDATE attachments SET state='pending_gc' WHERE id=?",
        (attachment_id,),
    )
    db.commit()
    paths = [row["relative_path"]]
    if row["inference_path"]:
        paths.append(row["inference_path"])
    deleted = all(image_utils.delete_managed_path(path) for path in paths)
    if deleted:
        db.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
        db.commit()
    return deleted


def _update_or_collect_attachments(
    db: sqlite3.Connection,
    attachment_ids: list[int],
    *,
    preserve: bool = False,
) -> None:
    for attachment_id in dict.fromkeys(attachment_ids):
        count = db.execute(
            "SELECT COUNT(*) FROM message_attachments WHERE attachment_id=?",
            (attachment_id,),
        ).fetchone()[0]
        if count:
            db.execute(
                "UPDATE attachments SET state='referenced' WHERE id=?",
                (attachment_id,),
            )
        elif preserve:
            db.execute(
                "UPDATE attachments SET state='staged' WHERE id=?",
                (attachment_id,),
            )
        else:
            _delete_attachment_record(db, attachment_id)
    db.commit()


def discard_staged_attachment(path: str) -> bool:
    """Remove a draft copy if no persisted message references it."""
    from ai_desktop.utils import images as image_utils

    relative_path = image_utils.managed_relative_path(path)
    if relative_path is None:
        return False
    with _attachment_lock:
        db = _conn()
        row = db.execute(
            "SELECT id FROM attachments WHERE relative_path=?",
            (relative_path,),
        ).fetchone()
        if row is None:
            return image_utils.delete_managed_path(relative_path)
        return _delete_attachment_record(db, int(row["id"]))


def set_attachment_inference_path(original_path: str, inference_path: str) -> None:
    """Register the reusable reduced copy created by the request worker."""
    from ai_desktop.utils import images as image_utils

    original_relative = image_utils.managed_relative_path(original_path)
    inference_relative = image_utils.managed_relative_path(inference_path)
    if original_relative is None or inference_relative is None:
        return
    with _attachment_lock:
        db = _conn()
        db.execute(
            "UPDATE attachments SET inference_path=? WHERE relative_path=?",
            (inference_relative, original_relative),
        )
        db.commit()


def retain_attachment_paths(paths: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Pin managed originals while a request snapshot may still read them."""
    from ai_desktop.utils import images as image_utils

    retained = tuple(
        dict.fromkeys(
            relative
            for path in paths
            if (relative := image_utils.managed_relative_path(path)) is not None
        )
    )
    with _attachment_lock:
        for relative_path in retained:
            _active_attachment_uses[relative_path] = (
                _active_attachment_uses.get(relative_path, 0) + 1
            )
    return retained


def release_attachment_paths(relative_paths: list[str] | tuple[str, ...]) -> None:
    """Release request pins and finish deferred collection when the last use ends."""
    with _attachment_lock:
        released: list[str] = []
        for relative_path in dict.fromkeys(relative_paths):
            count = _active_attachment_uses.get(relative_path, 0)
            if count <= 1:
                _active_attachment_uses.pop(relative_path, None)
                released.append(relative_path)
            else:
                _active_attachment_uses[relative_path] = count - 1
        if not released:
            return
        db = _conn()
        rows = db.execute(
            """
            SELECT id FROM attachments
            WHERE state='pending_gc' AND relative_path IN ({})
            """.format(",".join("?" for _ in released)),
            released,
        ).fetchall()
        for row in rows:
            _delete_attachment_record(db, int(row["id"]))


def collect_attachment_garbage(*, max_age_seconds: float = 24 * 60 * 60) -> int:
    """Retry pending deletions and remove old untracked files at startup."""
    from ai_desktop.utils import images as image_utils

    cutoff = time.time() - max(0, max_age_seconds)
    removed = 0
    with _attachment_lock:
        db = _conn()
        rows = db.execute(
            """
            SELECT a.id
            FROM attachments a
            WHERE a.state='pending_gc'
               OR (a.state='staged' AND a.created_at <= ?
                   AND NOT EXISTS (
                       SELECT 1 FROM message_attachments ma
                       WHERE ma.attachment_id=a.id
                   ))
            """,
            (cutoff,),
        ).fetchall()
        for row in rows:
            if _delete_attachment_record(db, int(row["id"])):
                removed += 1

        known = {
            path
            for row in db.execute(
                "SELECT relative_path, inference_path FROM attachments"
            ).fetchall()
            for path in (row["relative_path"], row["inference_path"])
            if path
        }
        for directory in (image_utils.images_dir(), image_utils.inference_dir()):
            for path in directory.iterdir():
                if not path.is_file():
                    continue
                relative_path = image_utils.managed_relative_path(path)
                try:
                    old_enough = path.stat().st_mtime <= cutoff
                except OSError:
                    continue
                if relative_path and relative_path not in known and old_enough:
                    if image_utils.delete_managed_path(relative_path):
                        removed += 1
    return removed


def list_attachments() -> list[dict]:
    """Return attachment records for diagnostics and lifecycle tests."""
    rows = _conn().execute("SELECT * FROM attachments ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def list_input_history(limit: int = 100) -> list[str]:
    """最近发送过的用户消息（去重、最新在前），用于输入框上下键浏览历史。"""
    db = _conn()
    rows = db.execute(
        """
        SELECT DISTINCT content FROM messages
        WHERE role='user' AND TRIM(content) != ''
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [r["content"] for r in rows]


# ── 设置持久化 ────────────────────────────────────────


def save_setting(key: str, value: str) -> None:
    db = _conn()
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    db.commit()


def get_setting(key: str, default: str = "") -> str:
    db = _conn()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ── 任务模型配置 ─────────────────────────────────────


def list_model_profile_records() -> list[dict]:
    rows = _conn().execute(
        "SELECT id, name, model, options, updated_at FROM model_profiles "
        "ORDER BY name COLLATE NOCASE, id"
    ).fetchall()
    return [dict(row) for row in rows]


def save_model_profile_record(record: dict) -> None:
    db = _conn()
    db.execute(
        """
        INSERT INTO model_profiles (id, name, model, options, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            model=excluded.model,
            options=excluded.options,
            updated_at=excluded.updated_at
        """,
        (
            record["id"],
            record["name"],
            record.get("model", ""),
            record.get("options", "{}"),
            record["updated_at"],
        ),
    )
    db.commit()


def delete_model_profile_record(profile_id: str) -> bool:
    db = _conn()
    cursor = db.execute("DELETE FROM model_profiles WHERE id=?", (profile_id,))
    db.commit()
    return cursor.rowcount > 0


def load_agent_profile_assignments() -> dict[str, str]:
    rows = _conn().execute(
        "SELECT agent_id, profile_id FROM agent_profile_assignments WHERE profile_id IS NOT NULL"
    ).fetchall()
    return {str(row["agent_id"]): str(row["profile_id"]) for row in rows}


def save_agent_profile_assignment(agent_id: str, profile_id: str | None) -> None:
    db = _conn()
    if profile_id is None:
        db.execute("DELETE FROM agent_profile_assignments WHERE agent_id=?", (agent_id,))
    else:
        db.execute(
            "INSERT INTO agent_profile_assignments (agent_id, profile_id) VALUES (?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET profile_id=excluded.profile_id",
            (agent_id, profile_id),
        )
    db.commit()


# ── 快捷动作 ─────────────────────────────────────────


def list_action_records() -> list[dict]:
    rows = _conn().execute(
        "SELECT id, name, agent_id, profile_id, input_types, instruction, "
        "pinned_order, enabled, updated_at FROM actions ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def save_action_record(record: dict) -> None:
    db = _conn()
    db.execute(
        """
        INSERT INTO actions (
            id, name, agent_id, profile_id, input_types, instruction,
            pinned_order, enabled, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            agent_id=excluded.agent_id,
            profile_id=excluded.profile_id,
            input_types=excluded.input_types,
            instruction=excluded.instruction,
            pinned_order=excluded.pinned_order,
            enabled=excluded.enabled,
            updated_at=excluded.updated_at
        """,
        (
            record["id"],
            record["name"],
            record["agent_id"],
            record.get("profile_id"),
            record["input_types"],
            record["instruction"],
            record.get("pinned_order"),
            int(bool(record.get("enabled", True))),
            record["updated_at"],
        ),
    )
    db.commit()


def delete_action_record(action_id: str) -> bool:
    db = _conn()
    cursor = db.execute("DELETE FROM actions WHERE id=?", (action_id,))
    db.commit()
    return cursor.rowcount > 0


# ── 自定义 Agent ──────────────────────────────────────

def load_custom_agents() -> list[dict]:
    """加载自定义 Agent 列表"""
    raw = get_setting("custom_agents", "[]")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def save_custom_agents(agents: list[dict]) -> None:
    """保存自定义 Agent 列表"""
    save_setting("custom_agents", json.dumps(agents, ensure_ascii=False))
