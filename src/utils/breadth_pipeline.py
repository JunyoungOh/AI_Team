"""Breadth Research pipeline — 검색, 수집, 필터링 순수 함수들.

Breadth Mode의 핵심 로직. 외부 의존(bridge, API)은 호출자가 주입.
bridge는 AnthropicBridge 또는 ClaudeCodeBridge — raw_query/structured_query 인터페이스 사용.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from src.config.settings import get_settings
from src.models.messages import DeepResearchResult
from src.prompts.breadth_prompts import (
    BREADTH_SYNTHESIS_PROMPT,
    FILTER_CHUNKS_PROMPT,
    TRANSLATE_QUERY_PROMPT,
)
from src.utils.logging import get_logger

logger = get_logger(agent_id="breadth_pipeline")

ProgressCb = Callable[[str, int, int], None] | None


# ── Pure helpers ──────────────────────────────────


def chunk_text(text: str, chunk_size: int = 1000) -> list[str]:
    """텍스트를 chunk_size 단위로 분할."""
    if not text:
        return []
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def deduplicate_urls(urls: list[str]) -> list[str]:
    """URL 목록에서 중복 제거 (순서 유지)."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


# ── Bridge-compatible search/fetch helpers ────────


async def _bridge_web_search(bridge: Any, query: str, max_results: int = 5) -> list[str]:
    """web_search를 bridge.raw_query + allowed_tools로 수행, URL 목록 반환."""
    prompt = (
        f"다음 검색어로 웹 검색을 수행하고, 검색 결과에서 URL만 추출하여 "
        f"한 줄에 하나씩 나열하세요. URL만 출력하세요.\n\n검색어: {query}"
    )
    text = await bridge.raw_query(
        system_prompt="웹 검색 결과의 URL만 추출하는 도우미입니다. URL만 출력하세요.",
        user_message=prompt,
        model=get_settings().breadth_filter_model,
        allowed_tools=["web_search"],
        max_turns=3,
        timeout=30,
    )
    urls = re.findall(r'https?://[^\s<>"\']+', text or "")
    return urls[:max_results]


async def _bridge_simple_text(bridge: Any, prompt: str, model: str = "haiku") -> str:
    """bridge.raw_query로 간단한 텍스트 응답 생성 (도구 없음)."""
    text = await bridge.raw_query(
        system_prompt="간결하게 응답하세요.",
        user_message=prompt,
        model=model,
        max_turns=1,
        timeout=15,
    )
    return text.strip() if text else ""


async def _bridge_web_fetch(bridge: Any, url: str) -> str:
    """bridge.raw_query + web_fetch 도구로 URL 내용 가져오기."""
    text = await bridge.raw_query(
        system_prompt="주어진 URL의 내용을 가져와 텍스트로 출력하세요. 요약하지 말고 원문을 그대로 출력하세요.",
        user_message=f"다음 URL의 내용을 가져오세요: {url}",
        model=get_settings().breadth_filter_model,
        allowed_tools=["web_fetch"],
        max_turns=2,
        timeout=30,
    )
    return text or ""


async def _bridge_firecrawl_scrape(bridge: Any, url: str) -> str:
    """firecrawl native tool로 URL 스크래핑."""
    from src.tools.native import TOOL_EXECUTORS
    executor = TOOL_EXECUTORS.get("firecrawl_scrape")
    if executor is None:
        return ""
    try:
        result = await executor(url=url)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception:
        return ""


# ── Async pipeline steps ─────────────────────────


