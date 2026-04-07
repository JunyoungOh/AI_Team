"""Engineering mode — single background dev-server manager.

Only one dev server may run per session.  Attempts to start a second
server while one is already running are rejected.  Termination uses
SIGTERM with a 5-second grace period then SIGKILL.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

from src.engineering.tools.terminal_runner import (
    ALLOWED_BINARIES,
    _RESTRICTED_PATH,
    _SHELL_META_RE,
)

# asyncio subprocess launcher (no shell)
_spawn_subprocess = asyncio.create_subprocess_exec  # noqa: E501


class ProcessManager:
    """Manages a single background dev-server process for a session.

    Parameters
    ----------
    workspace:
        Absolute path to the session workspace.  The process is started
        with this directory as its working directory.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace).resolve()
        self._proc: asyncio.subprocess.Process | None = None
        self._command: str | None = None
        self._port: int | None = None
        self._started_at: float | None = None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        """Return ``True`` when a background process is active."""
        if self._proc is None:
            return False
        return self._proc.returncode is None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, command: str, port: int) -> dict:
        """Start a background dev server.

        Parameters
        ----------
        command:
            Shell-free command string (first token must be in
            :data:`~src.engineering.tools.terminal_runner.ALLOWED_BINARIES`).
        port:
            Port number injected as the ``PORT`` environment variable.

        Returns
        -------
        dict
            ``{"status": "ok", "pid": int, "port": int}`` on success.
            ``{"status": "error", "message": str}`` on failure.
        """
        if self.is_running():
            return {
                "status": "error",
                "message": (
                    f"A dev server is already running (PID {self._proc.pid}, "  # type: ignore[union-attr]
                    f"port {self._port}).  Stop it first."
                ),
            }

        if _SHELL_META_RE.search(command):
            return {
                "status": "error",
                "message": f"Shell metacharacters are not allowed: '{command}'",
            }

        tokens = command.split()
        if not tokens:
            return {"status": "error", "message": "Empty command"}

        binary_name = tokens[0]
        if binary_name not in ALLOWED_BINARIES:
            return {
                "status": "error",
                "message": f"Binary '{binary_name}' is not in the allowed list.",
            }

        resolved_binary = ALLOWED_BINARIES[binary_name]
        args = [resolved_binary] + tokens[1:]
        env = {"PATH": _RESTRICTED_PATH, "PORT": str(port)}

        try:
            proc = asyncio.run(self._spawn_proc(args, env))
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

        self._proc = proc
        self._command = command
        self._port = port
        self._started_at = time.time()

        return {"status": "ok", "pid": proc.pid, "port": port}

    def stop(self) -> dict:
        """Stop the background dev server.

        Sends SIGTERM and waits up to 5 seconds; escalates to SIGKILL if
        the process has not exited.

        Returns
        -------
        dict
            ``{"status": "ok", "message": str}`` on success.
            ``{"status": "error", "message": str}`` if no server is running.
        """
        if not self.is_running():
            return {"status": "error", "message": "No dev server is currently running."}

        proc = self._proc
        assert proc is not None
        pid = proc.pid

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if proc.returncode is not None:
                break
            time.sleep(0.1)

        if proc.returncode is None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            time.sleep(0.2)

        self._proc = None
        self._command = None
        self._port = None
        self._started_at = None

        return {"status": "ok", "message": f"Dev server (PID {pid}) stopped."}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _spawn_proc(
        self, args: list[str], env: dict[str, str]
    ) -> asyncio.subprocess.Process:
        """Spawn the dev-server process without a shell and return immediately."""
        proc = await _spawn_subprocess(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._workspace),
            env=env,
        )
        return proc
