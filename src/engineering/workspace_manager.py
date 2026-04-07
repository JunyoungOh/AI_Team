"""AI Engineering mode — ephemeral workspace lifecycle management.

Each Engineering session gets an isolated directory under ``/tmp/eng/``.
When the session ends the workspace is optionally zipped for download then
deleted in full (Zero-Retention Policy).

Layout::

    /tmp/eng/<session_id>/
        workspace/        ← code lives here; returned by create()
        .active           ← sentinel marking a live session
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


class WorkspaceManager:
    """Manages ephemeral per-session workspaces on the Railway server.

    Parameters
    ----------
    base_dir:
        Root directory under which session directories are created.
        Defaults to ``/tmp/eng``.
    """

    def __init__(self, base_dir: str = "/tmp/eng") -> None:
        self._base = Path(base_dir)

    # ── Internal helpers ───────────────────────────────

    def _session_dir(self, session_id: str) -> Path:
        return self._base / session_id

    # ── Public API ─────────────────────────────────────

    def create(self, session_id: str) -> Path:
        """Create (or return existing) workspace for *session_id*.

        Returns the ``workspace/`` subdirectory path so callers can write
        files directly into it.

        Directory layout::

            <base_dir>/<session_id>/
                workspace/
                .active
        """
        session_dir = self._session_dir(session_id)
        workspace = session_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (session_dir / ".active").touch()
        return workspace

    def get_workspace(self, session_id: str) -> Path | None:
        """Return the workspace path for *session_id*, or ``None`` if absent."""
        workspace = self._session_dir(session_id) / "workspace"
        if workspace.exists():
            return workspace
        return None

    def create_zip(self, session_id: str) -> Path:
        """Zip the workspace contents and return the zip file path.

        The zip is written next to the ``workspace/`` directory as
        ``<session_id>.zip``.

        Raises
        ------
        FileNotFoundError
            If the session workspace does not exist.
        """
        workspace = self.get_workspace(session_id)
        if workspace is None:
            raise FileNotFoundError(
                f"Workspace for session '{session_id}' does not exist."
            )

        zip_path = self._session_dir(session_id) / f"{session_id}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in workspace.rglob("*"):
                if file.is_file():
                    # Store relative to workspace root
                    arcname = file.relative_to(workspace)
                    zf.write(file, arcname)

        return zip_path

    def cleanup(self, session_id: str) -> None:
        """Remove the entire session directory (Zero-Retention enforcement)."""
        shutil.rmtree(self._session_dir(session_id), ignore_errors=True)

    def check_size(self, session_id: str, max_mb: float = 500) -> bool:
        """Return ``True`` if total workspace size is within *max_mb* megabytes.

        Returns ``True`` for missing sessions (0 bytes <= any limit).
        """
        workspace = self.get_workspace(session_id)
        if workspace is None:
            return True

        total_bytes = sum(
            f.stat().st_size for f in workspace.rglob("*") if f.is_file()
        )
        max_bytes = max_mb * 1024 * 1024
        return total_bytes <= max_bytes

    def list_files(self, session_id: str, subpath: str = "") -> list[dict]:
        """Return a flat file tree for the workspace (or a sub-directory).

        Parameters
        ----------
        session_id:
            Target session.
        subpath:
            Optional path relative to ``workspace/`` to narrow the listing.

        Returns
        -------
        list[dict]
            Each entry is ``{"path": str, "size": int}`` where *path* is
            relative to the workspace root and *size* is in bytes.
            Returns an empty list when the session or subpath does not exist.
        """
        workspace = self.get_workspace(session_id)
        if workspace is None:
            return []

        root = workspace / subpath if subpath else workspace
        if not root.exists():
            return []

        entries: list[dict] = []
        for file in root.rglob("*"):
            if file.is_file():
                entries.append(
                    {
                        "path": str(file.relative_to(workspace)),
                        "size": file.stat().st_size,
                    }
                )
        return entries
