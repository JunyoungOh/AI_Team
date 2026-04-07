"""Planner-Executor-Synthesizer-Reviewer (P-E-S-R) 순차 루프 오케스트레이터.

worker_execution_node에서 호출됨. 워커를 역할별로 분리하여
4단계 순차 실행하고, Reviewer가 PASS할 때까지 루프한다 (최대 2회).

  ① Planner → ② Executor(s) 병렬 → ③ Synthesizer → ④ Reviewer
  → PASS: 종료 → Reporter
  → FAIL: gaps → ① Planner (갭 기반 보완) → ② 갭 관련 Executor만 재실행 → ③ 재합성 → ④ 재검증

모든 중간 결과는 PipelineBlackboard에 누적 보존되어
Reporter에게 손실 없이 전달된다.
"""

from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import AIMessage

from src.config.settings import get_settings
from src.models.messages import ReviewerLoopVerdict
from src.utils.blackboard import PipelineBlackboard, _extract_summary
from src.utils.logging import get_logger

logger = get_logger(agent_id="review_loop")


# ── Role splitting ──────────────────────────────────

def _split_workers_by_role(
    workers: list[dict],
) -> tuple[dict | None, list[dict], dict | None, dict | None]:
    """워커 목록을 planner, executors, synthesizer, reviewer로 분리.

    Returns: (planner, executors, synthesizer, reviewer)
    """
    planner = None
    synthesizer = None
    reviewer = None
    executors = []

    for w in workers:
        role = w.get("role_type", "executor")
        if role == "planner" and planner is None:
            planner = w
        elif role == "synthesizer" and synthesizer is None:
            synthesizer = w
        elif role == "reviewer" and reviewer is None:
            reviewer = w
        else:
            executors.append(w)

    return planner, executors, synthesizer, reviewer


def has_pesr_roles(workers: list[dict]) -> bool:
    """P-E-S-R 루프 진입 조건: planner와 (synthesizer 또는 reviewer)가 존재하는가?"""
    has_planner = any(w.get("role_type") == "planner" for w in workers)
    has_synth_or_review = (
        any(w.get("role_type") == "synthesizer" for w in workers)
        or any(w.get("role_type") == "reviewer" for w in workers)
    )
    return has_planner and has_synth_or_review


# 후방 호환: 이전 이름
has_per_roles = has_pesr_roles


