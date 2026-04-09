"""스킬 실행 이력 파일 저장소.

저장 구조:
  data/skills/runs/<slug>/<run_id>.json

run_id 형식: <unix_ts>-<short_uuid>
  - unix_ts가 앞에 와서 파일명 정렬이 자연스럽게 시간순
  - 8자 short_uuid로 동시 실행 충돌 방지
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


@dataclass
class RunRecord:
    run_id: str
    slug: str
    user_input: str
    result_text: str
    status: str  # "completed" | "error" | "timeout"
    tool_count: int
    duration_seconds: float
    started_at: str  # ISO 8601
    error_message: Optional[str] = None


_DEFAULT_RUNS_ROOT = Path("data/skills/runs")


def _validate_slug(slug: str) -> None:
    if not slug or "/" in slug or ".." in slug or "\\" in slug:
        raise ValueError(f"Invalid slug: {slug!r}")


def _generate_run_id() -> str:
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
        encoding="utf-8",
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def save_run(
    *,
    slug: str,
    user_input: str,
    result_text: str,
    status: str,
    tool_count: int,
    duration_seconds: float,
    error_message: Optional[str] = None,
    runs_root: Optional[Path] = None,
) -> RunRecord:
    _validate_slug(slug)
    runs_root = runs_root or _DEFAULT_RUNS_ROOT

    rec = RunRecord(
        run_id=_generate_run_id(),
        slug=slug,
        user_input=user_input,
        result_text=result_text,
        status=status,
        tool_count=tool_count,
        duration_seconds=duration_seconds,
        started_at=datetime.now(timezone.utc).isoformat(),
        error_message=error_message,
    )
    path = runs_root / slug / f"{rec.run_id}.json"
    _atomic_write(path, asdict(rec))
    return rec


def list_runs(
    slug: str, *, runs_root: Optional[Path] = None
) -> List[RunRecord]:
    _validate_slug(slug)
    runs_root = runs_root or _DEFAULT_RUNS_ROOT
    skill_dir = runs_root / slug
    if not skill_dir.exists():
        return []

    files = sorted(skill_dir.glob("*.json"), reverse=True)
    out: List[RunRecord] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(RunRecord(**data))
        except Exception:
            continue  # 손상된 파일은 무시
    return out


def load_run(
    slug: str, run_id: str, *, runs_root: Optional[Path] = None
) -> RunRecord:
    _validate_slug(slug)
    runs_root = runs_root or _DEFAULT_RUNS_ROOT
    path = runs_root / slug / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunRecord(**data)
