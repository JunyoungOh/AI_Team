"""Tests for src/engineering/session_store.py — TDD."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.engineering.session_store import SessionStore


@pytest.fixture
def tmp_store(tmp_path):
    """SessionStore backed by a temporary directory."""
    return SessionStore(base_dir=tmp_path)


# ─── test_save_and_load_context ────────────────────────────────────────────────

def test_save_and_load_context(tmp_store):
    """save() writes context; load() retrieves it with auto-set timestamps."""
    context = {
        "session_id": "eng_abc",
        "project_name": "Todo App",
        "stack": "React + Vite + TypeScript",
        "structure": ["src/", "src/App.tsx"],
        "decisions": ["Zustand", "Tailwind CSS"],
        "completed_steps": ["init", "components"],
        "remaining_todo": ["API", "deploy"],
        "last_phase": "implement",
    }

    before = time.time()
    tmp_store.save(context, user_id="user_abc")
    after = time.time()

    loaded = tmp_store.load("eng_abc")
    assert loaded is not None

    # Core fields preserved
    assert loaded["session_id"] == "eng_abc"
    assert loaded["project_name"] == "Todo App"
    assert loaded["stack"] == "React + Vite + TypeScript"
    assert loaded["structure"] == ["src/", "src/App.tsx"]
    assert loaded["decisions"] == ["Zustand", "Tailwind CSS"]
    assert loaded["completed_steps"] == ["init", "components"]
    assert loaded["remaining_todo"] == ["API", "deploy"]
    assert loaded["last_phase"] == "implement"
    assert loaded["user_id"] == "user_abc"

    # Auto-set timestamps
    assert before <= loaded["created_at"] <= after
    expected_expires = loaded["created_at"] + 7 * 86400
    assert abs(loaded["expires_at"] - expected_expires) < 1


def test_load_nonexistent_returns_none(tmp_store):
    """load() returns None when session_id does not exist."""
    assert tmp_store.load("nonexistent_session") is None


# ─── test_list_sessions ────────────────────────────────────────────────────────

def test_list_sessions(tmp_store):
    """list_sessions() returns summaries for all saved sessions."""
    ctx_a = {"session_id": "eng_001", "project_name": "Alpha", "stack": "Vue"}
    ctx_b = {"session_id": "eng_002", "project_name": "Beta", "stack": "React"}

    tmp_store.save(ctx_a, user_id="user_x")
    tmp_store.save(ctx_b, user_id="user_x")

    sessions = tmp_store.list_sessions()
    ids = {s["session_id"] for s in sessions}
    assert "eng_001" in ids
    assert "eng_002" in ids
    assert len(sessions) == 2


# ─── test_list_sessions_for_user ───────────────────────────────────────────────

def test_list_sessions_for_user(tmp_store):
    """list_sessions(user_id=...) filters to only that user's sessions."""
    ctx_a = {"session_id": "eng_u1", "project_name": "ForUser1", "stack": "Svelte"}
    ctx_b = {"session_id": "eng_u2", "project_name": "ForUser2", "stack": "Angular"}

    tmp_store.save(ctx_a, user_id="alice")
    tmp_store.save(ctx_b, user_id="bob")

    alice_sessions = tmp_store.list_sessions(user_id="alice")
    assert len(alice_sessions) == 1
    assert alice_sessions[0]["session_id"] == "eng_u1"

    bob_sessions = tmp_store.list_sessions(user_id="bob")
    assert len(bob_sessions) == 1
    assert bob_sessions[0]["session_id"] == "eng_u2"


# ─── test_expired_sessions_excluded ────────────────────────────────────────────

def test_expired_sessions_excluded(tmp_store):
    """list_sessions() auto-cleans expired sessions and excludes them."""
    ctx_valid = {
        "session_id": "eng_valid",
        "project_name": "Live Project",
        "stack": "Next.js",
    }
    ctx_expired = {
        "session_id": "eng_expired",
        "project_name": "Old Project",
        "stack": "jQuery",
    }

    tmp_store.save(ctx_valid, user_id="user_y")
    tmp_store.save(ctx_expired, user_id="user_y")

    # Manually backdate the expired session's expires_at
    expired_path = tmp_store._session_path("eng_expired")
    import json
    data = json.loads(expired_path.read_text(encoding="utf-8"))
    data["expires_at"] = time.time() - 1  # 1 second in the past
    expired_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    sessions = tmp_store.list_sessions(user_id="user_y")
    ids = {s["session_id"] for s in sessions}
    assert "eng_valid" in ids
    assert "eng_expired" not in ids

    # File should be deleted
    assert not expired_path.exists()


def test_load_expired_returns_none_and_deletes(tmp_store):
    """load() returns None for an expired session and removes its file."""
    import json

    ctx = {"session_id": "eng_old", "project_name": "Old", "stack": "Backbone"}
    tmp_store.save(ctx, user_id="user_z")

    expired_path = tmp_store._session_path("eng_old")
    data = json.loads(expired_path.read_text(encoding="utf-8"))
    data["expires_at"] = time.time() - 1
    expired_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    result = tmp_store.load("eng_old")
    assert result is None
    assert not expired_path.exists()
