# 스킬 탭 — Plan 2: 카드 클릭 실행 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Plan 1에서 만든 "내 스킬" 카드를 클릭하면 카드가 인라인으로 펼쳐지고, 자유 입력 textarea에 사용자가 입력값을 적은 뒤 "실행" 버튼을 누르면 SKILL.md 본문을 시스템 프롬프트로 주입한 격리 Claude Code 세션이 실행되어 도구 사용 이벤트를 실시간 스트리밍하고 결과/이력을 같은 카드 안에 표시한다.

**Architecture:**
- **백엔드**: `src/skill_builder/` 안에 4개 모듈을 추가한다 — `skill_loader`(스킬 컨텍스트 로드), `run_history`(실행 이력 CRUD), `execution_streamer`(stream-json 파서, `single_session._stream_session` 패턴 복제), `execution_runner`(오케스트레이터). 새 WebSocket 엔드포인트 `/ws/skill-execute`와 REST `/api/skill-builder/runs/<slug>` 추가.
- **격리 정책**: `required_mcps == []` 인 스킬은 `cwd=/tmp` + 빌트인 도구만 사용하여 자동 트리거와 CLAUDE.md 누출을 모두 차단. `required_mcps != []` 인 스킬은 `cwd=프로젝트 루트` + 명시된 `mcp__<server>__*` 도구만 허용하여 MCP는 사용 가능하되 `--add-dir ~/.claude/skills/`는 절대 추가하지 않아 자동 트리거 차단 유지.
- **프론트엔드**: `mode-skill.js`의 `_renderListPanel`을 확장하여 카드 클릭 시 인라인 펼침 패널을 카드 아래에 삽입. WebSocket으로 도구 사용 이벤트를 실시간 렌더. 같은 카드 안에 "이력 보기" 토글로 과거 실행 N건을 시간순 표시.

**Tech Stack:**
- Backend: Python 3.11, FastAPI WebSocket, asyncio create-subprocess-exec API, pydantic dataclasses
- Frontend: 바닐라 JS (ES5 호환, marked + DOMPurify), 풀와이드 chat-style UI
- Test: pytest, pytest-asyncio, FastAPI TestClient

---

## v1 결정 사항 (사용자와 합의)

| 항목 | 결정 | 이유 |
|------|------|------|
| 범위 분할 | Plan 2 = 실행만, Plan 3 = 스케줄링 (별도 플랜) | 실행 백엔드 안정화 후 그 위에 스케줄링을 얹는 게 테스트 가능 |
| 입력 폼 | 자유 입력 textarea만 | v1 단순화. skill_metadata.json 입력 스키마 추가는 후속 과제 |
| MCP 정책 | 조건부 격리 깨기 (`required_mcps`로 분기) | 자동 트리거(=핵심 위험)는 `--add-dir` 미사용으로 항상 차단, MCP는 필요한 스킬만 활성화 |
| 결과/이력 UX | 카드 인라인 펼침 + 같은 카드에 "이력 보기" 토글 | BACKLOG.md 명시 사양. 컨텍스트가 카드 한 곳에 모임 |

---

## File Structure

### CREATE (backend)

| 파일 | 책임 |
|------|------|
| `src/skill_builder/skill_loader.py` | registry → skill_path → SKILL.md 본문 + skill_metadata.json 로드, 격리 모드 결정 |
| `src/skill_builder/run_history.py` | `data/skills/runs/<slug>/<run_id>.json` CRUD, 시간순 정렬 |
| `src/skill_builder/execution_streamer.py` | asyncio subprocess + stream-json 파서, 콜백으로 이벤트 emit (DI 가능한 `_proc_factory`) |
| `src/skill_builder/execution_runner.py` | 오케스트레이터 — loader → streamer → history 순서 실행, RunRecord 반환 |

### CREATE (tests)

| 파일 | 대상 |
|------|------|
| `tests/test_skill_loader.py` | skill_loader: happy path, missing files, path traversal, isolation mode |
| `tests/test_run_history.py` | run_history: save/list/load, 정렬, missing dir, slug 검증 |
| `tests/test_execution_streamer.py` | execution_streamer: stub proc factory로 stream-json 라인 주입, 이벤트 매칭 |
| `tests/test_skill_execution_runner.py` | execution_runner: 모킹된 의존성으로 오케스트레이션 검증 |
| `tests/test_skill_execution_endpoint.py` | FastAPI TestClient: WS 핸드셰이크, REST 이력 조회 |

### CREATE (data)

| 파일 | 용도 |
|------|------|
| `data/skills/runs/.gitkeep` | runs 루트 디렉터리를 git에 포함 |

### MODIFY

| 파일 | 변경 내용 |
|------|----------|
| `src/skill_builder/__init__.py` | 새 모듈 exports |
| `src/ui/server.py` | `/ws/skill-execute` WebSocket + `/api/skill-builder/runs/{slug}` REST GET |
| `src/ui/static/js/mode-skill.js` | `_renderListPanel`에 카드 클릭 → 인라인 펼침 + 입력 폼 + 실시간 로그 + 결과 + 이력 토글 |
| `src/ui/static/css/skill.css` | 인라인 펼침 패널 + 활동 로그 + 이력 리스트 스타일 (기존 `.skill-card` 선언 위치) |
| `src/skill_builder/clarification_questions.py` | (Task 7에서 verification 후 필요 시 보강) handoff 프롬프트 8번 항목 강도 점검 |
| `BACKLOG.md` | Plan 2 완료 표시, 스케줄링은 Plan 3로 푸시 |

---

## Task 1: skill_loader — SKILL.md 로드 + 격리 모드 결정 (TDD)

**Files:**
- Create: `src/skill_builder/skill_loader.py`
- Test: `tests/test_skill_loader.py`

**책임 요약:**
- registry.json에서 slug → SkillRecord 조회
- `<skill_path>/SKILL.md` 본문 읽기 (frontmatter 제거하지 않음 — 본문 그대로 system prompt에 들어감)
- `<skill_path>/skill_metadata.json` 읽기 (없으면 빈 dict)
- `required_mcps`에 따라 `IsolationMode.ISOLATED` / `IsolationMode.WITH_MCPS` 결정
- slug에 path traversal 문자(`/`, `..`) 들어오면 거부

- [ ] **Step 1.1: 실패 테스트 작성**

```python
# tests/test_skill_loader.py
"""skill_loader 단위 테스트.

핵심 검증:
  - 정상 케이스: SKILL.md + skill_metadata.json이 모두 있으면 컨텍스트 반환
  - required_mcps 기반 isolation_mode 결정
  - 누락된 SKILL.md → FileNotFoundError
  - path traversal slug → ValueError
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.skill_builder.registry import SkillRecord, SkillRegistry
from src.skill_builder.skill_loader import (
    IsolationMode,
    SkillExecutionContext,
    load_skill_for_execution,
)


def _seed_registry(tmp_path: Path, record: SkillRecord) -> Path:
    reg_path = tmp_path / "registry.json"
    SkillRegistry(path=reg_path).add(record)
    return reg_path


def _make_skill_dir(
    base: Path, slug: str, *, body: str, required_mcps: list[str]
) -> Path:
    skill_dir = base / f"skill-tab-{slug}"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    (skill_dir / "skill_metadata.json").write_text(
        json.dumps({"required_mcps": required_mcps}),
        encoding="utf-8",
    )
    return skill_dir


def test_load_skill_returns_context_for_isolated_skill(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(
        tmp_path, "summarize", body="# Summarize\n\n3줄 요약", required_mcps=[]
    )
    record = SkillRecord(
        slug="summarize",
        name="3줄 요약",
        skill_path=str(skill_dir),
        required_mcps=[],
        source="created",
        created_at="2026-04-09T00:00:00+00:00",
    )
    reg_path = _seed_registry(tmp_path, record)

    ctx = load_skill_for_execution("summarize", registry_path=reg_path)

    assert isinstance(ctx, SkillExecutionContext)
    assert ctx.slug == "summarize"
    assert ctx.skill_body == "# Summarize\n\n3줄 요약"
    assert ctx.isolation_mode == IsolationMode.ISOLATED
    assert ctx.required_mcps == []


def test_load_skill_returns_with_mcps_when_required(tmp_path: Path) -> None:
    skill_dir = _make_skill_dir(
        tmp_path, "websearch", body="# Web search", required_mcps=["serper"]
    )
    record = SkillRecord(
        slug="websearch",
        name="웹 검색",
        skill_path=str(skill_dir),
        required_mcps=["serper"],
        source="created",
        created_at="2026-04-09T00:00:00+00:00",
    )
    reg_path = _seed_registry(tmp_path, record)

    ctx = load_skill_for_execution("websearch", registry_path=reg_path)

    assert ctx.isolation_mode == IsolationMode.WITH_MCPS
    assert ctx.required_mcps == ["serper"]


def test_load_skill_raises_when_skill_md_missing(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill-tab-broken"
    skill_dir.mkdir()
    record = SkillRecord(
        slug="broken",
        name="망가진 스킬",
        skill_path=str(skill_dir),
        required_mcps=[],
        source="created",
        created_at="2026-04-09T00:00:00+00:00",
    )
    reg_path = _seed_registry(tmp_path, record)

    with pytest.raises(FileNotFoundError):
        load_skill_for_execution("broken", registry_path=reg_path)


def test_load_skill_raises_when_slug_not_in_registry(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text("[]", encoding="utf-8")

    with pytest.raises(KeyError):
        load_skill_for_execution("nope", registry_path=reg_path)


def test_load_skill_rejects_path_traversal_slug(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError):
        load_skill_for_execution("../etc/passwd", registry_path=reg_path)


def test_load_skill_handles_missing_metadata_as_isolated(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill-tab-meta-less"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# X", encoding="utf-8")
    record = SkillRecord(
        slug="meta-less",
        name="메타 없음",
        skill_path=str(skill_dir),
        required_mcps=[],
        source="created",
        created_at="2026-04-09T00:00:00+00:00",
    )
    reg_path = _seed_registry(tmp_path, record)

    ctx = load_skill_for_execution("meta-less", registry_path=reg_path)
    assert ctx.isolation_mode == IsolationMode.ISOLATED
```

