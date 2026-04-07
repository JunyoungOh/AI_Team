"""Tool registry - maps worker domains to Claude Code + MCP tool names.

Claude Code executes these tools internally during headless queries.
MCP tools are auto-loaded from the project's .mcp.json config.
If an MCP server fails to start (e.g., missing API key), its tools
are silently unavailable — Claude uses remaining tools or falls back
to knowledge-only execution.

Domain-specific tools (finance, chart, legal, data) are native Python
implementations in src/tools/domain/ — registered in native.py.
"""

from __future__ import annotations

# ── MCP Tool Constants ───────────────────────────

# Fetch (HTTP direct access)
_FETCH = "mcp__fetch__fetch"

# Firecrawl (web scraping & extraction)
_FC_SCRAPE = "mcp__firecrawl__firecrawl_scrape"
_FC_CRAWL = "mcp__firecrawl__firecrawl_crawl"
_FC_EXTRACT = "mcp__firecrawl__firecrawl_extract"

# GitHub
_GH_SEARCH = "mcp__github__search_code"
_GH_FILE = "mcp__github__get_file_contents"
_GH_PR = "mcp__github__create_pull_request"
_GH_ISSUES = "mcp__github__list_issues"
_GH_CODE_SCAN = "mcp__github__list_code_scanning_alerts"
_GH_SECRET_SCAN = "mcp__github__list_secret_scanning_alerts"

# ── Composable Tool Sets ─────────────────────────

_GITHUB_DEV = [_GH_SEARCH, _GH_FILE, _GH_PR, _GH_ISSUES]
_GITHUB_SEC = [_GH_CODE_SCAN, _GH_SECRET_SCAN]
_FILE_OPS = ["Read", "Write", "Bash", "Glob", "Grep"]

# Search tool sets: native WebSearch/WebFetch + Firecrawl for JS-heavy pages
_BUILTIN_SEARCH = ["WebSearch", "WebFetch", _FC_SCRAPE]
_BUILTIN_DEEP_SEARCH = ["WebSearch", "WebFetch", "Bash", _FC_SCRAPE]

# ── Domain-Specific Tool Names (native Python, registered in native.py) ──

_FINANCE_CORE = [
    "world_bank_data", "exchange_rate", "imf_data",
    "dbnomics_data", "pykrx_stock", "yfinance_data",
]
_CHART = ["quickchart_render"]
_PATENT = ["patentsview_search"]
_HF_DATA = ["hf_datasets_search"]
_BLS = ["bls_data"]
_NVD = ["nvd_cve_search"]
_DART = ["dart_financial"]
_ECOS = ["ecos_data"]
_KOSIS = ["kosis_data"]

# ── Domain → Tool Mapping ────────────────────────

