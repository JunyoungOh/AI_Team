"""Tests for AI DataLab security module — Zero-Retention file lifecycle."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.datalab.security import (
    ALLOWED_EXTENSIONS,
    SECURITY_BANNER,
    cleanup_session,
    create_session_dir,
    purge_orphan_sessions,
    validate_download_path,
    validate_upload,
)


# ── Constants ──────────────────────────────────────────


class TestConstants:
    def test_allowed_extensions_contains_csv(self):
        assert ".csv" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_xlsx(self):
        assert ".xlsx" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_xls(self):
        assert ".xls" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_tsv(self):
        assert ".tsv" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_json(self):
        assert ".json" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_pdf(self):
        assert ".pdf" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_txt(self):
        assert ".txt" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_png(self):
        assert ".png" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_jpg(self):
        assert ".jpg" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_jpeg(self):
        assert ".jpeg" in ALLOWED_EXTENSIONS

    def test_allowed_extensions_is_set(self):
        assert isinstance(ALLOWED_EXTENSIONS, set)

    def test_allowed_extensions_rejects_exe(self):
        assert ".exe" not in ALLOWED_EXTENSIONS

    def test_allowed_extensions_rejects_sh(self):
        assert ".sh" not in ALLOWED_EXTENSIONS

    def test_security_banner_is_string(self):
        assert isinstance(SECURITY_BANNER, str)

    def test_security_banner_not_empty(self):
        assert len(SECURITY_BANNER) > 0


# ── create_session_dir ─────────────────────────────────


class TestCreateSessionDir:
    def test_creates_session_directory(self, tmp_path):
        result = create_session_dir(str(tmp_path), "sess_001")
        assert result.exists()
        assert result.is_dir()

    def test_creates_uploads_subdir(self, tmp_path):
        result = create_session_dir(str(tmp_path), "sess_001")
        assert (result / "uploads").exists()
        assert (result / "uploads").is_dir()

    def test_creates_outputs_subdir(self, tmp_path):
        result = create_session_dir(str(tmp_path), "sess_001")
        assert (result / "outputs").exists()
        assert (result / "outputs").is_dir()

    def test_creates_workspace_subdir(self, tmp_path):
        result = create_session_dir(str(tmp_path), "sess_001")
        assert (result / "workspace").exists()
        assert (result / "workspace").is_dir()

    def test_creates_active_sentinel(self, tmp_path):
        result = create_session_dir(str(tmp_path), "sess_001")
        assert (result / ".active").exists()
        assert (result / ".active").is_file()

    def test_returns_path_object(self, tmp_path):
        result = create_session_dir(str(tmp_path), "sess_001")
        assert isinstance(result, Path)

    def test_session_dir_name_contains_session_id(self, tmp_path):
        result = create_session_dir(str(tmp_path), "sess_abc123")
        assert "sess_abc123" in result.name


# ── cleanup_session ────────────────────────────────────


class TestCleanupSession:
    def test_removes_session_directory(self, tmp_path):
        session_dir = create_session_dir(str(tmp_path), "sess_cleanup")
        assert session_dir.exists()
        cleanup_session(str(tmp_path), "sess_cleanup")
        assert not session_dir.exists()

    def test_removes_files_inside_session(self, tmp_path):
        session_dir = create_session_dir(str(tmp_path), "sess_cleanup2")
        # Create some files inside
        (session_dir / "uploads" / "data.csv").write_text("a,b,c")
        (session_dir / "outputs" / "report.html").write_text("<h1>Report</h1>")
        cleanup_session(str(tmp_path), "sess_cleanup2")
        assert not session_dir.exists()

    def test_cleanup_nonexistent_session_does_not_raise(self, tmp_path):
        # Should not raise even if session dir doesn't exist
        cleanup_session(str(tmp_path), "nonexistent_session")

    def test_cleanup_removes_active_sentinel(self, tmp_path):
        session_dir = create_session_dir(str(tmp_path), "sess_sentinel")
        sentinel = session_dir / ".active"
        assert sentinel.exists()
        cleanup_session(str(tmp_path), "sess_sentinel")
        assert not sentinel.exists()


# ── validate_download_path ─────────────────────────────


class TestValidateDownloadPath:
    def test_valid_file_returns_path(self, tmp_path):
        session_dir = create_session_dir(str(tmp_path), "sess_dl")
        output_file = session_dir / "outputs" / "result.csv"
        output_file.write_text("x,y,z")
        result = validate_download_path(str(tmp_path), "sess_dl", "result.csv")
        assert result is not None
        assert result.exists()

    def test_nonexistent_file_returns_none(self, tmp_path):
        create_session_dir(str(tmp_path), "sess_dl2")
        result = validate_download_path(str(tmp_path), "sess_dl2", "missing.csv")
        assert result is None

    def test_path_traversal_dot_dot_returns_none(self, tmp_path):
        session_dir = create_session_dir(str(tmp_path), "sess_traversal")
        # Create a file outside the outputs dir
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP SECRET")
        result = validate_download_path(
            str(tmp_path), "sess_traversal", "../../secret.txt"
        )
        assert result is None

    def test_path_traversal_absolute_returns_none(self, tmp_path):
        create_session_dir(str(tmp_path), "sess_abs")
        result = validate_download_path(str(tmp_path), "sess_abs", "/etc/passwd")
        assert result is None

    def test_path_traversal_uploads_dir_returns_none(self, tmp_path):
        """Downloading should only come from outputs/, not uploads/."""
        session_dir = create_session_dir(str(tmp_path), "sess_up")
        upload_file = session_dir / "uploads" / "data.csv"
        upload_file.write_text("uploaded")
        result = validate_download_path(
            str(tmp_path), "sess_up", "../uploads/data.csv"
        )
        assert result is None

    def test_valid_nested_output_file(self, tmp_path):
        session_dir = create_session_dir(str(tmp_path), "sess_nested")
        subdir = session_dir / "outputs" / "charts"
        subdir.mkdir()
        chart = subdir / "chart.png"
        chart.write_bytes(b"\x89PNG")
        result = validate_download_path(
            str(tmp_path), "sess_nested", "charts/chart.png"
        )
        assert result is not None
        assert result.exists()


# ── purge_orphan_sessions ──────────────────────────────


class TestPurgeOrphanSessions:
    def test_removes_orphan_without_active_sentinel(self, tmp_path):
        # Create a session dir manually without .active
        orphan = tmp_path / "datalab_orphan1"
        orphan.mkdir()
        (orphan / "uploads").mkdir()
        count = purge_orphan_sessions(str(tmp_path))
        assert count == 1
        assert not orphan.exists()

    def test_keeps_session_with_active_sentinel(self, tmp_path):
        active_session = create_session_dir(str(tmp_path), "active1")
        count = purge_orphan_sessions(str(tmp_path))
        assert count == 0
        assert active_session.exists()

    def test_mixed_active_and_orphan(self, tmp_path):
        # Active session (has .active) — session_id already includes datalab_ prefix
        create_session_dir(str(tmp_path), "datalab_keep_me")
        # Orphan session (no .active)
        orphan = tmp_path / "datalab_orphan2"
        orphan.mkdir()
        count = purge_orphan_sessions(str(tmp_path))
        assert count == 1
        assert not orphan.exists()
        # Active session should still exist
        assert (tmp_path / "datalab_keep_me").exists()

    def test_ignores_non_datalab_directories(self, tmp_path):
        # Create a random dir that doesn't start with datalab_
        random_dir = tmp_path / "random_dir"
        random_dir.mkdir()
        count = purge_orphan_sessions(str(tmp_path))
        assert count == 0
        assert random_dir.exists()

    def test_returns_zero_on_empty_dir(self, tmp_path):
        count = purge_orphan_sessions(str(tmp_path))
        assert count == 0


# ── validate_upload ────────────────────────────────────


class TestValidateUpload:
    def test_valid_csv_returns_none(self):
        result = validate_upload("data.csv", 1_000_000)
        assert result is None

    def test_valid_xlsx_returns_none(self):
        result = validate_upload("report.xlsx", 5_000_000)
        assert result is None

    def test_valid_json_returns_none(self):
        result = validate_upload("config.json", 100)
        assert result is None

    def test_valid_pdf_returns_none(self):
        result = validate_upload("document.pdf", 10_000_000)
        assert result is None

    def test_valid_png_returns_none(self):
        result = validate_upload("chart.png", 2_000_000)
        assert result is None

    def test_invalid_extension_exe(self):
        result = validate_upload("malware.exe", 100)
        assert result is not None
        assert isinstance(result, str)

    def test_invalid_extension_py(self):
        result = validate_upload("script.py", 100)
        assert result is not None

    def test_invalid_extension_sh(self):
        result = validate_upload("hack.sh", 100)
        assert result is not None

    def test_oversized_file(self):
        # Default limit is 50MB = 50 * 1024 * 1024 bytes
        too_large = 51 * 1024 * 1024
        result = validate_upload("data.csv", too_large)
        assert result is not None
        assert isinstance(result, str)

    def test_exactly_at_limit(self):
        exact = 50 * 1024 * 1024
        result = validate_upload("data.csv", exact)
        assert result is None

    def test_zero_size_valid(self):
        result = validate_upload("empty.csv", 0)
        assert result is None

    def test_case_insensitive_extension(self):
        result = validate_upload("DATA.CSV", 1000)
        assert result is None

    def test_double_extension_only_last_matters(self):
        result = validate_upload("file.exe.csv", 1000)
        assert result is None

    def test_no_extension_rejected(self):
        result = validate_upload("noextension", 1000)
        assert result is not None
