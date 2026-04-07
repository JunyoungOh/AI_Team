"""Node: ceo_report_revise — CEO revises report based on Reviewer feedback."""

import json

from langchain_core.messages import AIMessage

from src.config.personas import CEO_PERSONA, format_persona_block
from src.config.settings import get_settings
from src.models.messages import CEOFinalReport
from src.utils.guards import node_error_handler
from src.utils.logging import get_logger
from src.utils.parallel import run_async

_logger = get_logger(agent_id="ceo_report_revise")

_REVISE_SYSTEM = """\
{persona_block}

당신은 CEO입니다. Reviewer의 피드백을 반영하여 리포트를 수정합니다.

## 원본 요청
{user_task}

## 현재 리포트 초안
{current_report}

## Reviewer 피드백
{critique}

## 누락 데이터
{missing_data}

## Worker 원재료
{worker_results_summary}

## 지시
1. Reviewer가 지적한 문제를 구체적으로 수정하세요.
2. 누락 데이터가 있으면 Worker 원재료에서 찾아 리포트에 반영하세요.
3. 기존 리포트의 좋은 부분은 유지하세요.
4. executive_summary, domain_results, recommendations 구조를 유지하세요.
"""


def _get_bridge():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


async def _revise_report(state: dict) -> CEOFinalReport:
    """CEO revises report with Reviewer feedback."""
    settings = get_settings()
    bridge = _get_bridge()

    report = state.get("final_report", {})
    report_text = json.dumps(report, ensure_ascii=False, indent=2)

    missing_data = state.get("report_review_missing_data", [])
    missing_text = "\n".join(f"- {d}" for d in missing_data) if missing_data else "(없음)"

    system = _REVISE_SYSTEM.format(
        persona_block=format_persona_block(CEO_PERSONA),
        user_task=state.get("user_task", ""),
        current_report=report_text[:50000],
        critique=state.get("report_review_feedback", ""),
        missing_data=missing_text,
        worker_results_summary=state.get("worker_results_summary", "")[:50000],
    )

    return await bridge.structured_query(
        system_prompt=system,
        user_message="Reviewer 피드백을 반영하여 리포트를 수정하세요.",
        output_schema=CEOFinalReport,
        model=settings.ceo_model,
        allowed_tools=[],
        timeout=settings.reporter_timeout,
        effort=settings.reporter_effort,
    )


@node_error_handler("ceo_report_revise")
def ceo_report_revise_node(state: dict) -> dict:
    """CEO revises the report based on Reviewer feedback."""
    try:
        result = run_async(_revise_report(state))
        report = result.model_dump()
    except Exception as exc:
        _logger.warning("ceo_report_revise_failed", error=str(exc)[:200])
        # Revision failure → proceed with current version for re-evaluation
        return {
            "phase": "report_review",
            "messages": [AIMessage(content="[CEO Revise] 수정 실패 — 현재 버전으로 재평가합니다.")],
        }

    # Save CEO-generated HTML directly
    report_path = ""
    ceo_html = report.get("report_html", "")
    if ceo_html and len(ceo_html) > 50:
        try:
            import os
            from src.graphs.nodes.ceo_final_report import _wrap_ceo_html
            session_id = state.get("session_id", "unknown")
            report_dir = os.path.join("data/reports", session_id)
            os.makedirs(report_dir, exist_ok=True)
            report_path = report_dir
            rp = os.path.join(report_dir, "results.html")
            with open(rp, "w", encoding="utf-8") as f:
                f.write(_wrap_ceo_html(ceo_html, state.get("user_task", "")))
        except Exception:
            _logger.warning("ceo_revise_html_save_failed", exc_info=True)

    _logger.info("ceo_report_revised", review_count=state.get("report_review_count", 0))

    return {
        "final_report": report,
        "report_file_path": report_path,
        "phase": "report_review",
        "messages": [AIMessage(content="[CEO Revise] 리포트 수정 완료 — 재평가 진행.")],
    }
