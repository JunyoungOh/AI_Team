"""Security domain tools — NVD (National Vulnerability Database).

NVD API 2.0 — no API key required (5 requests per 30 seconds).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

def _get_http() -> httpx.AsyncClient:
    """Create a fresh AsyncClient — avoids cross-event-loop binding."""
    return httpx.AsyncClient(timeout=30.0, follow_redirects=True)


async def nvd_cve_search(
    keyword: str = "",
    cve_id: str = "",
    results_per_page: int = 5,
) -> str:
    """Search CVE vulnerabilities in the National Vulnerability Database."""
    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    params = {"resultsPerPage": min(results_per_page, 10)}

    if cve_id:
        params["cveId"] = cve_id
    elif keyword:
        params["keywordSearch"] = keyword
    else:
        return json.dumps({"error": "keyword or cve_id required"}, ensure_ascii=False)

    resp = await _get_http().get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    vulnerabilities = data.get("vulnerabilities", [])
    if not vulnerabilities:
        return json.dumps({"results": [], "query": keyword or cve_id}, ensure_ascii=False)

    results = []
    for vuln in vulnerabilities:
        cve = vuln.get("cve", {})
        descriptions = cve.get("descriptions", [])
        desc_en = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")

        metrics = cve.get("metrics", {})
        cvss_score = None
        cvss_severity = None
        for version in ["cvssMetricV31", "cvssMetricV30", "cvssMetricV2"]:
            metric_list = metrics.get(version, [])
            if metric_list:
                cvss_data = metric_list[0].get("cvssData", {})
                cvss_score = cvss_data.get("baseScore")
                cvss_severity = cvss_data.get("baseSeverity", "")
                break

        results.append({
            "cve_id": cve.get("id", ""),
            "description": desc_en[:300],
            "cvss_score": cvss_score,
            "severity": cvss_severity,
            "published": cve.get("published", "")[:10],
            "modified": cve.get("lastModified", "")[:10],
        })

    return json.dumps({"results": results, "total": data.get("totalResults", 0)}, ensure_ascii=False)


NVD_CVE_TOOL: dict[str, Any] = {
    "name": "nvd_cve_search",
    "description": (
        "CVE 취약점 검색 — NIST 국가 취약점 데이터베이스. "
        "키워드 또는 CVE ID로 취약점 정보, CVSS 점수, 심각도 조회. "
        "API 키 불필요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "검색 키워드 (예: log4j, apache, buffer overflow)",
            },
            "cve_id": {
                "type": "string",
                "description": "특정 CVE ID (예: CVE-2021-44228)",
            },
            "results_per_page": {
                "type": "integer",
                "description": "결과 수 (기본: 5, 최대: 10)",
            },
        },
        "required": [],
    },
}
