"""Node: report_review — Reviewer evaluates CEO report draft quality."""

import json

from langchain_core.messages import AIMessage

from src.config.settings import get_settings
from src.models.messages import ReportReviewVerdict
from src.prompts.reviewer_prompts import REPORT_REVIEW_SYSTEM
from src.utils.guards import node_error_handler
from src.utils.logging import get_logger
from src.utils.parallel import run_async

_logger = get_logger(agent_id="report_review")

_MAX_REVIEW_CYCLES = 2


def _get_bridge():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


async def _review_report(state: dict) -> ReportReviewVerdict:
    """Reviewer evaluates CEO report draft."""
    settings = get_settings()
    bridge = _get_bridge()

    report = state.get("final_report", {})
    report_text = json.dumps(report, ensure_ascii=False, indent=2)

    # quality_criteria block (work_order removed in 2-tier refactor)
    quality_criteria_block = ""

    system = REPORT_REVIEW_SYSTEM.format(
        user_task=state.get("user_task", ""),
        report_draft=report_text[:50000],
        worker_results_summary=state.get("worker_results_summary", "")[:50000],
        quality_criteria_block=quality_criteria_block,
    )

    return await bridge.structured_query(
        system_prompt=system,
        user_message="위 CEO 리포트 초안을 4개 차원에서 평가하세요.",
        output_schema=ReportReviewVerdict,
        model=settings.ceo_model,
        allowed_tools=[],
        timeout=settings.reporter_timeout,
        effort=settings.reporter_effort,
    )


@node_error_handler("report_review")
def report_review_node(state: dict) -> dict:
    """Reviewer evaluates CEO report. Routes to revise or user_review."""
    review_count = state.get("report_review_count", 0)

    # Review with 1 retry on failure
    verdict = None
    for attempt in range(2):
        try:
            verdict = run_async(_review_report(state))
            break
        except Exception as exc:
            _logger.warning("report_review_failed", attempt=attempt + 1, error=str(exc)[:200])
    if verdict is None:
        # Both attempts failed → pass through to user (don't block pipeline)
        return {
            "phase": "user_review_results",
            "messages": [AIMessage(content="[Report Review] 리뷰 실패 — 현재 버전으로 진행합니다.")],
        }

    # Compute pass/fail from scores (overrides LLM's passed field)
    scores = [verdict.data_fidelity, verdict.logical_consistency,
              verdict.request_alignment, verdict.actionability]
    avg = sum(scores) / 4
    hard_fail = any(s <= 4 for s in scores)
    computed_pass = avg >= 7 and not hard_fail

    score_summary = (
        f"데이터충실성={verdict.data_fidelity} 논리일관성={verdict.logical_consistency} "
        f"요청부합도={verdict.request_alignment} 실행가능성={verdict.actionability} "
        f"평균={avg:.1f}"
    )

    _logger.info(
        "report_review_verdict",
        passed=computed_pass,
        scores=score_summary,
        review_count=review_count,
    )

    if computed_pass:
        return {
            "report_review_count": review_count,
            "phase": "user_review_results",
            "messages": [AIMessage(
                content=f"[Report Review] PASS — {score_summary}"
            )],
        }

    # Fail — check if we can retry
    if review_count >= _MAX_REVIEW_CYCLES:
        _logger.warning("report_review_max_cycles_reached", count=review_count)
        return {
            "report_review_count": review_count,
            "phase": "user_review_results",
            "messages": [AIMessage(
                content=f"[Report Review] FAIL (최대 {_MAX_REVIEW_CYCLES}회 도달) — 현재 버전으로 진행.\n{score_summary}"
            )],
        }

    return {
        "report_review_count": review_count + 1,
        "report_review_feedback": verdict.critique,
        "report_review_missing_data": verdict.missing_data,
        "phase": "ceo_report_revise",
        "messages": [AIMessage(
            content=f"[Report Review] FAIL — CEO 수정 요청 (#{review_count + 1})\n{score_summary}\n비평: {verdict.critique}"
        )],
    }
