"""Node: breadth_research — 경량 검색+수집 파이프라인으로 넓은 조사 실행.

Breadth Mode 전체 흐름:
  서브쿼리 -> 검색 -> 수집 -> Haiku 필터링 -> Sonnet 합성 -> DeepResearchResult
"""

from __future__ import annotations

import asyncio
import html as html_mod
import time
from pathlib import Path

from langchain_core.messages import AIMessage

from src.config.settings import get_settings
from src.graphs.nodes.deep_research import (
    _save_domain_html,
    _save_single_result_html,
    _safe_filename,
    _to_final_report,
)
from src.models.messages import DeepResearchResult
from src.utils.breadth_pipeline import (
    deduplicate_urls,
    filter_and_summarize,
    scrape_urls,
    search_all_sub_queries,
    synthesize_report,
)
from src.utils.guards import node_error_handler
from src.utils.logging import get_logger
from src.utils.parallel import run_async
from src.utils.progress import WorkerStatus, get_tracker

logger = get_logger(agent_id="breadth_research")


def _get_bridge():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


async def _run_breadth_single(
    domain: str,
    sub_queries: list[str],
    user_task: str,
    session_id: str,
    tracker=None,
    wid: str = "",
) -> DeepResearchResult:
    """단일 도메인 Breadth Research 실행."""
    bridge = _get_bridge()

    def _progress(step: str, done: int, total: int) -> None:
        if tracker and wid:
            labels = {"search": "검색", "scrape": "수집", "filter": "필터링", "synthesize": "합성"}
            label = labels.get(step, step)
            tracker.update(wid, WorkerStatus.RUNNING, summary=f"{label} 중 ({done}/{total})...")

    # Step 2: 검색
    url_map = await search_all_sub_queries(sub_queries, bridge, progress_cb=_progress)
    all_urls = deduplicate_urls([u for urls in url_map.values() for u in urls])
    logger.info("breadth_search_done", urls=len(all_urls))

    if not all_urls:
        raise RuntimeError("검색 결과 없음 — 모든 서브쿼리 실패")

    # Step 3: 수집
    scraped = await scrape_urls(all_urls, bridge, progress_cb=_progress)
    if not scraped:
        raise RuntimeError("수집 결과 없음 — 모든 URL 스크래핑 실패")

    # Step 4: 필터링
    filtered = await filter_and_summarize(scraped, sub_queries, bridge, progress_cb=_progress)

    # Step 5: 합성
    result = await synthesize_report(filtered, user_task, domain, bridge, progress_cb=_progress)
    return result


def _make_fallback_result(domain: str, error: Exception) -> DeepResearchResult:
    """실패 시 최소 DeepResearchResult 생성."""
    error_text = html_mod.escape(str(error)[:200])
    return DeepResearchResult(
        domain=domain,
        executive_summary=f"{domain} 리서치 실패: {str(error)[:100]}",
        report_html=f'<div style="padding:20px;color:#c62828;">리서치 실패: {error_text}</div>',
        confidence_score=1,
        gaps=[f"리서치 실패: {str(error)[:100]}"],
    )


