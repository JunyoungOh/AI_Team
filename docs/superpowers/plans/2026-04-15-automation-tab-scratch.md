# Automation Tab — 처음부터 구축하는 접근 (Plan A)

**작성일**: 2026-04-15
**상태**: 대안 검토 중 (n8n 포크 방식과 비교)
**전제**: 바이브코더도 쉽게 쓸 수 있는 비주얼 자동화 빌더

---

## 결정된 사항 (사용자 확정)

| # | 결정 | 선택 | 비고 |
|---|---|---|---|
| ① | Edge 의미 | **단순 전달 + 노드 내부 가공** | 포트 개념 없음, `{{previous}}` 치환으로 다음 노드가 알아서 사용 |
| ② | 캔버스 라이브러리 | **Drawflow** (~42KB, 바닐라 JS, MIT) | React Flow 탈락 (스택 불일치) |
| ③ | 워커 노드 | **사용자 정의 Custom Worker** — `claude_code.py` CLI 싱글세션 브릿지 | 기존 30개 워커 재사용 안 함, 자동화 전용 |
| ④ | "자동실행" 탭 | **흡수/제거**. 기존 ScheduledJob은 "Schedule trigger + 워커 1개" flow로 자동 마이그레이션 | 단일 개념화 |
| ⑤-1 | 실패 처리 | **성공한 노드까지 결과 + 실패 지점 로그**를 합친 `FlowExecutionResult` | 병렬 브랜치는 계속 실행 |
| ⑤-2 | 동시성 | **flow 단위 asyncio.Lock + FIFO 큐** (길이 5, TTL 30분) | 같은 flow 재실행은 대기 |
| 파일쓰기 | 양식 | text / markdown / json / csv / html / yaml, mode(write/append), 경로에 템플릿 치환 | 단일 노드 |
| 카카오워크 | 액션 | 웹훅 URL POST | "Webhook Message" 통합 노드의 프리셋으로 통합 검토 |
| Playwright | 액션 | **Phase 5**로 분리 (브라우저 세션 풀 + Secrets Vault 필요) | "PC 컨트롤" 아닌 "브라우저 자동화"로 포지셔닝 |

---

## 아키텍처

### 프론트엔드 (vanilla JS)

```
#card-mode-automation
  ├─ 좌 사이드 팔레트: 트리거 / 처리 / 액션 카테고리
  ├─ Drawflow 캔버스: 드래그, 연결, 확대축소, minimap
  └─ 우 속성 패널: 선택된 노드의 config 편집 + {{previous}} 치환 도우미
```

파일: `src/ui/static/js/mode-automation.js` (기존 `mode-schedule.js` 패턴 차용)

### 백엔드 (Python)

```
src/automation/
  models.py         # Flow, FlowNode, FlowEdge, CustomWorker, FlowExecutionResult
  store.py          # data/automation.db (SQLite): flows, workers, executions
  runner.py         # topo 실행 + {{previous}} 치환 + partial success + per-flow 큐
  template.py       # {{previous.field}} 파서
  nodes/
    triggers/manual.py, schedule.py, webhook.py
    processing/custom_worker.py, llm.py, http.py, transform.py, branch.py
    actions/webhook_msg.py (Slack/카카오워크/Discord 프리셋), file_write.py
    actions/gmail.py, gcal.py               # Phase 5
    actions/browser.py                       # Phase 5 (세션 풀 필요)
  workers/
    custom.py                                # CustomWorker → claude_code.py 브릿지

src/scheduler/ (수정)
  models.py      # ScheduledJob.flow_id: str | None 추가
  runner.py      # flow_id 있으면 automation.runner 호출
  migrate.py 신규 # 기존 job → flow 자동 마이그레이션

src/ui/routes/automation.py 신규
  GET  /api/automation/flows
  POST /api/automation/flows
  PUT  /api/automation/flows/{id}
  POST /api/automation/flows/{id}/run
  POST /api/automation/hook/{flow_id}   # 웹훅 트리거 엔드포인트
```

### 재사용 포인트

- `src/utils/dependency_graph.py` — Kahn's algorithm topo-sort
- `src/utils/claude_code.py` — Claude Code CLI 헤드리스 브릿지 (Custom Worker 실행용)
- `src/scheduler/` — Schedule trigger 발화 엔진

---

## 데이터 모델

