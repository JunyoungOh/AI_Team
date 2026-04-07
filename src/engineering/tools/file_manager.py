"""Engineering mode — sandboxed file I/O within workspace boundary.

All operations are confined to the initialised *workspace* directory.
Any path that resolves outside the boundary (``../../`` traversal or
symlink escape) is rejected with :exc:`PermissionError`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class FileManager:
    """Sandboxed file I/O scoped to a workspace directory.

    Parameters
    ----------
    workspace:
        Absolute path to the session workspace root.  Resolved once on
        construction so that subsequent ``_safe_path`` checks are fast.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(os.path.realpath(workspace))

    # ── Internal helpers ───────────────────────────────────────────────

    def _safe_path(self, relative_path: str | Path) -> Path:
        """Resolve *relative_path* and verify it stays inside the workspace.

        Uses ``os.path.realpath()`` to expand symlinks and ``..`` segments
        before comparison, blocking both directory-traversal and symlink
        escape attacks.

        Raises
        ------
        PermissionError
            When the resolved path lies outside *workspace*.
        """
        candidate = os.path.realpath(self._workspace / relative_path)
        resolved = Path(candidate)

        if not resolved.is_relative_to(self._workspace):
            raise PermissionError(
                f"Path '{relative_path}' escapes workspace boundary "
                f"'{self._workspace}'"
            )
        return resolved

    # ── Public API ─────────────────────────────────────────────────────

    def write_file(self, path: str | Path, content: str) -> dict:
        """Write *content* to *path*, creating intermediate directories.

        Returns
        -------
        dict
            ``{"status": "ok", "path": str, "size": int}``
        """
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "status": "ok",
            "path": str(target.relative_to(self._workspace)),
            "size": target.stat().st_size,
        }

    def read_file(self, path: str | Path) -> str:
        """Return the text content of *path*.

        Raises
        ------
        FileNotFoundError
            When *path* does not exist.
        """
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: '{path}'")
        return target.read_text(encoding="utf-8")

    def edit_file(self, path: str | Path, old_text: str, new_text: str) -> dict:
        """Replace the first occurrence of *old_text* with *new_text*.

        Uses an atomic ``tempfile + os.replace()`` write so a crash during
        the write does not corrupt the original file.

        Returns
        -------
        dict
            ``{"status": "ok", "path": str, "size": int}``

        Raises
        ------
        FileNotFoundError
            When *path* does not exist.
        ValueError
            When *old_text* is not found in the file.
        """
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: '{path}'")

        original = target.read_text(encoding="utf-8")
        if old_text not in original:
            raise ValueError(
                f"Text not found in '{path}': {old_text!r}"
            )

        updated = original.replace(old_text, new_text, 1)

        # Atomic write: write to a temp file in the same directory then rename
        dir_path = target.parent
        fd, tmp_path = tempfile.mkstemp(dir=dir_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(updated)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return {
            "status": "ok",
            "path": str(target.relative_to(self._workspace)),
            "size": target.stat().st_size,
        }

    def delete_file(self, path: str | Path) -> dict:
        """Delete the file at *path*.

        Returns
        -------
        dict
            ``{"status": "ok", "path": str}``

        Raises
        ------
        FileNotFoundError
            When *path* does not exist.
        """
        target = self._safe_path(path)
        if not target.exists():
            raise FileNotFoundError(f"File not found: '{path}'")
        target.unlink()
        return {
            "status": "ok",
            "path": str(target.relative_to(self._workspace)),
        }

    def list_files(self, subpath: str | Path = "") -> list[dict]:
        """Return a flat listing of files under *subpath* (default: workspace root).

        Returns
        -------
        list[dict]
            Each entry is ``{"path": str, "size": int}`` where *path* is
            relative to *workspace* and *size* is in bytes.
        """
        if subpath:
            root = self._safe_path(subpath)
        else:
            root = self._workspace

        if not root.exists():
            return []

        entries: list[dict] = []
        for file in root.rglob("*"):
            if file.is_file():
                entries.append(
                    {
                        "path": str(file.relative_to(self._workspace)),
                        "size": file.stat().st_size,
                    }
                )
        entries.sort(key=lambda e: e["path"])
        return entries
