"""Foresight tool schemas + executors — for the Sage engine.

Eight tools that SageEngine uses via the Anthropic API ``tools`` parameter.
Each tool has a JSON schema (for the API) and an async executor function.

Session context (uploads_dir, outputs_dir, workspace_dir, user_id) is passed
as the first ``ctx`` argument to every executor.  The caller (SageEngine)
binds this via ``functools.partial`` so that each engine instance has its
own isolated context — no module-level global state.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path
from typing import Any, Callable, Coroutine

from src.datalab.pipeline.ingest import identify_format
from src.datalab.pipeline.sandbox import run_code, SandboxResult
from src.foresight.storage import (
    DEFAULT_STORAGE_DIR,
    check_storage_quota,
    list_environments,
    load_environment,
    save_environment,
)

logger = logging.getLogger(__name__)


# ── Session context factory ──────────────────────────────


def make_session_context(session_dir: str, user_id: str = "") -> dict[str, str]:
    """Create and return a session-scoped directory context dict.

    Called once per ``SageEngine`` init.  The returned dict is passed
    to executor functions via ``functools.partial`` — there is no shared
    module-level state, so concurrent sessions stay isolated.
    """
    base = Path(session_dir)
    for sub in ("uploads", "outputs", "workspace"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return {
        "uploads_dir": str(base / "uploads"),
        "outputs_dir": str(base / "outputs"),
        "workspace_dir": str(base / "workspace"),
        "session_dir": str(base),
        "user_id": user_id,
    }


# ── Helpers ──────────────────────────────────────────────


def _find_file_fuzzy(directory: Path, filename: str) -> Path | None:
    """Find a file in *directory* tolerating Unicode normalisation diffs and case.

    Normalises both sides to NFC before comparing, and also performs a
    case-insensitive fallback so that Claude-generated filenames match
    regardless of capitalisation.
    """
    norm_target = unicodedata.normalize("NFC", filename.strip())
    # Exact NFC match first
    for f in directory.iterdir():
        if f.is_file():
            if unicodedata.normalize("NFC", f.name.strip()) == norm_target:
                return f
    # Case-insensitive fallback
    norm_lower = norm_target.lower()
    for f in directory.iterdir():
        if f.is_file():
            if unicodedata.normalize("NFC", f.name.strip()).lower() == norm_lower:
                return f
    return None


def _sync_upload_symlinks(uploads_dir: str, workspace_dir: str) -> None:
    """Ensure every uploaded file is symlinked into the workspace.

    Lets Python code use simple relative filenames (e.g.
    ``pd.read_csv("data.csv")``) instead of constructing full absolute
    paths — eliminating a common source of FileNotFoundError.

    Also ensures an ``outputs`` symlink exists in the workspace so that
    ``plt.savefig('outputs/chart.png')`` resolves correctly from the
    sandbox working directory.
    """
    uploads = Path(uploads_dir)
    workspace = Path(workspace_dir)
    if not uploads.exists():
        return

    # Symlink outputs directory into workspace for relative path access
    outputs_link = workspace / "outputs"
    outputs_real = workspace.parent / "outputs"
    if outputs_real.exists() and not outputs_link.exists() and not outputs_link.is_symlink():
        try:
            outputs_link.symlink_to(outputs_real.resolve())
        except OSError:
            logger.debug("Cannot symlink outputs into workspace")

    for f in uploads.iterdir():
        if not f.is_file():
            continue
        link = workspace / f.name
        if link.exists() or link.is_symlink():
            continue
        try:
            link.symlink_to(f.resolve())
        except OSError:
            try:
                link.write_bytes(f.read_bytes())
            except OSError:
                logger.debug("Cannot link/copy %s to workspace", f.name)


# ── Tool executors ───────────────────────────────────────
#
# Every executor takes ``ctx: dict[str, str]`` as its first argument.
# SageEngine binds this via functools.partial so that **inputs from
# Claude's tool_use blocks can be forwarded directly with **kwargs.


async def _read_uploaded_file(ctx: dict[str, str], filename: str) -> str:
    """Read an uploaded file and return its content or structure summary."""
    uploads_dir = ctx.get("uploads_dir")
    if not uploads_dir:
        return "Error: session context not initialised (uploads_dir missing)"

    uploads = Path(uploads_dir)
    if not uploads.exists():
        return "Error: uploads directory does not exist"

    target = uploads / filename
    # Path-traversal guard
    try:
        if not target.resolve().is_relative_to(uploads.resolve()):
            return "Error: invalid filename (path traversal blocked)"
    except ValueError:
        return "Error: invalid filename (path traversal blocked)"

    if not target.exists():
        fuzzy = _find_file_fuzzy(uploads, filename)
        if not fuzzy:
            available = [f.name for f in uploads.iterdir() if f.is_file()]
            available_str = ", ".join(available) if available else "(없음)"
            return f"Error: 파일 '{filename}'을(를) uploads에서 찾을 수 없습니다. 사용 가능한 파일: {available_str}"
        target = fuzzy

    # Try structured parsing first (CSV, Excel, JSON, etc.)
    suffix = target.suffix.lower()
    structured_suffixes = {".csv", ".xlsx", ".xls", ".json", ".parquet", ".tsv"}
    if suffix in structured_suffixes:
        try:
            info = identify_format(target)
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
        except Exception as exc:
            logger.debug("identify_format failed for %s: %s", target.name, exc)

    # Fallback: raw text read (truncated to ~3000 chars for preview)
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > 3000:
            content = content[:3000] + "\n\n... [미리보기 — 전체 파일은 run_python으로 로드하세요]"
        return f"File: {target.name}\n\n{content}"
    except Exception as exc:
        return f"Error reading file '{target.name}': {exc}"


async def _save_environment(ctx: dict[str, str], name: str, data: str) -> str:
    """Save a named environment snapshot to persistent storage."""
    user_id = ctx.get("user_id", "")

    # Check quota before saving
    if not check_storage_quota(DEFAULT_STORAGE_DIR, user_id):
        return "Error: 저장 공간 한도(200 MB)에 도달했습니다. 기존 환경을 삭제한 후 다시 시도하세요."

    # Accept JSON string or free text; store as {"content": ...} if not valid JSON
    try:
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            parsed = {"content": parsed}
    except json.JSONDecodeError:
        parsed = {"content": data}

    try:
        path = save_environment(DEFAULT_STORAGE_DIR, user_id, name, parsed)
        return f"환경 '{name}'이(가) 저장되었습니다. (경로: {path})"
    except Exception as exc:
        return f"Error saving environment '{name}': {exc}"


async def _load_environment(ctx: dict[str, str], name: str) -> str:
    """Load a named environment from persistent storage."""
    user_id = ctx.get("user_id", "")

    try:
        result = load_environment(DEFAULT_STORAGE_DIR, user_id, name)
    except Exception as exc:
        return f"Error loading environment '{name}': {exc}"

    if result is None:
        available = list_environments(DEFAULT_STORAGE_DIR, user_id)
        if available:
            available_str = ", ".join(available)
            return f"환경 '{name}'을(를) 찾을 수 없습니다. 저장된 환경 목록: {available_str}"
        return f"환경 '{name}'을(를) 찾을 수 없습니다. 저장된 환경이 없습니다."

    try:
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception:
        return str(result)


async def _list_environments(ctx: dict[str, str]) -> str:
    """List all saved environments for the current user."""
    user_id = ctx.get("user_id", "")

    try:
        names = list_environments(DEFAULT_STORAGE_DIR, user_id)
    except Exception as exc:
        return f"Error listing environments: {exc}"

    if not names:
        return "저장된 환경이 없습니다."

    lines = [f"저장된 환경 목록 ({len(names)}개):"]
    for n in sorted(names):
        lines.append(f"  - {n}")
    return "\n".join(lines)


async def _export_report(ctx: dict[str, str], filename: str, content: str) -> str:
    """Write report content to the outputs directory."""
    outputs_dir = ctx.get("outputs_dir")
    if not outputs_dir:
        return "Error: session context not initialised (outputs_dir missing)"

    out_path = Path(outputs_dir) / filename
    # Path-traversal guard
    try:
        if not out_path.resolve().is_relative_to(Path(outputs_dir).resolve()):
            return "Error: invalid filename (path traversal blocked)"
    except ValueError:
        return "Error: invalid filename (path traversal blocked)"

    try:
        out_path.write_text(content, encoding="utf-8")
        return f"보고서 저장 완료: {filename} ({len(content)} 자)"
    except Exception as exc:
        return f"Error writing report '{filename}': {exc}"


async def _export_interactive_report(
    ctx: dict[str, str], *, title: str, sections: str = "[]", timeline_data: str = "{}"
) -> str:
    """Generate a self-contained interactive HTML report.

    Combines text sections with an inline SVG timeline that supports
    click-to-reveal node descriptions. The HTML file has zero external
    dependencies — all CSS and JS are inlined.
    """
    outputs_dir = ctx.get("outputs_dir")
    if not outputs_dir:
        return "Error: session context not initialised (outputs_dir missing)"

    try:
        sections_list = json.loads(sections)
    except json.JSONDecodeError:
        return f"Error: sections is not valid JSON — {sections[:200]}"

    try:
        tl_data = json.loads(timeline_data)
    except json.JSONDecodeError:
        tl_data = {}

    # Build HTML
    html_parts = [
        '<!DOCTYPE html>',
        '<html lang="ko">',
        '<head>',
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">',
        f'<title>{_html_escape(title)}</title>',
        '<style>',
        _REPORT_CSS,
        '</style>',
        '</head>',
        '<body>',
        f'<header class="rpt-header"><h1>{_html_escape(title)}</h1>',
        f'<p class="rpt-date">Generated: {_now_kst()}</p></header>',
        '<main class="rpt-main">',
    ]

    # Sections
    for sec in sections_list:
        heading = _html_escape(sec.get("heading", ""))
        sec_type = sec.get("type", "text")
        html_parts.append('<section class="rpt-section">')
        html_parts.append(f'<h2>{heading}</h2>')
        if sec_type == "text":
            content = _html_escape(sec.get("content", ""))
            for line in content.split("\n"):
                html_parts.append(f'<p>{line}</p>')
        elif sec_type == "table":
            # Table content is raw HTML — NOT escaped (Sage generates safe table markup)
            raw_content = sec.get("content", "")
            html_parts.append(f'<div class="rpt-table-wrap">{raw_content}</div>')
        html_parts.append('</section>')

    # Interactive timeline
    if tl_data:
        html_parts.append('<section class="rpt-section">')
        html_parts.append('<h2>Timeline Forecast</h2>')
        html_parts.append(_build_report_timeline_svg(tl_data))
        html_parts.append('</section>')

    html_parts.extend([
        '</main>',
        '<script>',
        _REPORT_JS,
        '</script>',
        '</body>',
        '</html>',
    ])

    html_content = "\n".join(html_parts)

    # Generate filename
    import re
    safe_title = re.sub(r'[^\w\s-]', '', title)[:30].strip().replace(' ', '_')
    filename = f"forecast_report_{safe_title}.html"

    out_path = Path(outputs_dir) / filename
    try:
        if not out_path.resolve().is_relative_to(Path(outputs_dir).resolve()):
            return "Error: invalid filename (path traversal blocked)"
    except ValueError:
        return "Error: invalid filename (path traversal blocked)"

    out_path.write_text(html_content, encoding="utf-8")
    return f"인터랙티브 보고서 생성 완료: {filename} ({len(html_content)} 자)"


def _html_escape(text: str) -> str:
    """Escape HTML special characters."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _now_kst() -> str:
    """Return current time in KST as a formatted string."""
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")


