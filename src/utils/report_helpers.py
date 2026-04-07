"""Report generation helpers — HTML wrapping, result parsing, quality assessment.

ceo_final_report.py에서 추출 — 노드 로직과 무관한 유틸리티.
"""

from __future__ import annotations

import json
import re

from src.config.settings import get_settings
from src.prompts.reporter_prompts import REPORT_HTML_SYSTEM
from src.utils.logging import get_logger

logger = get_logger(agent_id="report_helpers")


def get_bridge_impl():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


def wrap_ceo_html(html_body: str, user_task: str) -> str:
    """Wrap CEO-generated HTML fragment in a full document structure."""
    if html_body.strip().lower().startswith("<!doctype") or html_body.strip().lower().startswith("<html"):
        return html_body
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{user_task[:100]}</title>
<style>
body {{ font-family: 'Apple SD Gothic Neo','Noto Sans KR','Segoe UI',sans-serif; margin: 0; padding: 20px; background: #f4f6f9; color: #1a1a2e; }}
.report-wrapper {{ max-width: 1100px; margin: 0 auto; background: #fff; box-shadow: 0 4px 40px rgba(0,0,0,0.10); border-radius: 12px; overflow: hidden; }}
</style>
</head>
<body>
<div class="report-wrapper">
{html_body}
</div>
</body>
</html>"""


async def generate_report_html(
    user_task: str,
    executive_summary: str,
    worker_results_summary: str,
) -> str:
    """Generate HTML report via raw text LLM call."""
    settings = get_settings()
    bridge = get_bridge_impl()

    system = REPORT_HTML_SYSTEM.format(
        user_task=user_task,
        executive_summary=executive_summary,
        worker_results_summary=worker_results_summary[:50000],
    )

    html = await bridge.raw_query(
        system_prompt=system,
        user_message="위 데이터를 기반으로 self-contained HTML 보고서를 작성하세요. HTML 코드만 출력하세요.",
        model=settings.ceo_model,
        allowed_tools=[],
        max_turns=1,
        timeout=settings.reporter_timeout,
        effort=settings.reporter_effort,
    )
    return html.strip()


def parse_result(raw) -> dict:
    """Parse worker execution_result from JSON string or dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            return {"result_summary": raw, "completion_percentage": 0}
    return {"result_summary": "(결과 없음)", "completion_percentage": 0}


def strip_agent_refs(text: str) -> str:
    """Remove leading agent self-references from text."""
    return re.sub(
        r'^(deep_researcher|researcher|data_analyst|analyst|reporter|'
        r'developer|designer|planner|strategist|writer)\s*[는가은이]\s*',
        '', text, flags=re.IGNORECASE,
    )


def count_completed_workers(state: dict) -> int:
    return sum(
        1 for w in state.get("workers", [])
        if w.get("status", "") in ("completed", "completed_degraded")
    )


def assess_result_quality(report: dict) -> tuple[int, list[str]]:
    """Assess quality of a single-worker fallback report.

    Returns (adjusted_score, warning_reasons).
    """
    warnings: list[str] = []
    score = 7

    domains = report.get("domain_results", [])
    if not domains:
        return 1, ["No domain results produced"]

    dr = domains[0]
    summary = dr.get("summary", "")
    deliverables = dr.get("key_deliverables", [])
    quality_from_gap = dr.get("quality_score", 7)

    if dr.get("reflection_passed", False):
        if not deliverables:
            score = min(score, 5)
            warnings.append("No key deliverables produced")
        if quality_from_gap < score:
            score = quality_from_gap
        return score, warnings

    if not summary or summary == "No results available.":
        score = 2
        warnings.append("Worker produced no result summary")
    elif len(summary) < 100:
        score = min(score, 4)
        warnings.append(f"Result summary too short ({len(summary)} chars)")

    failure_indicators = [
        "사용할 수 없", "접근할 수 없", "차단", "실패",
        "unable to", "could not", "failed to", "cannot access",
        "blocked", "permission denied", "not available",
        "Playwright", "브라우저를 사용",
    ]
    failure_hits = sum(1 for ind in failure_indicators if ind.lower() in summary.lower())
    if failure_hits >= 2:
        score = min(score, 3)
        warnings.append(f"Summary dominated by tool failure descriptions ({failure_hits} indicators)")

    if not deliverables:
        score = min(score, 5)
        warnings.append("No key deliverables produced")

    if quality_from_gap < score:
        score = quality_from_gap

    return score, warnings
