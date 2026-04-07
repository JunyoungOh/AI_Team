"""Tests for AI Engineering ResourceGuard — concurrent session limits and port allocation."""

from __future__ import annotations

import threading

import pytest

from src.engineering.resource_guard import (
    MAX_DAILY_SESSIONS_PER_USER,
    MAX_IMPLEMENT_SESSIONS,
    MAX_SESSIONS_PER_USER,
    MAX_TOTAL_SESSIONS,
    PORT_RANGE_END,
    PORT_RANGE_START,
    ImplementSlot,
    ResourceGuard,
)


# ── ImplementSlot ──────────────────────────────────────


class TestImplementSlot:
    def test_has_session_id(self):
        slot = ImplementSlot(session_id="sess_001", port=20000)
        assert slot.session_id == "sess_001"

    def test_has_port(self):
        slot = ImplementSlot(session_id="sess_001", port=20042)
        assert slot.port == 20042


# ── acquire_implement_slot ─────────────────────────────


class TestAcquireImplementSlot:
    def test_acquire_implement_slot_success(self):
        guard = ResourceGuard()
        slot = guard.acquire_implement_slot("sess_001")
        assert slot is not None
        assert isinstance(slot, ImplementSlot)
        assert slot.session_id == "sess_001"
        assert PORT_RANGE_START <= slot.port <= PORT_RANGE_END

    def test_acquire_implement_slot_returns_unique_ports(self):
        guard = ResourceGuard()
        slots = [guard.acquire_implement_slot(f"sess_{i:03d}") for i in range(3)]
        ports = [s.port for s in slots if s is not None]
        assert len(ports) == 3
        assert len(set(ports)) == 3, "All acquired ports must be unique"

    def test_acquire_implement_slot_fails_at_limit(self):
        guard = ResourceGuard()
        # Acquire up to the max (5)
        slots = [
            guard.acquire_implement_slot(f"sess_{i:03d}")
            for i in range(MAX_IMPLEMENT_SESSIONS)
        ]
        assert all(s is not None for s in slots), "Should succeed for first 5 slots"
        # The 6th acquisition should fail
        extra = guard.acquire_implement_slot("sess_overflow")
        assert extra is None

    def test_acquire_duplicate_session_id_fails(self):
        """Acquiring a slot for an already-active session_id should return None."""
        guard = ResourceGuard()
        first = guard.acquire_implement_slot("sess_dup")
        assert first is not None
        second = guard.acquire_implement_slot("sess_dup")
        assert second is None

    def test_acquire_returns_port_in_valid_range(self):
        guard = ResourceGuard()
        slot = guard.acquire_implement_slot("sess_range")
        assert slot is not None
        assert PORT_RANGE_START <= slot.port <= PORT_RANGE_END


# ── release_slot ───────────────────────────────────────


class TestReleaseSlot:
    def test_release_slot_frees_port(self):
        guard = ResourceGuard()
        # Fill all slots
        sessions = [f"sess_{i:03d}" for i in range(MAX_IMPLEMENT_SESSIONS)]
        slots = [guard.acquire_implement_slot(s) for s in sessions]
        assert all(s is not None for s in slots)

        # Release one slot — the port should be available again
        released_port = slots[0].port
        guard.release_slot(sessions[0])

        # Should now be able to acquire a new slot
        new_slot = guard.acquire_implement_slot("sess_new")
        assert new_slot is not None
        assert new_slot.port == released_port

    def test_release_nonexistent_slot_does_not_raise(self):
        guard = ResourceGuard()
        guard.release_slot("nonexistent_session")  # must not raise

    def test_release_slot_decrements_active_count(self):
        guard = ResourceGuard()
        guard.acquire_implement_slot("sess_dec")
        assert len(guard._implement_slots) == 1
        guard.release_slot("sess_dec")
        assert len(guard._implement_slots) == 0


# ── can_start_session / register_session / unregister_session ──


class TestTotalSessionLimit:
    def test_can_start_session_initially_true(self):
        guard = ResourceGuard()
        assert guard.can_start_session() is True

    def test_check_total_session_limit(self):
        guard = ResourceGuard()
        # Register up to the max
        for i in range(MAX_TOTAL_SESSIONS):
            guard.register_session(f"sess_{i:03d}")
        assert guard.can_start_session() is False

    def test_can_start_session_after_unregister(self):
        guard = ResourceGuard()
        sessions = [f"sess_{i:03d}" for i in range(MAX_TOTAL_SESSIONS)]
        for s in sessions:
            guard.register_session(s)
        assert guard.can_start_session() is False
        guard.unregister_session(sessions[0])
        assert guard.can_start_session() is True

    def test_unregister_nonexistent_does_not_raise(self):
        guard = ResourceGuard()
        guard.unregister_session("does_not_exist")  # must not raise


# ── can_user_start ─────────────────────────────────────


