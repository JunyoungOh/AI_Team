"""Core state definitions for the Enterprise Agent System.

The EnterpriseAgentState is the single TypedDict that flows through every node
in the LangGraph StateGraph. It captures the entire lifecycle of a task.

Flow: intake → ceo_route → ceo_questions → await_user_answers
      → ceo_task_decomposition → worker_execution
      → ceo_final_report → report_review → user_review_results → END/revision
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langchain_core.messages import BaseMessage


# Valid status values for type safety
WorkerStatus = Literal[
    "planning", "plan_submitted", "executing", "completed",
    "completed_degraded", "failed", "revision_needed",
]


class WorkerReport(TypedDict):
    """State for an individual worker agent's task."""

    worker_id: str
    worker_domain: str
    role_type: str  # "planner" | "executor" | "reviewer"
    plan: str
    execution_result: str
    status: WorkerStatus
    revision_feedback: str
    revision_count: int


class EnterpriseAgentState(TypedDict):
    """Top-level state for the entire enterprise agent workflow.

    This TypedDict parameterizes the LangGraph StateGraph.
    The ``messages`` field uses the ``add_messages`` reducer (append-only).
    All other fields use last-writer-wins (default).
    """

    messages: list[BaseMessage]
    user_task: str
    phase: str
    # Workers — flat list, CEO creates directly
    workers: list[WorkerReport]
    selected_domains: list[str]  # CEO가 선택한 도메인 (질문 생성용)
    clarifying_questions: list[str]  # CEO가 생성한 명확화 질문
    ceo_routing_rationale: str
    final_report: dict[str, Any]  # CEOFinalReport parsed dict
    iteration_counts: dict[str, Any]
    error_message: str
    # Scheduler support
    execution_mode: str  # "interactive" (default) or "scheduled"
    pre_context: dict[str, Any]  # Pre-supplied context for headless execution
    # Report export
    report_file_path: str
    session_id: str
    # Complexity
    estimated_complexity: str  # "low" / "medium" / "high" from CEO routing
    # User feedback (multi-turn collaboration at the end)
    user_result_feedback: str
    # Execution metrics (cost/time tracking)
    execution_metrics: dict[str, Any]
    # Research-First Pipeline
    research_data_full: str
    research_data_summary: str
    # Deep Research mode
    deep_research_mode: bool
    deep_research_results: list[dict[str, Any]]
    # Report visualization type classification
    report_type: str  # "comparison" | "market_research" | "trend_analysis" | "strategic" | "technical" | "general"
    # Planner-Executor-Reviewer loop blackboard
    blackboard: dict[str, Any]  # PipelineBlackboard.to_dict() — 루프 간 결과 누적
    # Deep Research dual mode
    deep_research_strategy: str          # "breadth" | "depth" | ""
    deep_research_sub_queries: list[str]
    # User answers (collected during ceo_questions phase)
    user_answers: list[str]
    # Report review loop
    report_review_count: int
    report_review_feedback: str
    report_review_missing_data: list[str]
    worker_results_summary: str


def create_initial_state(
    user_task: str,
    execution_mode: str = "interactive",
    pre_context: dict | None = None,
    session_id: str = "",
) -> dict:
    """Create the initial state dict for a new task execution."""
    return {
        "messages": [],
        "user_task": user_task,
        "phase": "intake",
        "workers": [],
        "selected_domains": [],
        "clarifying_questions": [],
        "ceo_routing_rationale": "",
        "final_report": {},
        "iteration_counts": {
            "ceo_rejections": 0,
            "escalation_count": 0,
            "result_revision_cycles": 0,
        },
        "error_message": "",
        "execution_mode": execution_mode,
        "pre_context": pre_context or {},
        "report_file_path": "",
        "session_id": session_id,
        "estimated_complexity": "",
        "user_result_feedback": "",
        "execution_metrics": {},
        "report_type": "general",
        "user_answers": [],
        "report_review_count": 0,
        "report_review_feedback": "",
        "report_review_missing_data": [],
        "worker_results_summary": "",
        "blackboard": {},
    }


def create_worker_report(
    worker_id: str,
    worker_domain: str,
    role_type: str = "executor",
) -> WorkerReport:
    """Create an initial WorkerReport for a newly assigned worker."""
    return WorkerReport(
        worker_id=worker_id,
        worker_domain=worker_domain,
        role_type=role_type,
        plan="",
        execution_result="",
        status="planning",
        revision_feedback="",
        revision_count=0,
    )
