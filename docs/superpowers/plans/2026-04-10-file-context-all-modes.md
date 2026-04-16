# 4개 모드 로컬 파일 워크스페이스 Implementation Plan (확정)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 인스턴트·나만의방식·야근팀·스킬 4개 모드에 모드별 전용 폴더(`data/workspace/{mode}/input/`, `output/`)를 제공하여, 사용자가 로컬 파일을 넣어두고 이름만으로 참조하고, 결과물은 output에 자동 저장되게 한다. 나만의방식 설계와 스킬 만들기 단계에서도 파일을 인식시켜 더 핏한 설계가 가능하게 한다.

**Architecture:** 웹 배포용 업로드 API를 쓰지 않는다. 로컬 앱이므로 서버와 사용자가 같은 파일시스템을 공유한다. 모드별 `data/workspace/{mode}/input/` 폴더에 파일을 넣어두면, 백엔드가 직접 읽어서 AI 컨텍스트로 주입한다. UI에는 폴더 내 파일 목록을 표시하고, 사용자가 선택하면 해당 파일을 인식한다. 파일을 선택하지 않으면 기존과 100% 동일하게 동작한다(하위호환).

**Tech Stack:** FastAPI (파일 목록 API), Vanilla JS (WorkspacePanel UI), 기존 WebSocket 프로토콜 확장

---

## 하위호환 보장

모든 변경은 옵셔널이다:
- `workspace_files`는 기본값 `[]` — 누락 시 빈 배열
- 파일 컨텍스트 주입은 `if workspace_files:` 조건부
- WorkspacePanel에서 아무것도 선택하지 않으면 기존 텍스트 전용 실행과 동일

---

## 폴더 구조

```
data/workspace/
  instant/
    input/          ← 사용자가 파일을 넣는 곳
    output/         ← AI 결과물 저장 ({session_id}/ 하위)
  builder/
    input/
    output/
  overtime/
    input/
    output/
  skill/
    input/
    output/
```

## 파일 인식 지점 총 정리

| 모드 | 설계(만들기) | 실행 |
|------|-------------|------|
| 인스턴트 | — | WorkspacePanel → `start.workspace_files` |
| 나만의방식 | WorkspacePanel → `builder_message.workspace_files` | WorkspacePanel → `start.workspace_files` |
| 야근팀 | — | WorkspacePanel → `start_overtime.data.workspace_files` |
| 스킬 | WorkspacePanel → `skill-builder start.data.workspace_files` | WorkspacePanel → `skill-execute execute.data.workspace_files` |

## 수정 대상 파일 구조

| 액션 | 경로 | 역할 |
|------|------|------|
| Create | `src/utils/workspace.py` | 워크스페이스 폴더 관리 + 파일→컨텍스트 변환 |
| Create | `src/ui/routes/workspace.py` | 파일 목록 조회 REST API + 폴더 열기 |
| Create | `src/ui/static/js/workspace-panel.js` | 공통 파일 목록 + 선택 UI 컴포넌트 |
| Create | `src/ui/static/css/workspace.css` | 워크스페이스 패널 스타일 |
| Modify | `src/ui/server.py` | 라우터 등록 + workspace 초기화 + builder/skill WS 확장 |
| Modify | `src/ui/sim_runner.py` | start 메시지에서 workspace_files 수신 → 컨텍스트 주입 |
| Modify | `src/ui/static/js/mode-company-card.js` | 인스턴트/나만의방식에 WorkspacePanel 연결 |
| Modify | `src/ui/static/js/mode-overtime.js` | 야근팀에 WorkspacePanel 연결 |
| Modify | `src/ui/static/js/mode-skill.js` | 스킬에 WorkspacePanel 연결 |
| Modify | `src/overtime/runner.py` | 파일 컨텍스트를 iteration 프롬프트에 주입 |
| Modify | `src/company_builder/builder_agent.py` | 방식 설계 시 파일 컨텍스트 주입 |
| Modify | `src/skill_builder/runner.py` | 스킬 만들기 시 파일 컨텍스트 주입 |
| Modify | `src/ui/static/index.html` | workspace JS/CSS 로드 |
| Create | `tests/test_workspace.py` | workspace 유틸 단위 테스트 |
| Create | `tests/test_workspace_api.py` | 파일 목록 API 테스트 |

