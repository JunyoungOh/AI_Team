"""Structured message schemas for inter-agent communication.

All agent-to-agent communication uses Pydantic models to ensure
structured JSON output and reliable parsing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ──────────────────────────────────────────
# 4.1 CEO → System: Routing Decision
# ──────────────────────────────────────────

class CEORoutingDecision(BaseModel):
    """CEO가 유관 리더를 선택하는 결정."""

    rationale: str = Field(description="선택 근거")
    selected_domains: list[str] = Field(description="선택된 리더 도메인 목록 (1-3개)")
    estimated_complexity: Literal["low", "medium", "high"] = "medium"
    selected_mode: Literal["hierarchical"] = "hierarchical"
    report_type: Literal[
        "comparison", "market_research", "trend_analysis",
        "strategic", "technical", "general",
    ] = Field(
        default="general",
        description=(
            "보고서 유형 분류. comparison=A vs B 비교, market_research=시장/산업 조사, "
            "trend_analysis=시계열/추세, strategic=전략/SWOT/의사결정, "
            "technical=기술 분석/아키텍처, general=기타"
        ),
    )


# ──────────────────────────────────────────
# 4.2 Leader → User: Clarifying Questions
# ──────────────────────────────────────────

class LeaderQuestions(BaseModel):
    """리더가 사용자에게 던지는 상세 질문."""

    questions: list[str] = Field(min_length=3, max_length=5, description="3-5개 질문")
    context: str = Field(description="질문의 맥락 설명")


# ──────────────────────────────────────────
# 4.2a CEO → System: Question Optimization
# ──────────────────────────────────────────

class OptimizedDomainQuestions(BaseModel):
    """최적화 후 특정 도메인에 배정된 질문."""

    domain: str = Field(description="리더 도메인명")
    questions: list[str] = Field(min_length=1, description="최적화된 질문 목록")


class CEOQuestionOptimization(BaseModel):
    """CEO가 여러 리더의 질문을 중복 제거·병합한 결과."""

    original_total: int = Field(description="최적화 전 전체 질문 수")
    optimized_total: int = Field(description="최적화 후 전체 질문 수")
    removed_duplicates: list[str] = Field(
        default_factory=list, description="제거된 중복 질문 요약"
    )
    optimized_questions: list[OptimizedDomainQuestions] = Field(
        description="도메인별 최적화된 질문 목록"
    )
    optimization_rationale: str = Field(description="최적화 근거 요약")


class CEOGeneratedQuestions(BaseModel):
    """CEO가 도메인별로 직접 생성한 질문."""

    questions_by_domain: list[OptimizedDomainQuestions] = Field(
        description="도메인별 질문 (각 도메인 최소 2개)"
    )
    total_questions: int = Field(description="전체 질문 수")
    generation_rationale: str = Field(description="질문 생성 근거")


# ──────────────────────────────────────────
# 4.2b CEO → System: Work Order (작업지시서)
# ──────────────────────────────────────────

class WorkOrderDomainDirective(BaseModel):
    """작업지시서의 도메인별 지시 사항."""

    domain: str = Field(description="리더 도메인명")
    objectives: list[str] = Field(min_length=1, description="해당 도메인의 핵심 목표")
    constraints: list[str] = Field(default_factory=list, description="제약 조건")
    expected_deliverables: list[str] = Field(min_length=1, description="기대 산출물")


class CEOWorkOrder(BaseModel):
    """CEO가 유저 답변을 종합하여 생성하는 공식 작업지시서."""

    title: str = Field(description="작업 제목")
    background: str = Field(description="작업 배경 및 맥락 요약")
    domain_directives: list[WorkOrderDomainDirective] = Field(
        min_length=1, description="도메인별 지시 사항"
    )
    cross_domain_dependencies: list[str] = Field(
        default_factory=list, description="도메인간 의존성/연계 사항"
    )
    quality_criteria: list[str] = Field(
        default_factory=list, description="전체 품질 기준"
    )
    rationale: str = Field(description="작업지시서 작성 근거")



# ──────────────────────────────────────────
# 4.4 Leader → System: Task Decomposition
# ──────────────────────────────────────────

class Subtask(BaseModel):
    """리더가 분해한 개별 작업 단위."""

    task_title: str = Field(description="작업 제목")
    worker_type: str = Field(description="실행할 워커 유형 (e.g. researcher, backend_developer) 또는 커스텀 역할명")
    tool_category: str | None = Field(
        default=None,
        description="커스텀 워커의 도구 카테고리 (research, development, analysis, security, general). 등록된 워커 유형이면 생략.",
    )
    objective: str = Field(description="이 작업의 구체적 목표")
    success_criteria: list[str] = Field(min_length=1, max_length=3, description="성공 기준 (최대 3개 — 초과 시 작업 분할)")
    dependencies: list[str] = Field(default_factory=list, description="의존하는 다른 subtask의 task_title")


class TaskDecomposition(BaseModel):
    """리더의 작업 분해 결과."""

    subtasks: list[Subtask] = Field(min_length=1, max_length=3, description="분해된 작업 단위 (최대 3개)")
    decomposition_rationale: str = Field(description="작업 분해 근거")


# ──────────────────────────────────────────
# 4.4b CEO → System: Direct Task Decomposition
# ──────────────────────────────────────────

class CEOSubtask(BaseModel):
    """CEO가 직접 생성한 개별 작업 단위."""

    task_title: str = Field(description="작업 제목")
    worker_name: str = Field(
        default="",
        description="워커의 역할 이름 (예: '시장 조사 전문가', '재무 분석가', '품질 검증관'). 빈 문자열이면 task_title에서 자동 생성.",
    )
    role_type: Literal["planner", "executor", "synthesizer", "reviewer"] = Field(
        default="executor",
        description="팀 내 역할 유형. planner=계획·설계, executor=실행·수집, synthesizer=결과 합성, reviewer=검증·평가",
    )
    objective: str = Field(description="구체적 목표 — 분석 방법론, 도구 전략, 출력 구조를 포함")
    success_criteria: list[str] = Field(min_length=1, max_length=5, description="성공 기준 (최대 5개)")
    tool_category: Literal[
        "research", "data", "development",
        "finance", "security", "legal", "hr",
    ] = Field(description="도구 카테고리")
    dependencies: list[str] = Field(default_factory=list, description="의존하는 다른 subtask의 task_title")


class CEOTaskDecomposition(BaseModel):
    """CEO의 직접 작업 분해 결과."""

    subtasks: list[CEOSubtask] = Field(min_length=1, max_length=5, description="분해된 작업 단위 (최대 5개)")
    decomposition_rationale: str = Field(description="작업 분해 근거")


# ──────────────────────────────────────────
# 4.5 Worker → Leader: Plan Submission
# ──────────────────────────────────────────

class WorkerPlan(BaseModel):
    """실무자가 작성한 상세 작업 계획."""

    plan_title: str
    steps: list[str] = Field(min_length=3, description="최소 3단계 실행 계획")
    expected_output: str
    dependencies: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────
# 4.6a Leader → CEO: Plan Quality Scores
# ──────────────────────────────────────────

class PlanScores(BaseModel):
    """계획 품질 점수 (각 1-10)."""

    completeness: int = Field(ge=1, le=10)
    feasibility: int = Field(ge=1, le=10)
    alignment: int = Field(ge=1, le=10)


# ──────────────────────────────────────────
# 4.6c CEO: Plan Confirmation
# ──────────────────────────────────────────

class CrossDomainAnalysis(BaseModel):
    """교차 검토 시 CEO의 도메인간 분석."""

    coherence_assessment: str
    dependencies_identified: list[str] = Field(default_factory=list, description="도메인간 의존성 설명 목록")
    conflicts_found: list[str] = Field(default_factory=list)


class CEOPlanConfirmation(BaseModel):
    """CEO의 계획 최종 컨펌 (단순/교차 이원화)."""

    review_mode: Literal["single_leader", "cross_domain"]
    leader_count: int
    confirmation_message: str
    cross_domain_analysis: CrossDomainAnalysis | None = None
    execution_order: list[str] = Field(default_factory=list)
    execution_notes: str = ""
    go_no_go: Literal["GO", "NO_GO"]


# ──────────────────────────────────────────
# 4.7 Worker → Leader: Execution Result
# ──────────────────────────────────────────

class LabeledFinding(BaseModel):
    """워커가 수집한 개별 정보 단위 — 종류와 중요도로 라벨링."""

    content: str = Field(description="정보 내용 (수치, 사실, 분석 등)")
    category: str = Field(
        description="정보 종류: fact(사실), statistic(수치/통계), quote(인용), "
        "analysis(분석/해석), recommendation(제안), risk(위험요인), opportunity(기회요인)",
    )
    importance: int = Field(
        ge=1, le=5,
        description="중요도: 5=사용자 질문에 직접 답하는 핵심, 4=핵심 뒷받침 근거, "
        "3=맥락 정보, 2=부가 참고, 1=배경 지식",
    )
    source: str = Field(default="", description="출처 (URL, 문서명 등)")


class ReflectionVerdict(BaseModel):
    """Haiku의 워커 결과 품질 판정."""

    passed: bool = Field(description="True=품질 충분, False=수정 필요")
    critique: str = Field(default="", description="FAIL 시 구체적 수정 지시")
    failed_criteria: list[str] = Field(
        default_factory=list,
        description="미충족 success_criteria 목록",
    )


class ReportReviewVerdict(BaseModel):
    """Reviewer의 CEO 리포트 품질 평가."""

    data_fidelity: int = Field(ge=1, le=10, description="데이터 충실성")
    logical_consistency: int = Field(ge=1, le=10, description="논리 일관성")
    request_alignment: int = Field(ge=1, le=10, description="요청 부합도")
    actionability: int = Field(ge=1, le=10, description="실행 가능성")
    passed: bool = Field(description="True=품질 충분")
    critique: str = Field(default="", description="FAIL 시 구체적 수정 지시")
    missing_data: list[str] = Field(default_factory=list, description="리포트에 누락된 핵심 데이터")


class ReviewerLoopVerdict(BaseModel):
    """Reviewer의 워커 실행 결과 품질 판정 (P-E-R 루프 제어용).

    Reviewer Worker가 아닌, structured_query 직접 호출로 파싱.
    """

    passed: bool = Field(description="True=품질 충분하여 루프 종료, False=재실행 필요")
    overall_score: int = Field(ge=1, le=10, description="전체 품질 점수")
    gaps: list[str] = Field(default_factory=list, description="부족한 영역 목록 (FAIL 시)")
    critique: str = Field(default="", description="구체적 수정 지시")
    strong_points: list[str] = Field(default_factory=list, description="잘 수행된 영역")


class WorkerResult(BaseModel):
    """실무자의 작업 실행 결과 보고."""

    result_summary: str
    deliverables: list[str]
    issues_encountered: list[str] = Field(default_factory=list)
    completion_percentage: int = Field(ge=0, le=100)
    deliverable_files: list[str] = Field(
        default_factory=list,
        description="생성된 파일의 절대 경로 목록 (e.g. /abs/path/to/report.md)",
    )
    is_partial: bool = Field(
        default=False,
        description="True if result was salvaged from a timeout (partial completion)",
    )
    labeled_findings: list[LabeledFinding] = Field(
        default_factory=list,
        description="수집된 정보를 종류·중요도로 라벨링한 목록. "
        "CEO가 핵심 정보를 빠르게 식별하도록 반드시 작성.",
    )
    reflection_passed: bool = Field(
        default=True, description="Reflection 통과 여부 (True=통과 or 미적용)",
    )
    reflection_repaired: bool = Field(
        default=False, description="Repair 실행 여부",
    )


# ──────────────────────────────────────────
# 4.8 Leader → CEO: Gap Analysis
# ──────────────────────────────────────────

class GapAnalysis(BaseModel):
    """리더의 계획 대비 결과물 갭 분석."""

    gaps: list[str]
    gap_severity: Literal["none", "minor", "major", "critical"]
    quality_scores: PlanScores | None = Field(default=None, description="차원별 결과 품질 점수")
    approved: bool
    overall_quality_score: float = Field(ge=0, le=10)
    recommendations: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────
# 4.9 CEO → User: Final Report
# ──────────────────────────────────────────

class DomainResult(BaseModel):
    """도메인별 결과 요약."""

    domain: str
    summary: str
    quality_score: float = Field(ge=0, le=10)
    key_deliverables: list[str]
    gaps: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(
        default_factory=list,
        description="이 도메인 워커들이 생성한 파일의 절대 경로 목록",
    )


class LeaderConsolidatedResult(BaseModel):
    """리더들이 모든 도메인 결과를 통합한 최종 결과물."""

    consolidated_summary: str = Field(description="통합 요약")
    domain_contributions: list[DomainResult] = Field(description="도메인별 기여 내용")
    cross_domain_synthesis: str = Field(description="도메인간 연계 분석 및 종합")
    remaining_gaps: list[str] = Field(default_factory=list, description="미해결 갭")
    recommendations: list[str] = Field(default_factory=list, description="권고 사항")


class DomainAnalystReport(BaseModel):
    """도메인 Analyst의 종합 분석 보고서.

    워커가 수집한 원재료를 합성하여 도메인별 완성된 보고서 섹션을 생성.
    """

    domain_summary: str = Field(
        default="",
        description="도메인 핵심 분석 요약 (3-5문장, 수치 포함)",
    )
    detailed_analysis: str = Field(
        default="",
        description="상세 분석 본문 (마크다운). 워커 데이터를 합성한 구조화된 내용.",
    )
    key_data_points: list[str] = Field(
        default_factory=list,
        description="핵심 데이터 포인트 (수치·팩트 위주, 출처 포함)",
    )
    gaps_identified: list[str] = Field(
        default_factory=list,
        description="워커 결과 대비 누락된 정보 또는 불충분한 영역",
    )
    gap_severity: Literal["none", "minor", "major", "critical"] = Field(
        default="none",
        description="갭 심각도: none=완전, minor=보충 불필요, major=보충 권장, critical=반드시 보충",
    )
    confidence_score: int = Field(
        ge=1, le=10, default=7,
        description="도메인 분석 완성도 (8+=충분, 5-7=보통, <5=부족)",
    )
    report_html: str = Field(
        default="",
        description="도메인별 리치 HTML 섹션 (inline style, 테이블/차트 포함)",
    )


class CEOFinalReport(BaseModel):
    """CEO의 최종 보고서."""

    executive_summary: str = Field(default="", description="사용자 요청에 대한 핵심 결과 요약 (3-5문장)")
    domain_results: list[DomainResult] = Field(default_factory=list)
    overall_gap_analysis: str = Field(default="", description="결과 완성도 1-2문장 요약")
    recommendations: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────
# 4.10 Leader → System: Worker Assembly Result
# ──────────────────────────────────────────

class SelectedWorker(BaseModel):
    """리더가 선발한 개별 워커 정보."""

    worker_domain: str
    specific_instructions: str = ""


class WorkerAssemblyResult(BaseModel):
    """리더가 워커 편성 결과를 반환."""

    selected_workers: list[SelectedWorker]


# ──────────────────────────────────────────
# 4.11 CEO → User: Fast Path Direct Response
# ──────────────────────────────────────────

class AnalystVerdict(BaseModel):
    """Analyst의 데이터 충분성 판단 결과."""

    verdict: Literal["sufficient", "insufficient"] = Field(
        description="sufficient=데이터 충분, insufficient=추가 수집 필요"
    )
    rationale: str = Field(description="판단 근거")
    coverage_assessment: str = Field(
        description="사용자 질문의 각 측면이 얼마나 커버되었는지 평가"
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="insufficient일 때: 부족한 데이터 영역 구체적 목록",
    )
    additional_research_directives: list[str] = Field(
        default_factory=list,
        description="insufficient일 때: 추가 리서치 지시 (어떤 정보를 어디서 찾을지)",
    )
    confidence_score: float = Field(
        ge=0, le=10, default=7,
        description="데이터 충분성에 대한 확신도 (8 이상이면 sufficient 권장)",
    )


class DeepResearchResult(BaseModel):
    """Deep Researcher(Opus)의 단일 도메인 심층 리서치 결과."""

    domain: str = Field(description="리서치 도메인명 (e.g. 'AI 반도체 시장')")
    executive_summary: str = Field(description="핵심 요약 3-5문장")
    report_html: str = Field(
        default="",
        description="완성된 리치 HTML 리포트 본문 (선택 — 비워두면 시스템이 생성)",
    )
    key_findings: list[LabeledFinding] = Field(default_factory=list)
    sources: list[str] = Field(
        default_factory=list,
        description="참조 출처 URL 목록",
    )
    confidence_score: int = Field(
        ge=1, le=10, default=7,
        description="리서치 완성도 (8+=충분, 5-7=보통, <5=부족)",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="추가 조사가 필요한 영역",
    )


class FastPathResponse(BaseModel):
    """CEO의 단순 작업 직접 응답."""

    response: str = Field(description="사용자에게 전달할 직접 응답 (마크다운)")
    key_points: list[str] = Field(default_factory=list, description="핵심 요점 (1-5개)")
    confidence_score: float = Field(ge=0, le=10, default=7, description="응답 신뢰도 점수")
    recommendations: list[str] = Field(default_factory=list, description="추가 권장사항")
    requires_deep_analysis: bool = Field(
        default=False,
        description="True면 fast path 취소하고 정상 파이프라인으로 전환",
    )


# ──────────────────────────────────────────
# Mode Dispatch: Participant Generation
# ──────────────────────────────────────────

class ModeParticipantSpec(BaseModel):
    """CEO가 생성한 모드 참가자 사양."""

    name: str = Field(description="참가자 이름 (역할을 나타냄)")
    persona: str = Field(description="전문성과 관점 (2-3문장)")
    role: str = Field(description="모드 내 역할 (speaker, pro, con, judge, drafter, reviewer, finalizer, stage_N)")
    worker_domain: str = Field(default="", description="registry 워커 타입 (financial_analyst 등). 빈 문자열이면 즉석 페르소나 모드.")
    viewpoint: str = Field(default="", description="토론 관점/입장 (토론 계열 모드에서만 사용)")
    tool_category: Literal["research", "verify", "none"] = "none"


class ModeParticipantList(BaseModel):
    """CEO가 생성한 모드 참가자 목록."""

    participants: list[ModeParticipantSpec] = Field(min_length=2, max_length=6)
    generation_rationale: str = Field(description="참가자 구성 근거")
