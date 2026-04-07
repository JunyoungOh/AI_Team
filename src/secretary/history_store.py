"""Chat history persistence — 3-tier retention (Hot/Warm/Cold).

Storage layout:
    data/secretary/
        index.json              # session summaries (Cold tier, permanent)
        sessions/
            sec_<id>/
                history.json    # full transcript (Warm tier, 7 days)
                meta.json       # session metadata

Tiers:
    Hot   — in-memory (ChatEngine.history), current session
    Warm  — JSON file, full transcript, retained 7 days
    Cold  — 1-line summary per session in index.json, permanent
    Delete — full transcript removed after 30 days
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_BASE_DIR = Path(__file__).parents[2] / "data" / "secretary"
_DEFAULT_PREFIX = "sec_"

# Legacy aliases for backward-compat (module-level references in run_retention_cleanup)
_BASE_DIR = _DEFAULT_BASE_DIR
_SESSIONS_DIR = _DEFAULT_BASE_DIR / "sessions"
_INDEX_PATH = _DEFAULT_BASE_DIR / "index.json"

# Retention thresholds
_WARM_DAYS = 7
_DELETE_DAYS = 30


class HistoryStore:
    """JSON file-based chat history persistence per session.

    When user_id is provided, data is stored under users/<user_id>/sessions/
    for per-user isolation. Without user_id, falls back to global sessions/.
    """

    def __init__(
        self,
        session_id: str,
        user_id: str = "",
        base_dir: Path | str | None = None,
        prefix: str | None = None,
    ):
        self._session_id = session_id
        self._user_id = user_id
        self._base_dir = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        self._prefix = prefix if prefix is not None else _DEFAULT_PREFIX

        # When user_id is set, store under users/<user_id>/sessions/ for isolation.
        if user_id:
            base = self._base_dir / "users" / user_id / "sessions"
        else:
            base = self._base_dir / "sessions"

        self._session_dir = base / f"{self._prefix}{session_id}"
        self._history_path = self._session_dir / "history.json"
        self._meta_path = self._session_dir / "meta.json"
        self._session_dir.mkdir(parents=True, exist_ok=True)

    def save(self, messages: list[dict]) -> None:
        """Save full transcript to JSON file.

        Args:
            messages: list of {"role", "content", "timestamp", "message_id"}
        """
        self._history_path.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._update_meta(len(messages))

    def load(self) -> list[dict]:
        """Load transcript from JSON file. Returns [] if not found."""
        if not self._history_path.exists():
            return []
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("history_load_error: %s", e)
            return []

    def _update_meta(self, message_count: int) -> None:
        """Update session metadata."""
        meta = self._load_meta()
        meta["session_id"] = self._session_id
        meta["last_active"] = time.time()
        meta["last_active_iso"] = datetime.now().isoformat()
        meta["message_count"] = message_count
        if "created_at" not in meta:
            meta["created_at"] = time.time()
        self._meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_meta(self) -> dict:
        if not self._meta_path.exists():
            return {}
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def get_meta(self) -> dict:
        return self._load_meta()

    # ─── Static methods for index & cleanup ───

    @staticmethod
    def list_sessions(user_id: str = "", base_dir: Path | str | None = None) -> list[dict]:
        """Load session index (Cold tier summaries)."""
        idx = HistoryStore._index_path(user_id, base_dir=base_dir)
        if not idx.exists():
            return []
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @staticmethod
    def _save_index(entries: list[dict], user_id: str = "", base_dir: Path | str | None = None) -> None:
        idx = HistoryStore._index_path(user_id, base_dir=base_dir)
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _index_path(user_id: str = "", base_dir: Path | str | None = None) -> Path:
        bd = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        if user_id:
            return bd / "users" / user_id / "index.json"
        return bd / "index.json"

    @staticmethod
    def _sessions_dir(user_id: str = "", base_dir: Path | str | None = None) -> Path:
        bd = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        if user_id:
            return bd / "users" / user_id / "sessions"
        return bd / "sessions"

    @staticmethod
    def find_recent_session(
        max_age_hours: int = 4,
        user_id: str = "",
        base_dir: Path | str | None = None,
        prefix: str | None = None,
    ) -> str | None:
        """Find the most recent session within max_age_hours.

        When user_id is provided, only searches that user's sessions directory.
        Returns session_id if found, None otherwise.
        """
        bd = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        pfx = prefix if prefix is not None else _DEFAULT_PREFIX

        if user_id:
            search_dir = bd / "users" / user_id / "sessions"
        else:
            search_dir = bd / "sessions"

        if not search_dir.exists():
            return None

        best_id = None
        best_time = 0.0
        cutoff = time.time() - (max_age_hours * 3600)

        for session_dir in search_dir.iterdir():
            if not session_dir.is_dir():
                continue
            meta_path = session_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                last_active = meta.get("last_active", 0)
                if last_active > cutoff and last_active > best_time:
                    best_time = last_active
                    best_id = meta.get("session_id", "")
            except Exception:
                continue

        return best_id if best_id else None

    @staticmethod
    def run_retention_cleanup(
        base_dir: Path | str | None = None,
        prefix: str | None = None,
    ) -> dict:
        """Apply retention policy on startup.

        Cleans both legacy global sessions/ and per-user users/*/sessions/.
        Returns summary: {"archived": N, "deleted": N}
        """
        bd = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
        pfx = prefix if prefix is not None else _DEFAULT_PREFIX
        sessions_dir = bd / "sessions"

        total_archived = 0
        total_deleted = 0

        # Collect all (sessions_dir, user_id) pairs to clean
        targets: list[tuple[Path, str]] = []
        if sessions_dir.exists():
            targets.append((sessions_dir, ""))
        users_dir = bd / "users"
        if users_dir.exists():
            for user_dir in users_dir.iterdir():
                if user_dir.is_dir():
                    sess_dir = user_dir / "sessions"
                    if sess_dir.exists():
                        targets.append((sess_dir, user_dir.name))

        now = time.time()
        warm_cutoff = now - (_WARM_DAYS * 86400)
        delete_cutoff = now - (_DELETE_DAYS * 86400)

        for sess_dir, user_id in targets:
            index = HistoryStore.list_sessions(user_id, base_dir=bd)
            index_ids = {e["session_id"] for e in index}
            archived = 0

            for session_dir in sess_dir.iterdir():
                if not session_dir.is_dir():
                    continue

                meta_path = session_dir / "meta.json"
                history_path = session_dir / "history.json"

                if not meta_path.exists():
                    continue

                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue

                last_active = meta.get("last_active", 0)
                session_id = meta.get("session_id", session_dir.name.replace(pfx, "", 1))

                if last_active < delete_cutoff:
                    if history_path.exists():
                        history_path.unlink()
                        total_deleted += 1
                    continue

                if last_active < warm_cutoff and session_id not in index_ids:
                    summary = meta.get("summary", "")
                    if not summary and history_path.exists():
                        try:
                            msgs = json.loads(history_path.read_text(encoding="utf-8"))
                            topics = [m["content"][:60] for m in msgs[:6] if m.get("role") == "user"]
                            summary = " / ".join(topics[:3]) if topics else "대화 기록"
                        except Exception:
                            summary = "대화 기록"

                    index.append({
                        "session_id": session_id,
                        "created_at": meta.get("created_at", last_active),
                        "last_active": last_active,
                        "last_active_iso": meta.get("last_active_iso", ""),
                        "message_count": meta.get("message_count", 0),
                        "summary": summary,
                    })
                    index_ids.add(session_id)
                    archived += 1

            if archived > 0:
                HistoryStore._save_index(index, user_id, base_dir=bd)
                total_archived += archived

        if total_archived or total_deleted:
            logger.info("retention_cleanup: archived=%d, deleted=%d", total_archived, total_deleted)

        return {"archived": total_archived, "deleted": total_deleted}
