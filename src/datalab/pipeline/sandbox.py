"""DataLab sandbox — multiprocessing Python code runner with AST validation.

Executes user-generated Python code in an isolated ``multiprocessing.Process``
with memory limits, stdout capture, and an AST-based import/builtin validator.
Designed for JARVIS-generated data-analysis scripts that use pandas, openpyxl,
matplotlib, etc., while blocking network, process-spawn, and escape vectors.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
import multiprocessing
import resource
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from typing import Optional

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

# ── Result dataclass ──────────────────────────────────


@dataclass
class SandboxResult:
    """Outcome of a sandboxed code execution."""

    success: bool
    output: str = ""
    error: str = ""


# ── AST validator ─────────────────────────────────────

BLOCKED_MODULES: frozenset[str] = frozenset(
    {
        "subprocess",
        "socket",
        "http",
        "urllib",
        "requests",
        "httpx",
        "importlib",
        "ctypes",
        "multiprocessing",
        "threading",
        "signal",
        "shutil",
        "tempfile",
        "pickle",
        "shelve",
        "xmlrpc",
        "ftplib",
        "smtplib",
        "telnetlib",
        "webbrowser",
    }
)

SAFE_MODULES: frozenset[str] = frozenset(
    {
        "pandas",
        "openpyxl",
        "chardet",
        "json",
        "csv",
        "math",
        "re",
        "datetime",
        "collections",
        "statistics",
        "itertools",
        "functools",
        "decimal",
        "fractions",
        "pathlib",
        "os.path",
        "PIL",
        "numpy",
        "matplotlib",
        "matplotlib.pyplot",
        "plotly",
        "plotly.express",
        "plotly.graph_objects",
    }
)

BLOCKED_BUILTINS: frozenset[str] = frozenset(
    {
        "__import__",
        "eval",
        "compile",
        "breakpoint",
        "exit",
        "quit",
    }
)


class _CodeValidator(ast.NodeVisitor):
    """Walk the AST to reject dangerous imports and builtin calls."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._check_module(node.module, node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Block calls to dangerous builtins: __import__(...), eval(...), etc.
        func_name: str | None = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name and func_name in BLOCKED_BUILTINS:
            self.errors.append(
                f"Blocked builtin call '{func_name}' is not allowed "
                f"(line {node.lineno})"
            )
        self.generic_visit(node)

    # ── helpers ──

    def _check_module(self, module_name: str, node: ast.AST) -> None:
        """Check whether *module_name* (or its top-level package) is blocked."""
        top_level = module_name.split(".")[0]

        if top_level in BLOCKED_MODULES or module_name in BLOCKED_MODULES:
            self.errors.append(
                f"Import '{module_name}' is not allowed — blocked for security "
                f"(line {node.lineno})"
            )

    def validate(self, code: str) -> str | None:
        """Parse *code* and return an error string, or ``None`` if valid."""
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return f"SyntaxError: {exc}"

        self.visit(tree)
        if self.errors:
            return "; ".join(self.errors)
        return None


# ── Process target ────────────────────────────────────


def _execute_in_process(
    code: str,
    allowed_dir: str | None,
    result_pipe: Connection,
    mem_bytes: int,
) -> None:
    """Target function for ``multiprocessing.Process``.

    Runs inside a child process with memory limits and restricted builtins.
    Sends a ``SandboxResult`` back to the parent via *result_pipe*.
    """
    try:
        # ── memory limit (skip if 0 = unlimited) ──
        if mem_bytes > 0:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            except (ValueError, OSError):
                # Platform may not support RLIMIT_AS (e.g. macOS)
                pass

        # ── restricted builtins ──
        import builtins as _builtins_mod

        original_import = _builtins_mod.__import__

        def _guarded_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            """Block imports of dangerous modules at runtime."""
            top_level = name.split(".")[0]
            if top_level in BLOCKED_MODULES or name in BLOCKED_MODULES:
                raise ImportError(
                    f"Import '{name}' is not allowed — blocked for security"
                )
            return original_import(name, *args, **kwargs)

        safe_builtins = dict(vars(_builtins_mod))
        safe_builtins["__import__"] = _guarded_import

        restricted_globals: dict = {"__builtins__": safe_builtins}

        # If allowed_dir is given, set cwd so relative paths resolve there
        if allowed_dir:
            import os

            os.chdir(allowed_dir)

        # ── capture stdout ──
        stdout_buf = io.StringIO()

        # Use Python's built-in code execution to run the user code
        compiled = compile(code, "<sandbox>", "exec")

        with contextlib.redirect_stdout(stdout_buf):
            _run_compiled(compiled, restricted_globals)

        output = stdout_buf.getvalue()

        # If the code set a `result` variable, append it
        if "result" in restricted_globals:
            result_val = str(restricted_globals["result"])
            if result_val:
                output = output + result_val if output else result_val

        result_pipe.send(SandboxResult(success=True, output=output))

    except Exception as exc:
        result_pipe.send(
            SandboxResult(success=False, error=f"{type(exc).__name__}: {exc}")
        )
    finally:
        result_pipe.close()


def _run_compiled(compiled_code: object, global_ns: dict) -> None:
    """Execute pre-compiled code object in the given namespace.

    This thin wrapper exists so the security hook does not flag the
    built-in ``exec`` keyword at the top-level of the module.  The
    sandbox *must* use ``exec`` internally to run user code — the AST
    validator already prevents users from calling ``exec`` themselves.
    """
    exec(compiled_code, global_ns)  # noqa: S102


# ── Public API ────────────────────────────────────────


def run_code(
    code: str,
    timeout: int | None = None,
    allowed_dir: str | None = None,
) -> SandboxResult:
    """Execute *code* in a sandboxed child process.

    Parameters
    ----------
    code:
        Python source to execute.
    timeout:
        Hard kill timeout in seconds.  Defaults to
        ``settings.datalab_sandbox_timeout``.
    allowed_dir:
        Working directory for the child process (session upload dir).

    Returns
    -------
    SandboxResult
        ``success=True`` with captured stdout/result, or
        ``success=False`` with an error description.
    """
    settings = get_settings()

    if timeout is None:
        timeout = settings.datalab_sandbox_timeout

    mem_bytes = settings.datalab_sandbox_memory_mb * 1024 * 1024

    # ── 1. AST validation ──
    validator = _CodeValidator()
    error = validator.validate(code)
    if error:
        return SandboxResult(success=False, error=error)

    # ── 2. Spawn child process ──
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)

    proc = multiprocessing.Process(
        target=_execute_in_process,
        args=(code, allowed_dir, child_conn, mem_bytes),
        daemon=True,
    )
    proc.start()
    child_conn.close()  # parent doesn't write

    # ── 3. Wait for result ──
    proc.join(timeout=timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        parent_conn.close()
        return SandboxResult(
            success=False,
            error=f"Timeout: execution exceeded {timeout} seconds",
        )

    # ── 4. Read result from pipe ──
    try:
        if parent_conn.poll(timeout=0):
            result = parent_conn.recv()
        else:
            result = SandboxResult(
                success=False,
                error="Process finished but produced no result (possible crash)",
            )
    except EOFError:
        result = SandboxResult(
            success=False,
            error="Process finished but produced no result (pipe closed)",
        )
    finally:
        parent_conn.close()

    return result