---

### Task 1: 워크스페이스 유틸 (`src/utils/workspace.py`)

**Files:**
- Create: `src/utils/workspace.py`
- Test: `tests/test_workspace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workspace.py
"""워크스페이스 유틸 테스트."""
from pathlib import Path
from src.utils.workspace import (
    ensure_workspace, list_input_files, read_files_as_context, get_output_dir,
)


def test_ensure_workspace_creates_dirs(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert (tmp_path / "instant" / "input").is_dir()
    assert (tmp_path / "instant" / "output").is_dir()


def test_list_input_files(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    inp = tmp_path / "instant" / "input"
    (inp / "data.csv").write_text("a,b\n1,2")
    (inp / "notes.txt").write_text("hello")
    files = list_input_files("instant", base=tmp_path)
    names = [f["name"] for f in files]
    assert "data.csv" in names
    assert "notes.txt" in names


def test_list_input_files_empty(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert list_input_files("instant", base=tmp_path) == []


def test_read_files_as_context_text(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    (tmp_path / "instant" / "input" / "report.csv").write_text("name,value\nfoo,42")
    ctx = read_files_as_context("instant", ["report.csv"], base=tmp_path)
    assert "report.csv" in ctx
    assert "foo,42" in ctx


def test_read_files_as_context_binary(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    (tmp_path / "instant" / "input" / "img.png").write_bytes(b"\x89PNG\r\n")
    ctx = read_files_as_context("instant", ["img.png"], base=tmp_path)
    assert "이미지" in ctx


def test_read_files_as_context_missing_file(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert read_files_as_context("instant", ["nope.txt"], base=tmp_path) == ""


def test_read_files_as_context_path_traversal(tmp_path):
    ensure_workspace("instant", base=tmp_path)
    assert read_files_as_context("instant", ["../../etc/passwd"], base=tmp_path) == ""


def test_read_files_as_context_empty_list(tmp_path):
    assert read_files_as_context("instant", [], base=tmp_path) == ""


def test_get_output_dir(tmp_path):
    out = get_output_dir("instant", "abc123", base=tmp_path)
    assert out.is_dir()
    assert "abc123" in str(out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_workspace.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement the utility**

```python
# src/utils/workspace.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_workspace.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/utils/workspace.py tests/test_workspace.py
git commit -m "feat: add local workspace utility for mode-specific file management"
```

---

### Task 2: 워크스페이스 REST API (`src/ui/routes/workspace.py`)

**Files:**
- Create: `src/ui/routes/workspace.py`
- Modify: `src/ui/server.py` (라우터 등록 + startup 초기화)
- Test: `tests/test_workspace_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workspace_api.py
"""워크스페이스 API 테스트."""
from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def ws_base(tmp_path):
    return tmp_path


@pytest.fixture
def client(ws_base):
    with patch("src.ui.routes.workspace.WS_BASE", ws_base):
        with patch("src.utils.workspace._DEFAULT_BASE", ws_base):
            from src.ui.server import app
            yield TestClient(app)


def test_list_files_empty(client, ws_base):
    (ws_base / "instant" / "input").mkdir(parents=True)
    resp = client.get("/api/workspace/instant/files")
    assert resp.status_code == 200
    assert resp.json()["files"] == []


def test_list_files_with_content(client, ws_base):
    inp = ws_base / "instant" / "input"
    inp.mkdir(parents=True)
    (inp / "data.csv").write_text("a,b")
    resp = client.get("/api/workspace/instant/files")
    assert len(resp.json()["files"]) == 1


def test_list_files_invalid_mode(client):
    resp = client.get("/api/workspace/invalid_mode/files")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_workspace_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the API**

