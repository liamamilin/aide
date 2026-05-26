"""
SQLite 持久化存储：对话记录
"""
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ai_desktop import config

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "chat_history.db"
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
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    db.commit()


@dataclass
class Message:
    role: str  # user / assistant
    content: str
    id: int = 0
    created_at: float = 0.0


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


def save_message(convo_id: int, role: str, content: str) -> Message:
    db = _conn()
    now = time.time()
    cur = db.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (convo_id, role, content, now),
    )

    # 第一条用户消息作为对话标题
    if role == "user":
        count = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (convo_id,)).fetchone()[0]
        if count == 1:
            title = content[:40].replace("\n", " ")
            db.execute("UPDATE conversations SET title=? WHERE id=?", (title, convo_id))

    db.commit()
    return Message(id=cur.lastrowid, role=role, content=content, created_at=now)


def _load_messages(convo_id: int) -> list[Message]:
    db = _conn()
    rows = db.execute(
        "SELECT id, role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id ASC",
        (convo_id,),
    ).fetchall()
    return [Message(id=r["id"], role=r["role"], content=r["content"], created_at=r["created_at"]) for r in rows]


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
