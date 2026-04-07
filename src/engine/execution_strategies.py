"""Worker execution strategies — flat parallel and staged (dependency-aware).

worker_execution.py에서 추출 — 실행 전략만 담당.
_execute_with_fallback은 호출 시 주입됨 (순환 참조 방지).
"""

from __future__ import annotations

import asyncio
import time

from langchain_core.messages import AIMessage

from src.utils.dependency_graph import (
    CircularDependencyError,
    build_execution_stages,
    build_predecessor_context,
)
from src.utils.guards import safe_gather
from src.utils.logging import get_logger
from src.utils.parallel import compute_stagger_delay, run_async
from src.utils.progress import WorkerStatus

logger = get_logger(agent_id="execution_strategies")


def flat_parallel_execution(
    all_workers: list[dict],
    settings,
    tracker,
    execute_fn,
    cache=None,
    exec_tracker=None,
    estimated_complexity: str = "medium",
    research_context: str = "",
    session_id: str = "",
    upstream_context: str = "",
) -> dict:
    """Run all workers in parallel — no dependency ordering."""
    flat_timeout = settings.parallel_task_timeout
    if len(all_workers) <= 1:
        if all_workers:
            w = all_workers[0]
            results = run_async(safe_gather(
                [execute_fn(
                    w, cache=cache, exec_tracker=exec_tracker,
                    estimated_complexity=estimated_complexity,
                    time_budget=float(flat_timeout),
                    research_context=research_context,
                    session_id=session_id,
                    upstream_context=upstream_context,
                )],
                timeout_seconds=flat_timeout,
                description="worker_execution_single",
            ))
            return reassemble_results(results, all_workers, tracker)
        return {"phase": "ceo_final_report"}

    stagger_delay = compute_stagger_delay(len(all_workers))

    async def _run():
        async def _staggered_execute(worker, index):
            if index > 0 and stagger_delay > 0:
                await asyncio.sleep(stagger_delay * index)
            worker_budget = flat_timeout - (stagger_delay * index)
            return await execute_fn(
                worker, cache=cache, exec_tracker=exec_tracker,
                estimated_complexity=estimated_complexity,
                time_budget=worker_budget,
                research_context=research_context,
                session_id=session_id,
                upstream_context=upstream_context,
            )

        coros = [_staggered_execute(w, i) for i, w in enumerate(all_workers)]
        return await safe_gather(
            coros,
            timeout_seconds=settings.parallel_task_timeout,
            description="worker_execution",
        )

    results = run_async(_run())
    return reassemble_results(results, all_workers, tracker)