class TestUserSessionLimit:
    def test_can_user_start_initially_true(self):
        guard = ResourceGuard()
        assert guard.can_user_start("user_alice") is True

    def test_check_user_session_limit(self):
        guard = ResourceGuard()
        guard.register_session("sess_001", user_id="user_alice")
        # Second session for same user should be denied (max 1 per user)
        assert guard.can_user_start("user_alice") is False

    def test_different_users_can_start_concurrently(self):
        guard = ResourceGuard()
        guard.register_session("sess_alice", user_id="user_alice")
        assert guard.can_user_start("user_bob") is True

    def test_can_user_start_after_session_ends(self):
        guard = ResourceGuard()
        guard.register_session("sess_alice", user_id="user_alice")
        assert guard.can_user_start("user_alice") is False
        guard.unregister_session("sess_alice")
        assert guard.can_user_start("user_alice") is True

    def test_empty_user_id_always_allowed(self):
        """Sessions without a user_id (anonymous) must not block each other."""
        guard = ResourceGuard()
        guard.register_session("sess_anon1", user_id="")
        guard.register_session("sess_anon2", user_id="")
        # Anonymous sessions don't consume per-user slots
        assert guard.can_user_start("") is True


# ── check_daily_limit ──────────────────────────────────


class TestDailyLimit:
    def test_check_daily_limit_initially_true(self):
        guard = ResourceGuard()
        assert guard.check_daily_limit("user_alice") is True

    def test_check_user_daily_limit(self):
        guard = ResourceGuard()
        for i in range(MAX_DAILY_SESSIONS_PER_USER):
            guard._record_daily_session("user_alice")
        assert guard.check_daily_limit("user_alice") is False

    def test_daily_limit_different_users_independent(self):
        guard = ResourceGuard()
        for i in range(MAX_DAILY_SESSIONS_PER_USER):
            guard._record_daily_session("user_alice")
        assert guard.check_daily_limit("user_alice") is False
        assert guard.check_daily_limit("user_bob") is True

    def test_daily_limit_one_below_max_still_allowed(self):
        guard = ResourceGuard()
        for i in range(MAX_DAILY_SESSIONS_PER_USER - 1):
            guard._record_daily_session("user_alice")
        assert guard.check_daily_limit("user_alice") is True


# ── Thread-safety ──────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_acquire_does_not_exceed_limit(self):
        guard = ResourceGuard()
        results: list[ImplementSlot | None] = []
        lock = threading.Lock()

        def try_acquire(session_id: str) -> None:
            slot = guard.acquire_implement_slot(session_id)
            with lock:
                results.append(slot)

        threads = [
            threading.Thread(target=try_acquire, args=(f"t_sess_{i:03d}",))
            for i in range(MAX_IMPLEMENT_SESSIONS * 2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successful = [r for r in results if r is not None]
        assert len(successful) <= MAX_IMPLEMENT_SESSIONS, (
            "Thread-safe acquire must never exceed the concurrency limit"
        )

    def test_concurrent_acquire_ports_are_unique(self):
        guard = ResourceGuard()
        slots: list[ImplementSlot] = []
        lock = threading.Lock()

        def try_acquire(session_id: str) -> None:
            slot = guard.acquire_implement_slot(session_id)
            if slot is not None:
                with lock:
                    slots.append(slot)

        threads = [
            threading.Thread(target=try_acquire, args=(f"p_sess_{i:03d}",))
            for i in range(MAX_IMPLEMENT_SESSIONS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ports = [s.port for s in slots]
        assert len(ports) == len(set(ports)), "No two concurrent slots may share a port"


# ── Constants ──────────────────────────────────────────


class TestConstants:
    def test_max_implement_sessions(self):
        assert MAX_IMPLEMENT_SESSIONS == 5

    def test_max_total_sessions(self):
        assert MAX_TOTAL_SESSIONS == 15

    def test_max_sessions_per_user(self):
        assert MAX_SESSIONS_PER_USER == 1

    def test_max_daily_sessions_per_user(self):
        assert MAX_DAILY_SESSIONS_PER_USER == 10

    def test_port_range_start(self):
        assert PORT_RANGE_START == 20000

    def test_port_range_end(self):
        assert PORT_RANGE_END == 20099

    def test_port_pool_size_matches_implement_limit(self):
        pool_size = PORT_RANGE_END - PORT_RANGE_START + 1
        assert pool_size >= MAX_IMPLEMENT_SESSIONS

    def test_resource_constants_exist(self):
        from src.engineering.resource_guard import (
            COMMAND_TIMEOUT_S,
            DEV_SERVER_TIMEOUT_S,
            MAX_API_CALLS_PER_SESSION,
            MAX_TOKENS_PER_SESSION,
            RECONNECT_GRACE_S,
            SESSION_IDLE_TIMEOUT_S,
            WORKSPACE_MAX_MB,
        )
        assert WORKSPACE_MAX_MB == 500
        assert COMMAND_TIMEOUT_S == 120
        assert SESSION_IDLE_TIMEOUT_S == 1800
        assert RECONNECT_GRACE_S == 600
        assert DEV_SERVER_TIMEOUT_S == 900
        assert MAX_API_CALLS_PER_SESSION == 100
        assert MAX_TOKENS_PER_SESSION == 2_000_000
