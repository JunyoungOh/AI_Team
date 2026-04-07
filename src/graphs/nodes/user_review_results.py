"""Node: user_review_results — human-in-the-loop review of worker execution results.

Interactive mode: Shows results summary and interrupts for user confirmation,
revision request, or abort.

Scheduled mode: Auto-skips (no interrupt — no human available).
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from src.engine import request_interrupt

from src.config.settings import get_settings
from src.utils.guards import node_error_handler
from src.utils.logging import get_logger

_logger = get_logger(agent_id="user_review_results")

# Strings treated as "confirm" (case-insensitive)
_CONFIRM_WORDS = {"확인", "ok", "ㅇㅋ", "좋아", "진행", "네", "yes", "y", ""}

# Strings treated as "abort"
_ABORT_WORDS = {"중단", "abort", "stop", "skip", "스킵", "그만"}

def _should_skip(state: dict) -> bool:
    """Determine if user review should be skipped."""
    settings = get_settings()

    # Disabled globally
    if not settings.enable_user_review:
        return True

    # Scheduled mode — no human available
    if state.get("execution_mode") == "scheduled":
        return True

    return False


def _build_results_summary(workers: list[dict]) -> tuple[str, list[dict]]:
    """Build a human-readable summary and structured worker details.

    Returns:
        (summary_text, worker_details) where worker_details is a list of
        dicts with keys: domain, leader, status, summary, pct, files, degraded.
    """
    parts = []
    total_workers = 0
    completed = 0
    failed = 0
    worker_details: list[dict] = []
    worker_summaries = []

    for w in workers:
        total_workers += 1
        status = w.get("status", "unknown")
        w_domain = w.get("worker_domain", "unknown")
        detail: dict = {
            "domain": w_domain,
            "leader": w_domain,
            "status": status,
            "summary": "",
            "pct": 0,
            "files": [],
            "degraded": status == "completed_degraded",
        }

        if status in ("completed", "completed_degraded"):
            completed += 1
            result_text = w.get("execution_result", "")
            summary = ""
            try:
                parsed = json.loads(result_text)
                summary = parsed.get("result_summary", "")[:200]
                pct = parsed.get("completion_percentage", "?")
                detail["summary"] = summary
                detail["pct"] = pct
                # Collect deliverable file paths (especially HTML)
                detail["files"] = parsed.get("deliverable_files", []) or []
                summary = f"{summary} ({pct}%)"
            except (json.JSONDecodeError, TypeError):
                summary = result_text[:200] if result_text else "결과 없음"
                detail["summary"] = summary

            marker = "[degraded] " if status == "completed_degraded" else ""
            worker_summaries.append(f"  - {w_domain}: {marker}{summary}")
        else:
            failed += 1
            detail["summary"] = f"[실패] {status}"
            worker_summaries.append(f"  - {w_domain}: [실패] {status}")

        worker_details.append(detail)

    parts.append("\n".join(worker_summaries))

    header = f"총 {total_workers}개 워커 | 완료: {completed} | 실패: {failed}"
    return header + "\n\n" + "\n\n".join(parts), worker_details


def _build_deep_research_summary(
    dr_results: list[dict],
) -> tuple[str, list[dict]]:
    """Build summary from deep_research_results when workers list is empty."""
    worker_details: list[dict] = []
    total = len(dr_results)
    completed = 0
    failed = 0

    for r in dr_results:
        domain = r.get("domain", "unknown")
        confidence = r.get("confidence_score", 0)
        findings = r.get("key_findings", [])
        sources = r.get("sources", [])
        status = "completed" if confidence >= 3 else "failed"

        if status == "completed":
            completed += 1
        else:
            failed += 1

        summary_text = (
            f"확신도 {confidence}/10, "
            f"{len(findings)}개 핵심 발견, "
            f"{len(sources)}개 출처"
        )

        worker_details.append({
            "domain": domain,
            "leader": domain,
            "status": status,
            "summary": summary_text,
            "pct": confidence * 10,
            "files": [],
            "degraded": False,
        })

    domain_names = ", ".join(r.get("domain", "?") for r in dr_results)
    header = f"총 {total}개 워커 | 완료: {completed} | 실패: {failed}"
    body = f"[{domain_names}]\n" + "\n".join(
        f"  - {d['domain']}: {d['summary']}" for d in worker_details
    )
    return header + "\n\n" + body, worker_details


def _parse_response(response) -> tuple[str, str]:
    """Parse user response into (action, feedback).

    Returns:
        ("confirm", "") - proceed to gap analysis
        ("revise", feedback) - send specific workers back for revision
        ("abort", "") - skip gap analysis, go directly to report
    """
    if isinstance(response, dict):
        action = response.get("action", "confirm")
        if action == "revise":
            return ("revise", response.get("feedback", ""))
        if action == "abort":
            return ("abort", "")
        return ("confirm", "")

    if isinstance(response, list):
        response = response[0] if response else ""

    text = str(response).strip().lower()
    if text in _CONFIRM_WORDS:
        return ("confirm", "")
    if text in _ABORT_WORDS:
        return ("abort", "")
    return ("revise", str(response).strip())


@node_error_handler("user_review_results")
def user_review_results_node(state: dict) -> dict:
    """워커 실행 결과를 사용자에게 보여주고 확인/수정/중단을 요청."""

    # Auto-skip conditions
    if _should_skip(state):
        _logger.info("user_review_skipped", reason="auto_skip")
        return {"phase": "complete"}

    workers = state.get("workers", [])
    summary, worker_details = _build_results_summary(workers)

    _logger.info("user_review_interrupt", summary_len=len(summary))

    # Collect report file paths from ceo_final_report (runs before this node)
    report_path = state.get("report_file_path", "")
    report_files = []
    if report_path:
        from pathlib import Path
        rp = Path(report_path)
        if rp.is_dir():
            report_files = [str(f) for f in sorted(rp.glob("*.html"))]

    # Instant mode: auto-approve results (no interrupt)
    _logger.info("user_review_auto_approved")
    action, feedback = "confirm", ""

    if action == "confirm":
        _logger.info("user_review_confirmed")
        return {
            "phase": "complete",
            "messages": [
                AIMessage(content="[System] 사용자가 최종 결과를 승인했습니다.")
            ],
        }
    elif action == "abort":
        _logger.info("user_review_aborted")
        return {
            "phase": "complete",
            "messages": [
                AIMessage(content="[System] 사용자가 중단을 요청했습니다. 작업을 종료합니다.")
            ],
        }
    else:
        # Revision cycle limit check
        settings = get_settings()
        max_cycles = 1 if settings.enable_worker_reflection else settings.max_result_revision_cycles
        current_cycles = state.get("iteration_counts", {}).get("result_revision_cycles", 0)

        if current_cycles >= max_cycles:
            _logger.info("revision_cycle_limit_reached", current=current_cycles, max=max_cycles)
            return {
                "phase": "complete",
                "messages": [
                    AIMessage(
                        content=f"[System] 수정 사이클 한도 도달 ({current_cycles}/{max_cycles}). 현재 결과로 완료합니다."
                    )
                ],
            }

        _logger.info("user_review_revision_requested", feedback=feedback[:100])
        return {
            "user_result_feedback": feedback,
            "phase": "worker_result_revision",
            "messages": [
                AIMessage(
                    content=f"[System] 사용자 피드백: {feedback[:200]}. 워커를 재실행합니다."
                )
            ],
        }
