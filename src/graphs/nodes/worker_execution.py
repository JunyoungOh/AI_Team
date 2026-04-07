"""Node: worker_execution - workers execute their approved plans.

Supports two execution modes:
  1. Flat parallel — all workers run simultaneously (legacy, default when no dependencies)
  2. Staged execution — dependency-aware topological ordering (Kahn's algorithm)

Tools are delegated to Claude Code (no manual ReAct loop).

Resilience: 3-tier fallback per worker:
  Tier 1 — Full execution with tools (execution_timeout)
  Tier 2 — Degraded execution without tools (degraded_timeout)
  Tier 3 — Skip with error message

Enhancements:
  - WorkerResultCache: skip duplicate execution for identical (domain, plan)
  - ExecutionTracker: record per-worker time, tier, model for metrics
"""

import asyncio
import json as _json
import os
import time

from langchain_core.messages import AIMessage

from src.agents.factory import create_worker
from src.config.settings import get_settings
from src.utils.claude_code import ClaudeCodeError, ClaudeCodeTimeoutError, SubprocessMetrics
from src.utils.dependency_graph import has_any_dependencies
from src.utils.execution_tracker import ExecutionTracker, get_exec_tracker
from src.utils.guards import node_error_handler, safe_gather
from src.utils.logging import get_logger
from src.utils.parallel import run_async
from src.utils.progress import WorkerStatus, get_tracker
from src.utils.worker_cache import WorkerResultCache
from src.engine.review_loop import has_pesr_roles, _ensure_four_roles, run_pesr_loop

logger = get_logger(agent_id="worker_execution")


# ── Upstream context builder ──────────────────────────


def _build_upstream_context(
    user_task: str,
    user_answers: list | None = None,
    clarifying_questions: list | None = None,
    **_kwargs,  # Legacy compat: active_leaders, work_order silently ignored
) -> str:
    """Build upstream context string for Worker prompt injection."""
    parts = [f"- 원본 요청: {user_task}"]

    questions = clarifying_questions or []
    all_answers = user_answers or []
    if questions and all_answers:
        qa_lines = []
        for i, q in enumerate(questions):
            a = all_answers[i] if i < len(all_answers) else "(미답변)"
            qa_lines.append(f"  - {q} → {a}")
        parts.append("- 사용자 Q&A:\n" + "\n".join(qa_lines))
    elif all_answers:
        parts.append("- 사용자 답변:\n" + "\n".join(f"  - {a}" for a in all_answers))

    return "\n".join(parts)


# ── Result file persistence ──────────────────────────


def _save_result_file(
    execution_result: str,
    worker_id: str,
    session_id: str,
    output_dir: str,
) -> str:
    """Save worker execution result to file. Returns file path."""
    result_dir = os.path.join(output_dir, session_id or "default")
    os.makedirs(result_dir, exist_ok=True)
    result_path = os.path.join(result_dir, f"{worker_id}.json")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(execution_result)
    return result_path


# ── Reflection (extracted to src/utils/worker_reflection.py) ──
from src.utils.worker_reflection import reflect_on_result as _reflect_on_result


# ── Single worker execution with 3-tier fallback ─────────


