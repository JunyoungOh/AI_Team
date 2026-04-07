"""야근팀 프롬프트 — iteration별 수집/평가/최종 보고서 생성."""

from __future__ import annotations

from datetime import date


OVERTIME_SYSTEM = """\
당신은 목표 달성까지 반복적으로 작업하는 리서치 에이전트입니다.
이번이 {iteration}번째 작업입니다.

## 현재 날짜
{today}

## 작업 규칙
1. 정보를 수집하여 `{work_dir}/raw_{iteration}.md` 파일에 마크다운으로 저장하세요
2. 이전 iteration 파일이 있으면 Read 도구로 읽고, 부족한 부분을 보강하세요
3. 같은 정보를 중복 수집하지 마세요
4. 수집한 데이터에는 반드시 출처와 날짜를 명시하세요
5. 검색 시 "{today_year}년" 등 연도를 포함하여 최신 정보를 우선하세요

## 도구 활용
- **Agent**: 서브에이전트 병렬 실행으로 여러 관점 동시 수집
- **WebSearch/WebFetch**: 정보 검색
- **Read**: 이전 iteration 결과 파일 읽기
- **Write**: 수집 결과를 md 파일로 저장
- **Bash**: mkdir 등 파일 시스템 작업
"""


EVALUATION_SYSTEM = """\
당신은 리서치 결과의 완성도를 평가하는 전문가입니다.
아래 파일들의 내용을 읽고, 목표 대비 달성률을 판단하세요.

## 평가 기준
1. 목표에서 요구하는 정보가 충분히 수집되었는가
2. 출처가 명시되고 신뢰할 수 있는가
3. 데이터 간 중복 없이 다양한 관점이 커버되었는가
4. 수치/팩트가 구체적인가

## 출력 형식 (반드시 이 JSON만 출력)
```eval_json
{
  "score": 75,
  "summary": "채널 분석과 고객 세그먼트는 충분하나, 재무 효율성 데이터 부족",
  "gaps": ["재무 ROI 데이터 미확보", "경쟁사 비교 불충분"],
  "recommendation": "재무 지표와 경쟁사 데이터 추가 수집 필요"
}
```
score는 0-100 정수. 90 이상이면 목표 달성으로 판단.
"""


FINAL_REPORT_SYSTEM = """\
당신은 수집된 리서치 데이터를 프로페셔널 HTML 보고서로 작성하는 전문가입니다.

## 현재 날짜
{today}

## 작업 순서
1. Bash 도구로 `mkdir -p {report_dir}` 실행
2. Read 도구로 `{work_dir}/raw_*.md` 파일들을 모두 읽기
3. 중복을 제거하고 내용을 통합
4. Write 도구로 `{report_dir}/results.html` 에 완성된 HTML 보고서 생성

## HTML 보고서 규격 (반드시 준수)

Write 도구로 생성하는 파일은 `<!DOCTYPE html>`로 시작하는 **완전한 HTML 파일**이어야 합니다.
마크다운이 아닌 HTML입니다. 반드시 <style> 태그 안에 CSS를 포함하세요.

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
- raw_*.md의 데이터를 빠짐없이 포함 — 축약 금지
"""


def build_iteration_prompt(
    task: str,
    strategy: dict | None,
    goal: str,
    iteration: int,
    work_dir: str,
    previous_eval: dict | None = None,
) -> tuple[str, str]:
    """iteration용 시스템 프롬프트 + 유저 프롬프트 반환."""
    today = date.today().isoformat()

    system = OVERTIME_SYSTEM.format(
        iteration=iteration,
        today=today,
        today_year=today[:4],
        work_dir=work_dir,
    )

    # 유저 프롬프트 조립
    parts = [f"## 작업\n{task}"]

    if strategy:
        perspectives = strategy.get("perspectives", [])
        if perspectives:
            parts.append(f"\n## 분석 프레임워크: {strategy.get('name', '')}")
            for p in perspectives:
                parts.append(f"- **{p.get('icon', '')} {p.get('name', '')}**: {p.get('instruction', '')}")
        special = strategy.get("special_instructions", "")
        if special:
            parts.append(f"\n## 특별 지시\n{special}")

    parts.append(f"\n## 목표\n{goal}")

    if iteration > 1:
        prev_files = ", ".join(f"`{work_dir}/raw_{i}.md`" for i in range(1, iteration))
        parts.append(f"\n## 이전 작업 결과\n다음 파일들을 Read 도구로 읽고 내용을 파악하세요: {prev_files}")

    if previous_eval:
        parts.append(f"\n## 이전 평가 결과 (달성률 {previous_eval.get('score', 0)}%)")
        parts.append(f"요약: {previous_eval.get('summary', '')}")
        gaps = previous_eval.get("gaps", [])
        if gaps:
            parts.append("보완 필요:")
            for g in gaps:
                parts.append(f"- {g}")
        rec = previous_eval.get("recommendation", "")
        if rec:
            parts.append(f"권고: {rec}")

    parts.append(f"\n## 출력\n수집 결과를 `{work_dir}/raw_{iteration}.md`에 저장하세요.")
    parts.append(f"먼저 `mkdir -p {work_dir}` 를 실행하세요.")

    return system, "\n\n".join(parts)


def build_evaluation_prompt(work_dir: str, goal: str, iteration: int) -> tuple[str, str]:
    """평가용 시스템 프롬프트 + 유저 프롬프트 반환."""
    files = ", ".join(f"`{work_dir}/raw_{i}.md`" for i in range(1, iteration + 1))
    user = f"## 목표\n{goal}\n\n## 평가 대상 파일\n다음 파일들을 Read 도구로 읽고 평가하세요: {files}"
    return EVALUATION_SYSTEM, user


def build_final_report_prompt(
    task: str,
    work_dir: str,
    report_dir: str,
) -> tuple[str, str]:
    """최종 보고서 생성용 시스템 + 유저 프롬프트 반환."""
    today = date.today().isoformat()
    system = FINAL_REPORT_SYSTEM.format(
        today=today,
        work_dir=work_dir,
        report_dir=report_dir,
    )
    user = f"## 작업\n{task}\n\n수집된 모든 raw_*.md 파일을 종합하여 최종 HTML 보고서를 생성하세요."
    return system, user
