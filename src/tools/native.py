"""Native Python tool implementations for Anthropic API direct mode.

Only client-side tools are implemented here. Server-side tools (web_search,
web_fetch) are handled by the Anthropic API automatically.

Tier 1: web_search + web_fetch (server-side, not in this file)
Tier 2: firecrawl (web_fetch fallback) + github + mem0 (this file)
"""

from __future__ import annotations

import base64
from typing import Any, Callable

import httpx

from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(agent_id="native-tools")

def _get_http() -> httpx.AsyncClient:
    """Create a fresh AsyncClient — avoids cross-event-loop binding."""
    return httpx.AsyncClient(timeout=30.0, follow_redirects=True)


class ToolError(Exception):
    """Tool execution failed — returned as tool_result with is_error=True."""


# ── Firecrawl (web_fetch fallback) ───────────────────


async def firecrawl_scrape(url: str, formats: list[str] | None = None) -> str:
    """Scrape a single page via Firecrawl — fallback when web_fetch fails."""
    settings = get_settings()
    if not settings.firecrawl_api_key:
        raise ToolError("FIRECRAWL_API_KEY not configured")
    resp = await _get_http().post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
        json={"url": url, "formats": formats or ["markdown"]},
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    return data.get("markdown", data.get("content", f"No content from {url}"))


FIRECRAWL_SCRAPE_TOOL: dict[str, Any] = {
    "name": "firecrawl_scrape",
    "description": (
        "웹 스크래핑 (Firecrawl) — web_fetch로 가져올 수 없는 페이지의 폴백. "
        "봇 차단, JS 렌더링 필요 사이트에 효과적."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "스크래핑할 URL"},
            "formats": {
                "type": "array",
                "items": {"type": "string"},
                "description": "출력 형식 (기본: ['markdown'])",
            },
        },
        "required": ["url"],
    },
}


async def firecrawl_crawl(url: str, max_pages: int = 10) -> str:
    """Crawl multiple pages from a site via Firecrawl."""
    settings = get_settings()
    if not settings.firecrawl_api_key:
        raise ToolError("FIRECRAWL_API_KEY not configured")
    resp = await _get_http().post(
        "https://api.firecrawl.dev/v1/crawl",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
        json={"url": url, "limit": max_pages, "formats": ["markdown"]},
        timeout=60.0,
    )
    resp.raise_for_status()
    result = resp.json()
    # Crawl returns a job ID — poll for results
    job_id = result.get("id", "")
    if not job_id:
        return result.get("data", "No crawl results")

    # Poll up to 5 times
    import asyncio
    for _ in range(5):
        await asyncio.sleep(3)
        status_resp = await _get_http().get(
            f"https://api.firecrawl.dev/v1/crawl/{job_id}",
            headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
        )
        status_resp.raise_for_status()
        status = status_resp.json()
        if status.get("status") == "completed":
            pages = status.get("data", [])
            parts = []
            for page in pages[:max_pages]:
                md = page.get("markdown", "")
                url_ = page.get("metadata", {}).get("url", "")
                parts.append(f"## {url_}\n\n{md[:5000]}")
            return "\n\n---\n\n".join(parts) if parts else "No pages crawled"
    return f"Crawl job {job_id} still in progress after polling"


FIRECRAWL_CRAWL_TOOL: dict[str, Any] = {
    "name": "firecrawl_crawl",
    "description": "멀티페이지 크롤링 — 사이트 내 여러 페이지를 자동 수집.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "크롤링 시작 URL"},
            "max_pages": {
                "type": "integer",
                "description": "최대 수집 페이지 수 (기본: 10)",
            },
        },
        "required": ["url"],
    },
}


# ── GitHub ───────────────────────────────────────────

_GITHUB_API = "https://api.github.com"


def _gh_headers() -> dict[str, str]:
    settings = get_settings()
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_personal_access_token:
        headers["Authorization"] = f"Bearer {settings.github_personal_access_token}"
    return headers


async def github_search_code(query: str) -> str:
    """Search code on GitHub."""
    resp = await _get_http().get(
        f"{_GITHUB_API}/search/code",
        params={"q": query, "per_page": 10},
        headers=_gh_headers(),
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    parts = []
    for item in items[:10]:
        repo = item.get("repository", {}).get("full_name", "")
        path = item.get("path", "")
        url = item.get("html_url", "")
        parts.append(f"- **{repo}** `{path}` — {url}")
    return "\n".join(parts) if parts else "No results found"


GITHUB_SEARCH_TOOL: dict[str, Any] = {
    "name": "github_search_code",
    "description": "GitHub 코드 검색 — 코드 패턴, 라이브러리 사용법 검색.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색 쿼리"},
        },
        "required": ["query"],
    },
}


