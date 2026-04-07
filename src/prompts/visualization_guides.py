"""Type-specific visualization guides and VizHints extraction.

Provides:
- VISUALIZATION_GUIDES: dict of report_type -> visualization guide text
- VizHints: dataclass with data characteristics extracted from findings
- extract_viz_hints(): rule-based extraction from labeled findings
- build_report_prompt(): composes base prompt + guide + hints
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class VizHints:
    """Data characteristics extracted from worker/researcher findings."""

    numeric_count: int = 0
    comparison_pairs: int = 0
    has_timeline: bool = False
    entity_count: int = 0
    top_entities: list[str] = field(default_factory=list)
    data_complexity: str = "low"
    recommend_interactive: bool = False

    def to_prompt_section(self) -> str:
        lines = ["## 시각화 힌트 (데이터 분석 결과)"]
        lines.append(f"- 수치 데이터: {self.numeric_count}개"
                     + (" (바 차트/KPI 적극 활용)" if self.numeric_count >= 5 else ""))
        if self.comparison_pairs > 0:
            lines.append(f"- 비교 쌍: {self.comparison_pairs}개 (대비 레이아웃 권장)")
        if self.has_timeline:
            lines.append("- 시계열: 감지됨 (타임라인 사용)")
        if self.top_entities:
            lines.append(f"- 주요 항목: {', '.join(self.top_entities[:5])}")
        lines.append(f"- 데이터 복잡도: {self.data_complexity}"
                     + (" → Chart.js CDN 사용 허용" if self.recommend_interactive else ""))
        return "\n".join(lines)


_COMPARISON_RE = re.compile(r"vs\.?|대비|비교|차이", re.IGNORECASE)
_TIMELINE_RE = re.compile(r"20[12]\d년|20[12]\d\b|[1-4]분기|Q[1-4]|[0-9]{1,2}월")


def extract_viz_hints(findings: list[dict]) -> VizHints:
    if not findings:
        return VizHints()

    numeric_count = 0
    comparison_pairs = 0
    has_timeline = False
    entity_counter: Counter = Counter()

    for f in findings:
        if not isinstance(f, dict):
            continue
        category = f.get("category", "")
        content = f.get("content", "")

        if category == "statistic":
            numeric_count += 1

        comparison_pairs += len(_COMPARISON_RE.findall(content))

        if _TIMELINE_RE.search(content):
            has_timeline = True

        for token in re.split(r"[,·/]|\s(?:vs\.?|대비|비교|및|그리고)\s", content):
            token = token.strip()
            if 2 <= len(token) <= 20 and not token.isdigit():
                entity_counter[token] += 1

    if numeric_count >= 10 or comparison_pairs >= 5:
        complexity = "high"
    elif numeric_count >= 5:
        complexity = "medium"
    else:
        complexity = "low"

    top_entities = [name for name, _ in entity_counter.most_common(5)]

    return VizHints(
        numeric_count=numeric_count,
        comparison_pairs=comparison_pairs,
        has_timeline=has_timeline,
        entity_count=len(entity_counter),
        top_entities=top_entities,
        data_complexity=complexity,
        recommend_interactive=(complexity == "high"),
    )


VISUALIZATION_GUIDES: dict[str, str] = {
    "comparison": """\
## 시각화 전략: 비교 분석
이 보고서는 항목 간 비교가 핵심입니다.

### 필수 컴포넌트:
1. 비교 매트릭스 — 항목을 행, 평가 기준을 열로 한 테이블 (체크/수치)
2. 점수 바 — 각 항목의 종합 점수를 그라데이션 바로 나란히 비교
3. 장단점 대비 — 강점(초록)/약점(빨간) 2열 레이아웃

### 금지:
- 항목별로 따로 서술하고 끝내기 (비교가 아닌 나열)
- 모든 항목에 동일한 카드 레이아웃 사용

### 데이터→차트 매핑:
- 정량 점수 → 바 차트 (나란히 비교)
- 범주별 유무 → 체크 매트릭스 테이블
- 종합 평가 → 레이더형 테이블 (항목별 점수 행렬)""",

    "market_research": """\
## 시각화 전략: 시장/산업 조사
이 보고서는 시장 규모, 점유율, 성장률 등 수치 중심입니다.

