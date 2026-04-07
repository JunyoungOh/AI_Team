"""Legal domain tools (Tier 1 — no API key required).

- patentsview_search: US patent database (1976-present)
"""

from __future__ import annotations

import json
from typing import Any

async def patentsview_search(
    query: str,
    per_page: int = 10,
) -> str:
    """Search US patents via Google Patents (public, no key required)."""
    # Google Patents is publicly accessible; fetch search results page
    encoded_query = query.replace(" ", "+")
    url = f"https://patents.google.com/?q={encoded_query}&num={min(per_page, 10)}&oq={encoded_query}"

    # Use the structured search results from the public API
    # Fallback: return search URL for the worker to fetch with web_fetch
    return json.dumps({
        "search_url": url,
        "query": query,
        "instruction": (
            "위 URL을 web_fetch로 가져와서 특허 결과를 파싱하세요. "
            "또는 web_search로 'site:patents.google.com {query}'를 검색하세요."
        ),
    }, ensure_ascii=False)


PATENTSVIEW_TOOL: dict[str, Any] = {
    "name": "patentsview_search",
    "description": (
        "미국 특허 검색 — Google Patents 검색 URL 생성. "
        "반환된 URL을 web_fetch로 가져오거나 web_search로 검색하세요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "검색 키워드 (영문)",
            },
            "per_page": {
                "type": "integer",
                "description": "결과 수 (기본: 10, 최대: 10)",
            },
        },
        "required": ["query"],
    },
}