DOMAIN_CLAUDE_TOOLS: dict[str, list[str]] = {
    # ── Research Workers ──
    "researcher": _BUILTIN_SEARCH,
    "deep_researcher": _BUILTIN_DEEP_SEARCH,
    "data_analyst": _BUILTIN_DEEP_SEARCH + _CHART + _HF_DATA + _KOSIS,
    "fact_checker": _BUILTIN_SEARCH,
    "tech_researcher": _BUILTIN_SEARCH,

    # ── Engineering Workers (GitHub MCP required) ──
    "backend_developer": ["WebSearch", "WebFetch"] + _GITHUB_DEV + [_FETCH] + _FILE_OPS,
    "frontend_developer": ["WebSearch", "WebFetch"] + _GITHUB_DEV + [_FETCH] + _FILE_OPS,
    "devops_engineer": ["WebSearch", "WebFetch"] + _GITHUB_DEV + [_FETCH] + _FILE_OPS,
    "architect": ["WebSearch", "WebFetch"] + _GITHUB_DEV + [_FETCH, "Read", "Glob", "Grep"],

    # ── Finance Workers — 금융 특화 도구 배정 ──
    "financial_analyst": _BUILTIN_DEEP_SEARCH + _FINANCE_CORE + _DART + _ECOS + _CHART,
    "accountant": _BUILTIN_DEEP_SEARCH + ["exchange_rate"] + _DART + _CHART,
    "finance_researcher": _BUILTIN_SEARCH + _FINANCE_CORE + ["fear_greed_index"] + _DART + _ECOS + _CHART,

    # ── Marketing Workers ──
    "content_writer": _BUILTIN_SEARCH,
    "strategist": _BUILTIN_SEARCH,
    "designer": _BUILTIN_SEARCH,
    "market_researcher": _BUILTIN_SEARCH + _CHART,

    # ── Operations Workers ──
    "project_manager": _BUILTIN_SEARCH,
    "process_analyst": _BUILTIN_SEARCH,
    "ops_researcher": _BUILTIN_SEARCH,

    # ── HR Workers ──
    "recruiter": _BUILTIN_SEARCH + _BLS + _KOSIS,
    "training_specialist": _BUILTIN_SEARCH + _BLS,
    "org_developer": _BUILTIN_SEARCH + _BLS,
    "compensation_analyst": _BUILTIN_DEEP_SEARCH + ["exchange_rate"] + _CHART + _BLS + _KOSIS,
    "hr_researcher": _BUILTIN_SEARCH + _BLS + _KOSIS,

    # ── Legal Workers — 특허 도구 배정 ──
    "legal_counsel": _BUILTIN_SEARCH,
    "compliance_officer": _BUILTIN_SEARCH,
    "ip_specialist": _BUILTIN_SEARCH + _PATENT,
    "legal_researcher": _BUILTIN_SEARCH + _PATENT,

    # ── Data/AI Workers — 데이터셋 도구 배정 ──
    "data_engineer": ["WebSearch", "WebFetch", _FC_SCRAPE, _GH_SEARCH, _FETCH] + _FILE_OPS,
    "ml_engineer": ["WebSearch", "WebFetch", _FC_SCRAPE, _GH_SEARCH, _FETCH] + _FILE_OPS + _HF_DATA,
    "data_scientist": _BUILTIN_DEEP_SEARCH + _CHART + _HF_DATA,
    "data_researcher": _BUILTIN_SEARCH + _HF_DATA,

    # ── Product Workers ──
    "product_manager": _BUILTIN_SEARCH,
    "ux_researcher": _BUILTIN_SEARCH,
    "product_analyst": _BUILTIN_DEEP_SEARCH + _CHART,

    # ── Security Workers (GitHub Security MCP required) ──
    "security_analyst": ["WebSearch", "WebFetch"] + _GITHUB_SEC + [_FETCH, "Bash"] + _NVD,
    "security_engineer": ["WebSearch", "WebFetch"] + _GITHUB_SEC + [_FETCH] + _FILE_OPS + _NVD,
    "privacy_specialist": ["WebSearch", "WebFetch"] + _GITHUB_SEC + [_FETCH] + _NVD,
    "security_researcher": _BUILTIN_SEARCH + _NVD,
}

# Default fallback for unknown domains
_DEFAULT_TOOLS = ["WebSearch", "WebFetch", _FC_SCRAPE]

# ── Tool Categories (7종 — 인스턴트 워커의 도구 배정 기준) ──

TOOL_CATEGORIES: dict[str, list[str]] = {
    "research": _BUILTIN_SEARCH,
    "data": _BUILTIN_DEEP_SEARCH + _CHART + _HF_DATA + _KOSIS,
    "development": ["WebSearch", "WebFetch"] + _GITHUB_DEV + [_FETCH, _FC_SCRAPE] + _FILE_OPS,
    "finance": _BUILTIN_DEEP_SEARCH + _FINANCE_CORE + ["fear_greed_index"] + _DART + _ECOS + _CHART,
    "security": ["WebSearch", "WebFetch"] + _GITHUB_SEC + [_FETCH, _FC_SCRAPE, "Bash"] + _NVD,
    "legal": _BUILTIN_SEARCH + _PATENT,
    "hr": _BUILTIN_SEARCH + _BLS + _KOSIS,
}

VALID_TOOL_CATEGORIES: list[str] = list(TOOL_CATEGORIES.keys())


def get_tools_for_category(category: str) -> list[str]:
    """Get tool list for a tool category. Returns default tools for unknown categories."""
    return TOOL_CATEGORIES.get(category, _DEFAULT_TOOLS)


def get_claude_tools_for_domain(worker_domain: str) -> list[str]:
    """Get Claude Code tool names for a given worker domain.

    With instant workers, this falls back to tool_category lookup.
    Legacy DOMAIN_CLAUDE_TOOLS is preserved for backward compatibility.
    """
    return DOMAIN_CLAUDE_TOOLS.get(worker_domain, _DEFAULT_TOOLS)
