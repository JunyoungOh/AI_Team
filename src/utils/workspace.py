"""모드별 로컬 워크스페이스 폴더 관리 + 파일→AI 컨텍스트 변환.

폴더 구조:
  data/workspace/{mode}/input/   ← 사용자가 파일을 넣는 곳
  data/workspace/{mode}/output/  ← AI 결과물 저장
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT_BASE = Path("data/workspace")

VALID_MODES = {"instant", "builder", "overtime", "skill"}

_TEXT_EXTENSIONS: set[str] = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".css", ".sql",
}
_IMAGE_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".svg"}
_MAX_TEXT_SIZE = 200 * 1024  # 200KB


def ensure_workspace(mode: str, base: Path | None = None) -> Path:
    """모드 워크스페이스 폴더를 생성하고 경로를 반환."""
    b = base or _DEFAULT_BASE
    ws = b / mode
    (ws / "input").mkdir(parents=True, exist_ok=True)
    (ws / "output").mkdir(parents=True, exist_ok=True)
    return ws


def list_input_files(mode: str, base: Path | None = None) -> list[dict]:
    """모드의 input 폴더 내 파일 목록을 반환."""
    b = base or _DEFAULT_BASE
    inp = b / mode / "input"
    if not inp.is_dir():
        return []
    return [
        {"name": f.name, "size": f.stat().st_size, "ext": f.suffix.lower()}
        for f in sorted(inp.iterdir())
        if f.is_file() and not f.name.startswith(".")
    ]


def _read_single_file(path: Path) -> str:
    """단일 파일을 AI 컨텍스트 문자열로 변환."""
    ext = path.suffix.lower()
    size = path.stat().st_size
    name = path.name

    if ext in _TEXT_EXTENSIONS and size <= _MAX_TEXT_SIZE:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return (
                f"[첨부파일: {name} ({size:,} bytes)]\n"
                f"--- 파일 내용 ---\n{content}\n--- 파일 끝 ---"
            )
        except Exception:
            pass

    kind = "이미지" if ext in _IMAGE_EXTENSIONS else "바이너리"
    return f"[첨부파일: {name} ({size:,} bytes, {kind} 파일)]"


def read_files_as_context(
    mode: str,
    filenames: list[str],
    base: Path | None = None,
) -> str:
    """선택된 파일명 목록 → 통합 AI 컨텍스트 문자열.

    경로 순회 방지: input 폴더 직속 파일만 허용.
    """
    if not filenames:
        return ""
    b = base or _DEFAULT_BASE
    inp = (b / mode / "input").resolve()

    parts: list[str] = []
    for name in filenames:
        path = (inp / name).resolve()
        # 경로 순회 방지: 반드시 input 폴더의 직속 자식이어야 함
        if path.parent != inp:
            continue
        if path.exists() and path.is_file():
            parts.append(_read_single_file(path))

    if not parts:
        return ""
    return "\n\n## 사용자 첨부 파일\n\n" + "\n\n".join(parts)


def get_output_dir(mode: str, session_id: str, base: Path | None = None) -> Path:
    """세션별 output 디렉토리를 생성하고 반환."""
    b = base or _DEFAULT_BASE
    out = b / mode / "output" / session_id
    out.mkdir(parents=True, exist_ok=True)
    return out