def _build_report_timeline_svg(tl_data: dict) -> str:
    """Build an inline SVG timeline from timeline data for the report."""
    nodes = tl_data.get("nodes", [])
    edges = tl_data.get("edges", [])
    events = tl_data.get("events", [])
    bands = tl_data.get("bands", [])

    svg_parts = [
        '<div class="rpt-timeline-wrap">',
        '<svg viewBox="0 0 900 500" class="rpt-timeline-svg">',
        # Grid
        '<line x1="80" y1="420" x2="820" y2="420" stroke="rgba(0,0,0,0.1)" stroke-width="1"/>',
    ]
    marks = [("80", "현재"), ("300", "6개월"), ("520", "1년"), ("740", "3년")]
    for mx, mt in marks:
        svg_parts.append(f'<text x="{mx}" y="445" text-anchor="middle" font-size="11" fill="#888">{mt}</text>')
        svg_parts.append(f'<line x1="{mx}" y1="420" x2="{mx}" y2="60" stroke="rgba(0,0,0,0.05)" stroke-dasharray="4,4"/>')

    # Present point
    svg_parts.append('<circle cx="80" cy="250" r="8" fill="#7c3aed"/>')

    # Bands
    for b in bands:
        svg_parts.append(f'<path d="{_html_escape(b.get("path",""))}" fill="{b.get("color","rgba(124,58,237,0.06)")}" stroke="none"/>')

    # Edges
    for e in edges:
        svg_parts.append(f'<path d="{_html_escape(e.get("path",""))}" fill="none" stroke="{e.get("color","#7c3aed")}" stroke-width="2"/>')

    # Nodes
    for n in nodes:
        x, y = n.get("x", 0), n.get("y", 0)
        color = n.get("color", "#7c3aed")
        label = _html_escape(n.get("label", ""))
        content = _html_escape(n.get("content", ""))
        node_id = _html_escape(n.get("id", ""))

        if n.get("node_type") == "fork":
            svg_parts.append(f'<circle cx="{x}" cy="{y}" r="10" fill="#f5f5f5" stroke="{color}" stroke-width="2" class="rpt-node" data-id="{node_id}"/>')
            svg_parts.append(f'<text x="{x}" y="{int(y)-14}" text-anchor="middle" font-size="10" fill="{color}" font-weight="600">{label}</text>')
        elif n.get("node_type") == "endpoint":
            prob = n.get("probability")
            svg_parts.append(f'<circle cx="{x}" cy="{y}" r="12" fill="{color}" fill-opacity="0.15" stroke="{color}" stroke-width="2" class="rpt-node" data-id="{node_id}"/>')
            svg_parts.append(f'<text x="{x}" y="{int(y)+4}" text-anchor="middle" font-size="9" fill="{color}" font-weight="bold">{label}</text>')
            if prob is not None:
                svg_parts.append(f'<text x="{int(x)+20}" y="{int(y)-8}" font-size="13" fill="{color}" font-weight="bold">{prob}%</text>')

        # Hidden tooltip
        if content:
            svg_parts.append(f'<foreignObject x="{int(x)+20}" y="{int(y)-60}" width="250" height="120" class="rpt-tooltip" data-for="{node_id}" style="display:none">')
            svg_parts.append(f'<div xmlns="http://www.w3.org/1999/xhtml" class="rpt-tip-box"><strong>{label}</strong><br/>{content}</div>')
            svg_parts.append('</foreignObject>')

    # Events
    for ev in events:
        x, y = ev.get("x", 0), ev.get("y", 0)
        label = _html_escape(ev.get("label", ""))
        svg_parts.append(f'<rect x="{int(x)-7}" y="{int(y)-7}" width="14" height="14" rx="3" fill="rgba(245,158,11,0.15)" stroke="#f59e0b" transform="rotate(45,{x},{y})"/>')
        svg_parts.append(f'<text x="{x}" y="{int(y)-14}" text-anchor="middle" font-size="9" fill="#d97706">{label}</text>')

    svg_parts.append('</svg>')
    svg_parts.append('</div>')
    return "\n".join(svg_parts)