- [ ] **Step 1.2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_skill_loader.py -v`
Expected: 모든 테스트가 ImportError로 실패 (`No module named 'src.skill_builder.skill_loader'`).

- [ ] **Step 1.3: skill_loader 구현**

```python
# src/skill_builder/skill_loader.py
"""스킬 실행 컨텍스트 로더.

실행 직전에 호출되어:
  1) registry에서 slug → SkillRecord 조회
  2) SKILL.md 본문 + skill_metadata.json 로드
  3) required_mcps 기반으로 격리 모드 결정

격리 모드는 execution_runner가 cwd / allowed_tools를 결정하는 데 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List

from src.skill_builder.registry import SkillRegistry


class IsolationMode(Enum):
    ISOLATED = "isolated"  # cwd=/tmp, builtin tools only
    WITH_MCPS = "with_mcps"  # cwd=project root, mcp__* tools allowed


@dataclass
class SkillExecutionContext:
    slug: str
    name: str
    skill_path: Path
    skill_body: str  # SKILL.md 전체 본문 (frontmatter 포함)
    required_mcps: List[str]
    isolation_mode: IsolationMode


def _validate_slug(slug: str) -> None:
    if not slug or "/" in slug or ".." in slug or "\\" in slug:
        raise ValueError(f"Invalid slug: {slug!r}")


def load_skill_for_execution(
    slug: str,
    *,
    registry_path: Path | None = None,
) -> SkillExecutionContext:
    """slug로 SkillExecutionContext를 만들어 반환.

    Raises:
        ValueError: slug가 유효하지 않음 (path traversal 등)
        KeyError: slug가 registry에 없음
        FileNotFoundError: SKILL.md가 디스크에 없음
    """
    _validate_slug(slug)

    reg_path = registry_path or Path("data/skills/registry.json")
    reg = SkillRegistry(path=reg_path)
    matching = [r for r in reg.list_all() if r.slug == slug]
    if not matching:
        raise KeyError(f"slug '{slug}' not found in registry")
    record = matching[0]

    skill_dir = Path(record.skill_path)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found at {skill_md}")

    body = skill_md.read_text(encoding="utf-8")

    required_mcps = list(record.required_mcps or [])
    mode = IsolationMode.WITH_MCPS if required_mcps else IsolationMode.ISOLATED

    return SkillExecutionContext(
        slug=record.slug,
        name=record.name,
        skill_path=skill_dir,
        skill_body=body,
        required_mcps=required_mcps,
        isolation_mode=mode,
    )
```

- [ ] **Step 1.4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_skill_loader.py -v`
Expected: 6개 테스트 모두 PASS.

- [ ] **Step 1.5: 커밋**

```bash
git add src/skill_builder/skill_loader.py tests/test_skill_loader.py
git commit -m "feat(skill-tab): add skill_loader for execution context"
```

---

## Task 2: run_history — 실행 이력 저장소 (TDD)

**Files:**
- Create: `src/skill_builder/run_history.py`
- Create: `data/skills/runs/.gitkeep`
- Test: `tests/test_run_history.py`

**책임 요약:**
- `data/skills/runs/<slug>/<run_id>.json` 단위 저장
- `run_id = <unix_ts>-<8char_uuid>` (정렬 시 자연스럽게 시간순)
- save → 디렉터리 생성, atomic write
- list → newest first (파일명 desc 정렬)
- load → 단일 RunRecord 반환
- slug 검증 (path traversal 차단)

- [ ] **Step 2.1: 실패 테스트 작성**

```python
# tests/test_run_history.py
"""run_history 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skill_builder.run_history import (
    RunRecord,
    list_runs,
    load_run,
    save_run,
)


def test_save_then_list_returns_run(tmp_path: Path) -> None:
    rec = save_run(
        slug="summarize",
        user_input="긴 글...",
        result_text="3줄 요약 결과",
        status="completed",
        tool_count=5,
        duration_seconds=12.3,
        runs_root=tmp_path,
    )
    assert isinstance(rec, RunRecord)
    assert rec.slug == "summarize"
    assert rec.status == "completed"

    listed = list_runs("summarize", runs_root=tmp_path)
    assert len(listed) == 1
    assert listed[0].run_id == rec.run_id
    assert listed[0].user_input == "긴 글..."


def test_list_runs_sorts_newest_first(tmp_path: Path) -> None:
    import time

    save_run(slug="x", user_input="a", result_text="r1", status="completed",
             tool_count=0, duration_seconds=1.0, runs_root=tmp_path)
    time.sleep(1.1)
    save_run(slug="x", user_input="b", result_text="r2", status="completed",
             tool_count=0, duration_seconds=1.0, runs_root=tmp_path)
    listed = list_runs("x", runs_root=tmp_path)
    assert len(listed) == 2
    assert listed[0].user_input == "b"
    assert listed[1].user_input == "a"


def test_list_runs_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_runs("nope", runs_root=tmp_path) == []


def test_load_run_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_run("x", "1234567890-abcdefgh", runs_root=tmp_path)


def test_save_run_with_error_status(tmp_path: Path) -> None:
    rec = save_run(slug="x", user_input="y", result_text="", status="error",
                   tool_count=0, duration_seconds=0.5,
                   error_message="timeout", runs_root=tmp_path)
    assert rec.status == "error"
    assert rec.error_message == "timeout"