```python
# src/ui/routes/workspace.py
"""워크스페이스 파일 목록/관리 API — 4개 모드 공유."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.utils.workspace import VALID_MODES, ensure_workspace, list_input_files

router = APIRouter()
WS_BASE = Path("data/workspace")


def _validate_mode(mode: str):
    if mode not in VALID_MODES:
        return JSONResponse(
            {"error": f"유효하지 않은 모드: {mode}"},
            status_code=400,
        )
    return None


@router.get("/api/workspace/{mode}/files")
async def workspace_list_files(mode: str):
    """모드의 input 폴더 내 파일 목록 반환."""
    err = _validate_mode(mode)
    if err:
        return err
    ensure_workspace(mode, base=WS_BASE)
    files = list_input_files(mode, base=WS_BASE)
    return {"files": files, "mode": mode}


@router.post("/api/workspace/{mode}/open")
async def workspace_open_folder(mode: str):
    """모드의 input 폴더를 OS 파일 탐색기로 열기."""
    err = _validate_mode(mode)
    if err:
        return err
    ws = ensure_workspace(mode, base=WS_BASE)
    folder = ws / "input"
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"opened": str(folder)}
```

- [ ] **Step 4: Register router + startup init in server.py**

`src/ui/server.py`에 추가:

```python
from src.ui.routes.workspace import router as workspace_router
app.include_router(workspace_router)
```

앱 시작 시 폴더 생성 (기존 startup 패턴에 맞춰서):

```python
from src.utils.workspace import ensure_workspace, VALID_MODES
for mode in VALID_MODES:
    ensure_workspace(mode)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_workspace_api.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add src/ui/routes/workspace.py tests/test_workspace_api.py src/ui/server.py
git commit -m "feat: add workspace file listing API and app startup init"
```

---

### Task 3: WorkspacePanel UI 컴포넌트

**Files:**
- Create: `src/ui/static/css/workspace.css`
- Create: `src/ui/static/js/workspace-panel.js`
- Modify: `src/ui/static/index.html`

- [ ] **Step 1: Create the CSS**

`src/ui/static/css/workspace.css` — 패널, 파일 칩, 선택 상태, 빈 상태 스타일.
(칩은 toggle 선택 방식, 선택 시 `selected` 클래스)

- [ ] **Step 2: Create the JS component**

`src/ui/static/js/workspace-panel.js`:

```javascript
var WorkspacePanel = (function () {
  'use strict';
  function create(container, mode) {
    // 파일 목록 fetch → 칩 렌더 → 클릭으로 toggle 선택
    // API: GET /api/workspace/{mode}/files
    // 반환: { getSelectedFiles(), getAllFiles(), refresh(), clear(), destroy() }
    // destroy()로 DOM에서 제거 (모드 전환 시 누수 방지)
  }
  return { create: create };
})();
```

핵심 메서드:
- `getSelectedFiles()` → `["data.csv", "memo.txt"]`
- `refresh()` → 파일 목록 재조회
- `destroy()` → DOM에서 패널 엘리먼트 제거 (모드 전환/재생성 시 호출)
- `clear()` → 선택 해제

- [ ] **Step 3: Add to index.html**

`src/ui/static/index.html` 스크립트 로드 부분에서 `mode-company-card.js` **앞에**:

```html
<link rel="stylesheet" href="/static/css/workspace.css">
<script src="/static/js/workspace-panel.js"></script>
```

- [ ] **Step 4: Commit**

```bash
git add src/ui/static/js/workspace-panel.js src/ui/static/css/workspace.css src/ui/static/index.html
git commit -m "feat: add WorkspacePanel UI component"
```

---

### Task 4: 인스턴트 + 나만의방식 (실행) — 백엔드

**Files:**
- Modify: `src/ui/sim_runner.py:54-68` (__init__)
- Modify: `src/ui/sim_runner.py:88-98` (start 메시지 파싱)
- Modify: `src/ui/sim_runner.py:136-177` (_run_graph 컨텍스트 주입)

- [ ] **Step 1: __init__에 필드 추가**

```python
self._workspace_files: list[str] = []
self._workspace_mode: str = "instant"
```

- [ ] **Step 2: start 메시지에서 추출**

`msg.get("type") == "start"` 블록:

```python
self._workspace_files = msg.get("workspace_files", [])
self._workspace_mode = msg.get("workspace_mode", "instant")
```

