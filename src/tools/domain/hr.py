"""HR domain tools — BLS (US Bureau of Labor Statistics).

BLS Public Data API v1 — no API key required (25 requests/day limit).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

def _get_http() -> httpx.AsyncClient:
    """Create a fresh AsyncClient — avoids cross-event-loop binding."""
    return httpx.AsyncClient(timeout=30.0, follow_redirects=True)


async def bls_data(
    series_id: str = "LNS14000000",
    start_year: int = 2020,
    end_year: int = 2025,
) -> str:
    """Fetch BLS labor statistics time series data."""
    url = f"https://api.bls.gov/publicAPI/v1/timeseries/data/{series_id}"
    params = {"startyear": str(start_year), "endyear": str(end_year)}
    resp = await _get_http().get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") != "REQUEST_SUCCEEDED":
        return json.dumps({"error": data.get("message", ["Unknown error"]), "series": series_id}, ensure_ascii=False)

    series_data = data.get("Results", {}).get("series", [])
    if not series_data:
        return json.dumps({"error": "No data found", "series": series_id}, ensure_ascii=False)

    records = []
    for entry in series_data[0].get("data", []):
        records.append({
            "year": entry.get("year"),
            "period": entry.get("periodName", ""),
            "value": entry.get("value"),
        })

    return json.dumps({
        "series_id": series_id,
        "data": sorted(records, key=lambda x: (x["year"], x["period"])),
    }, ensure_ascii=False)


BLS_DATA_TOOL: dict[str, Any] = {
    "name": "bls_data",
    "description": (
        "미국 노동 통계 — 실업률, 고용, 임금, CPI 등 노동 시장 데이터. "
        "API 키 불필요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "series_id": {
                "type": "string",
                "description": (
                    "BLS 시리즈 ID. 주요: "
                    "LNS14000000 (실업률), CES0000000001 (비농업 고용), "
                    "CES0500000003 (평균 시급), CUUR0000SA0 (CPI-U)"
                ),
            },
            "start_year": {"type": "integer", "description": "시작 연도 (기본: 2020)"},
            "end_year": {"type": "integer", "description": "종료 연도 (기본: 2025)"},
        },
        "required": ["series_id"],
    },
}