def staged_execution(
    all_workers: list[dict],
    settings,
    tracker,
    execute_fn,
    cache=None,
    exec_tracker=None,
    estimated_complexity: str = "medium",
    research_context: str = "",
    session_id: str = "",
    upstream_context: str = "",
) -> dict:
    """Run workers in dependency-ordered stages (Kahn's algorithm)."""
    try:
        stages = build_execution_stages(all_workers)
    except CircularDependencyError as e:
        logger.warning("circular_dependency_fallback_to_parallel", details=str(e))
        return flat_parallel_execution(
            all_workers, settings, tracker, execute_fn,
            cache=cache, exec_tracker=exec_tracker,
            estimated_complexity=estimated_complexity,
            session_id=session_id,
            upstream_context=upstream_context,
        )

    MAX_STAGES = 2
    if len(stages) > MAX_STAGES:
        logger.warning("excessive_stages_flattened", original=len(stages), limit=MAX_STAGES)
        overflow = []
        for s in stages[MAX_STAGES:]:
            overflow.extend(s)
        stages = stages[:MAX_STAGES]
        stages[-1].extend(overflow)

    logger.info(
        "staged_execution_plan",
        num_stages=len(stages),
        stages=[[all_workers[i].get("worker_domain", f"w{i}") for i in stage] for stage in stages],
    )

    first_stage_set = set(stages[0]) if stages else set()
    for i, w in enumerate(all_workers):
        if i not in first_stage_set:
            tracker.update(w.get("worker_id", w["worker_domain"]), WorkerStatus.WAITING)

    pipeline_start = time.monotonic()
    completed_results: dict[int, str] = {}
    all_stage_results: list[tuple[bool, tuple[dict, AIMessage] | Exception]] = []
    idx_to_result_pos: dict[int, int] = {}

    for stage_num, stage_indices in enumerate(stages):
        elapsed = time.monotonic() - pipeline_start
        if elapsed > settings.max_total_staged_timeout:
            logger.warning(
                "staged_execution_pipeline_timeout",
                elapsed=elapsed, max_timeout=settings.max_total_staged_timeout,
                remaining_stages=len(stages) - stage_num,
            )
            for remaining_stage in stages[stage_num:]:
                for idx in remaining_stage:
                    domain = all_workers[idx]["worker_domain"]
                    w_id = all_workers[idx].get("worker_id", domain)
                    tracker.update(w_id, WorkerStatus.FAILED, summary="pipeline timeout")
                    updated_w = dict(all_workers[idx])
                    updated_w["execution_result"] = "[Execution skipped: pipeline timeout]"
                    updated_w["status"] = "failed"
                    pos = len(all_stage_results)
                    idx_to_result_pos[idx] = pos
                    all_stage_results.append((True, (updated_w, AIMessage(
                        content=f"[{domain}] Skipped: pipeline timeout after {int(elapsed)}s"
                    ))))
            break

        logger.info(
            "stage_starting", stage=stage_num,
            workers=[all_workers[i].get("worker_domain", f"w{i}") for i in stage_indices],
        )

        pred_contexts: dict[int, str] = {}
        for idx in stage_indices:
            pred_ctx = build_predecessor_context(all_workers, completed_results, idx)
            pred_contexts[idx] = pred_ctx
            if pred_ctx:
                logger.info("context_injected", worker=all_workers[idx].get("worker_domain"), predecessor_len=len(pred_ctx))

        pipeline_remaining = settings.max_total_staged_timeout - elapsed
        effective_stage_timeout = max(60, min(settings.stage_timeout, int(pipeline_remaining)))
        stagger_delay = compute_stagger_delay(len(stage_indices))

        async def _run_stage(
            indices, contexts, _stagger=stagger_delay, _stage=stage_num,
            _cache=cache, _exec_tracker=exec_tracker,
            _complexity=estimated_complexity,
            _effective_stage_timeout=effective_stage_timeout,
            _research_ctx=research_context,
            _upstream_ctx=upstream_context,
        ):
            async def _staggered(idx, order):
                if order > 0 and _stagger > 0:
                    await asyncio.sleep(_stagger * order)
                worker_budget = _effective_stage_timeout - (_stagger * order)
                return await execute_fn(
                    all_workers[idx],
                    predecessor_context=contexts.get(idx, ""),
                    cache=_cache, exec_tracker=_exec_tracker,
                    stage=_stage, estimated_complexity=_complexity,
                    time_budget=worker_budget,
                    research_context=_research_ctx,
                    session_id=session_id,
                    upstream_context=_upstream_ctx,
                )

            coros = [_staggered(idx, i) for i, idx in enumerate(indices)]
            return await safe_gather(
                coros, timeout_seconds=_effective_stage_timeout,
                description=f"stage_{_stage}",
            )

        stage_results = run_async(_run_stage(stage_indices, pred_contexts))

        for idx, (success, result_or_error) in zip(stage_indices, stage_results):
            pos = len(all_stage_results)
            idx_to_result_pos[idx] = pos

            if success:
                updated_w, msg = result_or_error
                all_stage_results.append((True, (updated_w, msg)))
                completed_results[idx] = updated_w.get("execution_result", "")
            else:
                all_stage_results.append((False, result_or_error))
                completed_results[idx] = f"[Execution failed: {result_or_error}]"

    ordered_results = []
    for i in range(len(all_workers)):
        pos = idx_to_result_pos.get(i)
        if pos is not None:
            ordered_results.append(all_stage_results[pos])
        else:
            ordered_results.append((False, RuntimeError(f"Worker {i} was never executed")))

    return reassemble_results(ordered_results, all_workers, tracker)


def reassemble_results(
    results: list[tuple[bool, tuple[dict, AIMessage] | Exception]],
    all_workers: list[dict],
    tracker,
) -> dict:
    """Reassemble worker results back into flat workers list."""
    updated_workers = list(all_workers)
    messages = []

    for i, (success, result_or_error) in enumerate(results):
        if success:
            updated_w, msg = result_or_error
            updated_workers[i] = updated_w
            messages.append(msg)
        else:
            worker = updated_workers[i]
            domain = worker["worker_domain"]
            tracker.update(worker.get("worker_id", domain), WorkerStatus.FAILED, summary=str(result_or_error)[:50])
            updated_w = dict(worker)
            updated_w["execution_result"] = f"[Execution failed: {result_or_error}]"
            updated_w["status"] = "completed"
            updated_workers[i] = updated_w
            messages.append(AIMessage(
                content=f"[{domain}] Execution failed: {result_or_error}"
            ))

    return {
        "workers": updated_workers,
        "messages": messages,
        "phase": "ceo_final_report",
    }
