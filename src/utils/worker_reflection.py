"""Worker result reflection (Producer-Critic pattern).

Haiku가 워커 결과를 평가하고, 기준 미달 시 repair agent가 보완.
worker_execution.py에서 추출 — reflection 로직만 담당.
"""

from __future__ import annotations

import asyncio
import json as _json
import time

from src.agents.factory import create_worker
from src.agents.worker import _SEARCH_HEAVY_TYPES
from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(agent_id="worker_reflection")


# ── Reflection helpers ─────────────────────────────────

def extract_success_criteria(plan_json: str) -> list[str]:
    """Extract success_criteria from plan JSON. Returns [] if unavailable."""
    try:
        data = _json.loads(plan_json)
    except (ValueError, TypeError):
        return []
    return data.get("success_criteria", [])


_FAILURE_INDICATORS = [
    "실패", "사용할 수 없", "접근할 수 없", "차단",
    "unable to", "could not", "failed to", "cannot access",
    "blocked", "permission denied", "not available",
]


def count_failure_indicators(text: str) -> int:
    lower = text.lower()
    return sum(1 for ind in _FAILURE_INDICATORS if ind.lower() in lower)


def no_failure_regression(repair, original) -> bool:
    """Return True if repair does not have MORE failure indicators than original."""
    return count_failure_indicators(repair.result_summary) <= count_failure_indicators(original.result_summary)


async def reflect_on_result(
    worker: dict,
    result,  # WorkerResult
    time_budget: float | None,
    tier1_elapsed: float,
    exec_tracker=None,
) -> tuple[object, str]:
    """Evaluate worker result via Haiku reflection. Returns (final_result, reflection_status).

    reflection_status: "skipped"|"pass"|"fail_repaired"|"fail_kept_original"
    """
    from src.models.messages import ReflectionVerdict, WorkerResult
    from src.prompts.reflection_prompts import (
        WORKER_REFLECTION_SYSTEM, WORKER_REFLECTION_USER, REPAIR_CONTEXT_TEMPLATE,
    )

    settings = get_settings()
    domain = worker["worker_domain"]

    # Check if reflection applies
    if not settings.enable_worker_reflection:
        return result, "skipped"
    if domain not in _SEARCH_HEAVY_TYPES:
        return result, "skipped"

    # Sanity check (no LLM call)
    sanity_fail = (
        result.completion_percentage < 30
        or len(result.deliverables) == 0
        or len(result.result_summary) < 100
    )

    # Extract criteria
    plan_json = worker.get("plan", "")
    criteria = extract_success_criteria(plan_json)
    haiku_elapsed = 0.0

    if sanity_fail:
        critique = "Sanity check 실패: 결과가 비어있거나 극히 부족합니다."
        failed_criteria = criteria
    elif not criteria:
        return result, "pass"
    else:
        # Haiku evaluation
        haiku_start = time.monotonic()
        try:
            from src.utils.bridge_factory import get_bridge
            bridge = get_bridge()
            deliverables_text = "\n".join(
                f"- {d[:300]}" for d in result.deliverables[:5]
            )
            user_msg = WORKER_REFLECTION_USER.format(
                success_criteria="\n".join(f"- {c}" for c in criteria),
                result_summary=result.result_summary[:1000],
                deliverable_count=len(result.deliverables),
                deliverables_text=deliverables_text,
            )
            verdict = await bridge.structured_query(
                system_prompt=WORKER_REFLECTION_SYSTEM,
                user_message=user_msg,
                output_schema=ReflectionVerdict,
                model=settings.reflection_model,
                allowed_tools=[],
                timeout=settings.reflection_timeout,
            )
            haiku_elapsed = time.monotonic() - haiku_start
        except Exception as e:
            logger.warning("reflection_haiku_failed", worker=domain, error=str(e)[:100])
            return result, "pass"

        if verdict.passed:
            return result, "pass"

        critique = verdict.critique
        failed_criteria = verdict.failed_criteria

    # Time budget check
    if time_budget is not None:
        repair_budget = time_budget - tier1_elapsed - haiku_elapsed
    else:
        repair_budget = float(settings.execution_timeout) - tier1_elapsed - haiku_elapsed

    if repair_budget < settings.reflection_min_repair_budget:
        logger.warning(
            "reflection_skip_repair_no_budget",
            worker=domain, repair_budget=round(repair_budget, 1),
        )
        result.reflection_passed = False
        return result, "fail_kept_original"

    # Repair worker
    logger.info(
        "reflection_repair_start",
        worker=domain, critique=critique[:100],
        repair_budget=round(repair_budget, 1),
    )
    try:
        repair_context = REPAIR_CONTEXT_TEMPLATE.format(
            original_summary=result.result_summary[:1500],
            original_deliverables="\n".join(f"- {d[:200]}" for d in result.deliverables[:5]),
            critique=critique,
            failed_criteria="\n".join(f"- {c}" for c in failed_criteria),
        )
        tool_category = worker.get("tool_category")
        repair_agent = create_worker(domain, tool_category=tool_category, worker_name=worker.get("worker_name", ""))
        repair_result = await repair_agent.aexecute_plan(
            approved_plan=plan_json,
            predecessor_context=repair_context,
            time_budget=repair_budget,
        )
    except asyncio.CancelledError:
        logger.warning("reflection_repair_cancelled", worker=domain)
        result.reflection_passed = False
        return result, "fail_kept_original"
    except Exception as e:
        logger.warning("reflection_repair_failed", worker=domain, error=str(e)[:100])
        result.reflection_passed = False
        return result, "fail_kept_original"

    # Compare original vs repair
    repair_better = (
        repair_result.completion_percentage >= result.completion_percentage
        and len(repair_result.deliverables) >= len(result.deliverables)
        and len(repair_result.result_summary) >= len(result.result_summary)
        and no_failure_regression(repair_result, result)
    )

    if repair_better:
        repair_result.reflection_passed = True
        repair_result.reflection_repaired = True
        logger.info("reflection_repair_adopted", worker=domain)
        return repair_result, "fail_repaired"
    else:
        result.reflection_passed = False
        logger.info("reflection_repair_rejected", worker=domain)
        return result, "fail_kept_original"
