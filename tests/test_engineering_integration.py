"""Integration tests for AI Engineering mode — component interaction tests.

Tests the full Engineering session lifecycle by exercising multiple components
together. Uses real filesystem operations in /tmp for workspace tests and
mocking only for the WebSocket/Anthropic API layer.

Test classes:
    TestPhaseTransitions         — PhaseEngine full workflow end-to-end
    TestToolExecutorIntegration  — ToolExecutor + FileManager + TerminalRunner together
    TestWorkspaceLifecycle       — WorkspaceManager create/write/zip/cleanup
    TestResourceGuardIntegration — ResourceGuard concurrent limits
    TestSessionStoreIntegration  — SessionStore save/load/list data integrity

Run with:
    python3 -m pytest tests/test_engineering_integration.py -v
"""

from __future__ import annotations

import json
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from src.engineering.phase_engine import PhaseEngine, PlanStep
from src.engineering.resource_guard import (
    MAX_IMPLEMENT_SESSIONS,
    MAX_TOTAL_SESSIONS,
    ResourceGuard,
)
from src.engineering.session_store import SessionStore
from src.engineering.tools.executor import Phase, ToolExecutor
from src.engineering.workspace_manager import WorkspaceManager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_workspace() -> Path:
    """Create an isolated temp directory to use as a workspace."""
    tmp = tempfile.mkdtemp(prefix="eng_integ_")
    return Path(tmp).resolve()


def _make_plan_steps(*descriptions: str, status: str = "pending") -> list[PlanStep]:
    return [PlanStep(id=i, description=d, status=status) for i, d in enumerate(descriptions)]


# ---------------------------------------------------------------------------
# TestPhaseTransitions
# ---------------------------------------------------------------------------