async def github_get_file(owner: str, repo: str, path: str, ref: str = "main") -> str:
    """Get file contents from a GitHub repository."""
    resp = await _get_http().get(
        f"{_GITHUB_API}/repos/{owner}/{repo}/contents/{path}",
        params={"ref": ref},
        headers=_gh_headers(),
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode(errors="replace")
        if len(content) > 50000:
            content = content[:50000] + "\n\n[...truncated]"
        return content
    return data.get("content", "Unable to decode file")


GITHUB_GET_FILE_TOOL: dict[str, Any] = {
    "name": "github_get_file",
    "description": "GitHub 파일 조회 — 리포의 파일 내용 가져오기.",
    "input_schema": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "리포 소유자 (org 또는 user)"},
            "repo": {"type": "string", "description": "리포 이름"},
            "path": {"type": "string", "description": "파일 경로"},
            "ref": {"type": "string", "description": "브랜치/태그 (기본: main)"},
        },
        "required": ["owner", "repo", "path"],
    },
}


async def github_list_issues(owner: str, repo: str, state: str = "open",
                              per_page: int = 10) -> str:
    """List issues from a GitHub repository."""
    resp = await _get_http().get(
        f"{_GITHUB_API}/repos/{owner}/{repo}/issues",
        params={"state": state, "per_page": per_page},
        headers=_gh_headers(),
    )
    resp.raise_for_status()
    issues = resp.json()
    parts = []
    for issue in issues[:per_page]:
        num = issue.get("number", "")
        title = issue.get("title", "")
        labels = ", ".join(l.get("name", "") for l in issue.get("labels", []))
        parts.append(f"- #{num} **{title}** [{labels}]")
    return "\n".join(parts) if parts else "No issues found"


GITHUB_LIST_ISSUES_TOOL: dict[str, Any] = {
    "name": "github_list_issues",
    "description": "GitHub 이슈 목록 조회.",
    "input_schema": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "리포 소유자"},
            "repo": {"type": "string", "description": "리포 이름"},
            "state": {"type": "string", "description": "이슈 상태 (open/closed/all, 기본: open)"},
            "per_page": {"type": "integer", "description": "결과 수 (기본: 10)"},
        },
        "required": ["owner", "repo"],
    },
}


async def github_create_pr(owner: str, repo: str, title: str, body: str,
                            head: str, base: str = "main") -> str:
    """Create a pull request on GitHub."""
    resp = await _get_http().post(
        f"{_GITHUB_API}/repos/{owner}/{repo}/pulls",
        headers=_gh_headers(),
        json={"title": title, "body": body, "head": head, "base": base},
    )
    resp.raise_for_status()
    pr = resp.json()
    return f"PR #{pr.get('number')} created: {pr.get('html_url', '')}"


GITHUB_CREATE_PR_TOOL: dict[str, Any] = {
    "name": "github_create_pr",
    "description": "GitHub Pull Request 생성.",
    "input_schema": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "리포 소유자"},
            "repo": {"type": "string", "description": "리포 이름"},
            "title": {"type": "string", "description": "PR 제목"},
            "body": {"type": "string", "description": "PR 설명"},
            "head": {"type": "string", "description": "소스 브랜치"},
            "base": {"type": "string", "description": "타깃 브랜치 (기본: main)"},
        },
        "required": ["owner", "repo", "title", "body", "head"],
    },
}


# ── mem0 ─────────────────────────────────────────────

_MEM0_API = "https://api.mem0.ai/v1"


def _mem0_headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Token {settings.mem0_api_key}",
        "Content-Type": "application/json",
    }


async def mem0_search(query: str, user_id: str = "enterprise-agent",
                       limit: int = 10) -> str:
    """Search memories in mem0."""
    settings = get_settings()
    if not settings.mem0_api_key:
        return "mem0 not configured (MEM0_API_KEY missing)"
    resp = await _get_http().post(
        f"{_MEM0_API}/memories/search/",
        headers=_mem0_headers(),
        json={"query": query, "user_id": user_id, "limit": limit},
    )
    resp.raise_for_status()
    memories = resp.json().get("results", resp.json().get("memories", []))
    if not memories:
        return "No relevant memories found"
    parts = []
    for mem in memories[:limit]:
        text = mem.get("memory", mem.get("text", ""))
        score = mem.get("score", "")
        parts.append(f"- {text}" + (f" (relevance: {score:.2f})" if score else ""))
    return "\n".join(parts)


MEM0_SEARCH_TOOL: dict[str, Any] = {
    "name": "mem0_search",
    "description": "기억 검색 — 이전 세션에서 저장한 관련 지식 조회.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색할 내용"},
            "user_id": {"type": "string", "description": "사용자 ID (기본: enterprise-agent)"},
            "limit": {"type": "integer", "description": "최대 결과 수 (기본: 10)"},
        },
        "required": ["query"],
    },
}