```python
class FlowNode(BaseModel):
    id: str                   # "node_abc123"
    type: str                 # "trigger.schedule" | "processing.custom_worker" | "action.webhook_msg"
    position: dict            # {"x": 100, "y": 200}
    config: dict              # 노드 타입별 설정

class FlowEdge(BaseModel):
    from_node: str
    to_node: str              # 포트 필드 없음 — 전체 output 전달
    # 예외: Branch 노드만 {branch: "true"|"false"} 필드 추가

class AutomationFlow(BaseModel):
    flow_id: str
    name: str
    description: str
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    created_at: datetime
    updated_at: datetime

class CustomWorker(BaseModel):
    worker_id: str
    name: str
    description: str
    system_prompt: str                # 사용자가 UI에서 자유롭게 작성
    allowed_tools: list[str]          # 체크박스 UI
    timeout_seconds: int = 600
    model: str = "sonnet"
    input_contract: ???               # 🎯 사용자 결정 대기
    output_contract: ???              # 🎯 사용자 결정 대기

class FlowExecutionResult(BaseModel):
    flow_id: str
    execution_id: str
    status: Literal["success", "partial", "failed"]
    started_at: datetime
    completed_at: datetime | None
    node_results: dict[str, NodeResult]     # node_id → {status, output, error, duration}
    final_output: dict                      # 성공한 노드들의 결과 합본
```

---

## 템플릿 치환 (`{{previous}}`)

- `{{previous}}` — 이전 노드 output 전체
- `{{previous.field}}` — dict 필드 접근
- `{{previous.items.0.title}}` — 배열 인덱스 접근
- `{{flow_name}}`, `{{execution_id}}`, `{{date}}`, `{{now}}` — 런타임 변수

**누락 처리 정책**: 🎯 사용자 결정 대기 (빈 문자열 / 에러 / 리터럴 유지)

---

## Phased Plan

| Phase | 내용 | 예상 | 완료 후 |
|---|---|---|---|
| **0** 스캐폴딩 | 사이드바 탭 추가, `src/automation/` 뼈대, Drawflow CDN | 0.5일 | 빈 캔버스 열림 |
| **1** 디자이너 | 팔레트 · 캔버스 · 속성패널 · Flow 저장/불러오기 | 2일 | 실행 없이 디자인만 |
| **2** 러너 | 기본 노드 8종 + topo 실행 + `{{previous}}` 치환 + partial success + 큐 | 3~4일 | 단순 플로우 실제 실행 |
| **3** 커스텀 워커 | 워커 정의 다이얼로그 + `claude_code.py` 싱글세션 브릿지 | 2~3일 | 사용자 정의 워커 동작 |
| **4** 트리거 + 자동실행 흡수 | Schedule/Webhook 트리거, 기존 job 마이그레이션, 탭 제거 | 2~3일 | cron/webhook 자동 실행 |
| **5** 확장 액션 | Gmail, GCal, Playwright 브라우저 세션 풀, Secrets Vault | 3~4일 | 브라우저 자동화/메일/캘린더 |

**총 13~17 영업일 (≈ 2.5~3.5주)**

---

## 🎯 사용자 결정 대기 항목

### 즉시 필요 (Phase 0~1 시작 전)
1. Phased Plan 승인 범위 — Phase 0~3 리뷰 후 재개 vs Phase 0~5 쭉 진행
2. "Webhook Message" 통합 노드 + 프리셋 방식 승인
3. Playwright = Phase 5 승인

### Phase 3 진입 전까지
4. `CustomWorker.input_contract / output_contract` 스키마
   - (a) Free-form dict — 자유, 검증 없음
   - (b) 단순 필드 리스트 `[{name, type, description}]` — **추천**
   - (c) JSON Schema — 엄격
5. `{{previous.missing_field}}` 누락 시 — 빈 문자열 / 에러 / 리터럴 유지

---

## 재사용 위험도 체크

| 가정 | 근거 | 리스크 |
|---|---|---|
| `claude_code.py`가 커스텀 프롬프트 + 도구 세트를 받을 수 있다 | CEO/Leader/Reporter가 이미 그렇게 호출 | 낮음 |
| `dependency_graph.py`가 dict 노드 리스트를 받을 수 있다 | 기존 워커 실행에서 사용 중 | 낮음 |
| `scheduler.runner`가 `flow_id` 분기를 받아들일 수 있다 | 기존 파이프라인 호출 경로 분기 한 줄 추가 | 낮음 |
| 플러그인 MCP(Slack/Gmail/GCal/Playwright)가 프로젝트 툴 레지스트리와 브릿지 가능하다 | 환경에 뜸, 도구 이름만 등록하면 됨 | **중간** — 권한/auth 플로우 확인 필요 |
