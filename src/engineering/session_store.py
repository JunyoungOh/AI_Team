"""Engineering session persistence — 7-day technical context retention.

Storage layout:
    data/engineering/
        sessions/
            <session_id>.json   # full context blob

Each context file holds lightweight JSON metadata (stack, decisions, progress)
so users can resume a development session in a later conversation. Actual code
lives in the user's local zip; this store only keeps the *planning* context.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_RETENTION_DAYS = 7
_DEFAULT_BASE_DIR = Path(__file__).parents[2] / "data" / "engineering"


class SessionStore:
    """Flat-file JSON store for engineering session context.

    Each session is stored as a single ``<session_id>.json`` file under
    ``<base_dir>/sessions/``.  No per-user subdirectory is used for storage;
    ``user_id`` is embedded in the JSON and used as a filter in
    :meth:`list_sessions`.
    """

    _RETENTION_DAYS = _RETENTION_DAYS

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        self._sessions_dir = self._base_dir / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    # ── public helpers ──────────────────────────────────────────────────────

    def _session_path(self, session_id: str) -> Path:
        """Return the file path for a given session_id."""
        return self._sessions_dir / f"{session_id}.json"

    # ── core API ────────────────────────────────────────────────────────────

    def save(self, context: dict, user_id: str = "") -> None:
        """Persist *context* to disk, auto-setting ``created_at``, ``expires_at``, and ``user_id``.

        Args:
            context: Arbitrary dict that MUST contain ``session_id``.
            user_id: Owner identifier; stored inside the JSON and used for
                     filtering in :meth:`list_sessions`.

        Raises:
            ValueError: If ``session_id`` is missing from *context*.
        """
        session_id = context.get("session_id")
        if not session_id:
            raise ValueError("context must contain 'session_id'")

        now = time.time()
        blob = dict(context)
        blob.setdefault("created_at", now)
        blob["expires_at"] = blob["created_at"] + self._RETENTION_DAYS * 86400
        blob["user_id"] = user_id

        path = self._session_path(session_id)
        path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("session_store.save: session_id=%s user_id=%s", session_id, user_id)

    def load(self, session_id: str) -> dict | None:
        """Load a session context by *session_id*.

        Returns ``None`` (and deletes the file) if the session does not exist
        or has expired.
        """
        path = self._session_path(session_id)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("session_store.load_error: %s", exc)
            return None

        if self._is_expired(data):
            self._delete(path)
            return None

        return data

    def list_sessions(self, user_id: str = "") -> list[dict]:
        """Return summary dicts for all non-expired sessions.

        Expired files are deleted on the fly.  If *user_id* is given, only
        sessions whose stored ``user_id`` matches are returned.
        """
        results: list[dict] = []

        for path in self._sessions_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("session_store.list_read_error: %s", exc)
                continue

            if self._is_expired(data):
                self._delete(path)
                continue

            if user_id and data.get("user_id", "") != user_id:
                continue

            results.append(self._summary(data))

        return results

    # ── private helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _is_expired(data: dict) -> bool:
        expires_at = data.get("expires_at", 0)
        return time.time() > expires_at

    @staticmethod
    def _delete(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("session_store.delete_error: %s", exc)

    @staticmethod
    def _summary(data: dict) -> dict:
        """Return a lightweight summary dict (avoids returning huge structure lists)."""
        return {
            "session_id": data.get("session_id", ""),
            "project_name": data.get("project_name", ""),
            "stack": data.get("stack", ""),
            "last_phase": data.get("last_phase", ""),
            "created_at": data.get("created_at", 0.0),
            "expires_at": data.get("expires_at", 0.0),
            "user_id": data.get("user_id", ""),
        }