### 필수 컴포넌트:
1. KPI 대시보드 — 시장 규모, 성장률, 주요 지표를 큰 숫자 카드로
2. 점유율 바 차트 — 주요 기업/제품별 시장 점유율
3. 트렌드 타임라인 — 연도별/분기별 변화 추이

### 금지:
- 수치를 텍스트 문장으로만 나열
- 출처 없는 수치 제시

### 데이터→차트 매핑:
- 시장 규모/성장률 → KPI 카드 (큰 숫자 + 변동률 화살표)
- 기업별 점유율 → 수평 바 차트 (정렬)
- 연도별 추이 → 타임라인 (세로 점선 + 이벤트 카드)""",

    "trend_analysis": """\
## 시각화 전략: 시계열/추세 분석
이 보고서는 시간에 따른 변화가 핵심입니다.

### 필수 컴포넌트:
1. 타임라인 — 주요 이벤트를 시간순 세로 배치 (점선 + 카드)
2. 추이 시각화 — 기간별 수치 변화를 바/프로그레스 바로 표현
3. 이벤트 카드 — 전환점/주요 사건을 강조 카드로

### 금지:
- 시간 순서를 무시한 랜덤 나열
- 날짜 없는 트렌드 서술

### 데이터→차트 매핑:
- 연도별 수치 → 연도 라벨 + 프로그레스 바
- 분기별 이벤트 → 타임라인 (점선 + 이벤트 카드)
- 성장/하락률 → KPI 카드 (변동률 화살표)""",

    "strategic": """\
## 시각화 전략: 전략/SWOT/의사결정
이 보고서는 전략적 분석과 의사결정 지원이 핵심입니다.

### 필수 컴포넌트:
1. SWOT 매트릭스 — 2x2 그리드 (강점/약점/기회/위협)
2. 장단점 2열 대비 — 초록(장점)/빨간(단점) 배경
3. 프로세스 플로우 — 실행 단계를 번호 원 + 화살표로

### 금지:
- 분석 없이 사실만 나열
- SWOT 항목을 불릿 리스트로만 표현

### 데이터→차트 매핑:
- SWOT 분석 → 4분면 그리드 (배경색 구분)
- 옵션 비교 → 대비 테이블 + 추천 하이라이트
- 실행 로드맵 → 단계 표시 (번호 원 + 설명)""",

    "technical": """\
## 시각화 전략: 기술 분석/아키텍처
이 보고서는 기술 스펙, 아키텍처, 성능 데이터가 핵심입니다.

### 필수 컴포넌트:
1. 스펙 테이블 — 기술 사양을 행렬로 정리
2. 성능 바 차트 — 벤치마크/성능 수치 비교
3. 구조 다이어그램 — 컴포넌트 관계를 박스+화살표로

### 금지:
- 기술 용어 설명 없이 나열
- 성능 수치를 텍스트로만 서술

### 데이터→차트 매핑:
- 벤치마크 → 수평 바 차트 (정렬)
- 기술 스펙 → 비교 테이블 (항목 x 제품)
- 아키텍처 → 박스 다이어그램 (div + border + 화살표)""",

    "general": """\
## 시각화 전략: 범용
데이터 특성에 맞게 자유롭게 시각화하세요.

### 사용 가능한 컴포넌트:
- 수치 비교 → 인라인 바 차트 (div width%)
- 시계열 → 타임라인 (세로 점선 + 이벤트 카드)
- 카테고리 분류 → 카드 그리드 (flexbox)
- 장단점 → 2열 대비 레이아웃
- 핵심 지표 → KPI 대시보드 (큰 숫자 + 라벨)
- 프로세스 → 단계 표시 (번호 원 + 화살표)

### 핵심 원칙:
- 데이터가 형태를 결정 — 고정 템플릿 사용 금지
- importance 5-4 데이터를 빠짐없이 시각적으로 표현
- 이모지를 섹션 제목에 활용하여 시각적 앵커 제공""",
}


def build_report_prompt(
    base_prompt: str,
    report_type: str,
    viz_hints: VizHints | None = None,
) -> str:
    guide = VISUALIZATION_GUIDES.get(report_type, VISUALIZATION_GUIDES["general"])
    result = base_prompt + "\n\n" + guide
    if viz_hints:
        result += "\n\n" + viz_hints.to_prompt_section()
    return result
