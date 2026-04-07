# Enterprise Agent System — Backlog

> 2026-04-07 기준. 새 세션에서 이 파일을 읽고 미구현 사항을 파악하세요.

## 완료된 주요 변경

### 이전 세션
- **싱글 세션 아키텍처**: 멀티 CLI subprocess → 단일 CLI 세션 (8.5분→2.6분)
- **실시간 스트리밍**: stream-json 파싱 → WebSocket → 활동 대시보드 (도구별 카드+타이머)
- **나만의 팀 재설계**: 에이전트 구조 → 분석 전략 프리셋 (관점 카드 UI)
- **야근팀**: 목표 달성까지 반복 iteration (raw md → 평가 → 보고서)
- **스케줄팀**: 정기 자동 실행 + "지금 실행" + 프로그레스 오버레이
- **출력 형식**: HTML/PDF/Markdown/CSV/JSON 5종 지원
- **폴더명**: session_id → task 제목 기반
- **불필요 MCP 제거**: 6개→3개 (fetch, brave, serper 제거)
- **Findings 중복 제거**: dedup.py (difflib 기반)
- **rate limit 대응**: 에러 파싱 → 자동 대기 → 재시도

### 2026-04-07 세션
- **Delta 비교**: 스케줄 실행 시 이전 MD 파일을 Read로 읽어 "변동 사항" 섹션 자동 생성
- **Append 모드**: `output_mode: append` 시 기존 파일에 데이터 누적
- **HTML + MD 이중 생성**: 스케줄 실행 시 `results_YYYY-MM-DD.html` + `.md` 동시 생성 (MD는 다음 실행에서 CLI가 읽을 요약본)
- **시/분/요일 직관 UI**: 크론식 입력 → 시간 셀렉트 + 요일 토글 버튼으로 전환
- **output_mode 셀렉트**: "매번 새로(비교)" / "누적 추가" 선택 UI
- **보고서 보기 + 폴더 열기**: 스케줄팀/야근팀에 보고서 링크 + Finder 폴더 열기 버튼 추가
- **명확화 → 상세 설명**: 고정 질문 3개 → 자유 입력 textarea (스케줄/야근 공통)
- **버그 수정**: `add_run_record` dict 인자 수정, 활동 대시보드 탭 간 겹침 수정, StaticFiles 보고서 GET 충돌 수정, 날짜 파일명 보고서 서빙 추가

---

## 미구현 사항

### 1. 아키텍처

#### 1-1. 기획-실행-검증 프롬프트 강화
- **현재**: 싱글 세션 프롬프트에 "분석→수집→검증→보고서" 지시가 있지만, 모델의 자율 판단에 맡겨져 있음
- **목표**: depth=deep일 때 명시적 검증 단계 강제. 수집 후 자가 검증 → 부족하면 추가 수집
- **파일**: `src/prompts/single_session_prompts.py`

#### 1-2. 레거시 코드 정리
- **현재**: `use_single_session=True`일 때 사용되지 않는 모듈이 다수 존재
- **제거 후보**: 
  - `src/engine/review_loop.py` — PESR 루프 (싱글 세션이 대체)
  - `src/utils/blackboard.py` — 파이프라인 블랙보드 (세션 컨텍스트가 대체)
  - `src/utils/collection_blackboard.py` — findings 축적 (세션 내 자연어로 대체)
  - `src/utils/dependency_graph.py` — Kahn's algorithm (Agent 서브에이전트가 대체)
  - `src/utils/progress.py` — WorkerProgressTracker 대부분 (활동 대시보드가 대체)
- **주의**: `use_single_session=False` 레거시 모드와 공존해야 하므로 삭제가 아닌 분리 필요

#### 1-3. CLI 터미널 뷰 하이브리드
- **현재**: 캔버스에 도구별 카드 대시보드만 표시
- **목표**: 카드 대시보드 + 접을 수 있는 CLI 출력 패널 (실제 뭘 하는지 투명하게 보임)
- **구현 방향**: stream-json의 text 블록을 터미널 스타일로 렌더링
- **파일**: `src/ui/static/js/card-event-handler.js`, 새 CSS 필요

---

### 2. 데이터/출력

#### ~~2-1. Delta 비교 (이전 실행과의 차이)~~ ✅ 완료
#### ~~2-2. 누적 데이터 (Append 모드)~~ ✅ 완료

#### 2-3. Jinja2 템플릿 분리 (Phase 2)
- **현재**: 싱글 세션이 CLI에서 HTML을 직접 생성 → 타임아웃 문제를 우회
- **원래 계획**: 구조화된 JSON + Jinja2 렌더링으로 일관된 디자인 보장
- **상태**: 싱글 세션이 충분히 좋은 HTML을 생성하고 있어 우선순위 낮아짐
- **파일**: `src/utils/report_exporter.py` (레거시), `src/templates/` (미생성)

---

### 3. UI/UX

#### 3-1. 브라우저 새로고침 무한로딩
- **현상**: 특정 상황에서 새로고침 시 페이지 로딩이 멈춤
- **원인**: 미파악. JS 문법 에러는 아님 (node --check 통과)
- **조사 필요**: 네트워크 탭에서 어떤 요청이 멈추는지 확인
- **추가 이슈**: 새로고침 시 실행 중이던 인스턴트 작업이 UI에서 분리됨 (백엔드는 계속 실행)

