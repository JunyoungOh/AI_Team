"""Tests for breadth_pipeline pure functions and async pipeline steps."""

import asyncio
from unittest.mock import AsyncMock

from src.utils.breadth_pipeline import chunk_text, deduplicate_urls


def test_chunk_text_basic():
    text = "A" * 2500
    chunks = chunk_text(text, chunk_size=1000)
    assert len(chunks) == 3
    assert len(chunks[0]) == 1000
    assert len(chunks[2]) == 500


def test_chunk_text_short():
    text = "short text"
    chunks = chunk_text(text, chunk_size=1000)
    assert len(chunks) == 1


def test_chunk_text_empty():
    assert chunk_text("", chunk_size=1000) == []


def test_deduplicate_urls():
    urls = ["https://a.com", "https://b.com", "https://a.com", "https://c.com"]
    result = deduplicate_urls(urls)
    assert result == ["https://a.com", "https://b.com", "https://c.com"]


def test_deduplicate_urls_preserves_order():
    urls = ["https://c.com", "https://a.com", "https://b.com", "https://a.com"]
    result = deduplicate_urls(urls)
    assert result == ["https://c.com", "https://a.com", "https://b.com"]


def test_scrape_urls_fallback():
    """WebFetch 실패 시 Firecrawl fallback 동작 확인."""
    from src.utils.breadth_pipeline import scrape_urls

    bridge = AsyncMock()
    bridge.web_fetch = AsyncMock(side_effect=Exception("fetch failed"))
    long_content = "scraped content from firecrawl — " + "data " * 20
    bridge.firecrawl_scrape = AsyncMock(return_value=long_content)

    result = asyncio.run(scrape_urls(["https://example.com"], bridge))
    assert "https://example.com" in result
    assert "firecrawl" in result["https://example.com"]
    bridge.firecrawl_scrape.assert_called_once()


def test_filter_and_summarize_failure_passthrough():
    """필터링 실패 시 빈 리스트 반환 (에러 전파 안 함)."""
    from src.utils.breadth_pipeline import filter_and_summarize

    bridge = AsyncMock()
    bridge.simple_query = AsyncMock(side_effect=Exception("haiku down"))

    result = asyncio.run(filter_and_summarize(
        {"https://a.com": "A" * 2000},
        ["test query"],
        bridge,
    ))
    assert isinstance(result, list)
