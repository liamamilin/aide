"""
SQLite 持久化存储：对话记录
"""
import json
import logging
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
        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, created_at)
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
    db.commit()


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
    db.execute("DELETE FROM conversations WHERE id=?", (convo_id,))
    db.commit()


def save_message(convo_id: int, role: str, content: str, images: Optional[List[str]] = None) -> Message:
    db = _conn()
    now = time.time()
    images_json = json.dumps(images or [], ensure_ascii=False)
    cur = db.execute(
        "INSERT INTO messages (conversation_id, role, content, images, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (convo_id, role, content, images_json, now),
    )

    # 第一条用户消息作为对话标题
    if role == "user":
        count = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (convo_id,)).fetchone()[0]
        if count == 1:
            title = (content or "[图片]")[:40].replace("\n", " ")
            db.execute("UPDATE conversations SET title=? WHERE id=?", (title, convo_id))

    db.commit()
    return Message(id=cur.lastrowid, role=role, content=content, created_at=now, images=images or [])


def _load_messages(convo_id: int) -> list[Message]:
    db = _conn()
    rows = db.execute(
        "SELECT id, role, content, created_at, images FROM messages WHERE conversation_id=? ORDER BY id ASC",
        (convo_id,),
    ).fetchall()
    return [
        Message(
            id=r["id"],
            role=r["role"],
            content=r["content"],
            created_at=r["created_at"],
            images=_parse_images(r["images"]),
        )
        for r in rows
    ]


def _parse_images(raw) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(p) for p in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


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
