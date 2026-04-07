"""DataLab tool schemas + executors — run_python, read_uploaded_data, export_file.

These are the three DataLab-specific tools that JARVIS uses inside the
streaming tool_use loop.  Each tool has a JSON schema (for the Anthropic
API ``tools`` parameter) and an async executor function.

Session context (uploads_dir, outputs_dir, workspace_dir) is passed as
the first ``ctx`` argument to every executor.  The caller (JarvisEngine)
binds this via ``functools.partial`` so that each engine instance has its
own isolated context — no module-level global state.
"""
from __future__ import annotations

import logging
import unicodedata
from pathlib import Path
from typing import Any, Callable, Coroutine

from src.datalab.pipeline.ingest import identify_format, FileInfo
from src.datalab.pipeline.sandbox import run_code, SandboxResult

logger = logging.getLogger(__name__)


# ── Session context factory ──────────────────────────────


def make_session_context(session_dir: str) -> dict[str, str]:
    """Create and return a session-scoped directory context dict.

    Called once per ``JarvisEngine`` init.  The returned dict is passed
    to executor functions via ``functools.partial`` — there is no shared
    module-level state, so concurrent sessions stay isolated.
    """
    base = Path(session_dir)
    return {
        "uploads_dir": str(base / "uploads"),
        "outputs_dir": str(base / "outputs"),
        "workspace_dir": str(base / "workspace"),
        "session_dir": str(base),
    }


# ── Helpers ──────────────────────────────────────────────


def _find_file_fuzzy(directory: Path, filename: str) -> Path | None:
    """Find a file in *directory* tolerating Unicode normalisation diffs.

    macOS APFS may store filenames with different NFC/NFD forms than
    what Claude generates in tool-call parameters.  This helper
    normalises both sides to NFC before comparing.
    """
    norm_target = unicodedata.normalize("NFC", filename.strip())
    for f in directory.iterdir():
        if f.is_file():
            norm_name = unicodedata.normalize("NFC", f.name.strip())
            if norm_name == norm_target:
                return f
    return None


def _sync_upload_symlinks(uploads_dir: str, workspace_dir: str) -> None:
    """Ensure every uploaded file is symlinked into the workspace.

    This lets Python code use simple relative filenames (e.g.
    ``pd.read_excel("data.xlsx")``) instead of constructing full
    absolute paths — eliminating a common source of FileNotFoundError.
    """
    uploads = Path(uploads_dir)
    workspace = Path(workspace_dir)
    if not uploads.exists():
        return
    for f in uploads.iterdir():
        if not f.is_file():
            continue
        link = workspace / f.name
        if link.exists() or link.is_symlink():
            continue
        try:
            link.symlink_to(f.resolve())
        except OSError:
            # Symlinks may not be supported; fall back to hard copy
            try:
                link.write_bytes(f.read_bytes())
            except OSError:
                logger.debug("Cannot link/copy %s to workspace", f.name)


# ── Tool executors ───────────────────────────────────────
#
# Every executor takes ``ctx: dict[str, str]`` as its first argument.
# JarvisEngine binds this via functools.partial so that **inputs from
# Claude's tool_use blocks can be forwarded directly with **kwargs.


async def _run_python(ctx: dict[str, str], code: str) -> str:
    """Execute Python code in the sandbox and return output.

    Before execution, uploaded files are symlinked into the workspace so
    that relative filenames resolve correctly.

    Uses run_in_executor to avoid blocking the asyncio event loop —
    run_code() internally calls proc.join() which is synchronous.
    """
    import asyncio

    workspace = ctx.get("workspace_dir")
    if not workspace:
        return "Error: session context not initialised (workspace_dir missing)"

    # Make uploaded files accessible via relative paths in the sandbox
    uploads = ctx.get("uploads_dir")
    if uploads:
        _sync_upload_symlinks(uploads, workspace)
        # Force-copy if symlinks failed
        uploads_path = Path(uploads)
        workspace_path = Path(workspace)
        if uploads_path.exists():
            for f in uploads_path.iterdir():
                if f.is_file():
                    dest = workspace_path / f.name
                    if not dest.exists():
                        try:
                            import shutil
                            shutil.copy2(str(f), str(dest))
                        except Exception:
                            pass

    loop = asyncio.get_running_loop()
    result: SandboxResult = await loop.run_in_executor(
        None, lambda: run_code(code, allowed_dir=workspace)
    )
    if result.success:
        output = result.output or "(no output)"
        # Truncate very large outputs to stay within Claude context limits
        if len(output) > 16_000:
            output = output[:16_000] + "\n\n... [truncated — output too large]"
        return output
    return f"Error: {result.error}"


