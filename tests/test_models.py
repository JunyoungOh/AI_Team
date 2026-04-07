"""Unit tests for state models and message schemas."""

import pytest

from src.models.state import (
    create_initial_state,
    create_worker_report,
)
from src.models.messages import (
    CEOGeneratedQuestions,
    CEOQuestionOptimization,
    CEORoutingDecision,
    LeaderQuestions,
    OptimizedDomainQuestions,
    Subtask,
    TaskDecomposition,
    WorkerPlan,
    PlanScores,
    CEOPlanConfirmation,
    WorkerResult,
    GapAnalysis,
    CEOFinalReport,
    DomainResult,
    WorkerAssemblyResult,
    SelectedWorker,
)


# ── create_initial_state ──────────────────────────


def test_create_initial_state_defaults():
    state = create_initial_state("Analyze market")
    assert state["user_task"] == "Analyze market"
    assert state["phase"] == "intake"
    assert state["execution_mode"] == "interactive"
    assert state["pre_context"] == {}
    assert state["messages"] == []
    assert state["error_message"] == ""
    assert state["iteration_counts"]["ceo_rejections"] == 0


def test_create_initial_state_scheduled():
    ctx = {"background": "Weekly report", "domain_answers": {"research": ["A1"]}}
    state = create_initial_state("Run report", execution_mode="scheduled", pre_context=ctx)
    assert state["execution_mode"] == "scheduled"
    assert state["pre_context"]["background"] == "Weekly report"
    assert state["pre_context"]["domain_answers"]["research"] == ["A1"]


def test_create_initial_state_no_pre_context():
    state = create_initial_state("Task", execution_mode="scheduled")
    assert state["pre_context"] == {}


# ── create_worker_report ──────────────────────────


def test_create_worker_report():
    worker = create_worker_report("worker-001", "analyst")
    assert worker["worker_id"] == "worker-001"
    assert worker["worker_domain"] == "analyst"
    assert worker["status"] == "planning"
    assert worker["revision_count"] == 0


# ── Pydantic message models ──────────────────────


def test_ceo_routing_decision():
    d = CEORoutingDecision(
        rationale="Need research",
        selected_domains=["research"],
        estimated_complexity="high",
    )
    assert d.rationale == "Need research"
    data = d.model_dump()
    assert data["estimated_complexity"] == "high"


def test_leader_questions():
    q = LeaderQuestions(
        questions=["Q1?", "Q2?", "Q3?"],
        context="Market analysis",
    )
    assert len(q.questions) == 3
    assert q.context == "Market analysis"


def test_worker_plan_serialization():
    plan = WorkerPlan(
        plan_title="Research Plan",
        steps=["Step 1", "Step 2", "Step 3"],
        expected_output="Report",
    )
    json_str = plan.model_dump_json()
    restored = WorkerPlan.model_validate_json(json_str)
    assert restored.plan_title == "Research Plan"
    assert len(restored.steps) == 3


def test_plan_scores_validation():
    scores = PlanScores(completeness=8, feasibility=7, alignment=9)
    assert scores.completeness == 8


def test_ceo_plan_confirmation_go():
    c = CEOPlanConfirmation(
        review_mode="single_leader",
        leader_count=1,
        confirmation_message="Approved",
        go_no_go="GO",
    )
    assert c.go_no_go == "GO"


def test_worker_result():
    r = WorkerResult(
        result_summary="Done",
        deliverables=["report.pdf"],
        completion_percentage=95,
    )
    assert r.completion_percentage == 95


def test_gap_analysis():
    g = GapAnalysis(
        gaps=[],
        gap_severity="none",
        approved=True,
        overall_quality_score=9.0,
    )
    assert g.approved is True
    assert g.overall_quality_score == 9.0


def test_ceo_final_report():
    report = CEOFinalReport(
        executive_summary="Success",
        domain_results=[
            DomainResult(
                domain="research",
                summary="Done",
                quality_score=8.5,
                key_deliverables=["report"],
            )
        ],
        overall_gap_analysis="No gaps",
    )
    assert len(report.domain_results) == 1
    assert report.domain_results[0].quality_score == 8.5


def test_worker_assembly_result():
    r = WorkerAssemblyResult(
        selected_workers=[
            SelectedWorker(worker_domain="analyst"),
            SelectedWorker(worker_domain="researcher"),
        ]
    )
    assert len(r.selected_workers) == 2


# ── CEO Question Optimization ────────────────────


def test_ceo_question_optimization():
    opt = CEOQuestionOptimization(
        original_total=12,
        optimized_total=7,
        removed_duplicates=["예산 관련 중복 (research ≈ marketing)"],
        optimized_questions=[
            OptimizedDomainQuestions(domain="research", questions=["Q1?", "Q2?"]),
            OptimizedDomainQuestions(domain="marketing", questions=["Q3?"]),
        ],
        optimization_rationale="5개 중복 제거",
    )
    assert opt.original_total == 12
    assert opt.optimized_total == 7
    assert len(opt.removed_duplicates) == 1
    assert len(opt.optimized_questions) == 2
    assert opt.optimized_questions[0].domain == "research"
    assert len(opt.optimized_questions[0].questions) == 2
    data = opt.model_dump()
    assert data["optimization_rationale"] == "5개 중복 제거"


