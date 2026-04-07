"""Engineering mode — workspace-local git operations.

Provides init, commit, and diff helpers that wrap the system ``git``
binary via the project's :class:`TerminalRunner`.  No remote push
operations are exposed in v1.
"""

from __future__ import annotations

from pathlib import Path

from src.engineering.tools.terminal_runner import TerminalRunner


class GitManager:
    """Manages git operations scoped to a workspace directory.

    Parameters
    ----------
    workspace:
        Absolute path to the session workspace.  All git commands run
        with ``cwd=workspace``.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace).resolve()
        self._runner = TerminalRunner(self._workspace)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _git(self, *args: str) -> dict:
        """Run a git sub-command and return the runner result dict."""
        command = "git " + " ".join(args)
        return self._runner.run_command(command)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def init(self) -> dict:
        """Initialise a new git repository in the workspace.

        Performs:
        1. ``git init``
        2. ``git config user.email`` (engineering-bot placeholder)
        3. ``git config user.name``
        4. Creates a ``.gitignore`` and makes an initial empty commit.

        Returns
        -------
        dict
            ``{"status": "ok", "output": str}`` on success, or
            ``{"status": "error", "output": str}`` on failure.
        """
        steps = [
            ("git", "init"),
            ("git", "config user.email engineering-bot@localhost"),
            ("git", "config user.name Engineering-Bot"),
        ]

        outputs: list[str] = []
        for cmd_parts in steps:
            if len(cmd_parts) == 1:
                result = self._runner.run_command(cmd_parts[0])
            else:
                result = self._runner.run_command(f"{cmd_parts[0]} {cmd_parts[1]}")
            outputs.append(result.get("stdout", "") + result.get("stderr", ""))
            if result["exit_code"] != 0:
                return {
                    "status": "error",
                    "output": "\n".join(outputs),
                }

        # Write a .gitignore so __pycache__ and node_modules don't pollute diffs
        gitignore_path = self._workspace / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(
                "__pycache__/\n*.pyc\nnode_modules/\n.env\n", encoding="utf-8"
            )

        # Initial commit (git add then commit)
        add_result = self._runner.run_command("git add -A")
        outputs.append(add_result.get("stdout", "") + add_result.get("stderr", ""))
        if add_result["exit_code"] != 0:
            return {"status": "error", "output": "\n".join(outputs)}

        commit_result = self._runner.run_command(
            "git commit -m init"
        )
        outputs.append(
            commit_result.get("stdout", "") + commit_result.get("stderr", "")
        )
        # exit code 1 is acceptable when there's nothing to commit
        if commit_result["exit_code"] not in (0, 1):
            return {"status": "error", "output": "\n".join(outputs)}

        return {"status": "ok", "output": "\n".join(outputs).strip()}

    def commit(self, message: str) -> dict:
        """Stage all changes and create a commit with *message*.

        Parameters
        ----------
        message:
            Commit message text.  Single-word or simple multi-word strings
            work; avoid shell special characters.

        Returns
        -------
        dict
            ``{"status": "ok", "output": str}`` on success.
        """
        add_result = self._runner.run_command("git add -A")
        if add_result["exit_code"] != 0:
            return {
                "status": "error",
                "output": add_result.get("stderr", ""),
            }

        # Build the commit command — message is passed as a single token after
        # simple sanitisation (strip quotes that could confuse the split).
        safe_message = message.replace('"', "'").replace("`", "'")
        commit_result = self._runner.run_command(
            f"git commit -m {safe_message}"
        )

        combined = (
            commit_result.get("stdout", "")
            + commit_result.get("stderr", "")
        ).strip()

        if commit_result["exit_code"] not in (0, 1):
            return {"status": "error", "output": combined}

        return {"status": "ok", "output": combined}

    def diff(self) -> dict:
        """Return combined unstaged and staged diffs.

        Returns
        -------
        dict
            ``{"status": "ok", "unstaged": str, "staged": str}``
        """
        unstaged = self._runner.run_command("git diff")
        staged = self._runner.run_command("git diff --cached")

        return {
            "status": "ok",
            "unstaged": unstaged.get("stdout", ""),
            "staged": staged.get("stdout", ""),
        }
