"""User model and SQLite persistence for membership system.

Tables:
    users — account info (id, username, password_hash, status, role, etc.)

Status lifecycle:
    pending → approved (by admin) → disabled (by admin)
                      → rejected (by admin)

Role:
    admin — first registrant auto-promoted; can manage users
    user  — regular user (default)
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.config.settings import get_settings


def _delete_report_folder(session_id: str) -> bool:
    """Remove ``data/reports/disc_<session_id>/`` from disk.

    The DB stores ``file_path`` as a URL-style string (``/reports/...``)
    that does not include the ``data/`` prefix where files actually live,
    so we reconstruct the on-disk path from the configured
    ``report_output_dir`` setting and the session_id. Best-effort: any
    failure is swallowed because deletion is non-critical (cleanup will
    retry on the next pass).
    """
    import shutil
    if not session_id:
        return False
    try:
        base = Path(get_settings().report_output_dir)
        folder = base / f"disc_{session_id}"
        if folder.exists() and folder.is_dir() and folder.name.startswith("disc_"):
            shutil.rmtree(folder)
            return True
    except Exception:
        pass
    return False


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    display_name: str
    status: str  # pending | approved | rejected | disabled
    created_at: str
    role: str = "user"  # user | admin
    approved_at: str | None = None
    last_login: str | None = None
    company_name: str | None = None
    ceo_name: str | None = None
    secretary_character: str | None = None
    visible_modes: str | None = None  # JSON array, e.g. '["company","secretary"]'. null = all visible

    @property
    def is_active(self) -> bool:
        return self.status == "approved"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def get_visible_modes_list(self) -> list[str] | None:
        """Parse visible_modes JSON. Returns list or None (=all visible)."""
        if not self.visible_modes:
            return None
        import json
        try:
            return json.loads(self.visible_modes)
        except (json.JSONDecodeError, TypeError):
            return None

    def to_public_dict(self) -> dict:
        """Return user info safe to send to the client (no password_hash)."""
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "status": self.status,
            "role": self.role,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "last_login": self.last_login,
            "company_name": self.company_name,
            "ceo_name": self.ceo_name,
            "secretary_character": self.secretary_character,
            "visible_modes": self.get_visible_modes_list(),
        }


class UserDB:
    """Thread-safe SQLite user store (singleton)."""

    _instance: UserDB | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        db_path = get_settings().user_db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db()

    @classmethod
    def get(cls) -> UserDB:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = UserDB()
        return cls._instance

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id          TEXT PRIMARY KEY,
                    username    TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    role        TEXT NOT NULL DEFAULT 'user',
                    created_at  TEXT NOT NULL,
                    approved_at TEXT,
                    last_login  TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            # Migration: add role column to existing DBs
            try:
                conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            except sqlite3.OperationalError:
                pass  # column already exists
            # Migration: add company_name column to existing DBs
            try:
                conn.execute("ALTER TABLE users ADD COLUMN company_name TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            # Migration: add ceo_name column to existing DBs
            try:
                conn.execute("ALTER TABLE users ADD COLUMN ceo_name TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            # Migration: add secretary_character column to existing DBs
            try:
                conn.execute("ALTER TABLE users ADD COLUMN secretary_character TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            # Migration: add visible_modes column (JSON array, null = all modes visible)
            try:
                conn.execute("ALTER TABLE users ADD COLUMN visible_modes TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            # Discussion reports table (24h retention)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discussion_reports (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    topic       TEXT NOT NULL,
                    participants TEXT NOT NULL,
                    style       TEXT NOT NULL DEFAULT 'free',
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL,
                    file_path   TEXT NOT NULL
                )
            """)

    # ── CRUD ──────────────────────────────────────

    def create_user(self, username: str, password_hash: str, display_name: str,
                    *, role: str = "user", status: str = "pending") -> User:
        now = datetime.now().isoformat()
        approved_at = now if status == "approved" else None
        user = User(
            id=str(uuid.uuid4()),
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            status=status,
            role=role,
            created_at=now,
            approved_at=approved_at,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, status, role, created_at, approved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user.id, user.username, user.password_hash, user.display_name,
                 user.status, user.role, user.created_at, user.approved_at),
            )
        return user

    def get_by_username(self, username: str) -> User | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_id(self, user_id: str) -> User | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def list_all(self) -> list[User]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [self._row_to_user(r) for r in rows]

    def list_pending(self) -> list[User]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def has_any_admin(self) -> bool:
        """Check if at least one admin user exists."""
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        return row is not None

    def update_status(self, user_id: str, status: str) -> bool:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            extra = ", approved_at = ?" if status == "approved" else ""
            params: tuple = (status, now, user_id) if status == "approved" else (status, user_id)
            sql = f"UPDATE users SET status = ?{extra} WHERE id = ?"
            cursor = conn.execute(sql, params)
        return cursor.rowcount > 0

    def update_role(self, user_id: str, role: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        return cursor.rowcount > 0

    def update_last_login(self, user_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (datetime.now().isoformat(), user_id),
            )

    def update_password(self, user_id: str, password_hash: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id),
            )
        return cursor.rowcount > 0

    def update_company_name(self, user_id: str, company_name: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET company_name = ? WHERE id = ?",
                (company_name, user_id),
            )
        return cursor.rowcount > 0

    def update_ceo_name(self, user_id: str, ceo_name: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET ceo_name = ? WHERE id = ?",
                (ceo_name, user_id),
            )
        return cursor.rowcount > 0

    def update_secretary_character(self, user_id: str, character_json: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET secretary_character = ? WHERE id = ?",
                (character_json, user_id),
            )

    def update_visible_modes(self, user_id: str, modes_json: str | None) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET visible_modes = ? WHERE id = ?",
                (modes_json, user_id),
            )
        return cursor.rowcount > 0

    def get_secretary_character(self, user_id: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT secretary_character FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row["secretary_character"] if row else None

    def delete_user(self, user_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cursor.rowcount > 0

    def username_exists(self, username: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        return row is not None

    # ── Config (key-value store) ────────────────

    def get_config(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ── Discussion Reports ────────────────────────

    # 1-week retention: long enough for users to find recent reports,
    # short enough to prevent unbounded disk growth.
    REPORT_RETENTION_DAYS = 7

    def save_discussion_report(
        self, session_id: str, user_id: str, topic: str,
        participants: list[str], style: str, file_path: str,
    ) -> None:
        import json
        from datetime import timedelta
        now = datetime.now()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO discussion_reports "
                "(id, user_id, topic, participants, style, created_at, expires_at, file_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, user_id or "anonymous", topic,
                 json.dumps(participants, ensure_ascii=False), style,
                 now.isoformat(),
                 (now + timedelta(days=self.REPORT_RETENTION_DAYS)).isoformat(),
                 file_path),
            )

    def list_discussion_reports(self, user_id: str) -> list[dict]:
        now = datetime.now().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, topic, participants, style, created_at, file_path "
                "FROM discussion_reports "
                "WHERE user_id = ? AND expires_at > ? "
                "ORDER BY created_at DESC",
                (user_id, now),
            ).fetchall()
        import json
        return [
            {
                "id": r["id"], "topic": r["topic"],
                "participants": json.loads(r["participants"]),
                "style": r["style"], "created_at": r["created_at"],
                "file_path": r["file_path"],
            }
            for r in rows
        ]

    def delete_discussion_report(self, report_id: str, user_id: str) -> bool:
        """Delete a discussion report's DB row AND its on-disk folder.

        Why both: the history viewer's GET endpoint reconciles orphans by
        scanning data/reports/disc_*/ and re-inserting any folder it finds.
        If we delete only the DB row, that reconciliation immediately
        restores the row from the still-present folder, making the user's
        delete look like a no-op. Removing the folder closes the loop.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM discussion_reports WHERE id = ? AND user_id = ?",
                (report_id, user_id),
            )
        if cur.rowcount > 0:
            _delete_report_folder(report_id)
        return cur.rowcount > 0

    def cleanup_expired_reports(self) -> int:
        """Delete expired reports (DB rows + on-disk folders).

        Reuses :func:`_delete_report_folder` so the path-resolution rules
        stay in one place — the URL-style ``file_path`` stored in the DB
        is NOT a usable filesystem path, so we always reconstruct from
        ``settings.report_output_dir + session_id``.
        """
        now = datetime.now().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM discussion_reports WHERE expires_at <= ?",
                (now,),
            ).fetchall()
            if rows:
                conn.execute(
                    "DELETE FROM discussion_reports WHERE expires_at <= ?",
                    (now,),
                )
        for r in rows:
            _delete_report_folder(r["id"])
        return len(rows)

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            display_name=row["display_name"],
            status=row["status"],
            role=row["role"] if "role" in row.keys() else "user",
            created_at=row["created_at"],
            approved_at=row["approved_at"],
            last_login=row["last_login"],
            company_name=row["company_name"] if "company_name" in row.keys() else None,
            ceo_name=row["ceo_name"] if "ceo_name" in row.keys() else None,
            secretary_character=row["secretary_character"] if "secretary_character" in row.keys() else None,
            visible_modes=row["visible_modes"] if "visible_modes" in row.keys() else None,
        )