@node_error_handler("breadth_research")
def breadth_research_node(state: dict) -> dict:
    """Breadth Research 메인 노드 — 모든 도메인에 대해 파이프라인 실행."""
    selected_domains = state.get("selected_domains", [])
    domains = selected_domains if selected_domains else ["research"]
    user_task = state.get("user_task", "")
    sub_queries = state.get("deep_research_sub_queries", [])
    session_id = state.get("session_id", "unknown")
    settings = get_settings()

    if not domains:
        return {"phase": "error", "error_message": "Breadth Research: 도메인 없음"}

    if not sub_queries:
        return {"phase": "error", "error_message": "Breadth Research: 서브쿼리 없음"}

    # Initialize progress tracker
    tracker = get_tracker(session_id)
    tracker.start([
        (f"breadth_{_safe_filename(d)}", d) for d in domains
    ])

    start_time = time.monotonic()
    messages: list[AIMessage] = []

    if len(domains) == 1:
        # ── 단일 도메인 ──
        domain = domains[0]
        wid = f"breadth_{_safe_filename(domain)}"
        tracker.update(wid, WorkerStatus.RUNNING, tier=1)

        try:
            result = run_async(
                asyncio.wait_for(
                    _run_breadth_single(domain, sub_queries, user_task, session_id, tracker, wid),
                    timeout=settings.breadth_total_timeout,
                )
            )
            report_path = _save_single_result_html(result, session_id)
            tracker.update(wid, WorkerStatus.DONE, tier=1, summary=result.executive_summary[:50])

            # Emit research_finding events for UI
            if session_id:
                from src.modes.common import emit_mode_event
                for sq in sub_queries:
                    for finding in result.key_findings:
                        emit_mode_event(session_id, {
                            "type": "research_finding",
                            "data": {
                                "sub_query": sq,
                                "finding": finding.content,
                                "source": finding.source or "",
                                "depth": 0,
                            },
                        })
                        break  # one finding per sub_query to avoid flooding

            tracker.stop()

            elapsed = time.monotonic() - start_time
            logger.info("breadth_single_done", domain=domain, elapsed=round(elapsed, 1))

            messages.append(AIMessage(
                content=f"[Breadth Research] {domain} 완료 — {len(result.sources)}개 출처"
            ))

            return {
                "final_report": _to_final_report(result, report_path),
                "report_file_path": report_path,
                "messages": messages,
                "phase": "complete",
                "deep_research_results": [result.model_dump()],
            }

        except Exception as e:
            tracker.update(wid, WorkerStatus.FAILED, tier=1, summary=str(e)[:50])
            tracker.stop()
            raise

    else:
        # ── 다중 도메인: 병렬 실행 ──
        for d in domains:
            wid = f"breadth_{_safe_filename(d)}"
            tracker.update(wid, WorkerStatus.RUNNING, tier=1)

        async def _run_all():
            coros = [
                asyncio.wait_for(
                    _run_breadth_single(
                        d, sub_queries, user_task, session_id,
                        tracker, f"breadth_{_safe_filename(d)}",
                    ),
                    timeout=settings.breadth_total_timeout,
                )
                for d in domains
            ]
            return await asyncio.gather(*coros, return_exceptions=True)

        raw_results = run_async(_run_all())

        results: list[DeepResearchResult] = []
        for domain, raw in zip(domains, raw_results):
            wid = f"breadth_{_safe_filename(domain)}"
            if isinstance(raw, Exception):
                logger.error("breadth_domain_failed", domain=domain, error=str(raw)[:200])
                results.append(_make_fallback_result(domain, raw))
                tracker.update(wid, WorkerStatus.FAILED, tier=1, summary=str(raw)[:50])
            else:
                results.append(raw)
                _save_domain_html(raw, session_id)
                tracker.update(wid, WorkerStatus.DONE, tier=1, summary=raw.executive_summary[:50])

                # Emit research_finding events for UI
                if session_id:
                    from src.modes.common import emit_mode_event
                    for sq in sub_queries:
                        for finding in raw.key_findings:
                            emit_mode_event(session_id, {
                                "type": "research_finding",
                                "data": {
                                    "sub_query": sq,
                                    "finding": finding.content,
                                    "source": finding.source or "",
                                    "depth": 0,
                                },
                            })
                            break  # one finding per sub_query to avoid flooding

        tracker.stop()
        elapsed = time.monotonic() - start_time
        logger.info("breadth_multi_done", count=len(results), elapsed=round(elapsed, 1))

        for r in results:
            messages.append(AIMessage(
                content=f"[Breadth Research] {r.domain}: {len(r.sources)}개 출처"
            ))

        return {
            "deep_research_results": [r.model_dump() for r in results],
            "messages": messages,
            "phase": "ceo_final_report",
        }