- [ ] **Step 3: _run_graph에서 조건부 주입**

```python
from src.utils.workspace import read_files_as_context

effective_task = user_task
if self._workspace_files:
    file_ctx = read_files_as_context(self._workspace_mode, self._workspace_files)
    if file_ctx:
        effective_task = user_task + "\n\n" + file_ctx
```

`create_initial_state(effective_task, ...)` 로 전달.

- [ ] **Step 4: Commit**

```bash
git add src/ui/sim_runner.py
git commit -m "feat: SimSession reads workspace files into task context"
```

---

### Task 5: 나만의방식 (설계) — 백엔드

**Files:**
- Modify: `src/ui/server.py:507-510` (builder_message 핸들러)
- Modify: `src/company_builder/builder_agent.py:158` (BuilderSession.stream_response)

- [ ] **Step 1: server.py — builder_message에서 workspace_files 추출**

`server.py` line 507, `builder_message` 핸들러:

```python
if msg_type == "builder_message":
    content = msg.get("data", {}).get("content", "")
    workspace_files = msg.get("data", {}).get("workspace_files", [])
    if content:
        await session.stream_response(content, ws, workspace_files=workspace_files)
```

- [ ] **Step 2: BuilderSession.stream_response에 workspace_files 파라미터 추가**

`src/company_builder/builder_agent.py` `stream_response`:

```python
async def stream_response(self, user_message: str, ws, workspace_files: list[str] | None = None) -> None:
```

첫 호출(또는 매 호출) 시 파일 컨텍스트를 user_message에 덧붙이기:

```python
from src.utils.workspace import read_files_as_context

effective_message = user_message
if workspace_files:
    file_ctx = read_files_as_context("builder", workspace_files)
    if file_ctx:
        effective_message = user_message + "\n\n" + file_ctx
```

이하 `effective_message`를 기존 `user_message` 대신 사용.

- [ ] **Step 3: StrategyBuilderSession에도 동일 적용**

`StrategyBuilderSession.stream_response` (line 532)에도 동일한 `workspace_files` 파라미터 추가 및 컨텍스트 주입. server.py의 strategy 관련 메시지 핸들러도 동일하게 `workspace_files` 전달.

- [ ] **Step 4: Commit**

```bash
git add src/ui/server.py src/company_builder/builder_agent.py
git commit -m "feat: builder design phase accepts workspace files for context"
```

---

### Task 6: 인스턴트 + 나만의방식 — UI 연결

**Files:**
- Modify: `src/ui/static/js/mode-company-card.js`

- [ ] **Step 1: _showCompanyMode에서 WorkspacePanel 마운트**

모드 전환 시 기존 패널 destroy 후 새로 생성:

```javascript
var _wsPanel = null;

// _showCompanyMode 안에서:
if (_wsPanel) { _wsPanel.destroy(); _wsPanel = null; }
var chatArea = document.getElementById('card-chat-area');
if (chatArea) {
    var wsMode = (_activeMode === 'builder') ? 'builder' : 'instant';
    _wsPanel = WorkspacePanel.create(chatArea, wsMode);
}
```

- [ ] **Step 2: instant start 메시지에 workspace_files 포함**

`_handleChatMessage` instant 브랜치 (line ~504):

```javascript
var wsFiles = _wsPanel ? _wsPanel.getSelectedFiles() : [];
_sendWS({
    type: 'start', task: text, output_format: fmt,
    workspace_files: wsFiles,
    workspace_mode: (_activeMode === 'builder') ? 'builder' : 'instant',
});
```

- [ ] **Step 3: builder 설계 메시지에 workspace_files 포함**

`CardBuilder.sendMessage` 호출부에서:

```javascript
var wsFiles = _wsPanel ? _wsPanel.getSelectedFiles() : [];
// builder_message에 workspace_files 추가
```

- [ ] **Step 4: builder 실행 start에 workspace_files 포함**

`_startStrategyExecution` (line ~593):

```javascript
var wsFiles = _wsPanel ? _wsPanel.getSelectedFiles() : [];
_sendWS({
    type: 'start', task: text, strategy: strategy,
    workspace_files: wsFiles, workspace_mode: 'builder',
});
```

