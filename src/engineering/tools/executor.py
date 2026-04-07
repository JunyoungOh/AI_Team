"""Engineering mode — ToolExecutor dispatcher with phase-based access control.

The Anthropic API sends ``tool_use`` blocks whose ``name`` and ``input``
are routed here.  Access is gated by the current workflow *Phase*: only
the tool names listed in :data:`_PHASE_TOOLS` for the active phase are
permitted.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from src.engineering.tools.file_manager import FileManager
from src.engineering.tools.git_manager import GitManager
from src.engineering.tools.process_manager import ProcessManager
from src.engineering.tools.terminal_runner import TerminalRunner


# ---------------------------------------------------------------------------
# Phase enum
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    """Workflow phases for the AI Engineering session."""

    BRAINSTORM = "brainstorm"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    COMPLETE = "complete"


# ---------------------------------------------------------------------------
# Phase → allowed tool names
# ---------------------------------------------------------------------------

_PHASE_TOOLS: dict[Phase, set[str]] = {
    Phase.BRAINSTORM: set(),
    Phase.PLAN: set(),
    Phase.IMPLEMENT: {
        "write_file",
        "read_file",
        "edit_file",
        "delete_file",
        "list_files",
        "run_command",
        "git_init",
        "git_commit",
        "git_diff",
        "start_dev_server",
        "stop_dev_server",
    },
    Phase.VERIFY: {
        "read_file",
        "list_files",
        "run_command",
        "git_diff",
        "stop_dev_server",
    },
    Phase.COMPLETE: {
        "read_file",
        "list_files",
        "stop_dev_server",
    },
}

# ---------------------------------------------------------------------------
# Anthropic tool schema definitions
# ---------------------------------------------------------------------------

_ALL_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "write_file",
        "description": "Write content to a file inside the workspace, creating parent directories as needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within the workspace."},
                "content": {"type": "string", "description": "Text content to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "read_file",
        "description": "Read and return the text content of a file inside the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within the workspace."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace the first occurrence of old_text with new_text in an existing file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within the workspace."},
                "old_text": {"type": "string", "description": "Exact text to replace."},
                "new_text": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within the workspace."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_files",
        "description": "List all files in the workspace (or a sub-directory).",
        "input_schema": {
            "type": "object",
            "properties": {
                "subpath": {
                    "type": "string",
                    "description": "Optional sub-directory relative to workspace root.",
                    "default": "",
                },
            },
            "required": [],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a sandboxed shell command inside the workspace.  "
            "Only whitelisted binaries (npm, node, python3, git, ls, ...) are allowed.  "
            "Shell metacharacters and dangerous flags are blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command string to execute."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120).",
                    "default": 120,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "git_init",
        "description": "Initialise a git repository in the workspace with an initial commit.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "git_commit",
        "description": "Stage all changes and create a git commit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "git_diff",
        "description": "Show unstaged and staged diffs in the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "start_dev_server",
        "description": "Start a background dev server.  Only one server may run at a time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to start the server."},
                "port": {"type": "integer", "description": "Port number (injected as PORT env var)."},
            },
            "required": ["command", "port"],
        },
    },
    {
        "name": "stop_dev_server",
        "description": "Stop the running background dev server.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# Index by name for O(1) lookup
_TOOL_DEFS_BY_NAME: dict[str, dict] = {d["name"]: d for d in _ALL_TOOL_DEFINITIONS}


# ---------------------------------------------------------------------------
# ToolExecutor
# ---------------------------------------------------------------------------


class ToolExecutor:
    """Dispatches Anthropic tool_use calls to the appropriate manager.

    Parameters
    ----------
    workspace:
        Absolute path to the session workspace directory.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = Path(workspace).resolve()
        self._file_manager = FileManager(self._workspace)
        self._terminal_runner = TerminalRunner(self._workspace)
        self._git_manager = GitManager(self._workspace)
        self._process_manager = ProcessManager(self._workspace)

    # ------------------------------------------------------------------
    # Phase-aware dispatch
    # ------------------------------------------------------------------

    def execute(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        phase: Phase,
    ) -> dict:
        """Dispatch *tool_name* with *tool_input* under *phase* access control.

        Returns
        -------
        dict
            Tool result, or an error dict when the tool is not permitted.
        """
        allowed = _PHASE_TOOLS.get(phase, set())
        if tool_name not in allowed:
            return {
                "status": "error",
                "message": (
                    f"Tool '{tool_name}' is not available in phase '{phase.value}'.  "
                    f"Allowed tools: {sorted(allowed) or 'none'}"
                ),
            }

        return self._dispatch(tool_name, tool_input)

    def get_tool_definitions(self, phase: Phase) -> list[dict]:
        """Return Anthropic API tool schemas for tools available in *phase*.

        Parameters
        ----------
        phase:
            Current workflow phase.

        Returns
        -------
        list[dict]
            A list of tool definition dicts in Anthropic ``tools`` format.
        """
        allowed = _PHASE_TOOLS.get(phase, set())
        return [_TOOL_DEFS_BY_NAME[name] for name in sorted(allowed) if name in _TOOL_DEFS_BY_NAME]

    # ------------------------------------------------------------------
    # Internal dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, tool_name: str, tool_input: dict[str, Any]) -> dict:
        """Route *tool_name* to the correct manager method."""
        try:
            if tool_name == "write_file":
                return self._file_manager.write_file(
                    tool_input["path"], tool_input["content"]
                )

            if tool_name == "read_file":
                content = self._file_manager.read_file(tool_input["path"])
                return {"status": "ok", "content": content}

            if tool_name == "edit_file":
                return self._file_manager.edit_file(
                    tool_input["path"],
                    tool_input["old_text"],
                    tool_input["new_text"],
                )

            if tool_name == "delete_file":
                return self._file_manager.delete_file(tool_input["path"])

            if tool_name == "list_files":
                files = self._file_manager.list_files(tool_input.get("subpath", ""))
                return {"status": "ok", "files": files}

            if tool_name == "run_command":
                return self._terminal_runner.run_command(
                    tool_input["command"],
                    timeout=tool_input.get("timeout", 120),
                )

            if tool_name == "git_init":
                return self._git_manager.init()

            if tool_name == "git_commit":
                return self._git_manager.commit(tool_input["message"])

            if tool_name == "git_diff":
                return self._git_manager.diff()

            if tool_name == "start_dev_server":
                return self._process_manager.start(
                    tool_input["command"], tool_input["port"]
                )

            if tool_name == "stop_dev_server":
                return self._process_manager.stop()

            return {"status": "error", "message": f"Unknown tool: '{tool_name}'"}

        except FileNotFoundError as exc:
            return {"status": "error", "message": str(exc)}
        except PermissionError as exc:
            return {"status": "error", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