_REPORT_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#fafafa; color:#333; line-height:1.7; }
.rpt-header { background:linear-gradient(135deg,#7c3aed,#6d28d9); color:white; padding:40px 32px; }
.rpt-header h1 { font-size:24px; margin-bottom:4px; }
.rpt-date { font-size:12px; opacity:0.8; }
.rpt-main { max-width:900px; margin:0 auto; padding:32px 24px; }
.rpt-section { margin-bottom:32px; }
.rpt-section h2 { font-size:18px; color:#7c3aed; border-bottom:2px solid #7c3aed; padding-bottom:6px; margin-bottom:12px; }
.rpt-section p { margin-bottom:8px; }
.rpt-table-wrap { overflow-x:auto; }
.rpt-table-wrap table { border-collapse:collapse; width:100%; font-size:13px; }
.rpt-table-wrap th,.rpt-table-wrap td { border:1px solid #ddd; padding:6px 10px; }
.rpt-table-wrap th { background:#f0f0f0; }
.rpt-timeline-wrap { background:#08081a; border-radius:12px; padding:16px; margin:12px 0; }
.rpt-timeline-svg { width:100%; height:auto; }
.rpt-node { cursor:pointer; }
.rpt-node:hover { filter:drop-shadow(0 0 6px rgba(124,58,237,0.5)); }
.rpt-tip-box { background:rgba(255,255,255,0.95); border:1px solid #ddd; border-radius:8px; padding:8px 10px; font-size:11px; line-height:1.5; color:#333; box-shadow:0 4px 12px rgba(0,0,0,0.1); }
@media print { .rpt-header { background:#7c3aed !important; -webkit-print-color-adjust:exact; print-color-adjust:exact; } }
"""

_REPORT_JS = """
document.querySelectorAll('.rpt-node').forEach(function(node) {
  node.addEventListener('click', function() {
    var id = node.getAttribute('data-id');
    document.querySelectorAll('.rpt-tooltip').forEach(function(tip) {
      if (tip.getAttribute('data-for') === id) {
        tip.style.display = tip.style.display === 'none' ? '' : 'none';
      } else {
        tip.style.display = 'none';
      }
    });
  });
});
"""


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif"}


async def _run_python(ctx: dict[str, str], code: str) -> str:
    """Execute Python code in the sandbox and return output.

    Before execution, uploaded files are symlinked into the workspace so
    that relative filenames resolve correctly.

    Uses run_in_executor to avoid blocking the asyncio event loop —
    run_code() internally calls proc.join() which is synchronous.

    After execution, detects newly created files in outputs/ and workspace/
    and appends download URLs so the frontend can render charts inline.
    """
    import asyncio

    workspace = ctx.get("workspace_dir")
    if not workspace:
        return "Error: session context not initialised (workspace_dir missing)"

    uploads = ctx.get("uploads_dir")
    if uploads:
        _sync_upload_symlinks(uploads, workspace)

    # Snapshot existing files before execution
    outputs_dir = ctx.get("outputs_dir", "")
    pre_files = _snapshot_files(workspace, outputs_dir)

    loop = asyncio.get_running_loop()
    result: SandboxResult = await loop.run_in_executor(
        None, lambda: run_code(code, allowed_dir=workspace, timeout=120)
    )
    if result.success:
        output = result.output or "(no output)"
        if len(output) > 16_000:
            output = output[:16_000] + "\n\n... [truncated — output too large]"

        # Detect newly created files and append references
        new_files = _detect_new_files(workspace, outputs_dir, pre_files)
        if new_files:
            output += "\n\n" + _format_new_files(new_files, ctx)

        return output
    return f"Error: {result.error}"


def _snapshot_files(workspace: str, outputs_dir: str) -> set[str]:
    """Collect absolute paths of existing files in workspace + outputs."""
    files: set[str] = set()
    for d in (workspace, outputs_dir):
        if not d:
            continue
        p = Path(d)
        if p.exists():
            for f in p.iterdir():
                if f.is_file():
                    files.add(str(f.resolve()))
    return files


def _detect_new_files(
    workspace: str, outputs_dir: str, pre: set[str]
) -> list[Path]:
    """Return newly created files after code execution."""
    new: list[Path] = []
    for d in (workspace, outputs_dir):
        if not d:
            continue
        p = Path(d)
        if not p.exists():
            continue
        for f in p.iterdir():
            if f.is_file() and str(f.resolve()) not in pre:
                new.append(f)
    return sorted(new, key=lambda f: f.name)


def _format_new_files(files: list[Path], ctx: dict[str, str]) -> str:
    """Format new files as markdown-style references for the AI response.

    Image files get a special marker that the frontend markdown renderer
    converts to inline <img> tags.
    """
    session_dir = ctx.get("session_dir", "")
    outputs_dir = ctx.get("outputs_dir", "")
    workspace_dir = ctx.get("workspace_dir", "")

    lines = ["[생성된 파일]"]
    for f in files:
        # Skip symlinks (these are uploaded files, not generated)
        if f.is_symlink():
            continue
        # Determine relative location for download URL
        if outputs_dir and str(f).startswith(outputs_dir):
            rel = f.name
        elif workspace_dir and str(f).startswith(workspace_dir):
            # Move to outputs for download accessibility
            dest = Path(outputs_dir) / f.name
            try:
                import shutil
                shutil.move(str(f), str(dest))
                rel = f.name
            except Exception:
                continue
        else:
            continue
        lines.append(f"- {f.name} (FORESIGHT_FILE:{rel})")
    return "\n".join(lines) if len(lines) > 1 else ""


# ── Tool schemas (Anthropic API format) ──────────────────

READ_UPLOADED_FILE_SCHEMA: dict[str, Any] = {
    "name": "read_uploaded_file",
    "description": (
        "업로드된 파일을 읽어 내용 또는 구조 요약을 반환합니다. "
        "CSV, Excel, JSON 등 구조적 파일은 컬럼명·행 수·샘플 데이터를, "
        "텍스트 파일은 원문 내용(최대 3000자 미리보기)을 반환합니다. "
        "파일명이 정확하지 않아도 대소문자·유니코드 변환을 허용합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "읽을 파일명 (예: data.csv, report.xlsx)",
            },
        },
        "required": ["filename"],
    },
}

SAVE_ENVIRONMENT_SCHEMA: dict[str, Any] = {
    "name": "save_environment",
    "description": (
        "현재 분석 환경(가정, 시나리오 파라미터, 중간 결과 등)을 이름을 붙여 저장합니다. "
        "data는 JSON 문자열 또는 자유 텍스트를 모두 허용합니다. "
        "사용자당 최대 200 MB 쿼터가 적용됩니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "환경 이름 (예: base_case, optimistic_q3)",
            },
            "data": {
                "type": "string",
                "description": "저장할 데이터 (JSON 문자열 또는 자유 텍스트)",
            },
        },
        "required": ["name", "data"],
    },
}

LOAD_ENVIRONMENT_SCHEMA: dict[str, Any] = {
    "name": "load_environment",
    "description": (
        "이름으로 저장된 환경을 불러옵니다. "
        "찾지 못하면 사용 가능한 환경 목록을 함께 반환합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "불러올 환경 이름",
            },
        },
        "required": ["name"],
    },
}

LIST_ENVIRONMENTS_SCHEMA: dict[str, Any] = {
    "name": "list_environments",
    "description": "현재 사용자가 저장한 모든 환경 이름 목록을 반환합니다.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

EXPORT_REPORT_SCHEMA: dict[str, Any] = {
    "name": "export_report",
    "description": (
        "분석 보고서나 결과물을 파일로 저장합니다. "
        "텍스트 기반 파일(Markdown, HTML, JSON, CSV)에 적합합니다. "
        "Excel·이미지 등 바이너리 파일은 run_python에서 outputs 디렉토리에 직접 저장하세요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "출력 파일명 (예: forecast_report.md, summary.html)",
            },
            "content": {
                "type": "string",
                "description": "파일에 쓸 텍스트 내용",
            },
        },
        "required": ["filename", "content"],
    },
}

EXPORT_INTERACTIVE_REPORT_SCHEMA: dict[str, Any] = {
    "name": "export_interactive_report",
    "description": (
        "인터랙티브 HTML 보고서를 생성합니다. "
        "텍스트 섹션 + SVG 타임라인 + 클릭 가능한 노드 설명을 포함하는 "
        "self-contained HTML 파일을 outputs 디렉토리에 저장합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "보고서 제목 (예: 'AI SaaS 시장 3년 예측')",
            },
            "sections": {
                "type": "string",
                "description": (
                    'JSON 배열. 각 섹션: {"heading":"제목","content":"내용","type":"text"|"table"}'
                ),
            },
            "timeline_data": {
                "type": "string",
                "description": (
                    'JSON 객체. 타임라인 데이터: {"nodes":[...],"edges":[...],"events":[...],"bands":[...]}'
                ),
            },
        },
        "required": ["title", "sections", "timeline_data"],
    },
}

RUN_PYTHON_SCHEMA: dict[str, Any] = {
    "name": "run_python",
    "description": (
        "Python 코드를 샌드박스에서 실행합니다 (타임아웃 120초). "
        "pandas, numpy, matplotlib, scipy 등 데이터 분석·시뮬레이션 라이브러리를 사용할 수 있습니다. "
        "업로드된 파일은 작업 디렉토리에 심링크되어 파일명만으로 접근 가능합니다. "
        "네트워크 접근, subprocess, eval/exec는 차단됩니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "실행할 Python 코드. print()로 결과를 출력하세요.",
            },
        },
        "required": ["code"],
    },
}


# ── Timeline emission tool ────────────────────────────────


async def _emit_timeline(ctx: dict[str, str], action: str, data: str) -> str:
    """Validate and acknowledge a timeline visualization event."""
    try:
        json.loads(data)
    except (json.JSONDecodeError, TypeError) as exc:
        return f"Error: 유효하지 않은 JSON 데이터입니다 — {exc}"
    return f"타임라인 이벤트 전송 완료: action={action}"


EMIT_TIMELINE_SCHEMA: dict[str, Any] = {
    "name": "emit_timeline",
    "description": (
        "타임라인 시각화 데이터를 프론트엔드로 전송합니다. "
        "노드·엣지·이벤트·밴드 추가 및 스테이지 전환 등 "
        "애니메이션 타임라인 UI를 제어하는 구조화된 명령을 보냅니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add_node", "add_edge", "add_event", "add_band", "advance_stage", "add_dots", "add_tornado", "add_backcast"],
                "description": "타임라인 명령 유형",
            },
            "data": {
                "type": "string",
                "description": "명령에 필요한 데이터 (JSON 문자열)",
            },
        },
        "required": ["action", "data"],
    },
}


# ── Requirements analysis tools ───────────────────────────


async def _generate_clarifications(ctx: dict[str, str], *, questions: str) -> str:
    """Pass-through: returns questions JSON as-is for frontend rendering."""
    try:
        parsed = json.loads(questions)
        if not isinstance(parsed, list):
            return "Error: questions must be a JSON array"
        return f"명확화 질문 {len(parsed)}건 전송 완료"
    except json.JSONDecodeError:
        return f"Error: questions is not valid JSON — {questions[:200]}"


async def _analyze_requirements(ctx: dict[str, str], items: str) -> str:
    """Analyze prediction task and return required info items.
    Intercepted by SageEngine which forwards the items JSON to the frontend."""
    try:
        parsed = json.loads(items)
        if not isinstance(parsed, list):
            return "Error: items must be a JSON array"
        return f"정보 요청 항목 {len(parsed)}건 전송 완료"
    except json.JSONDecodeError:
        return f"Error: items is not valid JSON — {items[:200]}"


async def _emit_requirement_status(ctx: dict[str, str], item_id: str, status: str) -> str:
    """Emit requirement item status to frontend. Intercepted by engine."""
    return f"항목 상태 전송: {item_id} = {status}"


GENERATE_CLARIFICATIONS_SCHEMA: dict[str, Any] = {
    "name": "generate_clarifications",
    "description": (
        "예측 질문의 범위와 조건을 명확히 하기 위한 확인 질문을 생성합니다. "
        "사용자의 예측 요청을 분석하여 시간 범위, 지표, 가정, 범위 등을 확인하는 "
        "2~5개의 질문을 JSON 배열로 반환합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "string",
                "description": (
                    'JSON 배열 문자열. 예: [{"id":"timeframe","question":"예측 시간 범위는 어떻게 되나요?"}]'
                ),
            },
        },
        "required": ["questions"],
    },
}

ANALYZE_REQUIREMENTS_SCHEMA: dict[str, Any] = {
    "name": "analyze_requirements",
    "description": (
        "예측 태스크에 필요한 사전 정보 항목을 분석하여 프론트엔드에 전송합니다. "
        "각 항목은 id, label, description, default_method를 포함하는 JSON 배열입니다. "
        "default_method는 'web_search', 'file', 'text' 중 하나입니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "string",
                "description": (
                    'JSON 배열 문자열. 예: [{"id":"market","label":"시장 규모",'
                    '"description":"설명","default_method":"web_search"}]'
                ),
            },
        },
        "required": ["items"],
    },
}

EMIT_REQUIREMENT_STATUS_SCHEMA: dict[str, Any] = {
    "name": "emit_requirement_status",
    "description": (
        "정보 요청 항목의 수집 진행 상태를 프론트엔드에 전송합니다. "
        "웹검색으로 정보를 수집한 후, 해당 항목의 완료를 알릴 때 사용합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "item_id": {"type": "string", "description": "항목 ID"},
            "status": {
                "type": "string",
                "enum": ["searching", "done", "error"],
                "description": "항목 상태",
            },
        },
        "required": ["item_id", "status"],
    },
}


# ── Delphi panel tool ─────────────────────────────────────


async def _emit_delphi(ctx: dict[str, str], round: int, experts: str) -> str:
    """Emit Delphi panel round data to frontend."""
    try:
        parsed = json.loads(experts)
        if not isinstance(parsed, list):
            return "Error: experts must be a JSON array"
        return f"Delphi 패널 라운드 {round} 데이터 전송 완료 ({len(parsed)}명)"
    except json.JSONDecodeError:
        return f"Error: experts is not valid JSON — {experts[:200]}"


EMIT_DELPHI_SCHEMA: dict[str, Any] = {
    "name": "emit_delphi",
    "description": (
        "AI Delphi 패널의 라운드별 전문가 평가 데이터를 프론트엔드에 전송합니다. "
        "각 전문가는 시나리오별 확률 추정치와 근거를 포함합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "round": {
                "type": "integer",
                "description": "라운드 번호 (1, 2, 3)",
            },
            "experts": {
                "type": "string",
                "description": (
                    'JSON 배열. 각 전문가: {"name":"이름","role":"역할",'
                    '"icon":"이모지","color":"#hex",'
                    '"estimates":{"optimistic":25,"base":50,"skeptical":20,"crisis":5},'
                    '"reasoning":"근거 설명"}'
                ),
            },
        },
        "required": ["round", "experts"],
    },
}


# ── Phase transition tool ─────────────────────────────────


async def _compress_environment(ctx: dict, profile: str) -> str:
    """Compress environment into a structured profile for Phase 2 prediction.

    This tool triggers a Phase transition in SageEngine:
    Phase 1 (Sonnet) → Phase 2 (Opus).
    """
    return (
        "환경 프로필이 압축되었습니다. "
        "Phase 2 (미래 예측) 모드로 전환합니다. "
        "이제 예측 질문에 답변할 준비가 되었습니다."
    )


COMPRESS_ENVIRONMENT_SCHEMA: dict[str, Any] = {
    "name": "compress_environment",
    "description": (
        "Phase 1(환경 구축)에서 수집·분석한 모든 환경 정보를 구조화된 프로필로 압축합니다. "
        "사용자가 환경 검증을 완료하고 예측을 요청하면 이 도구를 호출하세요. "
        "호출 후 Phase 2(미래 예측) 모드로 전환됩니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "profile": {
                "type": "string",
                "description": (
                    "압축된 환경 프로필 (JSON 형식). "
                    "산업, 규모, 핵심변수, 강점, 리스크, 불확실성 등을 포함."
                ),
            },
        },
        "required": ["profile"],
    },
}


RUN_ENSEMBLE_FORECAST_SCHEMA: dict[str, Any] = {
    "name": "run_ensemble_forecast",
    "description": (
        "다중 에이전트 앙상블 예측을 실행합니다. "
        "N개의 독립 에이전트가 병렬로 검색·추론하고, "
        "결과를 통계적으로 집계·보정합니다. "
        "반드시 환경 프로필 압축(compress_environment) 후 호출하세요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "예측할 질문 (구체적이고 검증 가능한 형식)",
            },
            "time_horizon": {
                "type": "string",
                "description": "예측 시간 범위 (예: '6개월', '1년', '3년')",
            },
            "context": {
                "type": "string",
                "description": "환경 프로필에서 추출한 관련 배경 정보",
            },
        },
        "required": ["question"],
    },
}


# ── Registries ───────────────────────────────────────────

FORESIGHT_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_uploaded_file": READ_UPLOADED_FILE_SCHEMA,
    "save_environment": SAVE_ENVIRONMENT_SCHEMA,
    "load_environment": LOAD_ENVIRONMENT_SCHEMA,
    "list_environments": LIST_ENVIRONMENTS_SCHEMA,
    "export_report": EXPORT_REPORT_SCHEMA,
    "export_interactive_report": EXPORT_INTERACTIVE_REPORT_SCHEMA,
    "run_python": RUN_PYTHON_SCHEMA,
    "emit_timeline": EMIT_TIMELINE_SCHEMA,
    "generate_clarifications": GENERATE_CLARIFICATIONS_SCHEMA,
    "analyze_requirements": ANALYZE_REQUIREMENTS_SCHEMA,
    "emit_requirement_status": EMIT_REQUIREMENT_STATUS_SCHEMA,
    "emit_delphi": EMIT_DELPHI_SCHEMA,
    "compress_environment": COMPRESS_ENVIRONMENT_SCHEMA,
    "run_ensemble_forecast": RUN_ENSEMBLE_FORECAST_SCHEMA,
}

# Unbound executor references — SageEngine binds ctx via functools.partial
FORESIGHT_TOOL_EXECUTORS: dict[str, Callable[..., Coroutine[Any, Any, str]]] = {
    "read_uploaded_file": _read_uploaded_file,
    "save_environment": _save_environment,
    "load_environment": _load_environment,
    "list_environments": _list_environments,
    "export_report": _export_report,
    "export_interactive_report": _export_interactive_report,
    "run_python": _run_python,
    "emit_timeline": _emit_timeline,
    "generate_clarifications": _generate_clarifications,
    "analyze_requirements": _analyze_requirements,
    "emit_requirement_status": _emit_requirement_status,
    "emit_delphi": _emit_delphi,
    "compress_environment": _compress_environment,
}