- [ ] **Step 5: Commit**

```bash
git add src/ui/static/js/mode-company-card.js
git commit -m "feat: wire WorkspacePanel into instant and builder modes (design + execution)"
```

---

### Task 7: 야근팀 — 백엔드 + UI 연결

**Files:**
- Modify: `src/ui/server.py:738-757` (overtime WS)
- Modify: `src/overtime/runner.py:203-211`
- Modify: `src/ui/static/js/mode-overtime.js`

- [ ] **Step 1: server.py — start_overtime에서 workspace_files 추출 후 run_overtime에 전달**

```python
workspace_files = data.get("workspace_files", [])

from src.utils.workspace import read_files_as_context
file_ctx = read_files_as_context("overtime", workspace_files) if workspace_files else ""

# run_overtime 호출에 file_context 전달:
await run_overtime(
    task=task, strategy=strategy, goal=goal,
    session_id=_session_id, user_id=user_id,
    max_iterations=max_iterations, overtime_id=ot_id,
    file_context=file_ctx,  # <-- 여기서 전달
)
```

- [ ] **Step 2: run_overtime 시그니처 + 프롬프트 주입**

`src/overtime/runner.py`:

```python
async def run_overtime(
    ...,
    file_context: str = "",  # NEW
) -> str:
```

`build_iteration_prompt` 호출 전에:

```python
effective_task = task
if file_context:
    effective_task = task + "\n\n" + file_context
```

`build_iteration_prompt(task=effective_task, ...)` 로 전달.

- [ ] **Step 3: mode-overtime.js — WorkspacePanel 마운트 + payload에 포함**

`_render()`에서 폼 안에 WorkspacePanel 마운트:

```javascript
var _otWsPanel = null;
// 작업 입력 아래, 시작 버튼 위에:
_otWsPanel = WorkspacePanel.create(formSection, 'overtime');
```

`_doStartOvertime`에서:

```javascript
payload.workspace_files = _otWsPanel ? _otWsPanel.getSelectedFiles() : [];
```

- [ ] **Step 4: Commit**

```bash
git add src/ui/server.py src/overtime/runner.py src/ui/static/js/mode-overtime.js
git commit -m "feat: wire workspace files into overtime mode"
```

---

### Task 8: 스킬 (실행) — 백엔드 + UI

**Files:**
- Modify: `src/ui/server.py:887-915` (skill-execute WS)
- Modify: `src/ui/static/js/mode-skill.js`

- [ ] **Step 1: server.py — execute 메시지에서 workspace_files 추출**

```python
data = msg.get("data") or {}
workspace_files = data.get("workspace_files", [])

from src.utils.workspace import read_files_as_context
file_ctx = read_files_as_context("skill", workspace_files) if workspace_files else ""
effective_input = user_input
if file_ctx:
    effective_input = user_input + "\n\n" + file_ctx
```

`run_skill(user_input=effective_input, ...)` 로 전달.

- [ ] **Step 2: mode-skill.js — 스킬 카드 렌더 시 1회 마운트**

스킬 카드 확장 UI (textarea가 있는 `execArea`)를 렌더할 때 WorkspacePanel을 **1회 생성**:

```javascript
// 카드 렌더 시 (실행 버튼 위에):
var skillWsPanel = WorkspacePanel.create(execArea, 'skill');
```

execute 전송부:

```javascript
var wsFiles = skillWsPanel ? skillWsPanel.getSelectedFiles() : [];
_execWs.send(JSON.stringify({
    type: 'execute',
    data: { slug: skill.slug, user_input: input, workspace_files: wsFiles },
}));
```

- [ ] **Step 3: Commit**

```bash
git add src/ui/server.py src/ui/static/js/mode-skill.js
git commit -m "feat: wire workspace files into skill execution"
```

---

### Task 9: 스킬 (만들기) — 백엔드 + UI

**Files:**
- Modify: `src/ui/server.py:836-868` (skill-builder WS)
- Modify: `src/skill_builder/runner.py:53-131` (start 메시지 + _run_skill_creator_loop)
- Modify: `src/ui/static/js/mode-skill.js` (create 패널)