async def _execute_with_fallback(
    worker: dict,
    predecessor_context: str = "",
    cache: WorkerResultCache | None = None,
    exec_tracker: ExecutionTracker | None = None,
    stage: int = 0,
    estimated_complexity: str = "medium",
    time_budget: float | None = None,
    research_context: str = "",
    session_id: str = "",
    upstream_context: str = "",
) -> tuple[dict, AIMessage]:
    """Execute plan for a single worker with 3-tier fallback.

    Tier 0: Cache hit (skip execution entirely)
    Tier 1: Full execution with tools
    Tier 2: Knowledge-only execution (no tools, shorter timeout)
    Tier 3: Skip with error message

    Args:
        time_budget: Remaining seconds from outer timeout.
                     None = use default settings (backward compatible).
    """
    settings = get_settings()
    domain = worker["worker_domain"]
    wid = worker.get("worker_id", domain)  # unique key for progress tracker
    tracker = get_tracker(session_id)
    plan_json = worker.get("plan", "")
    has_deps = bool(worker.get("dependencies"))

    # Research-First: prepend research data to predecessor context
    if research_context:
        research_block = (
            "## 사전 리서치 데이터 (Deep Research 결과)\n"
            "아래 데이터는 이미 수집된 외부 리서치 결과입니다. "
            "이 데이터를 기반으로 작업하세요. 동일한 검색을 반복하지 마세요.\n\n"
            f"{research_context}\n\n---\n"
        )
        predecessor_context = f"{research_block}\n{predecessor_context}" if predecessor_context else research_block

    # Resolve model for tracking (adaptive selection happens inside create_worker)
    from src.utils.model_selector import select_worker_model
    model = select_worker_model(
        worker_domain=domain,
        estimated_complexity=estimated_complexity,
        has_tools=True,
        has_dependencies=has_deps,
        stage=stage,
    )

    # Start execution tracking
    if exec_tracker:
        exec_tracker.worker_start(
            worker_domain=domain,
            worker_id=worker.get("worker_id", ""),
            stage=stage,
            model=model,
            has_predecessor=bool(predecessor_context),
        )

    # ── Tier 0: Cache check ──
    if cache:
        cached = cache.get(domain, plan_json)
        if cached:
            updated_w = dict(worker)
            updated_w["execution_result"] = cached
            updated_w["status"] = "completed"
            tracker.update(wid, WorkerStatus.DONE, tier=1, summary="[cached]")
            if exec_tracker:
                exec_tracker.worker_end(domain, tier=0, success=True, cached=True)
            msg = AIMessage(content=f"[{domain}] Cache hit — 이전 결과 재사용")
            return updated_w, msg

    # ── Tier 1: Full execution with tools ──
    tool_category = worker.get("tool_category")
    tier1_start = time.monotonic()
    tier2_budget = time_budget if time_budget is not None else settings.degraded_timeout
    try:
        tracker.update(wid, WorkerStatus.RUNNING, tier=1)
        agent = create_worker(
            domain, tool_category=tool_category,
            estimated_complexity=estimated_complexity,
            has_dependencies=has_deps, stage=stage,
            worker_name=worker.get("worker_name", ""),
            role_type=worker.get("role_type", "executor"),
        )
        # Turn-based progress callback → real progress bar
        def _progress_cb(turn: int, max_t: int) -> None:
            if max_t > 0:
                tracker.set_real_progress(wid, turn / max_t)

        result = await agent.aexecute_plan(
            approved_plan=plan_json,
            predecessor_context=predecessor_context,
            time_budget=time_budget,
            progress_callback=_progress_cb,
            upstream_context=upstream_context,
        )

        # ── Reflection (Producer-Critic) ──
        tier1_elapsed_for_refl = time.monotonic() - tier1_start
        final_result, reflection_status = await _reflect_on_result(
            worker, result, time_budget, tier1_elapsed_for_refl, exec_tracker,
        )
        if exec_tracker:
            exec_tracker.set_reflection_result(domain, reflection_status)

        updated_w = dict(worker)
        updated_w["execution_result"] = final_result.model_dump_json()
        updated_w["status"] = "completed"
        try:
            updated_w["result_file_path"] = _save_result_file(
                updated_w["execution_result"], wid, session_id, settings.worker_output_dir,
            )
        except Exception:
            logger.warning("result_file_save_failed", worker=domain)
        summary = (final_result.result_summary or "")[:50]
        tracker.update(wid, WorkerStatus.DONE, tier=1, summary=summary)
        if exec_tracker:
            exec_tracker.worker_end(domain, tier=1, success=True)
        if cache:
            cache.put(domain, plan_json, updated_w["execution_result"])
        msg = AIMessage(
            content=(
                f"[{domain}] Execution complete ({final_result.completion_percentage}%): "
                f"{final_result.result_summary}"
            )
        )
        return updated_w, msg

    except (ClaudeCodeTimeoutError, ClaudeCodeError) as tier1_err:
        if not settings.enable_tool_fallback:
            tracker.update(wid, WorkerStatus.FAILED, tier=1, summary=str(tier1_err)[:50])
            if exec_tracker:
                exec_tracker.worker_end(domain, tier=1, success=False)
            raise

        # Capture Tier 1 partial context for Tier 2 injection
        # ClaudeCodeError sets partial_context (max_turns), ClaudeCodeTimeoutError sets partial_stdout (timeout)
        tier1_partial = (
            getattr(tier1_err, 'partial_context', None)
            or getattr(tier1_err, 'partial_stdout', None)
            or ""
        )

        # A3: Tier 2 budget 계산 — Tier 1 소요시간 차감
        tier1_elapsed = time.monotonic() - tier1_start
        tier2_budget = (time_budget - tier1_elapsed) if time_budget is not None else settings.degraded_timeout

        if tier2_budget < 30:
            logger.warning(
                "tier2_skipped_no_budget",
                worker=domain,
                remaining=round(tier2_budget, 1),
            )
            # Fall through to Tier 3
        else:
            logger.warning(
                "tier1_failed_trying_degraded",
                worker=domain,
                error=str(tier1_err)[:200],
                tier2_budget=round(tier2_budget, 1),
                has_partial_context=bool(tier1_partial),
            )

    # ── Tier 2: Degraded execution (builtin tools only, shorter timeout) ──
    # A3: Skip Tier 2 if budget already exhausted (tier2_budget set above)
    _BUILTIN_SEARCH_TOOLS = ["WebSearch", "WebFetch", "mcp__firecrawl__firecrawl_scrape"]
    if tier2_budget >= 30:
        try:
            tracker.update(wid, WorkerStatus.TIER2, tier=2)
            agent_degraded = create_worker(
                domain, tool_category=tool_category,
                estimated_complexity=estimated_complexity,
                has_dependencies=has_deps, stage=stage,
                worker_name=worker.get("worker_name", ""),
                role_type=worker.get("role_type", "executor"),
            )
            # Keep builtin search tools to preserve web search capability
            agent_degraded.allowed_tools = _BUILTIN_SEARCH_TOOLS

            # Inject Tier 1 partial progress as predecessor context
            tier2_context = predecessor_context
            if tier1_partial:
                tier1_block = (
                    "## Tier 1 작업 진척 (이어서 완료하세요)\n"
                    "이전 실행에서 아래 내용까지 진행했습니다. "
                    "이 내용을 기반으로 나머지 작업만 완료하세요. "
                    "동일한 작업을 반복하지 마세요.\n\n"
                    f"{tier1_partial[:3000]}\n\n---\n"
                )
                tier2_context = f"{tier1_block}\n{tier2_context}" if tier2_context else tier1_block
                logger.info(
                    "tier2_context_injected",
                    worker=domain,
                    partial_len=len(tier1_partial),
                )

            result = await agent_degraded.aexecute_plan(
                approved_plan=plan_json,
                predecessor_context=tier2_context,
                time_budget=tier2_budget,
                progress_callback=_progress_cb,
                upstream_context=upstream_context,
            )

            updated_w = dict(worker)
            updated_w["execution_result"] = result.model_dump_json()
            updated_w["status"] = "completed"
            try:
                updated_w["result_file_path"] = _save_result_file(
                    updated_w["execution_result"], wid, session_id, settings.worker_output_dir,
                )
            except Exception:
                logger.warning("result_file_save_failed", worker=domain)
            summary = (result.result_summary or "")[:50]
            tracker.update(wid, WorkerStatus.DONE, tier=2, summary=summary)
            if exec_tracker:
                exec_tracker.worker_end(domain, tier=2, success=True)
            if cache:
                cache.put(domain, plan_json, updated_w["execution_result"])
            msg = AIMessage(
                content=(
                    f"[{domain}] Degraded execution ({result.completion_percentage}%): "
                    f"{result.result_summary} [도구 없이 실행]"
                )
            )
            return updated_w, msg

        except Exception as tier2_err:
            logger.error(
                "tier2_degraded_also_failed",
                worker=domain,
                error=str(tier2_err)[:200],
            )
            # Collect Tier 2 partial context for Tier 3 salvage
            tier2_partial = getattr(tier2_err, 'partial_context', None) or ""
            if not tier2_partial and tier1_partial:
                tier2_partial = tier1_partial  # Fall back to Tier 1 progress

    # ── Tier 3: Salvage partial results or skip ──
    # If any tier produced partial context, wrap it as a degraded WorkerResult
    # instead of returning an empty failure message.
    _any_partial = locals().get("tier2_partial") or locals().get("tier1_partial") or ""
    if _any_partial and len(_any_partial) > 50:
        from src.models.messages import WorkerResult
        salvaged = WorkerResult(
            result_summary=f"[부분 결과 — max_turns 초과] {_any_partial[:500]}",
            deliverables=[_any_partial[:5000]],
            completion_percentage=40,
            is_partial=True,
        )
        updated_w = dict(worker)
        updated_w["execution_result"] = salvaged.model_dump_json()
        updated_w["status"] = "completed"
        try:
            updated_w["result_file_path"] = _save_result_file(
                updated_w["execution_result"], wid, session_id, settings.worker_output_dir,
            )
        except Exception:
            logger.warning("result_file_save_failed", worker=domain)
        tracker.update(wid, WorkerStatus.DONE, tier=3, summary="partial salvage")
        if exec_tracker:
            exec_tracker.worker_end(domain, tier=3, success=True)
        SubprocessMetrics().record_partial_recovery()
        logger.warning(
            "tier3_partial_salvage",
            worker=domain,
            partial_len=len(_any_partial),
        )
        msg = AIMessage(
            content=f"[{domain}] Partial result salvaged from previous tier ({len(_any_partial)} chars)"
        )
        return updated_w, msg

    tracker.update(wid, WorkerStatus.FAILED, tier=3, summary="all tiers exhausted")
    if exec_tracker:
        exec_tracker.worker_end(domain, tier=3, success=False)
    updated_w = dict(worker)
    updated_w["execution_result"] = f"[Execution failed: all tiers exhausted for {domain}]"
    updated_w["status"] = "failed"
    msg = AIMessage(
        content=f"[{domain}] Execution failed (all tiers exhausted). Skipping worker."
    )
    return updated_w, msg