def _ensure_four_roles(workers: list[dict]) -> list[dict]:
    """CEO가 역할을 빠뜨린 경우 안전장치.

    - planner 없으면: 합성 planner 추가
    - synthesizer 없으면: 합성 synthesizer 추가
    - reviewer 없으면: 합성 reviewer 추가 (CEO 설계 objective 없이 범용)
    기존 executor를 변환하지 않음 (도메인 커버리지 유지).
    """
    roles = {w.get("role_type", "executor") for w in workers}

    # 대표 도메인 추출
    dominant_cat = "research"
    for w in workers:
        if w.get("role_type") == "executor":
            dominant_cat = w.get("tool_category", "research")
            break

    user_task_hint = workers[0].get("task_title", "작업") if workers else "작업"

    if "planner" not in roles:
        planner = _make_synthetic_worker(
            role_type="planner",
            worker_name=f"{user_task_hint} 분석 기획자",
            task_title="분석 프레임워크 설계",
            objective="사용자 요구사항을 분석하여 실행자들의 작업 프레임워크를 설계한다.",
            tool_category=dominant_cat,
            success_criteria=["분석 프레임워크 정의", "데이터 수집 체크리스트"],
        )
        workers = [planner] + workers
        logger.info("synthetic_planner_added", category=dominant_cat)

    if "synthesizer" not in roles:
        executor_titles = [w.get("task_title", "") for w in workers if w.get("role_type") == "executor"]
        synthesizer = _make_synthetic_worker(
            role_type="synthesizer",
            worker_name="결과 통합 분석가",
            task_title="실행 결과 통합 합성",
            objective="실행자들의 개별 결과물을 하나의 통합 분석 문서로 합성한다. 교차 분석, 모순 해소, 핵심 인사이트 도출.",
            tool_category=dominant_cat,
            success_criteria=["통합 분석 문서", "교차 분석", "인사이트 도출"],
            dependencies=executor_titles,
        )
        # synthesizer는 executor 뒤, reviewer 앞에 삽입
        insert_idx = len(workers)
        for i, w in enumerate(workers):
            if w.get("role_type") == "reviewer":
                insert_idx = i
                break
        workers.insert(insert_idx, synthesizer)
        logger.info("synthetic_synthesizer_added", category=dominant_cat)

    if "reviewer" not in roles:
        synth_title = ""
        for w in workers:
            if w.get("role_type") == "synthesizer":
                synth_title = w.get("task_title", "")
                break
        reviewer = _make_synthetic_worker(
            role_type="reviewer",
            worker_name="데이터 품질 검증관",
            task_title="통합 분석 검증",
            objective="합성자의 통합 분석 문서의 정확성·완성도를 검증한다.",
            tool_category=dominant_cat,
            success_criteria=["데이터 교차검증", "누락 영역 식별"],
            dependencies=[synth_title] if synth_title else [],
        )
        workers.append(reviewer)
        logger.info("synthetic_reviewer_added", category=dominant_cat)

    # 의존성 정리
    planner_title = ""
    executor_titles = []
    synthesizer_title = ""
    for w in workers:
        rt = w.get("role_type", "executor")
        if rt == "planner":
            planner_title = w.get("task_title", "")
        elif rt == "executor":
            executor_titles.append(w.get("task_title", ""))
        elif rt == "synthesizer":
            synthesizer_title = w.get("task_title", "")

    # executor → planner
    if planner_title:
        for w in workers:
            if w.get("role_type") == "executor":
                deps = w.get("dependencies", [])
                if planner_title not in deps:
                    w["dependencies"] = deps + [planner_title]

    # synthesizer → executors
    for w in workers:
        if w.get("role_type") == "synthesizer":
            w["dependencies"] = executor_titles

    # reviewer → synthesizer
    for w in workers:
        if w.get("role_type") == "reviewer":
            w["dependencies"] = [synthesizer_title] if synthesizer_title else executor_titles

    return workers


# 후방 호환
_ensure_three_roles = _ensure_four_roles


def _make_synthetic_worker(
    role_type: str,
    worker_name: str,
    task_title: str,
    objective: str,
    tool_category: str,
    success_criteria: list[str],
    dependencies: list[str] | None = None,
) -> dict:
    return {
        "worker_id": "",
        "worker_domain": tool_category,
        "role_type": role_type,
        "worker_name": worker_name,
        "task_title": task_title,
        "tool_category": tool_category,
        "plan": json.dumps({
            "task_title": task_title,
            "objective": objective,
            "tool_category": tool_category,
            "success_criteria": success_criteria,
        }, ensure_ascii=False),
        "dependencies": dependencies or [],
        "status": "plan_submitted",
        "execution_result": "",
        "revision_feedback": "",
        "revision_count": 0,
    }


# ── Reviewer verdict (structured query, CEO 설계 objective 주입) ──

REVIEWER_VERDICT_SYSTEM = """\
당신은 데이터 품질 검증 전문가입니다.

{reviewer_persona}

## 원본 작업
{user_task}

## 기획자의 프레임워크
{planner_context}

## 합성자의 통합 분석 결과
{synthesis_context}

## 평가 기준
1. **데이터 충실성**: 수치 데이터가 구체적이고 출처가 명시되었는가?
2. **논리 일관성**: 분석의 전제→근거→결론 흐름에 비약이 없는가?
3. **요청 부합도**: 사용자 요구사항에 맞는 범위와 깊이인가?
4. **완성도**: 핵심 영역이 누락되지 않았는가?

## 판정 규칙
- 4개 기준 모두 7점 이상이면 passed=true
- 하나라도 4점 이하이면 반드시 passed=false
- gaps에는 구체적으로 보완해야 할 영역을 기술하세요
"""