def test_optimized_domain_questions_min_length():
    """Each domain must have at least 1 question."""
    import pytest

    with pytest.raises(Exception):
        OptimizedDomainQuestions(domain="research", questions=[])


# ── CEO Generated Questions ────────────────────


def test_ceo_generated_questions():
    gen = CEOGeneratedQuestions(
        questions_by_domain=[
            OptimizedDomainQuestions(domain="research", questions=["Q1?", "Q2?"]),
            OptimizedDomainQuestions(domain="marketing", questions=["Q3?", "Q4?"]),
        ],
        total_questions=4,
        generation_rationale="2 domains selected",
    )
    assert gen.total_questions == 4
    assert len(gen.questions_by_domain) == 2
    assert gen.questions_by_domain[0].domain == "research"
    assert len(gen.questions_by_domain[0].questions) == 2
    data = gen.model_dump()
    assert data["generation_rationale"] == "2 domains selected"


# ── Task Decomposition ──────────────────────────


def test_subtask():
    s = Subtask(
        task_title="Data Collection",
        worker_type="researcher",
        objective="Collect market data",
        success_criteria=["3+ sources", "YoY comparison"],
        dependencies=[],
    )
    assert s.task_title == "Data Collection"
    assert s.worker_type == "researcher"
    data = s.model_dump()
    assert len(data["success_criteria"]) == 2
    # Verify JSON serialization for worker["plan"] storage
    json_str = s.model_dump_json()
    restored = Subtask.model_validate_json(json_str)
    assert restored.objective == "Collect market data"


def test_subtask_requires_success_criteria():
    import pytest

    with pytest.raises(Exception):
        Subtask(
            task_title="Bad",
            worker_type="researcher",
            objective="No criteria",
            success_criteria=[],
        )


def test_task_decomposition():
    td = TaskDecomposition(
        subtasks=[
            Subtask(
                task_title="Collect Data",
                worker_type="researcher",
                objective="Gather market info",
                success_criteria=["3+ sources"],
            ),
            Subtask(
                task_title="Analyze Trends",
                worker_type="data_analyst",
                objective="Find key trends",
                success_criteria=["Top 3 trends"],
                dependencies=["Collect Data"],
            ),
        ],
        decomposition_rationale="Two-phase research approach",
    )
    assert len(td.subtasks) == 2
    assert td.subtasks[1].dependencies == ["Collect Data"]
    data = td.model_dump()
    assert data["decomposition_rationale"] == "Two-phase research approach"


def test_task_decomposition_requires_subtasks():
    import pytest

    with pytest.raises(Exception):
        TaskDecomposition(subtasks=[], decomposition_rationale="Empty")


def test_subtask_max_success_criteria():
    """Subtask rejects more than 3 success criteria."""
    import pytest

    with pytest.raises(Exception):
        Subtask(
            task_title="Overloaded",
            worker_type="researcher",
            objective="Too many criteria",
            success_criteria=["c1", "c2", "c3", "c4"],
        )


def test_subtask_three_criteria_ok():
    """Subtask accepts exactly 3 success criteria."""
    s = Subtask(
        task_title="Just right",
        worker_type="researcher",
        objective="Fits in 5 minutes",
        success_criteria=["c1", "c2", "c3"],
    )
    assert len(s.success_criteria) == 3


def test_task_decomposition_max_subtasks():
    """TaskDecomposition rejects more than 7 subtasks."""
    import pytest

    subtasks = [
        Subtask(
            task_title=f"Task {i}",
            worker_type="researcher",
            objective=f"Objective {i}",
            success_criteria=["c1"],
        )
        for i in range(8)
    ]
    with pytest.raises(Exception):
        TaskDecomposition(subtasks=subtasks, decomposition_rationale="Too many")


# ── CEORoutingDecision.report_type ──────────────

def test_ceo_routing_decision_report_type_default():
    d = CEORoutingDecision(
        rationale="test",
        selected_domains=["research"],
    )
    assert d.report_type == "general"


def test_ceo_routing_decision_report_type_explicit():
    d = CEORoutingDecision(
        rationale="compare A vs B",
        selected_domains=["research"],
        report_type="comparison",
    )
    assert d.report_type == "comparison"


def test_ceo_routing_decision_report_type_invalid():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CEORoutingDecision(
            rationale="test",
            selected_domains=["research"],
            report_type="invalid_type",
        )


def test_create_initial_state_report_type_default():
    state = create_initial_state("Test task")
    assert state["report_type"] == "general"
