"""Node 14: worker_result_revision - workers revise results based on gap analysis.

PARALLEL: Workers needing revision execute simultaneously via safe_gather.

3-tier execution fallback:
  T1 (Full):     Normal execution with tools   — 360s (handled in worker_execution)
  T2 (Degraded): Knowledge-only, no tools      — 120s (first-time failures get this retry)
  T3 (Skip):     Permanently failed, no retry   — (revision already attempted and failed)
"""

from langchain_core.messages import AIMessage

from src.agents.factory import create_worker
from src.config.settings import get_settings
from src.utils.guards import node_error_handler, increment_loop_counter, safe_gather
from src.utils.logging import get_logger
from src.utils.parallel import compute_stagger_delay, run_async

_logger = get_logger(agent_id="worker_result_revision")


def _needs_degraded_retry(worker: dict) -> bool:
    """First-time failure (T1 failed) — worth trying degraded mode (T2)."""
    result = worker.get("execution_result", "")
    return result.startswith("[Execution failed:")


def _is_permanently_failed(worker: dict) -> bool:
    """Already retried at least once — no more retries (T3)."""
    result = worker.get("execution_result", "")
    return result.startswith("[Revision failed:")


async def _revise_single_worker(
    worker: dict, gap_analysis: str
) -> tuple[dict, AIMessage]:
    """Revise a single worker's result — full mode with tools (async)."""
    agent = create_worker(worker["worker_domain"], tool_category=worker.get("tool_category"))
    revised_plan = f"{worker['plan']}\n\n[Gap analysis feedback]\n{gap_analysis}"
    result = await agent.aexecute_plan(approved_plan=revised_plan)

    updated_w = dict(worker)
    updated_w["execution_result"] = result.model_dump_json()
    updated_w["status"] = "completed"
    msg = AIMessage(
        content=f"[{worker['worker_domain']}] Result revision complete: {result.result_summary}"
    )
    return updated_w, msg


async def _degraded_revise_worker(
    worker: dict, gap_analysis: str
) -> tuple[dict, AIMessage]:
    """Degraded-mode retry: no tools, shorter timeout (T2)."""
    agent = create_worker(worker["worker_domain"], tool_category=worker.get("tool_category"))
    agent.allowed_tools = []  # No tools → timeout becomes llm_call_timeout (120s)

    degraded_plan = (
        f"{worker['plan']}\n\n"
        f"[Gap analysis feedback]\n{gap_analysis}\n\n"
        "[DEGRADED MODE - 도구 사용 불가]\n"
        "이전 실행이 시간 초과로 실패했습니다. "
        "외부 도구(웹 검색, 파일 작업 등) 없이 당신의 전문 지식만으로 "
        "최선의 분석, 전략, 권고사항을 작성하세요. "
        "구체적인 데이터 수집 없이도 전문가 관점의 가치 있는 결과를 제공할 수 있습니다."
    )
    result = await agent.aexecute_plan(approved_plan=degraded_plan)

    updated_w = dict(worker)
    updated_w["execution_result"] = result.model_dump_json()
    updated_w["status"] = "completed_degraded"
    msg = AIMessage(
        content=(
            f"[{worker['worker_domain']}] Degraded revision complete "
            f"(knowledge-only): {result.result_summary}"
        )
    )
    return updated_w, msg


