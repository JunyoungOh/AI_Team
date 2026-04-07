"""Persona DB model — CRUD for user personas + preview token cache."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from src.config.settings import get_settings

MAX_PERSONAS_PER_USER = 50
PREVIEW_TOKEN_TTL_MIN = 5


class PersonaDB:
    """Thread-safe SQLite store for personas. Singleton when using default path."""

    _instance: PersonaDB | None = None
    _lock = threading.Lock()

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or get_settings().user_db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def instance(cls) -> PersonaDB:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = PersonaDB()
        return cls._instance

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personas (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    summary TEXT DEFAULT '',
                    persona_text TEXT DEFAULT '',
                    source TEXT DEFAULT 'web',
                    avatar_url TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_personas_user ON personas(user_id)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS persona_previews (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            # Migration: add shared column to existing tables
            try:
                conn.execute("ALTER TABLE personas ADD COLUMN shared INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            conn.execute("""
                CREATE TABLE IF NOT EXISTS persona_usage (
                    user_id TEXT NOT NULL,
                    persona_id TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, persona_id)
                )
            """)

    def create(self, user_id: str, name: str, summary: str,
               persona_text: str, source: str, avatar_url: str = "") -> dict:
        now = datetime.now().isoformat()
        pid = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO personas (id, user_id, name, summary, persona_text, source, avatar_url, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, user_id, name, summary, persona_text, source, avatar_url, now, now),
            )
        return {"id": pid, "name": name, "summary": summary,
                "persona_text": persona_text, "source": source,
                "avatar_url": avatar_url, "shared": 0, "created_at": now, "updated_at": now}

    def get(self, persona_id: str, user_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM personas WHERE id = ? AND user_id = ?",
                (persona_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def get_usable(self, persona_id: str, user_id: str) -> dict | None:
        """Get persona owned by user OR shared by another user."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM personas WHERE id = ? AND (user_id = ? OR shared = 1)",
                (persona_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def list(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, summary, source, avatar_url, shared, created_at "
                "FROM personas WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update(self, persona_id: str, user_id: str, **fields) -> bool:
        if not fields:
            return False
        fields["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [persona_id, user_id]
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE personas SET {sets} WHERE id = ? AND user_id = ?", vals
            )
        return cur.rowcount > 0

    def delete(self, persona_id: str, user_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM personas WHERE id = ? AND user_id = ?",
                (persona_id, user_id),
            )
            if cur.rowcount > 0:
                # Clean up usage records from other users
                conn.execute("DELETE FROM persona_usage WHERE persona_id = ?", (persona_id,))
            return cur.rowcount > 0

    def count(self, user_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM personas WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row["cnt"] if row else 0

    def toggle_share(self, persona_id: str, user_id: str, shared: bool) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE personas SET shared = ? WHERE id = ? AND user_id = ?",
                (1 if shared else 0, persona_id, user_id),
            )
            if cur.rowcount > 0 and not shared:
                # Unsharing: clean up all usage records
                conn.execute("DELETE FROM persona_usage WHERE persona_id = ?", (persona_id,))
        return cur.rowcount > 0

    def list_shared(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT p.id, p.name, p.summary, p.persona_text, p.source, p.avatar_url, p.user_id, p.created_at, "
                "COALESCE(u.display_name, u.username, p.user_id) as owner_name "
                "FROM personas p LEFT JOIN users u ON p.user_id = u.id "
                "WHERE p.shared = 1 AND p.user_id != ? "
                "ORDER BY p.created_at DESC",
                (user_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                usage = conn.execute(
                    "SELECT enabled FROM persona_usage WHERE user_id = ? AND persona_id = ?",
                    (user_id, d["id"]),
                ).fetchone()
                d["used_by_me"] = bool(usage and usage["enabled"]) if usage else False
                result.append(d)
        return result

    def toggle_use(self, user_id: str, persona_id: str, enabled: bool) -> bool:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            # Verify persona is actually shared before allowing use
            if enabled:
                check = conn.execute(
                    "SELECT shared FROM personas WHERE id = ? AND shared = 1",
                    (persona_id,),
                ).fetchone()
                if not check:
                    return False
            if enabled:
                conn.execute(
                    "INSERT OR REPLACE INTO persona_usage (user_id, persona_id, enabled, created_at) "
                    "VALUES (?, ?, 1, ?)",
                    (user_id, persona_id, now),
                )
            else:
                conn.execute(
                    "UPDATE persona_usage SET enabled = 0 WHERE user_id = ? AND persona_id = ?",
                    (user_id, persona_id),
                )
        return True

    def list_usable(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            mine = conn.execute(
                "SELECT id, name, summary, source, avatar_url, created_at "
                "FROM personas WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            mine = [dict(r) | {"mine": True} for r in mine]

            shared = conn.execute(
                "SELECT p.id, p.name, p.summary, p.source, p.avatar_url, p.user_id, p.created_at, "
                "COALESCE(usr.display_name, usr.username, p.user_id) as owner_name "
                "FROM personas p JOIN persona_usage u ON p.id = u.persona_id "
                "LEFT JOIN users usr ON p.user_id = usr.id "
                "WHERE u.user_id = ? AND u.enabled = 1 AND p.shared = 1 AND p.user_id != ? "
                "ORDER BY p.created_at DESC",
                (user_id, user_id),
            ).fetchall()
            shared = [dict(r) | {"mine": False} for r in shared]
        return mine + shared

    def get_usable(self, persona_id: str, user_id: str) -> dict | None:
        """Get persona if owned OR shared+used."""
        own = self.get(persona_id, user_id)
        if own:
            return own
        with self._conn() as conn:
            row = conn.execute(
                "SELECT p.* FROM personas p "
                "JOIN persona_usage u ON p.id = u.persona_id "
                "WHERE p.id = ? AND p.shared = 1 AND u.user_id = ? AND u.enabled = 1",
                (persona_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def store_preview(self, user_id: str, data: dict) -> str:
        token = str(uuid.uuid4())[:12]
        expires = (datetime.now() + timedelta(minutes=PREVIEW_TOKEN_TTL_MIN)).isoformat()
        now = datetime.now().isoformat()
        with self._conn() as conn:
            # Purge expired tokens on each store to prevent unbounded growth
            conn.execute("DELETE FROM persona_previews WHERE expires_at <= ?", (now,))
            conn.execute(
                "INSERT INTO persona_previews (token, user_id, data, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, json.dumps(data, ensure_ascii=False), expires),
            )
        return token

    def consume_preview(self, token: str, user_id: str) -> dict | None:
        """Get and delete preview token (single use)."""
        data = self.get_preview(token, user_id)
        if data:
            with self._conn() as conn:
                conn.execute("DELETE FROM persona_previews WHERE token = ?", (token,))
        return data

    def get_preview(self, token: str, user_id: str) -> dict | None:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM persona_previews WHERE token = ? AND user_id = ? AND expires_at > ?",
                (token, user_id, now),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["data"])