# ── Flat parallel execution (legacy behavior) ────────────


# ── Execution strategies (extracted to src/engine/execution_strategies.py) ──
from src.engine.execution_strategies import (
    flat_parallel_execution as _flat_parallel_execution_impl,
    staged_execution as _staged_execution_impl,
)


def _flat_parallel_execution(all_workers, settings, tracker, **kwargs):
    return _flat_parallel_execution_impl(all_workers, settings, tracker, _execute_with_fallback, **kwargs)


def _staged_execution(all_workers, settings, tracker, **kwargs):
    return _staged_execution_impl(all_workers, settings, tracker, _execute_with_fallback, **kwargs)


# ── Main node entry point ────────────────────────────────


@node_error_handler("worker_execution")
def worker_execution_node(state: dict) -> dict:
    """Each worker executes their approved plan — with dependency-aware staging."""
    all_workers = list(state.get("workers", []))
    settings = get_settings()

    # Edge case: no workers to execute
    if not all_workers:
        logger.warning("no_workers_to_execute")
        return {
            "workers": [],
            "messages": [],
            "phase": "ceo_final_report",
        }

    # Assign unique worker_id only if not already set by leader_task_decomposition
    needs_id = [w for w in all_workers if not w.get("worker_id")]
    if needs_id:
        domain_counts: dict[str, int] = {}
        for w in all_workers:
            d = w["worker_domain"]
            domain_counts[d] = domain_counts.get(d, 0) + 1
        domain_seen: dict[str, int] = {}
        for w in all_workers:
            d = w["worker_domain"]
            if not w.get("worker_id"):
                idx = domain_seen.get(d, 0)
                domain_seen[d] = idx + 1
                w["worker_id"] = f"{d}_{idx}" if domain_counts[d] > 1 else d
            else:
                domain_seen[d] = domain_seen.get(d, 0) + 1

    # Initialize progress tracker for live dashboard (session-aware)
    sid = state.get("session_id", "")
    tracker = get_tracker(sid)
    tracker.start(all_workers)

    # Initialize enhancement modules
    cache = WorkerResultCache(ttl_seconds=settings.worker_cache_ttl) if settings.enable_worker_cache else None
    exec_tracker = get_exec_tracker()  # Shared singleton (also used by node_error_handler for node-level timing)
    complexity = state.get("estimated_complexity", "medium") or "medium"
    research_ctx = state.get("research_data_full", "")
    user_answers = state.get("user_answers", [])
    upstream_ctx = _build_upstream_context(
        user_task=state.get("user_task", ""),
        user_answers=user_answers,
        clarifying_questions=state.get("clarifying_questions", []),
    )

    try:
        # ── P-E-R loop: planner→executor→reviewer 순차 루프 ──
        # 조건: enable_review_loop=True + planner/reviewer 역할 모두 존재
        # builder-mode 팀(pre_context.team_agents)은 역할이 불완전할 수 있어 자동 스킵
        if settings.enable_review_loop and has_pesr_roles(all_workers):
            logger.info("using_pesr_loop_execution", worker_count=len(all_workers))
            per_result = run_async(run_pesr_loop(
                all_workers=all_workers,
                state=state,
                execute_worker_fn=_execute_with_fallback,
                settings=settings,
                tracker=tracker,
                cache=cache,
                exec_tracker=exec_tracker,
                estimated_complexity=complexity,
                research_context=research_ctx,
                session_id=sid,
                upstream_context=upstream_ctx,
            ))
            if per_result:
                # P-E-R 루프 성공
                if cache:
                    exec_tracker.set_cache_stats(cache.stats())
                per_result["execution_metrics"] = exec_tracker.summary()
                return per_result
            # per_result가 빈 dict → 역할 불완전, fallback으로 진행
            logger.info("pesr_loop_fallback_to_staged")

        if settings.enable_staged_execution and has_any_dependencies(all_workers):
            logger.info("using_staged_execution", worker_count=len(all_workers))
            result = _staged_execution(
                all_workers, settings, tracker,
                cache=cache, exec_tracker=exec_tracker,
                estimated_complexity=complexity,
                research_context=research_ctx,
                session_id=sid,
                upstream_context=upstream_ctx,
            )
        else:
            logger.info("using_flat_parallel_execution", worker_count=len(all_workers))
            result = _flat_parallel_execution(
                all_workers, settings, tracker,
                cache=cache, exec_tracker=exec_tracker,
                estimated_complexity=complexity,
                research_context=research_ctx,
                session_id=sid,
                upstream_context=upstream_ctx,
            )

        # Attach metrics to state
        if cache:
            exec_tracker.set_cache_stats(cache.stats())
        result["execution_metrics"] = exec_tracker.summary()

        return result
    finally:
        # Worker tracker is stopped by sim_runner after worker_execution completes.
        # CEO report progress is handled by step_progress (not worker tracker).
        pass