class TestPhaseTransitions:
    """PhaseEngine drives the full BRAINSTORM -> COMPLETE lifecycle."""

    def test_initial_state_is_brainstorm(self):
        engine = PhaseEngine()
        assert engine.current_phase == Phase.BRAINSTORM

    def test_full_phase_lifecycle_with_plan_steps(self):
        """Walk through all phases end-to-end with plan steps properly set."""
        engine = PhaseEngine()

        # BRAINSTORM -> PLAN
        assert engine.can_advance()
        engine.advance()
        assert engine.current_phase == Phase.PLAN

        # PLAN -> IMPLEMENT
        assert engine.can_advance()
        engine.advance()
        assert engine.current_phase == Phase.IMPLEMENT

        # IMPLEMENT: blocked until all steps done
        assert not engine.can_advance()

        steps = _make_plan_steps("Set up project", "Write core logic", "Add tests")
        engine.set_plan_steps(steps)
        assert not engine.can_advance()  # steps are still pending

        # Progressively mark done
        engine.update_step(0, "done")
        assert not engine.can_advance()
        engine.update_step(1, "done")
        assert not engine.can_advance()
        engine.update_step(2, "done")
        assert engine.can_advance()

        # IMPLEMENT -> VERIFY
        engine.advance()
        assert engine.current_phase == Phase.VERIFY

        # VERIFY -> COMPLETE
        assert engine.can_advance()
        engine.advance()
        assert engine.current_phase == Phase.COMPLETE

        # Terminal state
        assert not engine.can_advance()

    def test_plan_summary_tracks_progress(self):
        """Plan summary reflects step status changes in real time."""
        engine = PhaseEngine()
        engine.advance()   # PLAN
        engine.advance()   # IMPLEMENT

        steps = _make_plan_steps("Alpha", "Beta", "Gamma")
        engine.set_plan_steps(steps)

        summary = engine.get_plan_summary()
        assert summary["total"] == 3
        assert summary["pending"] == 3
        assert summary["done"] == 0
        assert summary["in_progress"] == 0

        engine.update_step(0, "in_progress")
        engine.update_step(1, "done")

        summary = engine.get_plan_summary()
        assert summary["in_progress"] == 1
        assert summary["done"] == 1
        assert summary["pending"] == 1

    def test_rewind_from_implement_to_brainstorm(self):
        """Rewinding clears ability to advance back to where we were."""
        engine = PhaseEngine()
        engine.advance()   # PLAN
        engine.advance()   # IMPLEMENT

        engine.rewind(Phase.BRAINSTORM)
        assert engine.current_phase == Phase.BRAINSTORM
        # Now we can advance again
        assert engine.can_advance()

    def test_rewind_and_re_advance(self):
        """After a rewind, advancing again reaches the correct phases."""
        engine = PhaseEngine()
        engine.advance()   # PLAN
        engine.advance()   # IMPLEMENT

        engine.rewind(Phase.PLAN)
        assert engine.current_phase == Phase.PLAN

        engine.advance()   # back to IMPLEMENT
        assert engine.current_phase == Phase.IMPLEMENT

    def test_force_advance_bypasses_incomplete_steps(self):
        """force_advance skips the IMPLEMENT guard when steps are not done."""
        engine = PhaseEngine()
        engine.advance()   # PLAN
        engine.advance()   # IMPLEMENT
        engine.set_plan_steps(_make_plan_steps("Incomplete task"))

        assert not engine.can_advance()
        engine.force_advance()
        assert engine.current_phase == Phase.VERIFY

    def test_setting_steps_twice_replaces_old_steps(self):
        """set_plan_steps replaces previous steps entirely."""
        engine = PhaseEngine()
        engine.set_plan_steps(_make_plan_steps("Old A", "Old B", "Old C"))
        engine.set_plan_steps(_make_plan_steps("New X"))

        summary = engine.get_plan_summary()
        assert summary["total"] == 1
        assert summary["steps"][0]["description"] == "New X"

    def test_get_plan_summary_steps_shape(self):
        """Each step in summary has id, description, and status."""
        engine = PhaseEngine()
        engine.set_plan_steps([PlanStep(id=0, description="Do thing", status="done")])
        summary = engine.get_plan_summary()
        step = summary["steps"][0]
        assert set(step.keys()) == {"id", "description", "status"}

    def test_plan_summary_inject_into_system_prompt(self):
        """Plan summary can be serialised and embedded in a string (as done in session.py)."""
        engine = PhaseEngine()
        engine.set_plan_steps([
            PlanStep(id=0, description="Write code", status="done"),
            PlanStep(id=1, description="Run tests", status="pending"),
        ])
        summary = engine.get_plan_summary()
        steps_text = "\n".join(
            f"  [{s['status']}] {s['id']}. {s['description']}"
            for s in summary["steps"]
        )
        assert "[done] 0. Write code" in steps_text
        assert "[pending] 1. Run tests" in steps_text


# ---------------------------------------------------------------------------
# TestToolExecutorIntegration
# ---------------------------------------------------------------------------