async def _read_uploaded_data(ctx: dict[str, str], filename: str | None = None) -> str:
    """Read metadata from uploaded file(s).

    If *filename* is given, inspect that single file.  Otherwise list all
    files in the uploads directory with their metadata.  Uses fuzzy
    filename matching to tolerate Unicode normalisation differences.
    """
    uploads_dir = ctx.get("uploads_dir")
    if not uploads_dir:
        return "Error: session context not initialised (uploads_dir missing)"

    uploads = Path(uploads_dir)
    if not uploads.exists():
        return "Error: uploads directory does not exist"

    if filename:
        target = uploads / filename
        # Path-traversal guard
        if not target.resolve().is_relative_to(uploads.resolve()):
            return "Error: invalid filename (path traversal blocked)"
        if not target.exists():
            # Fallback: Unicode-normalised fuzzy match
            fuzzy = _find_file_fuzzy(uploads, filename)
            if not fuzzy:
                return f"Error: file '{filename}' not found in uploads"
            target = fuzzy
        return _file_info_to_str(identify_format(target))

    # List all uploaded files
    files = sorted(uploads.iterdir())
    if not files:
        return "No files uploaded yet."

    parts: list[str] = []
    for f in files:
        if f.is_file():
            parts.append(_file_info_to_str(identify_format(f)))
    return "\n\n---\n\n".join(parts) if parts else "No files found."


def _file_info_to_str(info: FileInfo) -> str:
    """Format a FileInfo as a human-readable string for Claude."""
    lines = [
        f"File: {info.path.name}",
        f"Format: {info.format}",
        f"Encoding: {info.encoding}",
    ]
    if info.rows:
        lines.append(f"Rows: {info.rows}")
    if info.columns:
        lines.append(f"Columns: {info.columns}")
    if info.column_names:
        lines.append(f"Column names: {', '.join(info.column_names)}")
    if info.sheets:
        lines.append(f"Sheets: {info.sheets} ({', '.join(info.sheet_names)})")
    if info.merged_cells_count:
        lines.append(f"Merged cells: {info.merged_cells_count}")
    if info.sample_rows:
        lines.append("Sample rows (first 5):")
        for i, row in enumerate(info.sample_rows[:5], 1):
            lines.append(f"  {i}. {row}")
    return "\n".join(lines)


async def _export_file(ctx: dict[str, str], filename: str, content: str, encoding: str = "utf-8") -> str:
    """Write text content to the outputs directory and return the path.

    For text-based formats (CSV, JSON, HTML, Markdown).  Binary files
    (Excel, images) should be written directly in ``run_python`` code.
    """
    outputs_dir = ctx.get("outputs_dir")
    if not outputs_dir:
        return "Error: session context not initialised (outputs_dir missing)"

    out_path = Path(outputs_dir) / filename
    # Path-traversal guard
    if not out_path.resolve().is_relative_to(Path(outputs_dir).resolve()):
        return "Error: invalid filename (path traversal blocked)"

    try:
        out_path.write_text(content, encoding=encoding)
        return f"File exported: {filename} ({len(content)} chars)"
    except Exception as exc:
        return f"Error writing file: {exc}"


# ── Tool schemas (Anthropic API format) ──────────────────

RUN_PYTHON_SCHEMA: dict[str, Any] = {
    "name": "run_python",
    "description": (
        "Python 코드를 샌드박스에서 실행합니다. pandas, openpyxl, numpy, matplotlib 등 "
        "데이터 분석 라이브러리를 사용할 수 있습니다. 수치 계산은 반드시 이 도구로 실행하세요. "
        "네트워크 접근, subprocess, eval/exec는 차단됩니다. "
        "업로드된 파일은 작업 디렉토리에 심링크되어 있으므로 파일명만으로 접근 가능합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "실행할 Python 코드. print()로 결과를 출력하거나 result 변수에 할당하세요.",
            },
        },
        "required": ["code"],
    },
}

READ_UPLOADED_DATA_SCHEMA: dict[str, Any] = {
    "name": "read_uploaded_data",
    "description": (
        "업로드된 파일의 메타데이터를 조회합니다 — 컬럼명, 행 수, 인코딩, 샘플 데이터 등. "
        "filename을 지정하면 해당 파일만, 생략하면 전체 파일 목록을 반환합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "조회할 파일명 (생략 시 전체 목록)",
            },
        },
        "required": [],
    },
}

EXPORT_FILE_SCHEMA: dict[str, Any] = {
    "name": "export_file",
    "description": (
        "텍스트 기반 결과 파일(CSV, JSON, HTML, Markdown)을 생성합니다. "
        "Excel이나 이미지 등 바이너리 파일은 run_python에서 직접 outputs 디렉토리에 저장하세요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "출력 파일명 (예: report.html, data.csv)",
            },
            "content": {
                "type": "string",
                "description": "파일에 쓸 텍스트 내용",
            },
            "encoding": {
                "type": "string",
                "description": "파일 인코딩 (기본: utf-8)",
            },
        },
        "required": ["filename", "content"],
    },
}


# ── Registries ───────────────────────────────────────────

DATALAB_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "run_python": RUN_PYTHON_SCHEMA,
    "read_uploaded_data": READ_UPLOADED_DATA_SCHEMA,
    "export_file": EXPORT_FILE_SCHEMA,
}

# Unbound executor references — JarvisEngine binds ctx via functools.partial
DATALAB_TOOL_EXECUTORS: dict[str, Callable[..., Coroutine[Any, Any, str]]] = {
    "run_python": _run_python,
    "read_uploaded_data": _read_uploaded_data,
    "export_file": _export_file,
}
