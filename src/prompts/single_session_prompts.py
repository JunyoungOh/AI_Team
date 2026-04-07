"""Single CLI session execution prompts.

Replaces the multi-subprocess pipeline with a single Claude Code CLI session
that handles research, synthesis, and report generation using native tools.
"""

SINGLE_SESSION_SYSTEM = """\
당신은 Enterprise Agent System의 실행 엔진입니다.
사용자의 요청을 분석하고, 정보를 수집하고, 최종 HTML 보고서를 직접 생성합니다.

## 실행 전략

### 1단계: 분석 (30초)
사용자 요청과 Q&A를 분석하여 수집해야 할 정보 항목을 정리하세요.

### 2단계: 병렬 수집 (핵심)
독립적인 정보 수집은 **Agent 도구로 서브에이전트를 병렬 발동**하세요.
- 한 메시지에 여러 Agent를 호출하면 동시에 실행됩니다
- 각 Agent에게 구체적이고 명확한 검색 지시를 내리세요
- Agent 결과를 수신한 후 종합하세요

예시:
```
Agent 1: "카카오 주가 최근 동향 검색하고 핵심 수치를 정리해줘"
Agent 2: "카카오 경영진 변동 관련 최신 기사를 찾아줘"
Agent 3: "카카오 AI 전략 발표 내용을 검색해줘"
```

### 3단계: 검증
수집된 정보의 출처를 교차 검증하세요.
같은 사실의 반복(중복)을 제거하세요.

### 4단계: 리포트 생성
Write 도구로 HTML 보고서를 직접 생성하세요.

## 도구 활용 가이드
- **WebSearch**: 웹 검색 (기본 검색 도구)
- **WebFetch**: URL에서 콘텐츠 추출
- **Agent**: 서브에이전트 병렬 실행 (정보 수집 분산)
- **Write**: 파일 생성 (최종 보고서)
- **Bash**: 디렉토리 생성, 시스템 명령
- **mcp__firecrawl__firecrawl_scrape**: JS 렌더링이 필요한 사이트 스크래핑

## 보고서 품질 기준
- **데이터 충실성**: 수치, 출처, 날짜를 반드시 포함
- **최신 데이터 우선**: 반드시 현재 시점 기준의 최신 정보를 검색하세요. 검색 시 연도를 명시하세요.
- **중복 금지**: 같은 사실을 다른 표현으로 반복하지 마세요
- **사용자 관점**: 시스템 내부 용어(워커, 에이전트, 도구명) 언급 금지
- **실질적 인사이트**: 단순 사실 나열이 아닌 분석과 시사점 포함
"""


REPORT_HTML_GUIDE = """\
## HTML 보고서 규격

자체 완결형 HTML 파일을 생성하세요. 외부 CSS/JS 의존 없음.

### 구조
1. **커버 헤더**: 그라데이션 배경(#0f3460 → #16213e), 흰색 제목, 날짜
2. **목차**: 섹션 링크 가로 나열
3. **Executive Summary**: 다크 카드, 핵심 결과 3-5문장
4. **상세 섹션**: 각 주제별 분석, 테이블, 데이터 카드
5. **권고사항**: 번호 카드 형태
6. **참고자료**: 출처 URL 리스트
7. **푸터**: 생성 일시

### 디자인
- 컬러: Primary #0f3460, Accent #FEE500, Text #1a1a2e, BG #f4f6f9
- 폰트: 'Apple SD Gothic Neo','Noto Sans KR',sans-serif
- max-width: 1100px, 카드 border-radius: 12px
- @media print CSS 포함 (브라우저 PDF 출력 지원)
- 반응형: @media (max-width: 768px) 대응

### 콘텐츠 규칙
- 한국어 기본, 전문용어는 원문 병기
- 테이블로 구조화할 수 있는 데이터는 반드시 테이블 사용
- 모든 수치에 출처 명시
- 최소 분량: 섹션 2개 이상, 데이터 포인트 5개 이상
"""

