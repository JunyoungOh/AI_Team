# Enterprise Agent System — 프로젝트 가이드

## 아키텍처 개요

```
CEO (Opus) → Leader (Opus) → Worker (Sonnet) ← 네가 여기서 실행됨
```

- **CEO**: 도메인 라우팅, 질문 생성, 계획 승인
- **Leader**: 작업 분해, 워커 계획 수립, Gap 분석
- **Worker**: 실제 작업 실행 (네가 담당)
- **Reporter**: 최종 보고서 합성

## 핵심 파일 위치

```
src/
  agents/          # CEO, Leader, Worker, Reporter 에이전트
  config/
    settings.py    # 전체 설정 (timeout, 모델, 임계값)
    agent_registry.py  # 10개 도메인, 30개 워커 타입
  engine/           # 자체 PipelineEngine (LangGraph 대체)
  graphs/
    main_graph.py  # PipelineEngine 조립
    nodes/         # intake, ceo_route, worker_execution 등
  prompts/         # 시스템 프롬프트 템플릿
  tools/__init__.py  # 도메인별 도구 매핑
  utils/
    claude_code.py   # Claude Code CLI 브릿지 (subprocess)
    dependency_graph.py  # Kahn's algorithm 단계적 실행
    progress.py      # TUI 대시보드 (WorkerProgressTracker)
tests/             # pytest 단위/통합 테스트
```

## 테스트 명령어

```bash
# 전체 테스트 (라이브 제외)
python3 -m pytest tests/ --ignore=tests/test_integration_live.py -v

# 빠른 단위 테스트
python3 -m pytest tests/ --ignore=tests/test_integration_live.py -q

# 그래프 빌드 확인
python3 -c "from src.graphs.main_graph import build_pipeline; p = build_pipeline(); print('OK')"
```

## 코딩 컨벤션

### 에이전트 _query() 호출
CEO·Leader·Reporter는 반드시 `allowed_tools=[]`를 명시적으로 전달할 것.
이 경우 Claude Code가 `/tmp`에서 실행되어 MCP 서버를 기동하지 않음.

```python
# ✅ 올바름 (MCP 서버 미기동, 빠름)
result = self._query(
    system_prompt=system,
    user_content=content,
    output_schema=MySchema,
    allowed_tools=[],
    timeout=get_settings().planning_timeout,
)

# ❌ 잘못됨 (allowed_tools 생략 → MCP 6개 기동 → 30~60초 지연)
result = self._query(system_prompt=system, user_content=content, output_schema=MySchema)
```

### 설정값 참조
`Settings()` 직접 생성 대신 `get_settings()` 싱글턴 사용.

```python
from src.config.settings import get_settings
settings = get_settings()
```

### 환경변수
`pydantic-settings`는 `.env`를 읽지만 `os.environ`에 export하지 않음.
`main.py`와 `runner.py` 상단에서 `load_dotenv()` 필수.

## MCP 도구 네이밍 규칙

MCP 도구는 `mcp__{server}__{tool}` 형식:

```python
"mcp__serper__google_search"      # Serper Google 검색
"mcp__brave-search__brave_web_search"  # Brave 웹 검색
"mcp__firecrawl__firecrawl_scrape"     # Firecrawl 스크래핑
"mcp__mem0__search_memories"           # mem0 기억 검색
"mcp__mem0__add_memory"                # mem0 기억 저장
"mcp__github__search_code"             # GitHub 코드 검색
```

## 워커 실행 흐름

1. **의존성 없음**: `_flat_parallel_execution()` — 모든 워커 동시 병렬
2. **의존성 있음**: `_staged_execution()` — Kahn's algorithm으로 스테이지 정렬
   - Stage 0: 의존성 없는 워커 (병렬)
   - Stage N: 선행 워커 결과를 predecessor_context로 전달받아 실행

## 워커로서 네가 지켜야 할 것

1. **결과는 반드시 JSON 형식**으로 반환 (output_schema 준수)
2. **타임아웃 전에 부분 결과라도 반환** — 0% 결과보다 80% 결과가 유용
3. **mem0 활용**: 작업 시작 시 `search_memories`로 관련 기억 조회, 완료 시 `add_memory`로 저장
4. **도구 사용 실패 시 대체 도구로 전환** (serper → brave → fetch 순서)
5. **코드 작성 시 반드시 테스트 실행** 후 통과 여부 확인

## 커뮤니케이션 스타일

- 기술 설명은 비개발자(바이브코더)가 이해할 수 있는 쉬운 문장 사용
- 코드 블록은 그대로 보여주되, 설명에서 전문 용어는 쉬운 비유로 풀어쓸 것

## Superpowers 스킬 오버라이드

- **brainstorming 스킬은 사용자가 명시적으로 요청할 때만 실행**. "브레인스토밍 해줘", "설계부터 하자", "brainstorm" 등의 직접 요청이 없으면 자동 트리거하지 않는다. 버그 수정, CSS 수정, 리팩토링 등 명확한 작업은 brainstorming 없이 바로 실행한다.

## 금지 사항

- `Settings()` 직접 생성 남발 금지 (싱글턴 `get_settings()` 사용)
- CEO·Leader 프롬프트에 MCP 도구 추가 금지 (latency 폭증)
- `--allowedTools ""` 형태로 빈 문자열 전달 금지 (CLI hang 유발)
- `asyncio.run()` 중첩 호출 금지 (`run_async()` 사용)
