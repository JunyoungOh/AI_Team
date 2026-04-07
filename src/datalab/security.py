"""AI DataLab security — Zero-Retention file lifecycle management.

All uploaded data is confined to session-scoped directories and purged
when the session ends.  Path-traversal attacks are blocked via
``resolved.is_relative_to()`` checks, and upload validation enforces
an extension allowlist plus a configurable size cap.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src.config.settings import get_settings

# ── Constants ──────────────────────────────────────────

ALLOWED_EXTENSIONS: set[str] = {
    ".csv", ".xlsx", ".xls", ".tsv", ".json",
    ".pdf", ".txt", ".png", ".jpg", ".jpeg",
}

SECURITY_BANNER: str = (
    "Zero-Retention Policy: All uploaded files and generated outputs are "
    "automatically deleted when your DataLab session ends. No data is "
    "retained on the server after session termination."
)

# ── Session directory lifecycle ────────────────────────


def create_session_dir(base_dir: str, session_id: str) -> Path:
    """Create a session directory with standard subdirectories.

    Layout::

        <base_dir>/<session_id>/
            uploads/
            outputs/
            workspace/
            .active          ← sentinel marking a live session
    """
    session_dir = Path(base_dir) / session_id
    (session_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (session_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (session_dir / "workspace").mkdir(parents=True, exist_ok=True)
    (session_dir / ".active").touch()
    return session_dir


def cleanup_session(base_dir: str, session_id: str) -> None:
    """Remove the entire session directory (Zero-Retention enforcement)."""
    session_dir = Path(base_dir) / session_id
    shutil.rmtree(session_dir, ignore_errors=True)


# ── Download path validation ──────────────────────────


def validate_download_path(
    base_dir: str, session_id: str, filename: str
) -> Path | None:
    """Validate that *filename* resolves inside the session's ``outputs/`` dir.

    Returns the resolved ``Path`` when valid, or ``None`` when:
    - the resolved path escapes ``outputs/`` (path-traversal attempt), or
    - the file does not exist on disk.
    """
    outputs_dir = (
        Path(base_dir) / session_id / "outputs"
    ).resolve()
    resolved = (outputs_dir / filename).resolve()

    if not resolved.is_relative_to(outputs_dir):
        return None
    if not resolved.exists():
        return None
    return resolved


# ── Orphan session cleanup ────────────────────────────


def purge_orphan_sessions(base_dir: str) -> int:
    """Remove session directories that lack the ``.active`` sentinel.

    Only directories whose name starts with ``datalab_`` are inspected.
    Returns the number of directories removed.
    """
    base = Path(base_dir)
    removed = 0
    if not base.exists():
        return removed

    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.startswith("datalab_"):
            continue
        if not (entry / ".active").exists():
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1

    return removed


# ── Upload validation ─────────────────────────────────


def validate_upload(filename: str, size_bytes: int) -> str | None:
    """Validate an upload by extension and size.

    Returns an error message string if invalid, or ``None`` if acceptable.
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return (
            f"File type '{ext or '(none)'}' is not allowed. "
            f"Accepted types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    settings = get_settings()
    max_bytes = settings.datalab_max_upload_size_mb * 1024 * 1024
    if size_bytes > max_bytes:
        return (
            f"File size ({size_bytes / (1024 * 1024):.1f} MB) exceeds "
            f"the {settings.datalab_max_upload_size_mb} MB limit."
        )

    return None