async def search_all_sub_queries(
    sub_queries: list[str],
    bridge: Any,
    progress_cb: ProgressCb = None,
) -> dict[str, list[str]]:
    """Step 2: 서브쿼리별 한국어+영어 검색, URL 수집.

    Returns: {sub_query: [url1, url2, ...]}
    """
    settings = get_settings()

    async def _search_one(sq: str) -> tuple[str, list[str]]:
        urls: list[str] = []
        try:
            kr_results = await _bridge_web_search(bridge, sq, max_results=3)
            urls.extend(kr_results)
        except Exception as e:
            logger.warning("kr_search_failed", query=sq, error=str(e)[:100])

        try:
            en_query = await _bridge_simple_text(
                bridge,
                TRANSLATE_QUERY_PROMPT.format(query=sq),
                model=settings.breadth_filter_model,
            )
            if en_query:
                en_results = await _bridge_web_search(bridge, en_query.strip(), max_results=3)
                urls.extend(en_results)
        except Exception as e:
            logger.warning("en_search_failed", query=sq, error=str(e)[:100])

        return sq, urls

    tasks = [_search_one(sq) for sq in sub_queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    url_map: dict[str, list[str]] = {}
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning("search_sub_query_failed", query=sub_queries[i], error=str(r)[:100])
            url_map[sub_queries[i]] = []
        else:
            sq, urls = r
            url_map[sq] = urls

    if progress_cb:
        progress_cb("search", len(sub_queries), len(sub_queries))

    return url_map


async def scrape_urls(
    urls: list[str],
    bridge: Any,
    progress_cb: ProgressCb = None,
) -> dict[str, str]:
    """Step 3: URL별 텍스트 수집 (WebFetch -> Firecrawl fallback).

    Returns: {url: text}
    """
    settings = get_settings()
    sem = asyncio.Semaphore(settings.breadth_max_concurrent_scrapes)
    collected: dict[str, str] = {}
    done_count = 0

    async def _scrape_one(url: str) -> tuple[str, str | None]:
        nonlocal done_count
        async with sem:
            # WebFetch via bridge
            try:
                text = await asyncio.wait_for(
                    _bridge_web_fetch(bridge, url),
                    timeout=settings.breadth_scrape_timeout,
                )
                if text and len(text.strip()) > 50:
                    done_count += 1
                    if progress_cb:
                        progress_cb("scrape", done_count, len(urls))
                    return url, text
            except Exception:
                pass

            # Firecrawl fallback (direct native tool)
            try:
                text = await asyncio.wait_for(
                    _bridge_firecrawl_scrape(bridge, url),
                    timeout=settings.breadth_scrape_timeout,
                )
                if text and len(text.strip()) > 50:
                    done_count += 1
                    if progress_cb:
                        progress_cb("scrape", done_count, len(urls))
                    return url, text
            except Exception:
                pass

            done_count += 1
            if progress_cb:
                progress_cb("scrape", done_count, len(urls))
            return url, None

    tasks = [_scrape_one(u) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            continue
        url, text = r
        if text:
            collected[url] = text

    logger.info("scrape_done", total=len(urls), collected=len(collected))
    return collected


async def filter_and_summarize(
    scraped: dict[str, str],
    sub_queries: list[str],
    bridge: Any,
    progress_cb: ProgressCb = None,
) -> list[dict[str, str]]:
    """Step 4: Haiku로 청크별 관련성 필터링 + 요약.

    Returns: [{"summary": "...", "source": "url", "sub_query": "..."}]
    """
    settings = get_settings()
    chunk_size = settings.breadth_chunk_size
    all_query_text = " | ".join(sub_queries)

    # 모든 URL의 텍스트를 청크로 분할
    all_chunks: list[tuple[str, str]] = []  # (url, chunk)
    for url, text in scraped.items():
        chunks = chunk_text(text, chunk_size)
        for c in chunks:
            all_chunks.append((url, c))

    if not all_chunks:
        return []

    # 10개씩 배치로 Haiku 호출
    batch_size = 10
    batches = [all_chunks[i:i + batch_size] for i in range(0, len(all_chunks), batch_size)]
    sem = asyncio.Semaphore(5)

    async def _filter_batch(batch: list[tuple[str, str]]) -> str:
        chunks_text = ""
        for i, (url, chunk) in enumerate(batch):
            chunks_text += f"\n--- 조각 {i + 1} [출처: {url}] ---\n{chunk}\n"

        prompt = FILTER_CHUNKS_PROMPT.format(
            sub_query=all_query_text,
            chunks_text=chunks_text,
        )
        async with sem:
            try:
                result = await _bridge_simple_text(
                    bridge, prompt, model=settings.breadth_filter_model,
                )
                return result
            except Exception as e:
                logger.warning("filter_batch_failed", error=str(e)[:100])
                return ""

    tasks = [_filter_batch(b) for b in batches]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    filtered_summaries: list[dict[str, str]] = []
    for r in results:
        if isinstance(r, Exception) or not r:
            continue
        text = r if isinstance(r, str) else str(r)
        if text.strip() == "없음":
            continue
        filtered_summaries.append({"summary": text, "source": "multiple"})

    if progress_cb:
        progress_cb("filter", len(batches), len(batches))

    logger.info(
        "filter_done",
        total_chunks=len(all_chunks),
        batches=len(batches),
        filtered=len(filtered_summaries),
    )
    return filtered_summaries


async def synthesize_report(
    filtered: list[dict[str, str]],
    user_task: str,
    domain: str,
    bridge: Any,
    progress_cb: ProgressCb = None,
) -> DeepResearchResult:
    """Step 5: Sonnet이 필터링된 컨텍스트로 DeepResearchResult 생성."""
    settings = get_settings()

    filtered_context = "\n\n".join(f["summary"] for f in filtered)
    if not filtered_context.strip():
        filtered_context = "(수집된 관련 정보 없음)"

    if progress_cb:
        progress_cb("synthesize", 0, 1)

    result = await bridge.structured_query(
        system_prompt=BREADTH_SYNTHESIS_PROMPT.format(
            user_task=user_task,
            domain=domain,
            filtered_context=filtered_context,
        ),
        user_message=f"위 정보를 종합하여 '{domain}' 도메인 리포트를 작성하세요.",
        output_schema=DeepResearchResult,
        model=settings.breadth_synthesis_model,
        allowed_tools=[],
        timeout=settings.breadth_synthesis_timeout,
        max_tokens=65536,
    )

    if progress_cb:
        progress_cb("synthesize", 1, 1)

    return result
