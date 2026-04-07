"""Engineering mode — sandboxed command execution with binary allowlist.

CRITICAL SECURITY MODULE.

All commands are run via asyncio.create_subprocess_exec() with shell=False.
Only binaries explicitly listed in ALLOWED_BINARIES are permitted.
Shell metacharacters and dangerous argument patterns are rejected before
any subprocess is spawned.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Binary allowlist
# Nominal paths listed here; each is resolved to its real on-disk location at
# module load time via shutil.which() for cross-platform portability (macOS
# ships many tools under /usr/local/bin or Homebrew prefixes).
# ---------------------------------------------------------------------------

_NOMINAL_BINARIES: dict[str, str] = {
    "npm": "/usr/bin/npm",
    "npx": "/usr/bin/npx",
    "node": "/usr/bin/node",
    "python": "/usr/bin/python3",
    "python3": "/usr/bin/python3",
    "pip": "/usr/bin/pip3",
    "pip3": "/usr/bin/pip3",
    "git": "/usr/bin/git",
    "ls": "/usr/bin/ls",
    "cat": "/usr/bin/cat",
    "mkdir": "/usr/bin/mkdir",
    "cp": "/usr/bin/cp",
    "mv": "/usr/bin/mv",
    "echo": "/usr/bin/echo",
    "touch": "/usr/bin/touch",
    "pwd": "/usr/bin/pwd",
    "find": "/usr/bin/find",
    "grep": "/usr/bin/grep",
}


def _resolve_binaries() -> dict[str, str]:
    """Resolve each nominal binary path to its real filesystem location.

    Checks the nominal path first; falls back to shutil.which() so the
    module works correctly on macOS where some tools differ in location.
    Binaries that cannot be found on the current system are omitted.
    """
    resolved: dict[str, str] = {}
    for name, nominal in _NOMINAL_BINARIES.items():
        if os.path.isfile(nominal) and os.access(nominal, os.X_OK):
            resolved[name] = nominal
        else:
            found = shutil.which(name)
            if found:
                resolved[name] = found
    return resolved


# Module-level constant: resolved once at import time.
ALLOWED_BINARIES: dict[str, str] = _resolve_binaries()

# ---------------------------------------------------------------------------
# Regex guards
# ---------------------------------------------------------------------------

# Reject any command string containing shell metacharacters
_SHELL_META_RE = re.compile(r"[|;&]|\$\(|`")

# Reject dangerous flags that allow code-in-argument execution
_DANGEROUS_ARGS_RE = re.compile(
    r"(?:^|(?<=\s))(?:-e|-c|--upload-pack|--exec)(?:\s|$)"
)

# Restricted PATH injected into child-process environment
_RESTRICTED_PATH = "/usr/bin:/usr/local/bin"


# ---------------------------------------------------------------------------
# TerminalRunner
# ---------------------------------------------------------------------------


class TerminalRunner:
    """Sandboxed command runner scoped to a single workspace directory.

    All subprocess invocations use asyncio.create_subprocess_exec() with
    shell=False and a restricted PATH.  The working directory is always set
    to the workspace root.

    Parameters
    ----------
    workspace:
        Absolute path to the session workspace directory.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace).resolve()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_command(self, command: str) -> tuple[list[str], str | None]:
        """Validate *command* and return (args_list, error_or_None).

        Security checks applied in order:
        1. Shell metacharacter rejection.
        2. Binary allowlist enforcement.
        3. Dangerous argument pattern rejection.
        """
        if _SHELL_META_RE.search(command):
            return [], f"Shell metacharacters are not allowed: '{command}'"

        tokens = command.split()
        if not tokens:
            return [], "Empty command"

        binary_name = tokens[0]
        if binary_name not in ALLOWED_BINARIES:
            return [], (
                f"Binary '{binary_name}' is not in the allowed list. "
                f"Allowed: {sorted(ALLOWED_BINARIES.keys())}"
            )

        resolved_binary = ALLOWED_BINARIES[binary_name]

        args_tail = " ".join(tokens[1:])
        if _DANGEROUS_ARGS_RE.search(args_tail):
            return [], f"Dangerous argument pattern detected: '{args_tail}'"

        return [resolved_binary] + tokens[1:], None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_command(self, command: str, timeout: int = 120) -> dict:
        """Execute *command* sandboxed inside the workspace.

        Returns
        -------
        dict
            ``{"exit_code": int, "stdout": str, "stderr": str}``

            * Validation failures return ``exit_code=-1`` with the
              rejection reason in ``stderr``.
            * Timeout returns ``exit_code=-1`` with ``"timeout: ..."``
              in ``stderr``.
        """
        args, error = self._validate_command(command)
        if error:
            return {"exit_code": -1, "stdout": "", "stderr": error}

        return asyncio.run(self._run_async(args, timeout))

    async def _run_async(self, args: list[str], timeout: int) -> dict:
        """Internal async runner — uses create_subprocess_exec (no shell)."""
        restricted_env = {"PATH": _RESTRICTED_PATH}

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._workspace),
            env=restricted_env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                stdout_bytes, stderr_bytes = await proc.communicate()
            except Exception:
                stdout_bytes, stderr_bytes = b"", b""
            return {
                "exit_code": -1,
                "stdout": stdout_bytes.decode("utf-8", errors="replace"),
                "stderr": f"timeout: command exceeded {timeout}s",
            }

        return {
            "exit_code": proc.returncode if proc.returncode is not None else -1,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        }