async def _get_reviewer_verdict(
    user_task: str,
    planner_context: str,
    synthesis_context: str,
    reviewer_worker: dict | None = None,
) -> ReviewerLoopVerdict:
    """Reviewer verdict를 structured_query로 직접 획득.

    CEO가 설계한 reviewer의 objective를 persona로 주입.
    """
    settings = get_settings()

    from src.utils.bridge_factory import get_bridge
    bridge = get_bridge()

    # CEO가 설계한 reviewer objective를 persona로 주입
    reviewer_persona = ""
    if reviewer_worker:
        name = reviewer_worker.get("worker_name", "")
        # plan에서 objective 추출
        try:
            plan = json.loads(reviewer_worker.get("plan", "{}"))
            objective = plan.get("objective", "")
        except (json.JSONDecodeError, TypeError):
            objective = ""
        if name or objective:
            reviewer_persona = f"### 당신의 전문성: {name}\n{objective}" if objective else f"### {name}"

    system = REVIEWER_VERDICT_SYSTEM.format(
        user_task=user_task,
        reviewer_persona=reviewer_persona,
        planner_context=planner_context[:10000],
        synthesis_context=synthesis_context[:40000],
    )

    return await bridge.structured_query(
        system_prompt=system,
        user_message="위 합성 결과를 평가하고 ReviewerLoopVerdict 형식으로 판정하세요.",
        output_schema=ReviewerLoopVerdict,
        model=settings.ceo_model,
        allowed_tools=[],
        timeout=settings.reviewer_verdict_timeout,
        effort=settings.reporter_effort,
    )


# ── Loop orchestrator ────────────────────────────────