@node_error_handler("worker_result_revision")
def worker_result_revision_node(state: dict) -> dict:
    """Workers re-execute based on gap analysis feedback — IN PARALLEL.

    3-tier fallback:
      - Normal workers → full revision with tools
      - First-time failures ([Execution failed:]) → degraded retry (no tools, 120s)
      - Second-time failures ([Revision failed:]) → permanently skip

    Increments result_revision_cycles counter each time.
    Individual worker failures are isolated — they don't crash the entire node.
    """
    workers = state.get("workers", [])

    # Separate approved workers from those needing revision
    approved_workers = []
    revision_workers = []
    for worker in workers:
        if worker.get("result_quality_approved"):
            approved_workers.append(worker)
        else:
            revision_workers.append(worker)

    if not revision_workers:
        return {
            "workers": list(workers),
            "phase": "ceo_final_report",
            "iteration_counts": increment_loop_counter(state, "result_revision_cycles"),
        }

    # Append user feedback to gap analysis if present
    user_feedback = state.get("user_result_feedback", "")
    gap_analysis = state.get("gap_analysis", "")
    if user_feedback:
        gap_analysis = f"{gap_analysis}\n\n[사용자 피드백]\n{user_feedback}"

    # Classify workers into 3 tiers
    full_retry = []       # Normal workers → full revision
    full_retry_index = []
    degraded_retry = []   # First-time failures → degraded mode (T2)
    degraded_retry_index = []
    skipped_msgs = []

    for wi, worker in enumerate(revision_workers):
        if _is_permanently_failed(worker):
            # T3: Already retried — skip entirely
            _logger.info(
                "skip_permanently_failed",
                worker_domain=worker["worker_domain"],
                reason="revision already attempted — T3 skip",
            )
            skipped_msgs.append(AIMessage(
                content=f"[{worker['worker_domain']}] Skipped (revision already failed)"
            ))
        elif _needs_degraded_retry(worker):
            # T2: First-time failure → degraded knowledge-only retry
            _logger.info(
                "degraded_retry",
                worker_domain=worker["worker_domain"],
                reason="initial execution failed — trying degraded mode (T2)",
            )
            degraded_retry.append((worker, gap_analysis))
            degraded_retry_index.append(wi)
        else:
            # T1 result → normal full revision
            full_retry.append((worker, gap_analysis))
            full_retry_index.append(wi)

    all_retryable = full_retry + degraded_retry
    # If ALL workers were skipped, force-approve and proceed
    if not all_retryable:
        _logger.warning("all_workers_failed_force_approve", workers=len(revision_workers))
        force_approved = []
        for worker in revision_workers:
            updated_w = dict(worker)
            updated_w["result_quality_approved"] = True
            updated_w["auto_approved_reason"] = "all workers permanently failed"
            force_approved.append(updated_w)
        return {
            "workers": approved_workers + force_approved,
            "messages": skipped_msgs + [AIMessage(
                content="[system] All workers failed — force-approving to proceed to final report."
            )],
            "phase": "ceo_final_report",
            "iteration_counts": increment_loop_counter(state, "result_revision_cycles"),
        }

    # Build parallel coroutines — mixing full and degraded retries with stagger
    settings = get_settings()
    stagger_delay = compute_stagger_delay(len(all_retryable))

    async def _run():
        import asyncio

        async def _staggered(coro, index):
            if index > 0 and stagger_delay > 0:
                await asyncio.sleep(stagger_delay * index)
            return await coro

        coros = []
        for w, gap in full_retry:
            coros.append(_staggered(_revise_single_worker(w, gap), len(coros)))
        for w, gap in degraded_retry:
            coros.append(_staggered(_degraded_revise_worker(w, gap), len(coros)))
        return await safe_gather(
            coros,
            timeout_seconds=settings.parallel_task_timeout,
            description="worker_result_revision",
        )

    # Single worker: still run via safe_gather for uniform error handling
    results = run_async(_run())

    # Reassemble — handle partial failures
    updated_revision_workers = [dict(w) for w in revision_workers]
    for uw in updated_revision_workers:
        uw["result_quality_approved"] = False

    combined_index = full_retry_index + degraded_retry_index
    messages = list(skipped_msgs)
    for idx, (success, result_or_error) in zip(combined_index, results):
        if success:
            updated_w, msg = result_or_error
            updated_revision_workers[idx] = updated_w
            messages.append(msg)
        else:
            worker = updated_revision_workers[idx]
            _logger.error(
                "worker_revision_failed",
                worker_domain=worker["worker_domain"],
                error=str(result_or_error),
            )
            updated_w = dict(worker)
            updated_w["execution_result"] = f"[Revision failed: {result_or_error}]"
            updated_w["status"] = "completed"
            updated_revision_workers[idx] = updated_w
            messages.append(AIMessage(
                content=f"[{worker['worker_domain']}] Revision failed: {result_or_error}"
            ))

    return {
        "workers": approved_workers + updated_revision_workers,
        "messages": messages,
        "phase": "ceo_final_report",
        "iteration_counts": increment_loop_counter(state, "result_revision_cycles"),
    }