class TestToolExecutorIntegration:
    """ToolExecutor coordinates FileManager, TerminalRunner, etc. over a real workspace."""

    def setup_method(self):
        self.workspace = make_workspace()
        self.executor = ToolExecutor(self.workspace)

    def teardown_method(self):
        import shutil
        try:
            self.executor._process_manager.stop()
        except Exception:
            pass
        shutil.rmtree(self.workspace, ignore_errors=True)

    # -- write then read flow -----------------------------------------------

    def test_write_then_read_file_via_executor(self):
        """write_file followed by read_file returns the original content."""
        write_result = self.executor.execute(
            "write_file",
            {"path": "app.py", "content": "print('hello')"},
            Phase.IMPLEMENT,
        )
        assert write_result["status"] == "ok"
        assert write_result["path"] == "app.py"

        read_result = self.executor.execute(
            "read_file",
            {"path": "app.py"},
            Phase.IMPLEMENT,
        )
        assert read_result["status"] == "ok"
        assert read_result["content"] == "print('hello')"

    def test_write_then_read_via_verify_phase(self):
        """Files written in IMPLEMENT are readable in VERIFY."""
        self.executor.execute(
            "write_file",
            {"path": "output.txt", "content": "result data"},
            Phase.IMPLEMENT,
        )
        result = self.executor.execute(
            "read_file",
            {"path": "output.txt"},
            Phase.VERIFY,
        )
        assert result["status"] == "ok"
        assert result["content"] == "result data"

    # -- file lifecycle flow ------------------------------------------------

    def test_write_edit_read_file_sequence(self):
        """Write a file, edit it, then read back to verify the edit."""
        self.executor.execute(
            "write_file",
            {"path": "config.json", "content": '{"version": "1.0"}'},
            Phase.IMPLEMENT,
        )
        self.executor.execute(
            "edit_file",
            {
                "path": "config.json",
                "old_text": '"1.0"',
                "new_text": '"2.0"',
            },
            Phase.IMPLEMENT,
        )
        read_result = self.executor.execute(
            "read_file",
            {"path": "config.json"},
            Phase.IMPLEMENT,
        )
        assert '"2.0"' in read_result["content"]
        assert '"1.0"' not in read_result["content"]

    def test_write_then_delete_file(self):
        """Write a file, delete it, list_files should not contain it."""
        self.executor.execute(
            "write_file",
            {"path": "temp.txt", "content": "temporary"},
            Phase.IMPLEMENT,
        )
        del_result = self.executor.execute(
            "delete_file",
            {"path": "temp.txt"},
            Phase.IMPLEMENT,
        )
        assert del_result["status"] == "ok"

        list_result = self.executor.execute("list_files", {}, Phase.IMPLEMENT)
        paths = {f["path"] for f in list_result["files"]}
        assert "temp.txt" not in paths

    def test_write_multiple_files_list_shows_all(self):
        """Multiple writes appear in list_files output."""
        for name in ("main.py", "utils.py", "README.md"):
            self.executor.execute(
                "write_file",
                {"path": name, "content": f"# {name}"},
                Phase.IMPLEMENT,
            )

        list_result = self.executor.execute("list_files", {}, Phase.IMPLEMENT)
        assert list_result["status"] == "ok"
        paths = {f["path"] for f in list_result["files"]}
        assert {"main.py", "utils.py", "README.md"}.issubset(paths)

    def test_write_nested_directory_structure(self):
        """write_file creates nested directories transparently."""
        self.executor.execute(
            "write_file",
            {"path": "src/models/user.py", "content": "class User: pass"},
            Phase.IMPLEMENT,
        )
        assert (self.workspace / "src" / "models" / "user.py").exists()

        list_result = self.executor.execute("list_files", {}, Phase.IMPLEMENT)
        paths = {f["path"] for f in list_result["files"]}
        assert "src/models/user.py" in paths

    # -- command execution flow ---------------------------------------------

    def test_run_command_pwd_returns_workspace(self):
        """run_command pwd returns the workspace path."""
        result = self.executor.execute(
            "run_command",
            {"command": "pwd"},
            Phase.IMPLEMENT,
        )
        assert result["exit_code"] == 0
        assert str(self.workspace) in result["stdout"].strip()

    def test_run_command_ls_lists_written_files(self):
        """After writing files, ls confirms they exist on disk."""
        self.executor.execute(
            "write_file",
            {"path": "hello.txt", "content": "world"},
            Phase.IMPLEMENT,
        )
        result = self.executor.execute(
            "run_command",
            {"command": "ls"},
            Phase.IMPLEMENT,
        )
        assert result["exit_code"] == 0
        assert "hello.txt" in result["stdout"]

    def test_run_command_blocked_in_brainstorm(self):
        """run_command is not permitted in BRAINSTORM phase."""
        result = self.executor.execute(
            "run_command",
            {"command": "ls"},
            Phase.BRAINSTORM,
        )
        assert result["status"] == "error"
        assert "not available" in result["message"]

    def test_run_command_blocked_in_plan(self):
        """run_command is not permitted in PLAN phase."""
        result = self.executor.execute(
            "run_command",
            {"command": "ls"},
            Phase.PLAN,
        )
        assert result["status"] == "error"
        assert "not available" in result["message"]

    def test_run_command_allowed_in_verify(self):
        """run_command is allowed in VERIFY phase."""
        result = self.executor.execute(
            "run_command",
            {"command": "ls"},
            Phase.VERIFY,
        )
        assert result["exit_code"] == 0

    # -- phase access control integration -----------------------------------

    def test_write_blocked_in_verify(self):
        """write_file is not allowed in VERIFY phase."""
        result = self.executor.execute(
            "write_file",
            {"path": "attempt.txt", "content": "oops"},
            Phase.VERIFY,
        )
        assert result["status"] == "error"
        assert "not available" in result["message"]

    def test_tool_definitions_returned_per_phase(self):
        """get_tool_definitions returns different sets for different phases."""
        brainstorm_tools = self.executor.get_tool_definitions(Phase.BRAINSTORM)
        plan_tools = self.executor.get_tool_definitions(Phase.PLAN)
        implement_tools = self.executor.get_tool_definitions(Phase.IMPLEMENT)
        verify_tools = self.executor.get_tool_definitions(Phase.VERIFY)

        assert len(brainstorm_tools) == 0
        assert len(plan_tools) == 0
        assert len(implement_tools) > len(verify_tools)

        impl_names = {d["name"] for d in implement_tools}
        verify_names = {d["name"] for d in verify_tools}

        # write_file is IMPLEMENT-only
        assert "write_file" in impl_names
        assert "write_file" not in verify_names

        # read_file is in both
        assert "read_file" in impl_names
        assert "read_file" in verify_names

    def test_tool_definitions_all_have_schema(self):
        """All returned tool definitions conform to the Anthropic schema format."""
        for phase in Phase:
            for defn in self.executor.get_tool_definitions(phase):
                assert "name" in defn
                assert "description" in defn
                assert "input_schema" in defn
                assert defn["input_schema"]["type"] == "object"