async def run_pesr_loop(
    all_workers: list[dict],
    state: dict,
    execute_worker_fn,
    settings: Any,
    tracker: Any,
    **exec_kwargs,
) -> dict:
    """P-E-S-R 4단계 순차 루프 실행.

    Args:
        all_workers: 전체 워커 목록 (planner + executors + synthesizer + reviewer)
        state: 파이프라인 state dict
        execute_worker_fn: _execute_with_fallback 함수 참조
        settings: get_settings() 결과
        tracker: WorkerProgressTracker
        **exec_kwargs: execute_worker_fn에 전달할 추가 인자

    Returns:
        dict: {"workers": [...], "messages": [...], "blackboard": {...}, "phase": "ceo_final_report"}
        빈 dict: 역할 불완전 → caller가 기존 방식으로 fallback
    """
    from src.utils.guards import safe_gather

    user_task = state.get("user_task", "")
    complexity = state.get("estimated_complexity", "medium") or "medium"
    blackboard = PipelineBlackboard()
    messages: list = []
    total_start = time.monotonic()
    total_budget = settings.parallel_task_timeout

    # 복잡도 게이트: high만 2회 루프, low/medium은 1회
    max_iterations = settings.per_loop_max_iterations if complexity == "high" else 1

    planner, executors, synthesizer, reviewer = _split_workers_by_role(all_workers)

    if not planner or not executors:
        logger.warning("pesr_loop_missing_roles", has_planner=bool(planner), executor_count=len(executors))
        return {}

    logger.info(
        "pesr_loop_start",
        planner=planner.get("worker_name"),
        executor_count=len(executors),
        has_synthesizer=bool(synthesizer),
        has_reviewer=bool(reviewer),
        max_iterations=max_iterations,
    )

    for iteration in range(max_iterations):
        elapsed = time.monotonic() - total_start
        remaining = total_budget - elapsed

        if remaining < 120:
            logger.warning("pesr_loop_time_exhausted", iteration=iteration, remaining=remaining)
            blackboard.record_event("time_exhausted", iteration, f"remaining={remaining:.0f}s")
            break

        loop_label = f"Loop {iteration + 1}/{max_iterations}"
        messages.append(AIMessage(content=f"[P-E-S-R] {loop_label} 시작"))

        # ── Step 1: Planner ──
        planner_budget = min(remaining * 0.15, settings.execution_timeout)
        if iteration > 0:
            gap_context = blackboard.get_gaps_for_replanning()
            planner["_override_predecessor"] = gap_context
            logger.info("planner_replan", iteration=iteration, gap_length=len(gap_context))

        try:
            predecessor = planner.get("_override_predecessor", "")
            updated_planner, planner_msg = await execute_worker_fn(
                planner,
                predecessor_context=predecessor,
                time_budget=planner_budget,
                **exec_kwargs,
            )
            planner = updated_planner
            messages.append(planner_msg)
            blackboard.record_event("planner_complete", iteration, planner.get("worker_name", ""))
        except Exception as exc:
            logger.warning("planner_failed", iteration=iteration, error=str(exc)[:200])
            blackboard.record_event("planner_failed", iteration, str(exc)[:200])
            messages.append(AIMessage(content="[P-E-S-R] 기획자 실행 실패 — 기획 없이 진행"))

        # ── Step 2: Executors (parallel) ──
        executor_budget = min(
            remaining * 0.45,
            settings.execution_timeout,
        )
        executor_budget = max(executor_budget, 120)

        planner_result_text = planner.get("execution_result", "")
        planner_summary = _extract_summary(planner_result_text)
        planner_predecessor = f"## 기획자 분석 프레임워크\n{planner_summary}" if planner_summary else ""

        async def _run_one_executor(ex: dict) -> tuple[dict, AIMessage]:
            return await execute_worker_fn(
                ex,
                predecessor_context=planner_predecessor,
                time_budget=executor_budget,
                **exec_kwargs,
            )

        try:
            executor_tasks = [_run_one_executor(ex) for ex in executors]
            executor_results_raw = await safe_gather(
                executor_tasks,
                timeout_seconds=int(executor_budget + 30),
            )

            updated_executors = []
            for i, (success, result) in enumerate(executor_results_raw):
                if not success:
                    logger.warning("executor_failed", idx=i, error=str(result)[:200])
                    updated_executors.append(executors[i])
                    messages.append(AIMessage(content=f"[P-E-S-R] 실행자 {executors[i].get('worker_name', i)} 실패"))
                else:
                    updated_ex, ex_msg = result
                    updated_executors.append(updated_ex)
                    messages.append(ex_msg)

            executors = updated_executors
            blackboard.record_event("executors_complete", iteration, f"{len(executors)} workers")
        except Exception as exc:
            logger.warning("all_executors_failed", iteration=iteration, error=str(exc)[:200])
            blackboard.record_event("executors_failed", iteration, str(exc)[:200])
            messages.append(AIMessage(content="[P-E-S-R] 모든 실행자 실패 — 루프 종료"))
            break

        # ── Step 3: Synthesizer (Worker agent, 도구 없음) ──
        synthesis_context = ""
        if synthesizer:
            synth_budget = min(remaining * 0.25, settings.synthesizer_timeout)

            # executor 결과를 synthesizer의 predecessor_context로 구성
            exec_parts = []
            for ex in executors:
                name = ex.get("worker_name") or ex.get("worker_domain", "?")
                result_text = _extract_summary(ex.get("execution_result", ""))
                exec_parts.append(f"### {name}\n{result_text}")

            synth_predecessor = f"## 기획자 프레임워크\n{planner_summary}\n\n## 실행자 결과\n" + "\n\n".join(exec_parts)

            # 2차 루프: 이전 합성물 + 재합성 가이드 추가
            if iteration > 0 and blackboard.loops:
                prev_synth = blackboard.loops[-1].get("synthesizer_result", {})
                prev_synth_text = _extract_summary(prev_synth.get("execution_result", ""))
                synth_predecessor = (
                    f"## 이전 합성물 (보존 기반)\n{prev_synth_text}\n\n"
                    f"## 검증자 보완 요청\n"
                    + "\n".join(f"- {g}" for g in (blackboard.loops[-1].get("reviewer_gaps", [])))
                    + f"\n\n{synth_predecessor}"
                )

            try:
                updated_synth, synth_msg = await execute_worker_fn(
                    synthesizer,
                    predecessor_context=synth_predecessor,
                    time_budget=synth_budget,
                    **exec_kwargs,
                )
                synthesizer = updated_synth
                messages.append(synth_msg)
                synthesis_context = _extract_summary(synthesizer.get("execution_result", ""))
                blackboard.record_event("synthesizer_complete", iteration, synthesizer.get("worker_name", ""))
            except Exception as exc:
                logger.warning("synthesizer_failed", iteration=iteration, error=str(exc)[:200])
                blackboard.record_event("synthesizer_failed", iteration, str(exc)[:200])
                messages.append(AIMessage(content="[P-E-S-R] 합성자 실행 실패 — executor 원재료로 진행"))
                # fallback: executor 결과를 직접 합성 컨텍스트로 사용
                synthesis_context = "\n\n".join(exec_parts)
        else:
            # synthesizer 없음 — executor 결과를 직접 사용
            exec_parts = []
            for ex in executors:
                name = ex.get("worker_name") or ex.get("worker_domain", "?")
                result_text = _extract_summary(ex.get("execution_result", ""))
                exec_parts.append(f"### {name}\n{result_text}")
            synthesis_context = "\n\n".join(exec_parts)

        # ── Step 4: Reviewer verdict (structured query) ──
        reviewer_passed = True
        reviewer_gaps: list[str] = []
        reviewer_critique = ""

        if reviewer:
            try:
                verdict = await _get_reviewer_verdict(
                    user_task=user_task,
                    planner_context=planner_summary,
                    synthesis_context=synthesis_context,
                    reviewer_worker=reviewer,
                )
                reviewer_passed = verdict.passed
                reviewer_gaps = verdict.gaps
                reviewer_critique = verdict.critique

                verdict_label = "PASS" if reviewer_passed else "FAIL"
                messages.append(AIMessage(
                    content=f"[P-E-S-R] 검증 결과: {verdict_label} (점수: {verdict.overall_score}/10)"
                ))
                if not reviewer_passed and verdict.gaps:
                    gaps_text = ", ".join(verdict.gaps[:3])
                    messages.append(AIMessage(content=f"[P-E-S-R] 보완 필요: {gaps_text}"))

                blackboard.record_event(
                    "reviewer_verdict", iteration,
                    f"{verdict_label} score={verdict.overall_score} gaps={len(verdict.gaps)}",
                )
            except Exception as exc:
                logger.warning("reviewer_verdict_failed", error=str(exc)[:200])
                reviewer_passed = True
                blackboard.record_event("reviewer_failed", iteration, str(exc)[:200])
                messages.append(AIMessage(content="[P-E-S-R] 검증자 실행 실패 — 현재 결과로 진행"))

        # 블랙보드에 루프 결과 기록
        blackboard.record_loop_iteration(
            iteration=iteration,
            planner_result=planner,
            executor_results=executors,
            synthesizer_result=synthesizer,
            reviewer_passed=reviewer_passed,
            reviewer_gaps=reviewer_gaps,
            reviewer_critique=reviewer_critique,
        )

        if reviewer_passed:
            messages.append(AIMessage(content=f"[P-E-S-R] {loop_label} 완료 — 검증 통과"))
            break
        elif iteration < max_iterations - 1:
            messages.append(AIMessage(content=f"[P-E-S-R] {loop_label} — 보완 후 재실행"))
        else:
            messages.append(AIMessage(content=f"[P-E-S-R] 최대 반복 도달 — 현재 결과로 진행"))

    # 최종 workers 목록
    final_workers = [planner] + executors
    if synthesizer:
        final_workers.append(synthesizer)
    if reviewer:
        final_workers.append(reviewer)

    total_elapsed = time.monotonic() - total_start
    logger.info(
        "pesr_loop_complete",
        iterations=blackboard.get_iteration_count(),
        total_seconds=round(total_elapsed, 1),
    )

    return {
        "workers": final_workers,
        "messages": messages,
        "blackboard": blackboard.to_dict(),
        "phase": "ceo_final_report",
    }


# 후방 호환
run_per_loop = run_pesr_loop
