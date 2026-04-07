"""Tests for AI Engineering WorkspaceManager — ephemeral workspace lifecycle."""

import zipfile
from pathlib import Path

import pytest

from src.engineering.workspace_manager import WorkspaceManager


# ── create ─────────────────────────────────────────────


class TestCreateWorkspace:
    def test_create_workspace_returns_path(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        result = manager.create("sess_001")
        assert isinstance(result, Path)

    def test_create_workspace_directory_exists(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        result = manager.create("sess_001")
        assert result.exists()
        assert result.is_dir()

    def test_create_workspace_active_sentinel_exists(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_001")
        session_dir = tmp_path / "sess_001"
        assert (session_dir / ".active").exists()
        assert (session_dir / ".active").is_file()

    def test_create_workspace_idempotent(self, tmp_path):
        """Calling create twice for same session should not raise."""
        manager = WorkspaceManager(base_dir=str(tmp_path))
        p1 = manager.create("sess_dup")
        p2 = manager.create("sess_dup")
        assert p1 == p2
        assert p2.exists()


# ── path structure ─────────────────────────────────────


class TestWorkspacePathStructure:
    def test_workspace_parent_is_session_dir(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_abc")
        # workspace/ is a child of <base>/<session_id>/
        assert workspace.name == "workspace"
        assert workspace.parent.name == "sess_abc"

    def test_workspace_grandparent_is_base_dir(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_xyz")
        assert workspace.parent.parent == tmp_path

    def test_session_dir_named_after_session_id(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("my_session_123")
        session_dir = tmp_path / "my_session_123"
        assert session_dir.exists()
        assert session_dir.is_dir()

    def test_different_sessions_are_isolated(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        ws1 = manager.create("sess_A")
        ws2 = manager.create("sess_B")
        assert ws1 != ws2
        assert ws1.parent != ws2.parent


# ── get_workspace ──────────────────────────────────────


class TestGetWorkspace:
    def test_get_workspace_returns_path_for_existing_session(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_get")
        result = manager.get_workspace("sess_get")
        assert result is not None
        assert isinstance(result, Path)
        assert result.exists()

    def test_get_workspace_returns_none_for_missing(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        result = manager.get_workspace("nonexistent_session")
        assert result is None

    def test_get_workspace_returns_none_after_cleanup(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_gone")
        manager.cleanup("sess_gone")
        result = manager.get_workspace("sess_gone")
        assert result is None

    def test_get_workspace_path_matches_create_path(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        created = manager.create("sess_match")
        retrieved = manager.get_workspace("sess_match")
        assert created == retrieved


# ── create_zip ─────────────────────────────────────────


class TestCreateZip:
    def test_create_zip_returns_path(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_zip")
        (workspace / "hello.txt").write_text("hello world")
        zip_path = manager.create_zip("sess_zip")
        assert isinstance(zip_path, Path)

    def test_create_zip_file_exists(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_zip2")
        (workspace / "data.csv").write_text("a,b,c\n1,2,3")
        zip_path = manager.create_zip("sess_zip2")
        assert zip_path.exists()
        assert zip_path.is_file()

    def test_create_zip_is_valid_zip(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_zip3")
        (workspace / "script.py").write_text("print('hi')")
        zip_path = manager.create_zip("sess_zip3")
        assert zipfile.is_zipfile(zip_path)

    def test_create_zip_contains_files_with_correct_relative_paths(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_zip4")
        (workspace / "main.py").write_text("x = 1")
        subdir = workspace / "utils"
        subdir.mkdir()
        (subdir / "helpers.py").write_text("def noop(): pass")
        zip_path = manager.create_zip("sess_zip4")

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()

        # Paths must be relative (no absolute paths), workspace root as "."
        assert any("main.py" in n for n in names)
        assert any("helpers.py" in n for n in names)
        for name in names:
            assert not name.startswith("/")

    def test_create_zip_empty_workspace(self, tmp_path):
        """Zipping an empty workspace should still produce a valid zip."""
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_zip_empty")
        zip_path = manager.create_zip("sess_zip_empty")
        assert zip_path.exists()
        assert zipfile.is_zipfile(zip_path)

    def test_create_zip_for_missing_session_raises(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        with pytest.raises((FileNotFoundError, ValueError)):
            manager.create_zip("no_such_session")


# ── cleanup ────────────────────────────────────────────


class TestCleanup:
    def test_cleanup_removes_directory(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_clean")
        session_dir = tmp_path / "sess_clean"
        assert session_dir.exists()
        manager.cleanup("sess_clean")
        assert not session_dir.exists()

    def test_cleanup_removes_workspace_contents(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_clean2")
        (workspace / "important.py").write_text("secret = 42")
        manager.cleanup("sess_clean2")
        assert not (tmp_path / "sess_clean2").exists()

    def test_cleanup_nonexistent_session_does_not_raise(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        # Should not raise
        manager.cleanup("nonexistent_session_xyz")

    def test_cleanup_removes_active_sentinel(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_sentinel")
        sentinel = tmp_path / "sess_sentinel" / ".active"
        assert sentinel.exists()
        manager.cleanup("sess_sentinel")
        assert not sentinel.exists()


# ── check_size ─────────────────────────────────────────


class TestCheckSize:
    def test_check_size_within_limit_returns_true(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_size")
        # Write a small file (well under 500 MB)
        (workspace / "small.txt").write_text("a" * 1000)
        assert manager.check_size("sess_size", max_mb=500) is True

    def test_check_size_exceeds_limit_returns_false(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_big")
        # Write 2 MB of data
        (workspace / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        # Limit to 1 MB — should return False
        assert manager.check_size("sess_big", max_mb=1) is False

    def test_check_size_exactly_at_limit_returns_true(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_exact")
        one_mb = 1 * 1024 * 1024
        (workspace / "exact.bin").write_bytes(b"z" * one_mb)
        assert manager.check_size("sess_exact", max_mb=1) is True

    def test_check_size_empty_workspace_returns_true(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_empty_size")
        assert manager.check_size("sess_empty_size", max_mb=500) is True

    def test_check_size_missing_session_returns_true(self, tmp_path):
        """Missing session has zero bytes, which is within any limit."""
        manager = WorkspaceManager(base_dir=str(tmp_path))
        assert manager.check_size("ghost_session", max_mb=500) is True


# ── list_files ─────────────────────────────────────────


class TestListFiles:
    def test_list_files_returns_list(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_ls")
        result = manager.list_files("sess_ls")
        assert isinstance(result, list)

    def test_list_files_empty_workspace(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        manager.create("sess_ls_empty")
        result = manager.list_files("sess_ls_empty")
        assert result == []

    def test_list_files_returns_tree_with_path_and_size(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_ls2")
        (workspace / "app.py").write_text("print('hello')")
        result = manager.list_files("sess_ls2")
        assert len(result) == 1
        entry = result[0]
        assert "path" in entry
        assert "size" in entry

    def test_list_files_returns_correct_file_count(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_ls3")
        (workspace / "a.py").write_text("a")
        (workspace / "b.py").write_text("b")
        (workspace / "c.txt").write_text("c")
        result = manager.list_files("sess_ls3")
        assert len(result) == 3

    def test_list_files_includes_nested_files(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_ls4")
        subdir = workspace / "models"
        subdir.mkdir()
        (workspace / "main.py").write_text("x")
        (subdir / "model.py").write_text("class M: pass")
        result = manager.list_files("sess_ls4")
        paths = [e["path"] for e in result]
        assert any("main.py" in p for p in paths)
        assert any("model.py" in p for p in paths)

    def test_list_files_size_matches_actual_content(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_ls5")
        content = "hello world!"
        (workspace / "greeting.txt").write_text(content)
        result = manager.list_files("sess_ls5")
        assert len(result) == 1
        assert result[0]["size"] == len(content.encode())

    def test_list_files_subpath_filters_results(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        workspace = manager.create("sess_ls6")
        subdir = workspace / "src"
        subdir.mkdir()
        (workspace / "README.md").write_text("readme")
        (subdir / "core.py").write_text("core")
        result = manager.list_files("sess_ls6", subpath="src")
        assert len(result) == 1
        assert any("core.py" in e["path"] for e in result)

    def test_list_files_missing_session_returns_empty(self, tmp_path):
        manager = WorkspaceManager(base_dir=str(tmp_path))
        result = manager.list_files("no_such_session")
        assert result == []