# ---------------------------------------------------------------------------
# TestWorkspaceLifecycle
# ---------------------------------------------------------------------------


class TestWorkspaceLifecycle:
    """WorkspaceManager integration: create/write/zip/cleanup."""

    def setup_method(self):
        self._tmp_base = tempfile.mkdtemp(prefix="eng_ws_")
        self.mgr = WorkspaceManager(base_dir=self._tmp_base)
        self.session_id = "integ_sess_001"

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmp_base, ignore_errors=True)

    def test_create_workspace_returns_writable_directory(self):
        workspace = self.mgr.create(self.session_id)
        assert workspace.is_dir()
        test_file = workspace / "test.txt"
        test_file.write_text("hello")
        assert test_file.exists()

    def test_active_sentinel_created_on_create(self):
        self.mgr.create(self.session_id)
        session_dir = Path(self._tmp_base) / self.session_id
        assert (session_dir / ".active").exists()

    def test_get_workspace_returns_same_path_as_create(self):
        created = self.mgr.create(self.session_id)
        retrieved = self.mgr.get_workspace(self.session_id)
        assert created == retrieved

    def test_write_files_via_file_manager_and_list_via_workspace_mgr(self):
        """FileManager writes and WorkspaceManager.list_files both see the same files."""
        from src.engineering.tools.file_manager import FileManager

        workspace = self.mgr.create(self.session_id)
        fm = FileManager(workspace)
        fm.write_file("app.py", "print('hello')")
        fm.write_file("src/utils.py", "def helper(): pass")

        files = self.mgr.list_files(self.session_id)
        paths = {f["path"] for f in files}
        assert "app.py" in paths
        assert "src/utils.py" in paths

    def test_zip_contains_written_files(self):
        """Files written to workspace appear inside the created zip."""
        workspace = self.mgr.create(self.session_id)
        (workspace / "main.py").write_text("x = 1")
        (workspace / "README.md").write_text("# Project")

        zip_path = self.mgr.create_zip(self.session_id)
        assert zip_path.exists()
        assert zipfile.is_zipfile(zip_path)

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()

        assert any("main.py" in n for n in names)
        assert any("README.md" in n for n in names)

    def test_zip_paths_are_relative(self):
        """Zip archive uses relative paths (no leading slash)."""
        workspace = self.mgr.create(self.session_id)
        sub = workspace / "src"
        sub.mkdir()
        (sub / "core.py").write_text("core code")

        zip_path = self.mgr.create_zip(self.session_id)
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                assert not name.startswith("/"), f"Absolute path found in zip: {name}"

    def test_zip_file_content_integrity(self):
        """Content extracted from zip matches what was written."""
        workspace = self.mgr.create(self.session_id)
        original_content = "def add(a, b): return a + b\n"
        (workspace / "math_utils.py").write_text(original_content)

        zip_path = self.mgr.create_zip(self.session_id)
        with zipfile.ZipFile(zip_path) as zf:
            extracted = zf.read("math_utils.py").decode("utf-8")

        assert extracted == original_content

    def test_cleanup_removes_workspace_and_zip(self):
        """Cleanup removes the entire session directory including any zip."""
        workspace = self.mgr.create(self.session_id)
        (workspace / "data.txt").write_text("some data")
        self.mgr.create_zip(self.session_id)

        session_dir = Path(self._tmp_base) / self.session_id
        assert session_dir.exists()

        self.mgr.cleanup(self.session_id)
        assert not session_dir.exists()
        assert self.mgr.get_workspace(self.session_id) is None

    def test_workspace_size_check_integration(self):
        """check_size returns False when data exceeds the limit."""
        workspace = self.mgr.create(self.session_id)
        # Write 2 MB — exceeds 1 MB limit
        (workspace / "large.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        assert self.mgr.check_size(self.session_id, max_mb=1) is False
        assert self.mgr.check_size(self.session_id, max_mb=10) is True

    def test_tool_executor_files_visible_in_workspace_list(self):
        """Files written via ToolExecutor are visible via WorkspaceManager.list_files."""
        workspace = self.mgr.create(self.session_id)
        executor = ToolExecutor(workspace)

        executor.execute(
            "write_file",
            {"path": "index.html", "content": "<html></html>"},
            Phase.IMPLEMENT,
        )
        executor.execute(
            "write_file",
            {"path": "style.css", "content": "body { margin: 0; }"},
            Phase.IMPLEMENT,
        )

        files = self.mgr.list_files(self.session_id)
        paths = {f["path"] for f in files}
        assert "index.html" in paths
        assert "style.css" in paths

        try:
            executor._process_manager.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TestResourceGuardIntegration
# ---------------------------------------------------------------------------


class TestResourceGuardIntegration:
    """ResourceGuard enforces concurrency limits across multiple sessions."""

    def setup_method(self):
        self.guard = ResourceGuard()

    # -- total session limit ------------------------------------------------

    def test_can_start_up_to_max_sessions(self):
        """Guard allows exactly MAX_TOTAL_SESSIONS sessions."""
        for i in range(MAX_TOTAL_SESSIONS):
            assert self.guard.can_start_session(), f"Should allow session {i}"
            self.guard.register_session(f"sess_{i}", user_id="")

        # Now at capacity
        assert not self.guard.can_start_session()

    def test_unregistering_frees_capacity(self):
        """After unregistering a session, capacity is available again."""
        for i in range(MAX_TOTAL_SESSIONS):
            self.guard.register_session(f"sess_{i}", user_id="")

        assert not self.guard.can_start_session()
        self.guard.unregister_session("sess_0")
        assert self.guard.can_start_session()

    # -- per-user limit -----------------------------------------------------

    def test_user_cannot_have_two_sessions(self):
        """A named user may not start a second session while one is active."""
        self.guard.register_session("s1", user_id="alice")
        assert not self.guard.can_user_start("alice")

    def test_user_limit_freed_on_unregister(self):
        """User can start again after their session is unregistered."""
        self.guard.register_session("s1", user_id="alice")
        assert not self.guard.can_user_start("alice")

        self.guard.unregister_session("s1")
        assert self.guard.can_user_start("alice")

    def test_anonymous_users_not_limited(self):
        """Anonymous sessions (user_id='') are not subject to per-user limits."""
        for i in range(5):
            self.guard.register_session(f"anon_{i}", user_id="")
        # Anonymous users can always start
        assert self.guard.can_user_start("")

    def test_different_users_independent(self):
        """Limits are per-user; one user's session doesn't block another."""
        self.guard.register_session("alice_sess", user_id="alice")
        assert not self.guard.can_user_start("alice")
        assert self.guard.can_user_start("bob")

    # -- implement slot management ------------------------------------------

    def test_acquire_implement_slots_up_to_max(self):
        """Can acquire exactly MAX_IMPLEMENT_SESSIONS slots."""
        slots = []
        for i in range(MAX_IMPLEMENT_SESSIONS):
            slot = self.guard.acquire_implement_slot(f"impl_sess_{i}")
            assert slot is not None, f"Should acquire slot {i}"
            slots.append(slot)

        # All slots have unique ports
        ports = [s.port for s in slots]
        assert len(ports) == len(set(ports)), "All ports must be unique"

    def test_acquire_beyond_max_returns_none(self):
        """Acquiring more slots than MAX_IMPLEMENT_SESSIONS returns None."""
        for i in range(MAX_IMPLEMENT_SESSIONS):
            self.guard.acquire_implement_slot(f"impl_sess_{i}")

        slot = self.guard.acquire_implement_slot("overflow_sess")
        assert slot is None

    def test_release_slot_makes_port_available_again(self):
        """Releasing a slot allows a new session to acquire one."""
        for i in range(MAX_IMPLEMENT_SESSIONS):
            self.guard.acquire_implement_slot(f"impl_sess_{i}")

        assert self.guard.acquire_implement_slot("overflow") is None

        # Release one slot
        self.guard.release_slot("impl_sess_0")

        # Now a new session can acquire
        new_slot = self.guard.acquire_implement_slot("new_sess")
        assert new_slot is not None

    def test_slot_port_is_in_expected_range(self):
        """Acquired port numbers fall in the 20000–20099 range."""
        from src.engineering.resource_guard import PORT_RANGE_END, PORT_RANGE_START

        slot = self.guard.acquire_implement_slot("range_test")
        assert slot is not None
        assert PORT_RANGE_START <= slot.port <= PORT_RANGE_END

    def test_duplicate_slot_acquisition_returns_none(self):
        """Attempting to acquire a slot for a session that already holds one returns None."""
        self.guard.acquire_implement_slot("double_sess")
        duplicate = self.guard.acquire_implement_slot("double_sess")
        assert duplicate is None

    def test_release_idempotent(self):
        """Releasing a slot multiple times does not raise."""
        self.guard.acquire_implement_slot("safe_release")
        self.guard.release_slot("safe_release")
        self.guard.release_slot("safe_release")  # second release must not raise

    # -- daily limit --------------------------------------------------------

    def test_daily_limit_enforced(self):
        """User is blocked once daily session limit is hit."""
        from src.engineering.resource_guard import MAX_DAILY_SESSIONS_PER_USER

        for _ in range(MAX_DAILY_SESSIONS_PER_USER):
            assert self.guard.check_daily_limit("daily_user")
            self.guard._record_daily_session("daily_user")

        assert not self.guard.check_daily_limit("daily_user")

    def test_daily_limit_anonymous_always_allowed(self):
        """Anonymous users are exempt from daily limits."""
        from src.engineering.resource_guard import MAX_DAILY_SESSIONS_PER_USER

        for _ in range(MAX_DAILY_SESSIONS_PER_USER + 5):
            self.guard._record_daily_session("")
        assert self.guard.check_daily_limit("")

    # -- full session lifecycle integration ---------------------------------

    def test_full_session_lifecycle_registers_and_releases(self):
        """Simulate a complete session: register, acquire slot, release, unregister."""
        user_id = "lifecycle_user"
        session_id = "lifecycle_sess"

        assert self.guard.can_start_session()
        assert self.guard.can_user_start(user_id)
        assert self.guard.check_daily_limit(user_id)

        self.guard.register_session(session_id, user_id=user_id)
        self.guard._record_daily_session(user_id)

        # User now blocked from starting another session
        assert not self.guard.can_user_start(user_id)

        slot = self.guard.acquire_implement_slot(session_id)
        assert slot is not None

        # Session end
        self.guard.release_slot(session_id)
        self.guard.unregister_session(session_id)

        # User can start again
        assert self.guard.can_user_start(user_id)
        assert self.guard.can_start_session()


# ---------------------------------------------------------------------------
# TestSessionStoreIntegration
# ---------------------------------------------------------------------------


class TestSessionStoreIntegration:
    """SessionStore save/load/list/expiry data integrity tests."""

    def setup_method(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="eng_store_")
        self.store = SessionStore(base_dir=self._tmp_dir)

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # -- save / load round-trip ---------------------------------------------

    def test_save_and_load_basic_context(self):
        """Context saved via save() is fully recoverable via load()."""
        context = {
            "session_id": "sess_001",
            "last_phase": "verify",
            "api_calls": 12,
            "total_tokens": 45000,
        }
        self.store.save(context, user_id="user_a")

        loaded = self.store.load("sess_001")
        assert loaded is not None
        assert loaded["session_id"] == "sess_001"
        assert loaded["last_phase"] == "verify"
        assert loaded["api_calls"] == 12
        assert loaded["total_tokens"] == 45000
        assert loaded["user_id"] == "user_a"

    def test_save_adds_created_at_and_expires_at(self):
        """save() injects created_at and expires_at timestamps."""
        before = time.time()
        self.store.save({"session_id": "ts_sess"}, user_id="")
        after = time.time()

        loaded = self.store.load("ts_sess")
        assert loaded is not None
        assert before <= loaded["created_at"] <= after
        assert loaded["expires_at"] > after  # expires in the future

    def test_save_plan_summary_round_trip(self):
        """Complex nested structure (plan_summary) survives save/load."""
        plan_summary = {
            "total": 3,
            "done": 2,
            "in_progress": 0,
            "pending": 1,
            "steps": [
                {"id": 0, "description": "Setup", "status": "done"},
                {"id": 1, "description": "Build", "status": "done"},
                {"id": 2, "description": "Test", "status": "pending"},
            ],
        }
        context = {
            "session_id": "plan_sess",
            "plan_summary": plan_summary,
        }
        self.store.save(context, user_id="")

        loaded = self.store.load("plan_sess")
        assert loaded["plan_summary"]["total"] == 3
        assert loaded["plan_summary"]["steps"][2]["description"] == "Test"

    def test_load_nonexistent_returns_none(self):
        """Loading a session that was never saved returns None."""
        result = self.store.load("ghost_session")
        assert result is None

    # -- session file storage -----------------------------------------------

    def test_save_creates_json_file(self):
        """save() writes a JSON file under the sessions directory."""
        self.store.save({"session_id": "file_sess"}, user_id="")
        expected_path = Path(self._tmp_dir) / "sessions" / "file_sess.json"
        assert expected_path.exists()
        # File must be valid JSON
        data = json.loads(expected_path.read_text())
        assert data["session_id"] == "file_sess"

    def test_save_overwrites_on_resave(self):
        """Saving the same session_id twice overwrites the first version."""
        self.store.save({"session_id": "overwrite_sess", "value": 1}, user_id="")
        self.store.save({"session_id": "overwrite_sess", "value": 2}, user_id="")

        loaded = self.store.load("overwrite_sess")
        assert loaded["value"] == 2

    def test_save_requires_session_id(self):
        """save() raises ValueError when context lacks session_id."""
        with pytest.raises(ValueError, match="session_id"):
            self.store.save({"data": "no id here"}, user_id="")

    # -- list_sessions ------------------------------------------------------

    def test_list_sessions_returns_all_saved(self):
        """list_sessions returns summaries for all saved sessions."""
        for i in range(3):
            self.store.save({"session_id": f"list_sess_{i}"}, user_id="")

        sessions = self.store.list_sessions()
        session_ids = {s["session_id"] for s in sessions}
        assert {"list_sess_0", "list_sess_1", "list_sess_2"}.issubset(session_ids)

    def test_list_sessions_filtered_by_user(self):
        """list_sessions with user_id filters by user."""
        self.store.save({"session_id": "alice_1"}, user_id="alice")
        self.store.save({"session_id": "alice_2"}, user_id="alice")
        self.store.save({"session_id": "bob_1"}, user_id="bob")

        alice_sessions = self.store.list_sessions(user_id="alice")
        assert len(alice_sessions) == 2
        ids = {s["session_id"] for s in alice_sessions}
        assert "alice_1" in ids
        assert "alice_2" in ids
        assert "bob_1" not in ids

    def test_list_sessions_summary_shape(self):
        """Each summary dict has expected keys."""
        self.store.save(
            {
                "session_id": "shape_sess",
                "project_name": "My App",
                "stack": "python",
                "last_phase": "implement",
            },
            user_id="tester",
        )
        summaries = self.store.list_sessions()
        assert len(summaries) >= 1

        summary = next(s for s in summaries if s["session_id"] == "shape_sess")
        expected_keys = {"session_id", "project_name", "stack", "last_phase",
                         "created_at", "expires_at", "user_id"}
        assert expected_keys.issubset(set(summary.keys()))

    # -- expiry handling ----------------------------------------------------

    def test_expired_session_not_loaded(self):
        """A session with an expired expires_at is not returned by load()."""
        context = {
            "session_id": "expired_sess",
            "created_at": time.time() - 10 * 86400,  # 10 days ago
        }
        self.store.save(context, user_id="")

        # Manually overwrite expires_at to be in the past
        sess_file = Path(self._tmp_dir) / "sessions" / "expired_sess.json"
        data = json.loads(sess_file.read_text())
        data["expires_at"] = time.time() - 1  # expired 1 second ago
        sess_file.write_text(json.dumps(data))

        result = self.store.load("expired_sess")
        assert result is None

    def test_expired_session_deleted_by_list(self):
        """list_sessions deletes expired files and excludes them from results."""
        self.store.save({"session_id": "to_expire"}, user_id="")

        sess_file = Path(self._tmp_dir) / "sessions" / "to_expire.json"
        data = json.loads(sess_file.read_text())
        data["expires_at"] = time.time() - 1
        sess_file.write_text(json.dumps(data))

        sessions = self.store.list_sessions()
        ids = {s["session_id"] for s in sessions}
        assert "to_expire" not in ids

        # File should also be deleted
        assert not sess_file.exists()

    # -- full context save integration with PhaseEngine ---------------------

    def test_phase_engine_summary_saved_and_restored(self):
        """Simulate session.py's _save_context: PhaseEngine summary -> save -> load."""
        engine = PhaseEngine()
        engine.advance()   # PLAN
        engine.advance()   # IMPLEMENT
        engine.set_plan_steps(_make_plan_steps("Setup", "Build", "Deploy"))
        engine.update_step(0, "done")
        engine.update_step(1, "done")

        # Build the same context dict that session.py._save_context creates
        context = {
            "session_id": "phase_integration_sess",
            "last_phase": engine.current_phase.value,
            "plan_summary": engine.get_plan_summary(),
            "api_calls": 7,
            "total_tokens": 22000,
        }
        self.store.save(context, user_id="developer_1")

        loaded = self.store.load("phase_integration_sess")
        assert loaded is not None
        assert loaded["last_phase"] == "implement"
        assert loaded["plan_summary"]["total"] == 3
        assert loaded["plan_summary"]["done"] == 2
        assert loaded["plan_summary"]["pending"] == 1
        assert loaded["api_calls"] == 7
        assert loaded["user_id"] == "developer_1"
