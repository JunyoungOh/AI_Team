"""AI Engineering ResourceGuard — concurrent session limits and port allocation.

Enforces:
- Max 5 concurrent IMPLEMENT sessions (ports drawn from pool 20000-20099)
- Max 15 total Engineering sessions
- Max 1 active session per named user
- Max 10 daily sessions per named user
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass

# ── Session / concurrency limits ───────────────────────

MAX_IMPLEMENT_SESSIONS: int = 5
MAX_TOTAL_SESSIONS: int = 15
MAX_SESSIONS_PER_USER: int = 1
MAX_DAILY_SESSIONS_PER_USER: int = 10

# ── Port pool ──────────────────────────────────────────

PORT_RANGE_START: int = 20000
PORT_RANGE_END: int = 20099

# ── Resource / runtime constants ───────────────────────

WORKSPACE_MAX_MB: int = 500
COMMAND_TIMEOUT_S: int = 120
SESSION_IDLE_TIMEOUT_S: int = 1800
RECONNECT_GRACE_S: int = 600
DEV_SERVER_TIMEOUT_S: int = 900
MAX_API_CALLS_PER_SESSION: int = 100
MAX_TOKENS_PER_SESSION: int = 2_000_000


# ── Data model ─────────────────────────────────────────


@dataclass
class ImplementSlot:
    """A reserved IMPLEMENT execution slot with an exclusive dev-server port."""

    session_id: str
    port: int


# ── ResourceGuard ──────────────────────────────────────


class ResourceGuard:
    """Thread-safe guard for Engineering-mode resource limits.

    Usage::

        guard = ResourceGuard()

        # On session start
        if not guard.can_start_session():
            raise RuntimeError("Server at capacity")
        if not guard.can_user_start(user_id):
            raise RuntimeError("User already has an active session")
        if not guard.check_daily_limit(user_id):
            raise RuntimeError("Daily session limit reached")

        guard.register_session(session_id, user_id=user_id)
        guard._record_daily_session(user_id)

        # For IMPLEMENT mode
        slot = guard.acquire_implement_slot(session_id)
        if slot is None:
            raise RuntimeError("No IMPLEMENT slot available")

        # … run the engineering session …

        # On session end
        guard.release_slot(session_id)
        guard.unregister_session(session_id)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # IMPLEMENT slots: session_id → ImplementSlot
        self._implement_slots: dict[str, ImplementSlot] = {}

        # Available ports (ordered so released ports are re-used first)
        self._available_ports: list[int] = list(
            range(PORT_RANGE_START, PORT_RANGE_END + 1)
        )

        # Total active Engineering sessions: session_id → user_id (or "")
        self._active_sessions: dict[str, str] = {}

        # Daily session counters: user_id → count (resets externally / per-process)
        self._daily_counts: dict[str, int] = defaultdict(int)

    # ── IMPLEMENT slot management ──────────────────────

    def acquire_implement_slot(self, session_id: str) -> ImplementSlot | None:
        """Reserve an IMPLEMENT slot for *session_id*.

        Returns an :class:`ImplementSlot` with a unique port on success, or
        ``None`` when the concurrency limit is reached or *session_id* already
        holds a slot.
        """
        with self._lock:
            if session_id in self._implement_slots:
                return None
            if len(self._implement_slots) >= MAX_IMPLEMENT_SESSIONS:
                return None
            if not self._available_ports:
                return None

            port = self._available_ports.pop(0)
            slot = ImplementSlot(session_id=session_id, port=port)
            self._implement_slots[session_id] = slot
            return slot

    def release_slot(self, session_id: str) -> None:
        """Release the IMPLEMENT slot held by *session_id*.

        Idempotent — does not raise if *session_id* has no slot.
        """
        with self._lock:
            slot = self._implement_slots.pop(session_id, None)
            if slot is not None:
                # Return the port to the front of the pool so it is re-used next
                self._available_ports.insert(0, slot.port)

    # ── Total session management ───────────────────────

    def can_start_session(self) -> bool:
        """Return ``True`` if the server has capacity for another Engineering session."""
        with self._lock:
            return len(self._active_sessions) < MAX_TOTAL_SESSIONS

    def register_session(self, session_id: str, user_id: str = "") -> None:
        """Record an active Engineering session.

        Call this after all guard checks pass so the slot is immediately
        reflected in subsequent :meth:`can_start_session` / :meth:`can_user_start`
        calls from concurrent threads.
        """
        with self._lock:
            self._active_sessions[session_id] = user_id

    def unregister_session(self, session_id: str) -> None:
        """Remove *session_id* from the active-session registry.

        Idempotent — does not raise if *session_id* is unknown.
        """
        with self._lock:
            self._active_sessions.pop(session_id, None)

    # ── Per-user session guard ─────────────────────────

    def can_user_start(self, user_id: str) -> bool:
        """Return ``True`` when *user_id* may open another Engineering session.

        Anonymous sessions (``user_id == ""``) are always allowed — they do
        not count toward named-user per-session limits.
        """
        if not user_id:
            return True
        with self._lock:
            active_for_user = sum(
                1 for uid in self._active_sessions.values() if uid == user_id
            )
            return active_for_user < MAX_SESSIONS_PER_USER

    # ── Daily limit ────────────────────────────────────

    def check_daily_limit(self, user_id: str) -> bool:
        """Return ``True`` when *user_id* has not yet reached today's session cap.

        Anonymous sessions (``user_id == ""``) are always allowed.
        """
        if not user_id:
            return True
        with self._lock:
            return self._daily_counts[user_id] < MAX_DAILY_SESSIONS_PER_USER

    def _record_daily_session(self, user_id: str) -> None:
        """Increment *user_id*'s daily session counter.

        Call this once per successfully started session.  For anonymous
        sessions (``user_id == ""``) this is a no-op.
        """
        if not user_id:
            return
        with self._lock:
            self._daily_counts[user_id] += 1
