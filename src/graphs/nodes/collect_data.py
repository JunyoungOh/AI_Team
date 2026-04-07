"""Node: collect_data — parallel data collection via Sonnet Collectors.

Replaces deep_research for the Collect-Then-Reason pipeline.
Collectors gather raw data without analysis, tag with domain labels,
and store results in CollectionBlackboard for downstream Opus Workers.

Supports two modes:
1. Parallel mode (default): splits into 2-4 collection queries, runs concurrently
2. Single mode (fallback): one collector gathers all data sequentially
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from src.config.settings import get_settings
from src.prompts.collector_prompts import (
    COLLECTION_PLAN_SYSTEM,
    COLLECTOR_SYSTEM,
)
from src.utils.anthropic_client import AnthropicClient, SdkQueryError
from src.utils.collection_blackboard import CollectedItem, CollectionBlackboard
from src.utils.parallel import run_async
from src.utils.guards import node_error_handler, safe_gather
from src.utils.progress import get_tracker, WorkerStatus
from src.utils.logging import get_logger
from src.utils.tool_definitions import cli_tools_to_sdk
from src.utils.tool_executor import ToolExecutor

_logger = get_logger(agent_id="collect_data")


# ── Pydantic schemas ────────────────────────────────────


class CollectionQuery(BaseModel):
    """단일 수집 쿼리."""
    query: str = Field(description="검색 쿼리")
    domain_hint: str = Field(default="research", description="예상 관련 도메인")
    search_focus: str = Field(default="", description="검색 초점")
    expected_data_type: str = Field(default="article", description="기대 데이터 유형")


class CollectionPlan(BaseModel):
    """수집 쿼리 분해 결과."""
    parallel: bool = Field(description="병렬 수집 가능 여부")
    rationale: str = Field(default="", description="판단 근거")
    queries: list[CollectionQuery] = Field(default_factory=list)


# ── Planning ────────────────────────────────────────────


def _plan_collection(user_task: str, research_context: str) -> CollectionPlan | None:
    """작업을 병렬 수집 쿼리로 분해 (~2-5초, Sonnet)."""
    settings = get_settings()
    content = f"수집 작업: {user_task}"
    if research_context:
        content += f"\n\n{research_context}"

    try:
        sdk = AnthropicClient()
        result = run_async(sdk.structured_query(
            system_prompt=COLLECTION_PLAN_SYSTEM,
            user_message=content,
            output_schema=CollectionPlan,
            model="sonnet",
            effort=settings.default_effort,
            timeout=float(settings.llm_call_timeout),
        ))
        if isinstance(result, CollectionPlan):
            return result
    except SdkQueryError as exc:
        _logger.warning("collection_plan_failed", error=str(exc))
    return None


# ── Single collector execution ──────────────────────────


async def _run_collector(
    query: CollectionQuery,
    user_task: str,
    tracker_id: str,
) -> list[CollectedItem]:
    """단일 Collector: 웹 검색으로 데이터 수집, JSON 파싱."""
    settings = get_settings()
    tracker = get_tracker()
    tracker.update(tracker_id, WorkerStatus.RUNNING, tier=1)

    prompt = COLLECTOR_SYSTEM.format(
        search_focus=query.search_focus or query.query,
        expected_data_type=query.expected_data_type,
    )

    user_msg = (
        f"전체 작업: {user_task}\n\n"
        f"당신의 수집 쿼리: {query.query}\n"
        f"관련 도메인: {query.domain_hint}\n"
        f"기대 데이터 유형: {query.expected_data_type}\n\n"
        f"위 쿼리에 대해 관련 데이터를 최대한 많이 수집하세요."
    )

    try:
        sdk_tools = cli_tools_to_sdk(settings.collector_allowed_tools)
        executor = ToolExecutor()
        try:
            raw = await AnthropicClient().tool_use_query(
                system_prompt=prompt,
                user_message=user_msg,
                tools=sdk_tools,
                tool_executor=executor,
                model=settings.collector_model,
                max_tokens=8192,
                max_turns=settings.collector_max_turns,
                effort=settings.default_effort,
                timeout=float(settings.collector_timeout),
            )
            raw = raw if isinstance(raw, str) else str(raw)
        finally:
            await executor.close()

        items = _parse_collection_result(raw, query.domain_hint)
        tracker.update(tracker_id, WorkerStatus.DONE, tier=1,
                       summary=f"{len(items)}건 수집")
        _logger.info("collector_done", query=query.query[:60], items=len(items))
        return items

    except Exception as exc:
        tracker.update(tracker_id, WorkerStatus.FAILED, tier=1)
        _logger.error("collector_failed", query=query.query[:60], error=str(exc))
        return []


def _parse_collection_result(raw: str, default_domain: str) -> list[CollectedItem]:
    """Collector 출력에서 CollectedItem 리스트 파싱.

    JSON 블록 추출 → items 배열 파싱. 실패 시 텍스트를 단일 항목으로 변환.
    """
    # Try to extract JSON block
    json_match = re.search(r'\{[\s\S]*"items"\s*:\s*\[[\s\S]*\][\s\S]*\}', raw)
    if json_match:
        try:
            data = json.loads(json_match.group())
            items = []
            for item_data in data.get("items", []):
                items.append(CollectedItem(
                    source_url=item_data.get("source_url", ""),
                    title=item_data.get("title", ""),
                    content=item_data.get("content", ""),
                    domain_label=item_data.get("domain_label", default_domain),
                    data_type=item_data.get("data_type", "article"),
                    relevance_score=float(item_data.get("relevance_score", 0.5)),
                ))
            if items:
                return items
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    # Fallback: treat entire output as a single collected item
    if len(raw.strip()) > 100:
        return [CollectedItem(
            source_url="",
            title="수집 결과 (비구조화)",
            content=raw.strip()[:10000],
            domain_label=default_domain,
            data_type="article",
            relevance_score=0.3,
        )]
    return []


# ── Node entry point ────────────────────────────────────


@node_error_handler("collect_data")
def collect_data_node(state: dict) -> dict:
    """Sonnet Collector 병렬 수집 + CollectionBlackboard 기록."""
    settings = get_settings()
    user_task = state.get("user_task", "")
    session_id = state.get("session_id", "")

    # Build research context from Q&A (same as deep_research)
    questions = (
        state.get("deep_research_questions")
        or state.get("pre_context", {}).get("research_questions", [])
    )
    answers = (
        state.get("deep_research_answers")
        or state.get("pre_context", {}).get("research_answers", [])
    )
    research_context = ""
    if questions and answers:
        lines = ["## 사용자 제공 리서치 방향"]
        for q, a in zip(questions, answers):
            lines.append(f"- Q: {q}\n  A: {a}")
        research_context = "\n".join(lines)

    _logger.info("collect_data_start", task=user_task[:100])

    # ── Plan collection queries ──
    plan = _plan_collection(user_task, research_context)
    tracker = get_tracker()

    if plan and plan.parallel and len(plan.queries) >= 2:
        # ── Parallel collection ──
        _logger.info(
            "parallel_collection_activated",
            queries=[q.query[:50] for q in plan.queries],
        )
        tracker_ids = [f"collector_{i+1}" for i in range(len(plan.queries))]
        tracker.start(tracker_ids)

        async def _run_all():
            tasks = [
                _run_collector(query, user_task, tid)
                for query, tid in zip(plan.queries, tracker_ids)
            ]
            return await safe_gather(
                tasks,
                timeout_seconds=settings.collector_timeout + 60,
                description="parallel_collection",
            )

        results = run_async(
            _run_all(),
            timeout_ceiling=settings.collector_timeout + 120,
        )

        # Collect all items into blackboard
        blackboard = CollectionBlackboard(
            max_chars_per_domain=settings.collection_blackboard_max_chars_per_domain,
        )
        total_items = 0
        for i, (success, items_or_error) in enumerate(results):
            if success and isinstance(items_or_error, list):
                for item in items_or_error:
                    blackboard.write_item(item.domain_label or "research", item)
                    total_items += 1
            else:
                _logger.warning(
                    "collector_gather_failed",
                    query=plan.queries[i].query[:50] if i < len(plan.queries) else "?",
                    error=str(items_or_error),
                )

        tracker.stop()

    else:
        # ── Single collector fallback ──
        _logger.info("single_collection_mode")
        single_query = CollectionQuery(
            query=user_task,
            domain_hint="research",
            search_focus=user_task,
            expected_data_type="article",
        )
        tracker.start(["collector_1"])

        items = run_async(
            _run_collector(single_query, user_task, "collector_1"),
            timeout_ceiling=settings.collector_timeout + 60,
        )

        blackboard = CollectionBlackboard(
            max_chars_per_domain=settings.collection_blackboard_max_chars_per_domain,
        )
        total_items = 0
        if isinstance(items, list):
            for item in items:
                blackboard.write_item(item.domain_label or "research", item)
                total_items += 1

        tracker.stop()

    bb_stats = blackboard.stats()
    _logger.info("collect_data_done", **bb_stats)

    summary = blackboard.get_summary()

    return {
        "collection_blackboard": blackboard.serialize(),
        "research_data_summary": summary,
        "collection_metadata": {
            "total_items": total_items,
            "domains": bb_stats.get("domains", []),
            "items_per_domain": bb_stats.get("items_per_domain", {}),
        },
        "messages": [
            AIMessage(
                content=(
                    f"[Collector] 데이터 수집 완료. "
                    f"{total_items}건 수집, "
                    f"도메인: {', '.join(bb_stats.get('domains', []))}. "
                    f"라우팅 판단으로 이동합니다."
                )
            )
        ],
        "phase": "collection_complete",
    }