def test_save_run_rejects_path_traversal_slug(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_run(slug="../escape", user_input="x", result_text="y",
                 status="completed", tool_count=0, duration_seconds=1.0,
                 runs_root=tmp_path)
```

- [ ] **Step 2.2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_run_history.py -v`
Expected: ImportError 또는 NameError로 모두 FAIL.

- [ ] **Step 2.3: run_history 구현**

```python
# src/skill_builder/run_history.py
"""스킬 실행 이력 파일 저장소.

저장 구조: data/skills/runs/<slug>/<run_id>.json
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
        mode="w", dir=str(path.parent), delete=False,
        suffix=".tmp", encoding="utf-8",
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
    slug: str, *, runs_root: Optional[Path] = None,
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
            continue
    return out


def load_run(
    slug: str, run_id: str, *, runs_root: Optional[Path] = None,
) -> RunRecord:
    _validate_slug(slug)
    runs_root = runs_root or _DEFAULT_RUNS_ROOT
    path = runs_root / slug / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunRecord(**data)
```

- [ ] **Step 2.4: gitkeep 생성 + 테스트 통과 확인**

```bash
mkdir -p data/skills/runs
touch data/skills/runs/.gitkeep
```

Run: `python3 -m pytest tests/test_run_history.py -v`
Expected: 6개 테스트 PASS.

- [ ] **Step 2.5: 커밋**

```bash
git add src/skill_builder/run_history.py tests/test_run_history.py data/skills/runs/.gitkeep
git commit -m "feat(skill-tab): add run_history storage for skill executions"
```

---

## Task 3: execution_streamer — stream-json 파서 (TDD)

**Files:**
- Create: `src/skill_builder/execution_streamer.py`
- Test: `tests/test_execution_streamer.py`

**책임 요약:**
- `single_session._stream_session()` 패턴을 그대로 복제하되:
  - mode_event_queue 의존성 제거 → 콜백 `on_event(dict)`로 교체
  - 테스트 가능하게 `_proc_factory` 의존성 주입 (기본값 = 진짜 asyncio API)
- 반환: `(full_text, tool_count, timed_out)` 튜플
- 콜백 이벤트 종류:
  - `{"action": "started", "elapsed": 0}`
  - `{"action": "tool_use", "tool": str, "tool_count": int, "elapsed": float}`
  - `{"action": "text", "chunk": str, "elapsed": float}`
  - `{"action": "completed", "elapsed": float, "tool_count": int, "timed_out": bool}`
  - `{"action": "timeout", "elapsed": float}` (timed_out 시 추가)

- [ ] **Step 3.1: 실패 테스트 작성**

```python
# tests/test_execution_streamer.py
"""execution_streamer 단위 테스트.

핵심 전략:
  - 진짜 subprocess 대신 fake proc factory를 주입
  - fake proc은 stream-json 라인을 yield하는 async iterator를 가진 stdout
  - on_event 콜백이 받은 이벤트를 list에 모아 검증
"""

from __future__ import annotations

import json
from typing import List

import pytest

from src.skill_builder.execution_streamer import stream_skill_execution


class _FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, lines: list[bytes]) -> None:
        self.stdout = _FakeStream(lines)
        self.stderr = _FakeStream([])
        self.pid = 99999
        self.returncode = 0

    def kill(self) -> None:
        pass

    async def wait(self):
        return 0


def _line(d: dict) -> bytes:
    return (json.dumps(d) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_stream_emits_started_and_completed() -> None:
    lines = [
        _line({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "안녕"}]},
        }),
        _line({"type": "result", "result": "안녕", "is_error": False}),
    ]

    async def fake_factory(*args, **kwargs):
        return _FakeProc(lines)

    events: List[dict] = []
    full_text, tool_count, timed_out = await stream_skill_execution(
        prompt="요약해줘",
        system_prompt="당신은 요약가입니다",
        model="sonnet",
        allowed_tools=["Read", "Write"],
        cwd="/tmp",
        timeout=5,
        on_event=lambda e: events.append(e),
        _proc_factory=fake_factory,
    )

    assert "안녕" in full_text
    assert tool_count == 0
    assert timed_out is False
    assert events[0]["action"] == "started"
    assert events[-1]["action"] == "completed"


@pytest.mark.asyncio
async def test_stream_emits_tool_use_events() -> None:
    lines = [
        _line({
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]},
        }),
        _line({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {}},
                {"type": "text", "text": "끝"},
            ]},
        }),
        _line({"type": "result", "result": "끝", "is_error": False}),
    ]

    async def fake_factory(*args, **kwargs):
        return _FakeProc(lines)

    events: List[dict] = []
    full_text, tool_count, timed_out = await stream_skill_execution(
        prompt="x", system_prompt="x", model="sonnet",
        allowed_tools=["Read", "Write"], cwd="/tmp", timeout=5,
        on_event=lambda e: events.append(e),
        _proc_factory=fake_factory,
    )

    tool_events = [e for e in events if e["action"] == "tool_use"]
    assert len(tool_events) == 2
    assert tool_events[0]["tool"] == "Read"
    assert tool_events[1]["tool"] == "Write"
    assert tool_count == 2
    assert "끝" in full_text


@pytest.mark.asyncio
async def test_stream_handles_result_error_block() -> None:
    lines = [
        _line({"type": "result", "result": "내부 오류 발생", "is_error": True}),
    ]

    async def fake_factory(*args, **kwargs):
        return _FakeProc(lines)

    events: List[dict] = []
    full_text, _tc, timed_out = await stream_skill_execution(
        prompt="x", system_prompt="x", model="sonnet",
        allowed_tools=[], cwd="/tmp", timeout=5,
        on_event=lambda e: events.append(e),
        _proc_factory=fake_factory,
    )

    assert "내부 오류" in full_text
    assert timed_out is False
```

- [ ] **Step 3.2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_execution_streamer.py -v`
Expected: ImportError로 FAIL.

- [ ] **Step 3.3: execution_streamer 구현**

```python
# src/skill_builder/execution_streamer.py
"""Claude Code 서브프로세스를 stream-json으로 실행하고 콜백으로 이벤트 emit.

`single_session._stream_session()` 패턴을 그대로 복제하되:
  - mode_event_queue 의존성 제거 → on_event 콜백
  - 의존성 주입 가능한 _proc_factory (테스트용)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Awaitable, Callable, Optional


EventCallback = Callable[[dict], None]


async def _default_proc_factory(
    cmd: list[str], *, cwd: str, env: dict[str, str]
):
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
        env=env,
    )


async def stream_skill_execution(
    *,
    prompt: str,
    system_prompt: str,
    model: str,
    allowed_tools: list[str],
    cwd: str,
    timeout: int,
    on_event: EventCallback,
    _proc_factory: Optional[Callable[..., Awaitable]] = None,
) -> tuple[str, int, bool]:
    """스트리밍 실행. Returns: (full_text, tool_count, timed_out)"""
    factory = _proc_factory or _default_proc_factory

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--max-turns", "30",
        "--append-system-prompt", system_prompt,
        "--permission-mode", "auto",
    ]
    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    proc = await factory(cmd, cwd=cwd, env=env)

    full_text = ""
    tool_count = 0
    timed_out = False
    start_time = time.time()

    on_event({"action": "started", "elapsed": 0})

    try:
        async with asyncio.timeout(timeout):
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")
                elapsed = round(time.time() - start_time, 1)

                if event_type == "assistant":
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            chunk = block.get("text", "")
                            full_text += chunk
                            on_event({
                                "action": "text",
                                "chunk": chunk,
                                "elapsed": elapsed,
                            })
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_count += 1
                            on_event({
                                "action": "tool_use",
                                "tool": tool_name,
                                "tool_count": tool_count,
                                "elapsed": elapsed,
                            })
                elif event_type == "result":
                    result_text = event.get("result", "")
                    if event.get("is_error"):
                        if not full_text:
                            full_text = result_text
                    elif not full_text and result_text:
                        full_text = result_text
    except TimeoutError:
        timed_out = True
        elapsed = round(time.time() - start_time, 1)
        on_event({"action": "timeout", "elapsed": elapsed})
        try:
            proc.kill()
        except Exception:
            pass

    elapsed = round(time.time() - start_time, 1)
    on_event({
        "action": "completed",
        "elapsed": elapsed,
        "tool_count": tool_count,
        "timed_out": timed_out,
    })

    return full_text, tool_count, timed_out
```

- [ ] **Step 3.4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_execution_streamer.py -v`
Expected: 3개 테스트 PASS.

- [ ] **Step 3.5: 커밋**

```bash
git add src/skill_builder/execution_streamer.py tests/test_execution_streamer.py
git commit -m "feat(skill-tab): add execution_streamer for subprocess streaming"
```

---

## Task 4: execution_runner — 오케스트레이터 (TDD)

**Files:**
- Create: `src/skill_builder/execution_runner.py`
- Test: `tests/test_skill_execution_runner.py`

**책임 요약:**
- 단일 진입점 `run_skill(slug, user_input, on_event)` 제공
- 내부 흐름:
  1. `skill_loader.load_skill_for_execution(slug)` → context
  2. system_prompt = context.skill_body + 실행 컨텍스트 안내
  3. isolation_mode에 따라 cwd / allowed_tools 결정:
     - ISOLATED: cwd=`/tmp`, allowed_tools=`["Read","Write","Edit","Bash","Glob","Grep"]`
     - WITH_MCPS: cwd=`os.getcwd()`, allowed_tools=builtin + `mcp__<mcp>__*`
  4. `execution_streamer.stream_skill_execution(...)` 호출
  5. 결과 → `run_history.save_run(...)` 저장
  6. 저장된 RunRecord 반환
- 실패 시: try/except로 잡아서 status="error" RunRecord로 저장 후 반환 (예외 미전파)

- [ ] **Step 4.1: 실패 테스트 작성**

```python
# tests/test_skill_execution_runner.py
"""execution_runner 단위 테스트 — 의존성 모킹으로 오케스트레이션 검증."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.skill_builder.execution_runner import run_skill
from src.skill_builder.skill_loader import (
    IsolationMode,
    SkillExecutionContext,
)


def _make_ctx(slug: str = "summarize", *, required_mcps=None) -> SkillExecutionContext:
    required_mcps = required_mcps or []
    return SkillExecutionContext(
        slug=slug,
        name="3줄 요약",
        skill_path=Path(f"/tmp/skill-tab-{slug}"),
        skill_body="# 3줄 요약\n\n사용자 텍스트를 3줄로 요약합니다.",
        required_mcps=required_mcps,
        isolation_mode=(
            IsolationMode.WITH_MCPS if required_mcps else IsolationMode.ISOLATED
        ),
    )


@pytest.mark.asyncio
async def test_run_skill_isolated_uses_tmp_cwd_and_builtins(tmp_path) -> None:
    captured: dict = {}

    async def fake_streamer(**kwargs):
        captured.update(kwargs)
        kwargs["on_event"]({"action": "started", "elapsed": 0})
        kwargs["on_event"]({
            "action": "completed", "elapsed": 1.0,
            "tool_count": 0, "timed_out": False,
        })
        return ("결과 텍스트", 0, False)

    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        return_value=_make_ctx(),
    ), patch(
        "src.skill_builder.execution_runner.stream_skill_execution",
        side_effect=fake_streamer,
    ):
        record = await run_skill(
            slug="summarize",
            user_input="긴 글입니다",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert captured["cwd"] == "/tmp"
    assert "Read" in captured["allowed_tools"]
    assert "Write" in captured["allowed_tools"]
    assert not any(t.startswith("mcp__") for t in captured["allowed_tools"])
    assert record.status == "completed"
    assert record.result_text == "결과 텍스트"


@pytest.mark.asyncio
async def test_run_skill_with_mcps_uses_project_cwd(tmp_path) -> None:
    captured: dict = {}

    async def fake_streamer(**kwargs):
        captured.update(kwargs)
        kwargs["on_event"]({
            "action": "completed", "elapsed": 1.0,
            "tool_count": 1, "timed_out": False,
        })
        return ("ok", 1, False)

    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        return_value=_make_ctx(required_mcps=["serper", "firecrawl"]),
    ), patch(
        "src.skill_builder.execution_runner.stream_skill_execution",
        side_effect=fake_streamer,
    ):
        record = await run_skill(
            slug="summarize",
            user_input="검색 요청",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert captured["cwd"] != "/tmp"
    assert any("serper" in t for t in captured["allowed_tools"])
    assert any("firecrawl" in t for t in captured["allowed_tools"])
    assert record.status == "completed"


@pytest.mark.asyncio
async def test_run_skill_streamer_failure_saved_as_error(tmp_path) -> None:
    async def fake_streamer(**kwargs):
        raise RuntimeError("CLI 충돌")

    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        return_value=_make_ctx(),
    ), patch(
        "src.skill_builder.execution_runner.stream_skill_execution",
        side_effect=fake_streamer,
    ):
        record = await run_skill(
            slug="summarize",
            user_input="x",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert record.status == "error"
    assert "CLI 충돌" in (record.error_message or "")


@pytest.mark.asyncio
async def test_run_skill_loader_failure_saved_as_error(tmp_path) -> None:
    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        side_effect=FileNotFoundError("SKILL.md missing"),
    ):
        record = await run_skill(
            slug="broken",
            user_input="x",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert record.status == "error"
    assert "SKILL.md missing" in (record.error_message or "")
```

- [ ] **Step 4.2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_skill_execution_runner.py -v`
Expected: ImportError로 FAIL.

- [ ] **Step 4.3: execution_runner 구현**

```python
# src/skill_builder/execution_runner.py
"""스킬 실행 오케스트레이터.

run_skill(slug, user_input, on_event):
  1) skill_loader로 컨텍스트 로드
  2) isolation_mode에 따라 cwd / allowed_tools 결정
  3) execution_streamer 호출
  4) 결과를 run_history에 저장
  5) RunRecord 반환

에러는 모두 잡아서 status="error" RunRecord로 저장 후 반환.
호출자(WS endpoint)는 예외를 처리할 필요 없이 record.status만 보면 된다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from src.skill_builder.execution_streamer import stream_skill_execution
from src.skill_builder.run_history import RunRecord, save_run
from src.skill_builder.skill_loader import (
    IsolationMode,
    load_skill_for_execution,
)


_BUILTIN_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]


def _build_allowed_tools_for_mcps(required_mcps: list[str]) -> list[str]:
    """required_mcps를 mcp__<server>__<tool> 형식으로 변환.

    Claude Code의 --allowedTools는 정확한 도구명을 요구하므로 각 MCP 서버의
    잘 알려진 도구를 명시한다.

    주의: 플러그인으로 설치된 MCP는 네임스페이스가 `mcp__plugin_<ns>_<server>__<tool>`
    형태로 달라질 수 있다 (예: context7는 플러그인 버전에서
    `mcp__plugin_context7_context7__query-docs`). 아래 맵은 프로젝트 `.mcp.json`에
    직접 등록된 서버 기준이다. 새 MCP를 추가할 때는 `src/utils/tool_definitions.py`
    와 실제 스트림에서 관찰되는 도구명으로 검증할 것.

    후속 과제: skill_metadata.json에 구체적 tool 이름(`required_tools`) 필드를
    추가하면 이 매핑이 불필요해진다.
    """
    known_tools_by_server = {
        "serper": ["mcp__serper__google_search"],
        "firecrawl": [
            "mcp__firecrawl__firecrawl_scrape",
            "mcp__firecrawl__firecrawl_search",
        ],
        "brave-search": ["mcp__brave-search__brave_web_search"],
        "github": [
            "mcp__github__search_code",
            "mcp__github__search_repositories",
        ],
        "mem0": [
            "mcp__mem0__search_memories",
            "mcp__mem0__add_memory",
        ],
        # context7는 플러그인 네임스페이스가 필요할 수 있어 초기 맵에서 제외.
        # 사용자가 required_mcps=["context7"]로 스킬을 만들면 알려진 도구가
        # 없어서 빈 리스트가 반환되고, 호출자는 빌트인 도구만으로 진행한다.
    }
    out: list[str] = []
    for server in required_mcps:
        out.extend(known_tools_by_server.get(server, []))
    return out


def _build_system_prompt(skill_body: str) -> str:
    return (
        skill_body
        + "\n\n---\n\n## 실행 컨텍스트\n"
        + "사용자의 입력은 다음 user message로 전달됩니다. "
        + "이 스킬의 절차에 따라 작업을 수행하고 결과를 한국어로 반환하세요.\n"
    )


async def run_skill(
    *,
    slug: str,
    user_input: str,
    on_event: Callable[[dict], None],
    timeout: int = 600,
    model: str = "sonnet",
    runs_root: Optional[Path] = None,
) -> RunRecord:
    """스킬을 한 번 실행하고 RunRecord를 반환.

    예외는 내부에서 모두 잡아서 status="error" 레코드로 저장한다.
    """
    started = time.time()

    try:
        ctx = load_skill_for_execution(slug)
    except Exception as e:
        record = save_run(
            slug=slug,
            user_input=user_input,
            result_text="",
            status="error",
            tool_count=0,
            duration_seconds=round(time.time() - started, 2),
            error_message=str(e),
            runs_root=runs_root,
        )
        on_event({"action": "error", "message": f"스킬 로드 실패: {e}"})
        return record

    if ctx.isolation_mode == IsolationMode.ISOLATED:
        cwd = "/tmp"
        allowed_tools = list(_BUILTIN_TOOLS)
    else:
        cwd = os.getcwd()
        allowed_tools = list(_BUILTIN_TOOLS) + _build_allowed_tools_for_mcps(
            ctx.required_mcps
        )

    system_prompt = _build_system_prompt(ctx.skill_body)

    try:
        full_text, tool_count, timed_out = await stream_skill_execution(
            prompt=user_input or "스킬을 실행하세요",
            system_prompt=system_prompt,
            model=model,
            allowed_tools=allowed_tools,
            cwd=cwd,
            timeout=timeout,
            on_event=on_event,
        )
    except Exception as e:
        return save_run(
            slug=slug,
            user_input=user_input,
            result_text="",
            status="error",
            tool_count=0,
            duration_seconds=round(time.time() - started, 2),
            error_message=str(e),
            runs_root=runs_root,
        )

    status = "timeout" if timed_out else "completed"
    return save_run(
        slug=slug,
        user_input=user_input,
        result_text=full_text,
        status=status,
        tool_count=tool_count,
        duration_seconds=round(time.time() - started, 2),
        runs_root=runs_root,
    )
```

- [ ] **Step 4.4: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_skill_execution_runner.py -v`
Expected: 4개 테스트 PASS.

- [ ] **Step 4.5: 회귀 테스트**

Run: `python3 -m pytest tests/ --ignore=tests/test_integration_live.py -q`
Expected: 기존 + 새 테스트 모두 PASS.

- [ ] **Step 4.6: 커밋**

```bash
git add src/skill_builder/execution_runner.py tests/test_skill_execution_runner.py
git commit -m "feat(skill-tab): add execution_runner orchestrator"
```

---

## Task 5: WebSocket + REST 엔드포인트

**Files:**
- Modify: `src/ui/server.py`
- Modify: `src/skill_builder/__init__.py` (exports)
- Test: `tests/test_skill_execution_endpoint.py`

### WS 프로토콜

**Client → Server:**
```json
{"type": "execute", "data": {"slug": "summarize", "user_input": "..."}}
{"type": "cancel"}
```

**Server → Client:**
```json
{"type": "started"}
{"type": "tool_use", "data": {"tool": "Read", "tool_count": 1, "elapsed": 1.2}}
{"type": "text", "data": {"chunk": "...", "elapsed": 2.5}}
{"type": "completed", "data": {"run_id": "...", "result_text": "...", "tool_count": 5, "duration_seconds": 12.3, "status": "completed"}}
{"type": "error", "data": {"message": "..."}}
```

### REST 엔드포인트

`GET /api/skill-builder/runs/{slug}` → `{"runs": [RunRecord JSON, ...]}` (newest first)

- [ ] **Step 5.1: 실패 테스트 작성**

```python
# tests/test_skill_execution_endpoint.py
"""스킬 실행 엔드포인트 통합 테스트.

WS는 run_skill 함수를 모킹하여 와이어링만 검증.
REST는 임시 runs_root에 fixture 데이터를 넣고 응답 검증.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.skill_builder.run_history import save_run
from src.ui.server import app


def test_runs_endpoint_returns_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.skill_builder.run_history._DEFAULT_RUNS_ROOT", tmp_path
    )
    save_run(
        slug="summarize",
        user_input="긴 글",
        result_text="짧은 글",
        status="completed",
        tool_count=3,
        duration_seconds=10.0,
        runs_root=tmp_path,
    )
    client = TestClient(app)
    res = client.get("/api/skill-builder/runs/summarize")
    assert res.status_code == 200
    body = res.json()
    assert "runs" in body
    assert len(body["runs"]) == 1
    assert body["runs"][0]["slug"] == "summarize"


def test_runs_endpoint_empty_for_unknown_slug(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.skill_builder.run_history._DEFAULT_RUNS_ROOT", tmp_path
    )
    client = TestClient(app)
    res = client.get("/api/skill-builder/runs/never-ran")
    assert res.status_code == 200
    assert res.json() == {"runs": []}


def test_runs_endpoint_rejects_path_traversal() -> None:
    client = TestClient(app)
    res = client.get("/api/skill-builder/runs/..%2Fetc")
    # FastAPI는 보통 URL 디코딩 후 라우팅 — 400 또는 404 모두 OK
    assert res.status_code in (400, 404, 422)


def test_execute_ws_completed_flow(tmp_path: Path, monkeypatch) -> None:
    """WebSocket: execute 메시지 → started → tool_use → completed 흐름."""
    from src.skill_builder.run_history import RunRecord

    fake_record = RunRecord(
        run_id="123-abc",
        slug="summarize",
        user_input="x",
        result_text="결과",
        status="completed",
        tool_count=2,
        duration_seconds=1.0,
        started_at="2026-04-09T00:00:00+00:00",
    )

    async def fake_run(*, slug, user_input, on_event, **kwargs):
        on_event({"action": "started", "elapsed": 0})
        on_event({
            "action": "tool_use", "tool": "Read",
            "tool_count": 1, "elapsed": 0.5,
        })
        on_event({
            "action": "completed", "elapsed": 1.0,
            "tool_count": 2, "timed_out": False,
        })
        return fake_record

    # CRITICAL: server.py imports run_skill INSIDE the endpoint function,
    # so we must patch the source module (execution_runner) not ui.server.
    # Patching src.ui.server.run_skill would silently no-op.
    with patch(
        "src.skill_builder.execution_runner.run_skill",
        side_effect=fake_run,
    ):
        client = TestClient(app)
        with client.websocket_connect("/ws/skill-execute") as ws:
            ws.send_json({
                "type": "execute",
                "data": {"slug": "summarize", "user_input": "x"},
            })
            messages = []
            for _ in range(20):
                try:
                    messages.append(ws.receive_json())
                except Exception:
                    break
                if messages[-1]["type"] in ("completed", "error"):
                    break

    types = [m["type"] for m in messages]
    assert "started" in types
    assert "tool_use" in types
    assert "completed" in types
    completed_msg = next(m for m in messages if m["type"] == "completed")
    assert completed_msg["data"]["run_id"] == "123-abc"
    assert completed_msg["data"]["result_text"] == "결과"
```

- [ ] **Step 5.2: 테스트 실행 → 실패 확인**

Run: `python3 -m pytest tests/test_skill_execution_endpoint.py -v`
Expected: 라우트 미존재로 모두 FAIL.

- [ ] **Step 5.3: server.py 수정**

`src/ui/server.py`의 기존 `/api/skill-builder/list` 엔드포인트(824번 줄) 바로 아래에 다음 추가:

```python
@app.get("/api/skill-builder/runs/{slug}")
async def skill_builder_runs(slug: str):
    """특정 스킬의 실행 이력을 newest first로 반환."""
    from dataclasses import asdict
    from fastapi.responses import JSONResponse

    from src.skill_builder.run_history import list_runs

    try:
        records = list_runs(slug)
    except ValueError:
        return JSONResponse({"error": "invalid slug"}, status_code=400)
    return {"runs": [asdict(r) for r in records]}


@app.websocket("/ws/skill-execute")
async def skill_execute_endpoint(ws: WebSocket):
    """스킬 카드 실행 — single shot 실행 후 종료.

    Protocol: tests/test_skill_execution_endpoint.py 참고.
    """
    import asyncio

    from src.skill_builder.execution_runner import run_skill

    await ws.accept()
    task = None  # Track runner so we can cancel on disconnect
    try:
        msg = await ws.receive_json()
        if msg.get("type") != "execute":
            await ws.send_json({
                "type": "error",
                "data": {"message": "첫 메시지는 execute 타입이어야 합니다"},
            })
            return

        data = msg.get("data") or {}
        slug = (data.get("slug") or "").strip()
        user_input = data.get("user_input") or ""
        if not slug:
            await ws.send_json({
                "type": "error",
                "data": {"message": "slug는 필수입니다"},
            })
            return

        pending: list[dict] = []

        def on_event(ev: dict) -> None:
            # streamer는 동기 콜백을 호출 — 큐에 쌓아두고 메인 task가 flush
            pending.append(ev)

        async def flush_pending():
            while pending:
                ev = pending.pop(0)
                action = ev.get("action")
                if action == "tool_use":
                    await ws.send_json({"type": "tool_use", "data": ev})
                elif action == "text":
                    await ws.send_json({"type": "text", "data": ev})
                elif action == "started":
                    await ws.send_json({"type": "started"})
                elif action == "timeout":
                    await ws.send_json({"type": "timeout", "data": ev})
                elif action == "error":
                    await ws.send_json({"type": "error", "data": ev})
                # 'completed'는 run_skill 반환 후 record와 함께 보냄

        async def runner_task():
            return await run_skill(
                slug=slug,
                user_input=user_input,
                on_event=on_event,
            )

        task = asyncio.create_task(runner_task())
        # 50ms마다 flush — 실시간성 vs 오버헤드 균형
        while not task.done():
            await flush_pending()
            await asyncio.sleep(0.05)
        await flush_pending()
        record = await task

        await ws.send_json({
            "type": "completed",
            "data": {
                "run_id": record.run_id,
                "result_text": record.result_text,
                "tool_count": record.tool_count,
                "duration_seconds": record.duration_seconds,
                "status": record.status,
                "error_message": record.error_message,
            },
        })
    except WebSocketDisconnect:
        # Cancel orphaned runner task so subprocess is torn down
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return
    except Exception as e:
        if task and not task.done():
            task.cancel()
        try:
            await ws.send_json({"type": "error", "data": {"message": str(e)}})
        except Exception:
            pass
```

- [ ] **Step 5.4: __init__.py exports 업데이트**

```python
# src/skill_builder/__init__.py
# (기존 exports가 있으면 함께 유지)
from src.skill_builder.execution_runner import run_skill
from src.skill_builder.run_history import RunRecord, list_runs, save_run
from src.skill_builder.skill_loader import (
    IsolationMode,
    SkillExecutionContext,
    load_skill_for_execution,
)

__all__ = [
    "run_skill",
    "RunRecord",
    "list_runs",
    "save_run",
    "load_skill_for_execution",
    "IsolationMode",
    "SkillExecutionContext",
]
```

- [ ] **Step 5.5: 테스트 실행 → 통과 확인**

Run: `python3 -m pytest tests/test_skill_execution_endpoint.py -v`
Expected: 4개 PASS.

- [ ] **Step 5.6: 그래프 빌드 회귀**

Run: `python3 -c "from src.graphs.main_graph import build_pipeline; p = build_pipeline(); print('OK')"`
Expected: `OK`

- [ ] **Step 5.7: 커밋**

```bash
git add src/ui/server.py src/skill_builder/__init__.py tests/test_skill_execution_endpoint.py
git commit -m "feat(skill-tab): add WS execute endpoint and REST runs history"
```

---

## Task 6: 프론트엔드 — 인라인 펼침 + 입력 폼 + 실시간 로그 + 이력

**Files:**
- Modify: `src/ui/static/js/mode-skill.js`
- Modify: `src/ui/static/css/skill.css` (기존 `.skill-card` 스타일이 여기 있음 — plan 가정과 다름)

수동 검증 위주 (Plan 1과 동일 패턴). 자동 테스트는 WS 와이어링이 Task 5에서 검증되었으므로 UI는 Task 7에서 Playwright 수동 E2E로 확인.

### 카드 인라인 펼침 동작 사양

1. 카드 클릭 → 카드 본체에 `expanded` 클래스 추가, 아래쪽에 `.skill-exec-panel` 삽입
2. 패널 구조:
   ```
   .skill-exec-panel
     .skill-exec-form
       textarea (placeholder: "이 스킬에 어떤 입력을 줄까요?")
       button "실행"
     .skill-exec-log (활동 로그, 실행 중에만 표시)
     .skill-exec-result (마크다운 결과, 완료 후 표시)
     .skill-exec-history-toggle (버튼 "이력 보기 (N건)")
     .skill-exec-history (토글 시 표시)
   ```
3. 카드 다시 클릭 → 패널 제거 (펼침 해제)
4. 한 카드만 펼칠 수 있음 — 다른 카드 클릭 시 기존 패널 닫힘
5. 실행 중에는 펼침 해제 비활성

- [ ] **Step 6.1: mode-skill.js 확장 (카드 forEach 블록 교체)**

`_renderListPanel(root)` 함수 안의 `skills.forEach(function (s) {...})` 블록을 다음과 같이 교체:

```javascript
skills.forEach(function (s) {
  var card = document.createElement('div');
  card.className = 'skill-card';
  card.setAttribute('data-slug', s.slug);

  var h = document.createElement('h4');
  h.textContent = s.name || s.slug;
  card.appendChild(h);

  var meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent =
    (s.source === 'created' ? '직접 생성' : '가져옴')
    + ' · ' + (s.created_at || '').slice(0, 10);
  card.appendChild(meta);

  if (s.required_mcps && s.required_mcps.length) {
    var mcps = document.createElement('div');
    mcps.className = 'mcps';
    mcps.textContent = 'MCP: ' + s.required_mcps.join(', ');
    card.appendChild(mcps);
  }

  var hint = document.createElement('div');
  hint.className = 'skill-card-hint';
  hint.textContent = '클릭해서 실행';
  card.appendChild(hint);

  card.addEventListener('click', function (e) {
    if (e.target.closest('.skill-exec-panel')) return;
    _toggleExecPanel(card, s);
  });

  grid.appendChild(card);
});
```

- [ ] **Step 6.2: mode-skill.js 확장 (헬퍼 함수 추가)**

같은 IIFE 안(기존 `_renderListPanel` 아래)에 다음 함수들을 추가:

```javascript
var _activeExecCard = null;
var _execWs = null;

function _toggleExecPanel(card, skill) {
  if (_activeExecCard && _activeExecCard !== card) {
    _closeExecPanel(_activeExecCard);
  }
  if (card.classList.contains('expanded')) {
    _closeExecPanel(card);
    return;
  }
  _openExecPanel(card, skill);
}

function _closeExecPanel(card) {
  if (_execWs) {
    try { _execWs.close(); } catch (e) {}
    _execWs = null;
  }
  var panel = card.querySelector('.skill-exec-panel');
  if (panel) panel.parentNode.removeChild(panel);
  card.classList.remove('expanded');
  if (_activeExecCard === card) _activeExecCard = null;
}

function _openExecPanel(card, skill) {
  card.classList.add('expanded');
  _activeExecCard = card;

  var panel = document.createElement('div');
  panel.className = 'skill-exec-panel';

  var form = document.createElement('div');
  form.className = 'skill-exec-form';

  var textarea = document.createElement('textarea');
  textarea.className = 'skill-exec-input';
  textarea.placeholder = '이 스킬에 어떤 입력을 줄까요?\n자유롭게 적어주세요.';
  form.appendChild(textarea);

  var runBtn = document.createElement('button');
  runBtn.className = 'skill-exec-run-btn';
  runBtn.textContent = '실행';
  form.appendChild(runBtn);
  panel.appendChild(form);

  var log = document.createElement('div');
  log.className = 'skill-exec-log';
  log.style.display = 'none';
  panel.appendChild(log);

  var result = document.createElement('div');
  result.className = 'skill-exec-result';
  result.style.display = 'none';
  panel.appendChild(result);

  var historyToggle = document.createElement('button');
  historyToggle.className = 'skill-exec-history-toggle';
  historyToggle.textContent = '이력 불러오는 중...';
  panel.appendChild(historyToggle);

  var historyEl = document.createElement('div');
  historyEl.className = 'skill-exec-history';
  historyEl.style.display = 'none';
  panel.appendChild(historyEl);

  card.appendChild(panel);

  fetch('/api/skill-builder/runs/' + encodeURIComponent(skill.slug))
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var runs = (data && data.runs) || [];
      historyToggle.textContent = '이력 보기 (' + runs.length + '건)';
      historyToggle.onclick = function () {
        if (historyEl.style.display === 'none') {
          _renderHistory(historyEl, runs);
          historyEl.style.display = '';
          historyToggle.textContent = '이력 숨기기';
        } else {
          historyEl.style.display = 'none';
          historyToggle.textContent = '이력 보기 (' + runs.length + '건)';
        }
      };
    })
    .catch(function () {
      historyToggle.textContent = '이력 불러오기 실패';
      historyToggle.disabled = true;
    });

  runBtn.onclick = function () {
    var input = textarea.value.trim();
    runBtn.disabled = true;
    textarea.disabled = true;
    log.innerHTML = '';
    log.style.display = '';
    result.style.display = 'none';
    _startExecution(skill, input, log, result, runBtn, textarea, historyToggle);
  };

  textarea.focus();
}

function _renderHistory(el, runs) {
  while (el.firstChild) el.removeChild(el.firstChild);
  if (!runs.length) {
    var empty = document.createElement('div');
    empty.className = 'skill-exec-history-empty';
    empty.textContent = '아직 실행 이력이 없습니다';
    el.appendChild(empty);
    return;
  }
  runs.forEach(function (r) {
    var item = document.createElement('div');
    item.className = 'skill-exec-history-item status-' + r.status;

    var head = document.createElement('div');
    head.className = 'history-head';
    head.textContent =
      (r.started_at || '').replace('T', ' ').slice(0, 19)
      + ' · ' + r.status
      + ' · 도구 ' + r.tool_count + '회';
    item.appendChild(head);

    var inputEl = document.createElement('div');
    inputEl.className = 'history-input';
    inputEl.textContent = '입력: ' + (r.user_input || '(없음)').slice(0, 100);
    item.appendChild(inputEl);

    if (r.result_text) {
      var resEl = document.createElement('div');
      resEl.className = 'history-result';
      _renderMarkdownSafe(resEl, r.result_text);
      item.appendChild(resEl);
    }
    if (r.error_message) {
      var errEl = document.createElement('div');
      errEl.className = 'history-error';
      errEl.textContent = '❌ ' + r.error_message;
      item.appendChild(errEl);
    }
    el.appendChild(item);
  });
}

function _startExecution(skill, input, logEl, resultEl, runBtn, textarea, historyToggle) {
  var proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  _execWs = new WebSocket(proto + location.host + '/ws/skill-execute');

  function appendLog(text, kind) {
    var line = document.createElement('div');
    line.className = 'exec-log-line ' + (kind || '');
    line.textContent = text;
    logEl.appendChild(line);
    logEl.scrollTop = logEl.scrollHeight;
  }

  _execWs.onopen = function () {
    appendLog('▶ 실행 시작', 'started');
    _execWs.send(JSON.stringify({
      type: 'execute',
      data: { slug: skill.slug, user_input: input },
    }));
  };

  _execWs.onmessage = function (ev) {
    var msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'started') {
      appendLog('🚀 세션 시작', 'started');
    } else if (msg.type === 'tool_use') {
      var tool = (msg.data && msg.data.tool) || '?';
      var elapsed = (msg.data && msg.data.elapsed) || 0;
      appendLog('🔧 ' + tool + ' (' + elapsed + 's)', 'tool');
    } else if (msg.type === 'text') {
      // 텍스트 청크는 결과 누적용 — 실시간 표시는 옵션
    } else if (msg.type === 'timeout') {
      appendLog('⏱️ 타임아웃', 'timeout');
    } else if (msg.type === 'completed') {
      var data = msg.data || {};
      appendLog(
        '✅ 완료 (' + data.duration_seconds + 's, 도구 ' + data.tool_count + '회)',
        'completed'
      );
      _renderMarkdownSafe(resultEl, data.result_text || '(빈 결과)');
      resultEl.style.display = '';
      runBtn.disabled = false;
      textarea.disabled = false;
      fetch('/api/skill-builder/runs/' + encodeURIComponent(skill.slug))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var runs = (data && data.runs) || [];
          historyToggle.textContent = '이력 보기 (' + runs.length + '건)';
        });
    } else if (msg.type === 'error') {
      appendLog('❌ ' + ((msg.data && msg.data.message) || '오류'), 'error');
      runBtn.disabled = false;
      textarea.disabled = false;
    }
  };

  _execWs.onerror = function () {
    appendLog('[오류] WebSocket 연결 실패', 'error');
    runBtn.disabled = false;
    textarea.disabled = false;
  };

  _execWs.onclose = function () {
    appendLog('세션 종료', 'closed');
  };
}
```

- [ ] **Step 6.3: skill.css에 스타일 추가**

`skill.css`의 기존 `.skill-card` 블록(line 280 근처)에 `cursor: pointer;`만 추가하고, 나머지 신규 스타일은 `.skill-empty` 아래에 append. `.skill-card:hover`는 이미 있으므로 재정의하지 않음.

```css
/* 기존 .skill-card 블록에 한 줄 추가: cursor: pointer; */

/* 아래는 .skill-empty 블록 뒤에 append */
.skill-card.expanded {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.18);
}
.skill-card-hint {
  color: rgba(255, 255, 255, 0.4);
  font-size: 0.78rem;
  margin-top: 0.4rem;
}
.skill-card.expanded .skill-card-hint { display: none; }

.skill-exec-panel {
  margin-top: 0.8rem;
  padding-top: 0.8rem;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.skill-exec-form { display: flex; flex-direction: column; gap: 0.5rem; }
.skill-exec-input {
  width: 100%;
  min-height: 80px;
  padding: 0.6rem 0.7rem;
  background: rgba(0, 0, 0, 0.25);
  color: #f0f0f0;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  font-family: inherit;
  resize: vertical;
}
.skill-exec-input:disabled { opacity: 0.5; }
.skill-exec-run-btn {
  align-self: flex-end;
  padding: 0.45rem 1.1rem;
  background: #4a8cff;
  color: white;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.skill-exec-run-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.skill-exec-log {
  background: rgba(0, 0, 0, 0.3);
  border-radius: 6px;
  padding: 0.5rem 0.7rem;
  max-height: 180px;
  overflow-y: auto;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 0.82rem;
  color: rgba(255, 255, 255, 0.85);
}
.exec-log-line { padding: 0.15rem 0; }
.exec-log-line.tool { color: #a8c8ff; }
.exec-log-line.error { color: #ff9090; }
.exec-log-line.completed { color: #90e090; }
.exec-log-line.timeout { color: #ffb060; }

.skill-exec-result {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 6px;
  padding: 0.8rem 1rem;
  color: #f0f0f0;
  line-height: 1.55;
}
.skill-exec-result h1, .skill-exec-result h2, .skill-exec-result h3 {
  margin-top: 0.6rem;
}
.skill-exec-result pre {
  background: rgba(0, 0, 0, 0.4);
  padding: 0.6rem;
  border-radius: 4px;
  overflow-x: auto;
}

.skill-exec-history-toggle {
  align-self: flex-start;
  background: none;
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: rgba(255, 255, 255, 0.7);
  padding: 0.35rem 0.8rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.82rem;
}
.skill-exec-history-toggle:hover { background: rgba(255, 255, 255, 0.05); }

.skill-exec-history { display: flex; flex-direction: column; gap: 0.5rem; }
.skill-exec-history-empty {
  color: rgba(255, 255, 255, 0.5);
  font-size: 0.85rem;
  padding: 0.4rem;
}
.skill-exec-history-item {
  background: rgba(0, 0, 0, 0.2);
  border-left: 3px solid rgba(255, 255, 255, 0.2);
  padding: 0.5rem 0.7rem;
  border-radius: 4px;
}
.skill-exec-history-item.status-completed { border-left-color: #6dca6d; }
.skill-exec-history-item.status-error { border-left-color: #ff7070; }
.skill-exec-history-item.status-timeout { border-left-color: #ffa040; }
.history-head {
  font-size: 0.78rem;
  color: rgba(255, 255, 255, 0.6);
  margin-bottom: 0.3rem;
}
.history-input {
  font-size: 0.85rem;
  color: rgba(255, 255, 255, 0.85);
  margin-bottom: 0.3rem;
}
.history-result {
  font-size: 0.85rem;
  color: rgba(255, 255, 255, 0.95);
  background: rgba(0, 0, 0, 0.25);
  padding: 0.4rem 0.6rem;
  border-radius: 3px;
}
.history-error { font-size: 0.85rem; color: #ff9090; }
```

- [ ] **Step 6.4: 빌드 회귀 확인**

Run: `python3 -m pytest tests/ --ignore=tests/test_integration_live.py -q`
Expected: 모든 테스트 PASS (프론트엔드 변경은 단위 테스트에 영향 없음).

Run: `python3 -c "from src.graphs.main_graph import build_pipeline; p = build_pipeline(); print('OK')"`
Expected: `OK`

- [ ] **Step 6.5: 커밋**

```bash
git add src/ui/static/js/mode-skill.js src/ui/static/css/skill.css
git commit -m "feat(skill-tab): inline card execution UI with streaming log and history"
```

---

## Task 7: Handoff 프롬프트 검증 + 수동 E2E + BACKLOG 정리

### 배경
Plan 1의 `clarification_questions.py:91`에 "rule 8: NO AUTO-TRIGGER, neutral description" 지시가 이미 있다. 하지만 실제 skill-creator 응답이 이 지시를 100% 따르는지는 검증되지 않았다. Plan 2에서 카드 실행을 시작하기 전 마지막 안전망 점검.

- [ ] **Step 7.1: 서버 구동**

Run: `python3 -m src.ui.server` (또는 평소 실행 명령)
Expected: `http://localhost:8000` 접근 가능.

- [ ] **Step 7.2: 새 스킬 생성 — 트리거 유발 시나리오**

UI에서 "스킬 만들기" 패널 진입 → 다음 설명으로 시작:
> 긴 텍스트를 받아서 정확히 3줄로 한국어 요약을 만들어줘

skill-creator와 다중 턴 대화 진행 → `[SKILL_COMPLETE]` 수신 후 카드가 "내 스킬"에 등장하는지 확인.

- [ ] **Step 7.3: 생성된 SKILL.md description 필드 점검**

Run: `head -10 ~/.claude/skills/skill-tab-<생성된-slug>/SKILL.md`

Expected: YAML frontmatter의 `description:` 필드가 다음 패턴을 **포함하지 않아야** 함:
- "use this whenever..."
- "사용자가 ... 라고 할 때"
- "반드시 사용"
- "must trigger"
- "use when the user mentions ..."

만약 위 패턴이 있으면 → **Step 7.3a로 이동**, 없으면 → Step 7.4.

- [ ] **Step 7.3a: (조건부) handoff 프롬프트 강화**

`src/skill_builder/clarification_questions.py`의 rule 8 블록에 다음 내용을 추가:

```python
"""
   - **EXAMPLE OF BAD DESCRIPTION (DO NOT WRITE LIKE THIS):**
     "Use this whenever the user asks to summarize or shorten text"
     "사용자가 요약을 요청할 때 반드시 사용"
   - **EXAMPLE OF GOOD DESCRIPTION (NEUTRAL, FACTUAL):**
     "Generates a 3-line Korean summary from a given long text"
     "긴 텍스트로부터 3줄 한국어 요약을 생성합니다"
   - If your draft description starts with "Use this", "사용", "When", "Whenever",
     STOP and rewrite it to start with the action verb instead.
"""
```

수정 후 Step 7.2부터 재시도.

- [ ] **Step 7.4: 카드 실행 — 정상 흐름 (Playwright 또는 수동)**

UI에서 "내 스킬" 패널 → 방금 만든 카드 클릭 → 인라인 펼침 확인 → textarea에 다음 입력:
> 인공지능은 1950년대 앨런 튜링의 '계산 기계와 지능' 논문에서 시작되어 (...긴 단락...) 현대의 트랜스포머 모델까지 발전해왔다.

"실행" 버튼 클릭 → 활동 로그에 다음 순서로 나타나는지 확인:
1. `▶ 실행 시작`
2. `🚀 세션 시작`
3. `🔧 Read/Write/...` (도구 사용 1회 이상)
4. `✅ 완료 (Ns, 도구 N회)`

결과가 결과 영역에 마크다운으로 렌더되는지 확인 (3줄 요약).

- [ ] **Step 7.5: 격리 검증 — CRITICAL (probe 기반)**

**주의**: Task 3의 `execution_streamer`는 `tool_use` 이벤트에서 도구 이름과 카운트만 emit하고 **도구 입력(파일 경로 등)은 포함하지 않는다**. 따라서 활동 로그만 봐서는 "CLAUDE.md를 읽었는지" 확인할 수 없다. 대신 **probe 입력**으로 실제 cwd를 결과 텍스트에 역전달받아 확인한다.

방금 만든 스킬을 다시 실행하되, 입력란에 다음 probe 텍스트를 넣는다:
```
이 작업을 시작하기 전에 먼저 현재 작업 디렉터리 경로를 확인해서 결과 맨 위에 "CWD: <경로>" 형식으로 한 줄 출력하세요. 그 다음에 평소처럼 스킬을 실행하세요.
```

실행 후 결과 영역의 첫 줄을 확인:
- **ISOLATED 모드 정상**: `CWD: /tmp` 또는 `CWD: /private/tmp` 등 — macOS에서 `/tmp`는 `/private/tmp`의 심볼릭 링크
- **격리가 깨진 경우**: `CWD: /Users/elvis.costello/Desktop/backup_web_local` — 프로젝트 루트가 보이면 즉시 `execution_runner.py`의 `IsolationMode.ISOLATED` 분기 점검

추가 검증: `ls -la /tmp/CLAUDE.md` → 파일이 없음을 확인 (프로젝트의 CLAUDE.md가 /tmp에 링크되거나 복사돼 있지 않아야 함)

또한 `data/skills/runs/<slug>/`에 새 .json 파일이 1건 생성되었는지 확인:
Run: `ls -lt data/skills/runs/<slug>/ | head -3`
Expected: 방금 실행한 타임스탬프의 `.json` 파일 1개.

**MCP 스킬이 있다면 추가 verification**: `required_mcps != []` 스킬을 만들어 같은 probe로 실행 → `CWD: /Users/.../backup_web_local`이 나와야 정상 (의도적 노출).

- [ ] **Step 7.6: 이력 토글 검증**

같은 카드의 "이력 보기 (1건)" 버튼 클릭 → 방금 실행한 이력 1건이 표시되는지, 결과 마크다운이 정상 렌더되는지 확인. 다시 클릭 → 숨김.

- [ ] **Step 7.7: 두 번째 실행 + 카드 전환**

같은 카드에서 다른 입력으로 한 번 더 실행 → "이력 보기 (2건)"으로 카운트 갱신 확인.
다른 카드를 클릭 → 첫 카드는 자동 닫힘 + 새 카드가 펼쳐지는지 확인.

- [ ] **Step 7.8: 에러 흐름 — slug 누락**

브라우저 콘솔에서:
```js
var ws = new WebSocket('ws://localhost:8000/ws/skill-execute');
ws.onopen = function(){ ws.send(JSON.stringify({type:'execute', data:{slug:'', user_input:'x'}})); };
ws.onmessage = function(e){ console.log(e.data); };
```
Expected: `{"type":"error","data":{"message":"slug는 필수입니다"}}`

- [ ] **Step 7.9: BACKLOG.md 업데이트**

`BACKLOG.md`의 "스킬 탭 — Plan 2: 카드 실행 + 스케줄링" 섹션을 다음과 같이 분리:

```markdown
## 스킬 탭 — Plan 2: 카드 실행 ✅ 완료 (2026-04-09)

**완료 범위**:
- skill_loader / run_history / execution_streamer / execution_runner 4개 모듈
- /ws/skill-execute WebSocket + /api/skill-builder/runs/{slug} REST
- 카드 인라인 펼침 UI: 입력 폼, 실시간 활동 로그, 마크다운 결과, 이력 토글
- 격리 정책: required_mcps에 따른 cwd / allowed_tools 분기
- E2E 검증 (Task 7): handoff 프롬프트 description 중립성 확인

## 스킬 탭 — Plan 3: 카드 실행 스케줄링 (미착수)

**전제**: Plan 2 완료
**범위**:
  - 기존 스케줄팀(`card-mode-schedule`) 인프라에 "스킬 실행" 작업 타입 추가
  - 스케줄 등록 UI에서 스킬 + 입력값 사전 지정
  - 자동 실행 시 execution_runner 호출 + 결과를 run_history에 저장
  - 스킬 실행 결과를 스케줄 보고서와 동일 구조로 HTML/MD 이중 생성
참고: docs/superpowers/plans/2026-04-09-skill-tab-plan2.md (Plan 2 완료)
```

- [ ] **Step 7.10: 최종 회귀 테스트**

Run: `python3 -m pytest tests/ --ignore=tests/test_integration_live.py -v`
Expected: 전체 PASS.

Run: `python3 -c "from src.graphs.main_graph import build_pipeline; p = build_pipeline(); print('OK')"`
Expected: `OK`

- [ ] **Step 7.11: 최종 커밋**

```bash
git add BACKLOG.md
# Step 7.3a를 거쳤다면 clarification_questions.py도 함께
git commit -m "docs(skill-tab): mark Plan 2 (card execution) complete, defer scheduling to Plan 3"
```

---

## 주의사항 및 알려진 제약

1. **자동 트리거 차단의 다층 방어**:
   - **방어 1 (생성 시)**: handoff 프롬프트 rule 8이 description에서 트리거 어구 금지
   - **방어 2 (실행 시)**: `--add-dir ~/.claude/skills/`를 절대 사용하지 않음 → Claude Code가 `~/.claude/skills/` 자체를 발견 못함
   - **방어 3 (격리 모드)**: `cwd=/tmp` + 빌트인 도구만 → CLAUDE.md / 다른 스킬 / 프로젝트 파일 모두 차단
   - 한 층이 뚫려도 나머지가 막음. Task 7.5의 수동 검증은 방어 2/3이 작동하는지 확인하는 용도.

2. **MCP 모드 격리 한계**: `WITH_MCPS` 모드는 `cwd=프로젝트 루트`라서 CLAUDE.md를 읽을 수 있다. 트레이드오프로 받아들이되, 사용자가 명시적으로 MCP가 필요한 스킬을 만들었을 때만 진입하는 분기이므로 의도적 노출이다. 후속 과제: 임시 작업 디렉터리를 만들고 필요한 MCP만 담은 최소 `.mcp.json`을 작성하는 방식으로 격리 강화.

3. **`_build_allowed_tools_for_mcps`의 불완전성**: 현재는 잘 알려진 MCP 서버 6개(serper, firecrawl, brave-search, github, mem0, context7)의 대표 도구만 매핑한다. 새 MCP가 추가되면 이 함수에 추가해야 한다. 후속 과제: `skill_metadata.json`에 `required_tools` 필드(정확한 `mcp__...` 도구명)를 추가하면 이 매핑이 불필요해진다.

4. **WS flush 주기 50ms**: 실시간성과 이벤트 루프 부하의 균형. 도구 사용이 매우 빠른 경우 한 flush 주기에 여러 이벤트가 묶여 나갈 수 있지만 UX상 문제 없음. 필요 시 `asyncio.sleep(0.02)`로 조정.

5. **timeout 기본 600초**: 긴 스킬(예: 검색 후 보고서 작성)은 부족할 수 있음. 후속 과제: 카드 UI에서 사용자가 timeout 조정 가능하게.

6. **Run history 무한 누적**: 현재는 모든 실행을 영구 저장. 후속 과제: 자동 회전 (예: 카드당 최근 50건 유지).

7. **WebSocket 재연결 미지원**: 페이지 새로고침 시 진행 중인 실행과의 연결이 끊어진다. `data/skills/runs/`에는 결과가 저장되지만 UI에서 진행 상황을 다시 볼 수 없음. Plan 3 또는 후속에서 처리.

8. **테스트의 subprocess 모킹 한계**: `tests/test_execution_streamer.py`는 fake proc factory로 라인을 주입하지만, 실제 asyncio subprocess API의 stdin/stdout 동작과 100% 동일하지는 않다. 진짜 통합 검증은 Task 7의 수동 E2E에 의존.

9. **테스트의 전역 상태 (runs_root)**: `test_skill_execution_endpoint.py`의 REST 테스트는 `monkeypatch.setattr`로 `_DEFAULT_RUNS_ROOT`를 임시 경로로 바꾼다. 이는 모듈 전역 변수이므로 테스트 병렬 실행 시 문제가 될 수 있음. pytest 기본은 순차 실행이므로 현재는 안전하지만, 병렬화 도입 시 재설계 필요.

10. **REST path traversal 테스트의 FastAPI 동작**: `/api/skill-builder/runs/..%2Fetc`는 Starlette의 라우팅 단계에서 URL 디코딩되어 다양한 상태 코드를 반환할 수 있다. 테스트는 `400/404/422` 중 하나를 허용한다.

11. **Plan 3에서 다룰 범위**: 스케줄링 통합, 스킬 실행 결과 알림(브라우저 Notification), Slack/Email 전송, run history 자동 회전, timeout 사용자 조정, 임시 작업 디렉터리 기반 강화 격리.

---

## Related Skills

- @superpowers:test-driven-development — 각 Task가 RED → GREEN → REFACTOR 사이클
- @superpowers:executing-plans — 이 계획을 실행할 때
- @superpowers:subagent-driven-development — 서브에이전트 가용 시 선호
- @superpowers:verification-before-completion — Task 7의 수동 검증은 성공 주장 전 필수
- @superpowers:systematic-debugging — Task 7.5에서 격리가 깨졌을 경우 근본 원인 추적