#### 3-2. 탭 전환 시 컨텍스트 유지
- **현재**: 실행 중 탭 전환 보호는 구현 (`_running` 체크)
- **미완**: 비실행 상태에서 채팅 히스토리, 전략 카드 등이 탭 전환 시 사라짐
- **파일**: `src/ui/static/js/mode-company-card.js` `_showCompanyMode()`

#### 3-3. 스케줄 완료 알림
- **현재**: 스케줄 자동 실행 완료 시 알림 없음 (보고서만 저장)
- **목표**: 브라우저 Notification API 또는 소리로 알림
- **추가 고려**: Slack MCP를 통한 메시지 전송

#### ~~3-4. 야근팀 완료 이벤트 누락~~ — 재검증 필요
- **현상**: 최종 보고서 생성 후 "완료" 이벤트가 UI에 안 뜨는 경우 있음
- **수정**: `server.py`에 0.5초 대기 + 수동 flush 추가했으나 재검증 필요

#### ~~3-5. 활동 대시보드 타이머~~ ✅ 완료 (탭 간 겹침 수정 포함)

---

### 4. 스케줄팀/야근팀

#### ~~4-1. AI 기반 명확화 질문 생성~~ → 상세 설명 입력으로 전환 완료 ✅

#### 4-2. 스케줄 status "running" 잔류
- **현상**: HeadlessGraphRunner 완료 후 status가 completed로 안 바뀜
- **보정**: 보고서 파일이 존재하면 status를 completed로 강제 변경
- **근본 원인**: `ExecutionRecord.status`가 graph 완료 상태를 제대로 반영 못함
- **파일**: `src/ui/server.py` `run_schedule_now` 핸들러, `src/scheduler/runner.py`

#### 4-3. CLI 5시간 사용량 리셋 정밀 감지
- **현재**: rate limit 에러 메시지에서 대기 시간을 파싱 (regex)
- **한계**: CLI에 usage/quota 조회 API가 없어 정확한 리셋 시각 보장 불가
- **구현된 것**: `_parse_cooldown_seconds()` — "retry after N seconds", "in N minutes", "reset at HH:MM" 패턴
- **파일**: `src/overtime/runner.py`

#### 4-4. 야근팀 개발 작업 지원
- **현재**: 평가 기준이 "데이터 충분성" (리서치 특화)
- **목표**: "테스트 통과"를 평가 기준으로 사용하는 개발 모드
- **구현 방향**: 야근팀 폼에 "작업 유형" 선택 (리서치 / 개발), 유형에 따라 평가 프롬프트 분기
- **파일**: `src/overtime/prompts.py` EVALUATION_SYSTEM

#### 4-5. 외부 서비스 전송
- **현재**: 완료 시 로컬 파일 저장만
- **목표**: Slack/Email로 결과 요약 전송
- **구현 방향**: 완료 후 Hook 패턴 — `post_execution_hook` 설정에 따라 Slack MCP 호출
- **대상 모드**: 스케줄팀 (정기 실행 결과 전송이 가장 유용)

---

### 5. 나만의 팀

#### 5-1. 전략 사이드바 목록 렌더링
- **현재**: `_renderSidebarStrategyList()` 함수가 빈 상태 (스텁)
- **목표**: 저장된 전략 목록을 사이드바에 표시, 클릭하면 로드
- **파일**: `src/ui/static/js/card-builder.js`

#### 5-2. 전략 수정 요청 흐름
- **현재**: "✏️ 전략 수정 요청" 버튼 → 입력 안내만 표시
- **목표**: 수정 요청 입력 → StrategyBuilderSession에 전달 → 전략 업데이트 → 카드 갱신
- **파일**: `src/ui/static/js/card-builder.js`, `src/company_builder/builder_agent.py`

---

## 파일 참조

| 주요 파일 | 역할 |
|-----------|------|
| `src/graphs/nodes/single_session.py` | 싱글 세션 실행 노드 (스트리밍) |
| `src/prompts/single_session_prompts.py` | 실행/출력 프롬프트 |
| `src/overtime/runner.py` | 야근팀 iteration 엔진 |
| `src/overtime/prompts.py` | 야근팀 프롬프트 |
| `src/company_builder/builder_agent.py` | 전략 설계 에이전트 |
| `src/company_builder/storage.py` | strategy/overtime CRUD |
| `src/company_builder/scheduler.py` | 스케줄→ScheduledJob 변환 (delta/append 포함) |
| `src/company_builder/schedule_storage.py` | 스케줄 CRUD |
| `src/scheduler/models.py` | PreContext (previous_report_path, output_mode) |
| `src/ui/server.py` | WebSocket 엔드포인트, 보고서 서빙 |
| `src/ui/sim_runner.py` | 그래프 실행 + WS 브릿지 |
| `src/ui/static/js/mode-company-card.js` | 인스턴트/빌더 모드 UI |
| `src/ui/static/js/card-event-handler.js` | WS 이벤트 → UI 매핑 |
| `src/ui/static/js/card-builder.js` | 전략 설계 + 저장 |
| `src/ui/static/js/mode-overtime.js` | 야근팀 UI |
| `src/ui/static/js/mode-schedule.js` | 스케줄팀 UI (시/분/요일, output_mode, 상세설명) |
| `src/utils/pdf_converter.py` | HTML→PDF 변환 (Playwright) |
| `src/utils/dedup.py` | findings 중복 제거 |
| `src/config/settings.py` | `use_single_session` 토글 |
| `.mcp.json` | MCP 서버 설정 (firecrawl, github, mem0) |