REPORT_MARKDOWN_GUIDE = """\
## Markdown 문서 규격

마크다운(.md) 파일을 생성하세요.

### 구조
- `# 제목` — 문서 제목
- `## Executive Summary` — 핵심 요약
- `## 섹션명` — 주제별 상세 분석
- 테이블: `| 항목 | 값 |` 마크다운 테이블 형식
- 출처: 각 데이터 뒤에 `(출처: URL)` 표기
- `## 참고자료` — URL 목록

### 규칙
- 한국어 기본, 전문용어 원문 병기
- 데이터는 마크다운 테이블로 구조화
- 모든 수치에 출처 명시
"""

REPORT_CSV_GUIDE = """\
## CSV 데이터 규격

CSV(.csv) 파일을 생성하세요.

### 규칙
- 첫 행은 헤더 (컬럼명)
- UTF-8 인코딩 (한글 지원)
- 쉼표(,) 구분, 필드 내 쉼표는 큰따옴표로 감싸기
- 날짜 형식: YYYY-MM-DD
- 출처 URL은 별도 컬럼으로
- 데이터가 여러 카테고리면 'category' 컬럼 추가
"""

REPORT_JSON_GUIDE = """\
## JSON 데이터 규격

JSON(.json) 파일을 생성하세요.

### 구조
```
{
  "title": "분석 제목",
  "generated_at": "2026-04-07",
  "summary": "핵심 요약",
  "data": [ ... ],
  "sources": [ ... ],
  "recommendations": [ ... ]
}
```

### 규칙
- UTF-8 인코딩
- 들여쓰기 2칸
- 날짜 형식: ISO 8601 (YYYY-MM-DD)
- data 배열 안에 구조화된 항목들
- 모든 항목에 source 필드 포함
"""

# 형식별 매핑 (pdf는 HTML 생성 후 변환)
OUTPUT_FORMAT_MAP = {
    "html": {"ext": "results.html", "guide": REPORT_HTML_GUIDE},
    "pdf": {"ext": "results.html", "guide": REPORT_HTML_GUIDE},  # HTML 생성 → 후처리로 PDF 변환
    "markdown": {"ext": "results.md", "guide": REPORT_MARKDOWN_GUIDE},
    "csv": {"ext": "results.csv", "guide": REPORT_CSV_GUIDE},
    "json": {"ext": "results.json", "guide": REPORT_JSON_GUIDE},
}