- [ ] **Step 1: server.py — skill-builder start에서 workspace_files 전달**

skill-builder WS는 `run_skill_builder_session(ws)`에 위임하므로, runner 내부에서 직접 처리.

- [ ] **Step 2: runner.py — start 메시지에서 workspace_files 추출**

`run_skill_builder_session` (line 66):

```python
description = (msg.get("data") or {}).get("description", "").strip()
workspace_files = (msg.get("data") or {}).get("workspace_files", [])
```

`_run_skill_creator_loop` 호출에 전달:

```python
await _run_skill_creator_loop(ws, description, workspace_files=workspace_files)
```

- [ ] **Step 3: _run_skill_creator_loop에서 첫 턴 description에 주입**

```python
async def _run_skill_creator_loop(
    ws, description: str, workspace_files: list[str] | None = None,
) -> None:
```

첫 턴 `user_message`에 파일 컨텍스트 덧붙이기:

```python
from src.utils.workspace import read_files_as_context

effective_desc = description
if workspace_files:
    file_ctx = read_files_as_context("skill", workspace_files)
    if file_ctx:
        effective_desc = description + "\n\n" + file_ctx
```

`bridge.raw_query(user_message=effective_desc, ...)` 로 전달.

- [ ] **Step 4: mode-skill.js — create 패널에 WorkspacePanel 마운트**

스킬 만들기 패널(`_renderCreatePanel`)에서 description 입력 아래에 WorkspacePanel 마운트:

```javascript
var createWsPanel = WorkspacePanel.create(createPanel, 'skill');
```

start 메시지 전송 시:

```javascript
var wsFiles = createWsPanel ? createWsPanel.getSelectedFiles() : [];
_ws.send(JSON.stringify({
    type: 'start',
    data: { description: desc, workspace_files: wsFiles },
}));
```

- [ ] **Step 5: Commit**

```bash
git add src/skill_builder/runner.py src/ui/static/js/mode-skill.js
git commit -m "feat: skill creation phase accepts workspace files for context"
```

---

### Task 10: .gitignore + gitkeep

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: workspace 사용자 파일 git 제외**

```
# 모드별 워크스페이스 (사용자 파일 + AI 결과물)
data/workspace/*/input/*
data/workspace/*/output/*
!data/workspace/*/input/.gitkeep
!data/workspace/*/output/.gitkeep
```

- [ ] **Step 2: gitkeep 파일 + 폴더 구조 생성**

```bash
for mode in instant builder overtime skill; do
  mkdir -p "data/workspace/$mode/input" "data/workspace/$mode/output"
  touch "data/workspace/$mode/input/.gitkeep" "data/workspace/$mode/output/.gitkeep"
done
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore data/workspace/
git commit -m "chore: add workspace folder structure, ignore user files"
```

---

### Task 11: 통합 테스트

**Files:**
- Modify: `tests/test_workspace.py`

- [ ] **Step 1: E2E 파이프라인 테스트 추가**

```python
def test_e2e_file_to_context(tmp_path):
    from src.utils.workspace import ensure_workspace, list_input_files, read_files_as_context
    ensure_workspace("instant", base=tmp_path)
    inp = tmp_path / "instant" / "input"
    (inp / "sales.csv").write_text("month,revenue\nJan,100\nFeb,200")
    (inp / "memo.txt").write_text("Q1 분석 요청")

    files = list_input_files("instant", base=tmp_path)
    assert len(files) == 2

    names = [f["name"] for f in files]
    ctx = read_files_as_context("instant", names, base=tmp_path)
    assert "sales.csv" in ctx
    assert "Jan,100" in ctx
    assert "Q1 분석 요청" in ctx


def test_backward_compat_no_files(tmp_path):
    """파일 미선택 시 빈 문자열 → 기존 동작과 동일."""
    ctx = read_files_as_context("instant", [], base=tmp_path)
    assert ctx == ""
```

- [ ] **Step 2: Run all tests**

Run: `python3 -m pytest tests/test_workspace.py tests/test_workspace_api.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_workspace.py
git commit -m "test: add E2E and backward-compat workspace tests"
```