async def mem0_add(content: str, user_id: str = "enterprise-agent",
                    metadata: dict | None = None) -> str:
    """Add a memory to mem0."""
    settings = get_settings()
    if not settings.mem0_api_key:
        return "mem0 not configured (MEM0_API_KEY missing)"
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": content}],
        "user_id": user_id,
    }
    if metadata:
        body["metadata"] = metadata
    resp = await _get_http().post(
        f"{_MEM0_API}/memories/",
        headers=_mem0_headers(),
        json=body,
    )
    resp.raise_for_status()
    return "Memory saved successfully"


MEM0_ADD_TOOL: dict[str, Any] = {
    "name": "mem0_add",
    "description": "기억 저장 — 핵심 발견사항을 세션 간 메모리에 저장.",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "저장할 내용"},
            "user_id": {"type": "string", "description": "사용자 ID (기본: enterprise-agent)"},
            "metadata": {
                "type": "object",
                "description": "추가 메타데이터 (선택)",
            },
        },
        "required": ["content"],
    },
}


# ── Domain-specific tools ────────────────────────────

from src.tools.domain.finance import (
    world_bank_data, WORLD_BANK_TOOL,
    exchange_rate, EXCHANGE_RATE_TOOL,
    imf_data, IMF_DATA_TOOL,
    dbnomics_data, DBNOMICS_TOOL,
    pykrx_stock, PYKRX_TOOL,
    yfinance_data, YFINANCE_TOOL,
    fear_greed_index, FEAR_GREED_TOOL,
    dart_financial, DART_FINANCIAL_TOOL,
    ecos_data, ECOS_DATA_TOOL,
    kosis_data, KOSIS_DATA_TOOL,
)
from src.tools.domain.chart import quickchart_render, QUICKCHART_TOOL
from src.tools.domain.legal import patentsview_search, PATENTSVIEW_TOOL
from src.tools.domain.data import hf_datasets_search, HF_DATASETS_TOOL
from src.tools.domain.hr import bls_data, BLS_DATA_TOOL
from src.tools.domain.security import nvd_cve_search, NVD_CVE_TOOL


# ── Registry ─────────────────────────────────────────

CLIENT_TOOLS: dict[str, dict[str, Any]] = {
    # Core tools
    "firecrawl_scrape": FIRECRAWL_SCRAPE_TOOL,
    "firecrawl_crawl": FIRECRAWL_CRAWL_TOOL,
    "github_search_code": GITHUB_SEARCH_TOOL,
    "github_get_file": GITHUB_GET_FILE_TOOL,
    "github_list_issues": GITHUB_LIST_ISSUES_TOOL,
    "github_create_pr": GITHUB_CREATE_PR_TOOL,
    # Domain: Finance
    "world_bank_data": WORLD_BANK_TOOL,
    "exchange_rate": EXCHANGE_RATE_TOOL,
    "imf_data": IMF_DATA_TOOL,
    "dbnomics_data": DBNOMICS_TOOL,
    "pykrx_stock": PYKRX_TOOL,
    "yfinance_data": YFINANCE_TOOL,
    "fear_greed_index": FEAR_GREED_TOOL,
    "dart_financial": DART_FINANCIAL_TOOL,
    "ecos_data": ECOS_DATA_TOOL,
    "kosis_data": KOSIS_DATA_TOOL,
    # Domain: Chart (전 도메인 공용)
    "quickchart_render": QUICKCHART_TOOL,
    # Domain: Legal
    "patentsview_search": PATENTSVIEW_TOOL,
    # Domain: Data
    "hf_datasets_search": HF_DATASETS_TOOL,
    # Domain: HR
    "bls_data": BLS_DATA_TOOL,
    # Domain: Security
    "nvd_cve_search": NVD_CVE_TOOL,
}

TOOL_EXECUTORS: dict[str, Callable] = {
    # Core tools
    "firecrawl_scrape": firecrawl_scrape,
    "firecrawl_crawl": firecrawl_crawl,
    "github_search_code": github_search_code,
    "github_get_file": github_get_file,
    "github_list_issues": github_list_issues,
    "github_create_pr": github_create_pr,
    # Domain: Finance
    "world_bank_data": world_bank_data,
    "exchange_rate": exchange_rate,
    "imf_data": imf_data,
    "dbnomics_data": dbnomics_data,
    "pykrx_stock": pykrx_stock,
    "yfinance_data": yfinance_data,
    "fear_greed_index": fear_greed_index,
    "dart_financial": dart_financial,
    "ecos_data": ecos_data,
    "kosis_data": kosis_data,
    # Domain: Chart
    "quickchart_render": quickchart_render,
    # Domain: Legal
    "patentsview_search": patentsview_search,
    # Domain: Data
    "hf_datasets_search": hf_datasets_search,
    # Domain: HR
    "bls_data": bls_data,
    # Domain: Security
    "nvd_cve_search": nvd_cve_search,
}