def build_execution_prompt(
    user_task: str,
    user_answers: list[str] | None = None,
    clarifying_questions: list[str] | None = None,
    domains: list[str] | None = None,
    complexity: str = "low",
    report_dir: str = "data/reports/default",
    strategy: dict | None = None,
    output_format: str = "html",
    previous_report_path: str | None = None,
    output_mode: str = "replace",
    is_scheduled: bool = False,
) -> str:
    """싱글 세션에 전달할 실행 프롬프트 조립."""

    # Q&A 컨텍스트
    qa_block = ""
    if clarifying_questions and user_answers:
        qa_pairs = []
        for i, q in enumerate(clarifying_questions):
            a = user_answers[i] if i < len(user_answers) else "(미답변)"
            qa_pairs.append(f"Q: {q}\nA: {a}")
        qa_block = "\n\n## 명확화 Q&A\n" + "\n\n".join(qa_pairs)

    # 전략 프리셋이 있으면 관점별 지시를 주입
    strategy_block = ""
    if strategy:
        perspectives = strategy.get("perspectives", [])
        if perspectives:
            lines = [f"\n\n## 분석 프레임워크: {strategy.get('name', '분석 전략')}"]
            lines.append(f"{strategy.get('description', '')}\n")
            lines.append("### 분석 관점 (각 관점별로 Agent 서브에이전트를 병렬 실행하세요)")
            for p in perspectives:
                lines.append(f"- **{p.get('icon', '📌')} {p.get('name', '')}**: {p.get('instruction', '')}")
            special = strategy.get("special_instructions", "")
            if special:
                lines.append(f"\n### 특별 지시\n{special}")
            strategy_block = "\n".join(lines)

        # 전략의 depth/output_format으로 복잡도 오버라이드
        depth_override = strategy.get("depth")
        if depth_override:
            depth_to_complexity = {"light": "low", "standard": "medium", "deep": "high"}
            complexity = depth_to_complexity.get(depth_override, complexity)

    # 복잡도별 가이드
    depth_map = {
        "high": "심층 분석이 필요합니다. 다각도로 조사하고, 데이터 간 상관관계를 분석하세요. Agent 서브에이전트를 적극 활용하여 병렬 수집하세요.",
        "medium": "적절한 깊이의 분석이 필요합니다. 핵심 데이터를 충실히 수집하세요.",
        "low": "간결하고 핵심적인 정보 수집에 집중하세요. 불필요한 확장을 피하세요.",
    }
    depth_guide = depth_map.get(complexity, depth_map["low"])

    # 도메인 가이드 (전략이 있으면 생략)
    domain_block = ""
    if domains and not strategy:
        domain_block = f"\n\n## 분석 도메인\n{', '.join(domains)}"

    from datetime import date
    today = date.today().isoformat()

    # 출력 형식에 따른 파일명 + 가이드
    fmt = OUTPUT_FORMAT_MAP.get(output_format, OUTPUT_FORMAT_MAP["html"])
    output_filename = fmt["ext"]
    output_guide = fmt["guide"]

    # 스케줄 실행 시: 파일명에 날짜 포함 + MD 동시 생성
    dated_output_block = ""
    if is_scheduled:
        dated_html = f"results_{today}.html"
        dated_md = f"results_{today}.md"
        dated_output_block = f"""
**스케줄 실행이므로 다음 2개 파일을 모두 생성하세요:**
1. `{report_dir}/{dated_html}` — 사람이 보는 HTML 보고서
2. `{report_dir}/{dated_md}` — 다음 실행에서 참조할 Markdown 요약

MD 파일 규칙:
- HTML과 동일한 내용을 Markdown 형식으로 작성
- 테이블, 수치, 출처를 모두 포함 (정보 손실 없이)
- CSS/HTML 태그 없이 순수 Markdown만 사용
"""
        # 스케줄에서는 날짜 포함 파일명을 기본 출력으로 사용
        output_filename = dated_html

    # Delta 비교 블록: 이전 MD 파일이 있으면 비교 지시
    delta_block = ""
    if previous_report_path:
        delta_block = f"""

## 이전 실행 결과 비교 (Delta)
이전 실행의 요약 파일이 다음 경로에 있습니다:
`{previous_report_path}`

**반드시 다음 단계를 수행하세요:**
1. Read 도구로 이전 파일을 읽으세요
2. 이번에 수집한 데이터와 이전 데이터를 비교하세요
3. 보고서에 **"변동 사항 (Delta)"** 섹션을 추가하세요:
   - 새로 추가된 정보
   - 변경된 수치/사실 (이전 값 → 현재 값)
   - 삭제/소멸된 항목
4. 변동이 없으면 "주요 변동 없음"으로 표기하세요
"""

    # Append 모드 블록: 기존 파일에 데이터 누적
    append_block = ""
    if output_mode == "append" and previous_report_path:
        append_block = f"""

## 누적 모드 (Append)
기존 데이터 파일이 다음 경로에 있습니다:
`{previous_report_path}`

**이번 실행에서 수집한 데이터를 기존 파일에 추가하세요:**
- CSV: 기존 파일을 Read로 읽고, 새 행을 아래에 추가하여 같은 경로에 Write
- JSON: 기존 JSON의 data 배열에 새 항목을 추가하여 같은 경로에 Write
- Markdown: 기존 파일을 Read로 읽고, `---` 구분선 뒤에 새 날짜 섹션을 추가하여 Write
- 날짜 컬럼/필드를 반드시 포함하여 언제 추가된 데이터인지 구분되게 하세요
- 기존 데이터를 수정하거나 삭제하지 마세요
"""

    return f"""## 작업
{user_task}

## 현재 날짜
{today} — 이 날짜 기준으로 최신 정보를 검색하세요. 검색 시 "{today[:4]}년" 등 연도를 포함하세요.
{qa_block}
{strategy_block}
{domain_block}

## 분석 깊이
{depth_guide}
{delta_block}
{append_block}
## 출력
최종 결과를 다음 경로에 파일로 생성하세요:
`{report_dir}/{output_filename}`

먼저 `mkdir -p {report_dir}` 를 실행하세요.
{dated_output_block}
{output_guide}
"""
