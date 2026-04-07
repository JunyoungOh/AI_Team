"""Data domain tools (Tier 1 — no API key required).

- hf_datasets_search: Search HuggingFace Datasets Hub (100k+ datasets)
"""

from __future__ import annotations

import json
from typing import Any

import httpx

def _get_http() -> httpx.AsyncClient:
    """Create a fresh AsyncClient — avoids cross-event-loop binding."""
    return httpx.AsyncClient(timeout=30.0, follow_redirects=True)


async def hf_datasets_search(
    query: str,
    limit: int = 10,
    sort: str = "downloads",
) -> str:
    """Search HuggingFace Datasets Hub for ML datasets."""
    url = "https://huggingface.co/api/datasets"
    params = {
        "search": query,
        "limit": min(limit, 20),
        "sort": sort,
        "direction": -1,
    }
    resp = await _get_http().get(url, params=params)
    resp.raise_for_status()
    datasets = resp.json()

    if not datasets:
        return json.dumps({"results": [], "query": query}, ensure_ascii=False)

    results = []
    for ds in datasets:
        record = {
            "id": ds.get("id", ""),
            "description": (ds.get("description") or "")[:200],
            "downloads": ds.get("downloads", 0),
            "likes": ds.get("likes", 0),
            "tags": ds.get("tags", [])[:5],
        }
        card = ds.get("cardData") or {}
        if card.get("language"):
            record["language"] = card["language"]
        if card.get("task_categories"):
            record["tasks"] = card["task_categories"]
        results.append(record)

    return json.dumps({"results": results, "query": query}, ensure_ascii=False)


HF_DATASETS_TOOL: dict[str, Any] = {
    "name": "hf_datasets_search",
    "description": (
        "HuggingFace 데이터셋 검색 — 10만+ ML 데이터셋 메타데이터 탐색. "
        "텍스트, 이미지, 오디오, 테이블 등 다양한 유형. "
        "다운로드 수, 태그, 언어, 태스크 카테고리로 필터링."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "검색 키워드 (예: sentiment analysis, Korean NER, image classification)",
            },
            "limit": {
                "type": "integer",
                "description": "결과 수 (기본: 10, 최대: 20)",
            },
            "sort": {
                "type": "string",
                "description": "정렬 기준: downloads, likes, created (기본: downloads)",
                "enum": ["downloads", "likes", "created"],
            },
        },
        "required": ["query"],
    },
}
