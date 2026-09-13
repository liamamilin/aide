"""
SQLite 持久化存储：对话记录
"""
import json
import logging
import os
import shutil
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


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
            shutil.copy2(str(dev_path), str(prod_path))
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


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db() -> None:
    db = _conn()
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
    db.commit()
    if _db_uses_app_data_root():
        collect_attachment_garbage()


def _ensure_column(db, table: str, column: str, definition: str) -> None:
    """若列不存在则 ALTER TABLE 添加（SQLite 迁移，幂等）。"""
    cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db.commit()


@dataclass
class Message:
    role: str  # user / assistant
    content: str
    id: int = 0
    created_at: float = 0.0
    images: List[str] = field(default_factory=list)
    missing_images: List[str] = field(default_factory=list)


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
        "SELECT id, title, agent_id, created_at FROM conversations ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        Conversation(id=r["id"], agent_id=r["agent_id"], title=r["title"], created_at=r["created_at"])
        for r in rows
    ]


def list_conversations_with_counts(limit: int = 50) -> list[dict]:
    """列出对话（含消息数），用于历史浏览"""
    db = _conn()
    rows = db.execute(
        """
        SELECT c.id, c.title, c.agent_id, c.created_at,
               (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS msg_count
        FROM conversations c
        ORDER BY c.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_conversations(query: str, limit: int = 50) -> list[dict]:
    """全文搜索对话标题和消息内容"""
    db = _conn()
    pattern = f"%{query}%"
    rows = db.execute(
        """
        SELECT c.id, c.title, c.agent_id, c.created_at,
               (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) AS msg_count
        FROM conversations c
        WHERE c.title LIKE ?
           OR EXISTS (SELECT 1 FROM messages WHERE conversation_id = c.id AND content LIKE ?)
        ORDER BY c.created_at DESC
        LIMIT ?
        """,
        (pattern, pattern, limit),
    ).fetchall()
    return [dict(r) for r in rows]


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
