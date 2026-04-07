"""SDK tool definitions for messages.create(tools=[...]) parameter.

Two types of tools:
- Server tools (web_search, web_fetch): Anthropic executes server-side, no ToolExecutor needed
- Custom tools (firecrawl, github, file ops): We execute via ToolExecutor
"""

from __future__ import annotations

# ── Server tools (Anthropic executes these) ──────

SERVER_WEB_SEARCH = {
    "type": "web_search_20250305",
    "name": "web_search",
}

SERVER_WEB_FETCH = {
    "type": "web_fetch_20250910",
    "name": "web_fetch",
}

# ── Custom tool schemas (we execute these) ──────

TOOL_SERPER_SEARCH = {
    "name": "serper_search",
    "description": "Search Google via Serper API. Returns top 10 results with titles, URLs, and snippets.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords"}
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

TOOL_FIRECRAWL_SCRAPE = {
    "name": "firecrawl_scrape",
    "description": "Scrape a URL to extract structured markdown content using Firecrawl.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to scrape"}
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}

TOOL_GITHUB_SEARCH = {
    "name": "github_search",
    "description": "Search GitHub repositories for code matching a query.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "GitHub code search query"}
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

TOOL_READ_FILE = {
    "name": "read_file",
    "description": "Read file contents from the local filesystem.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to file"}
        },
        "required": ["file_path"],
        "additionalProperties": False,
    },
}

TOOL_WRITE_FILE = {
    "name": "write_file",
    "description": "Write content to a file on the local filesystem.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    },
}

TOOL_RUN_BASH = {
    "name": "run_bash",
    "description": "Execute a shell command and return stdout/stderr.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"}
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}

TOOL_GLOB = {
    "name": "glob",
    "description": "Find files matching a glob pattern in the working directory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py')"}
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
}

TOOL_GREP = {
    "name": "grep",
    "description": "Search file contents for a regex pattern.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search"},
            "path": {"type": "string", "description": "Directory or file to search in"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
}

# ── Tool sets ────────────────────────────────

SEARCH_TOOLS = [SERVER_WEB_SEARCH, SERVER_WEB_FETCH]
DEV_TOOLS = [TOOL_READ_FILE, TOOL_WRITE_FILE, TOOL_RUN_BASH, TOOL_GLOB, TOOL_GREP]
GITHUB_TOOLS = [TOOL_GITHUB_SEARCH]

# ── Server tool names (skip in ToolExecutor) ──

SERVER_TOOL_TYPES = frozenset({
    "web_search_tool_result",
    "web_fetch_tool_result",
    "server_tool_use",
})

# ── CLI name → SDK name mapping ──────────────

# Special marker for server tools (not in _ALL_TOOLS, handled separately)
_SERVER = "__server__"

_CLI_TO_SDK: dict[str, str | None] = {
    # Builtins → server tools
    "WebSearch": _SERVER,
    "WebFetch": _SERVER,
    # Builtins → custom tools
    "Read": "read_file",
    "Write": "write_file",
    "Bash": "run_bash",
    "Glob": "glob",
    "Grep": "grep",
    # MCP → server tools (search)
    "mcp__brave-search__brave_web_search": _SERVER,
    "mcp__brave-search__brave_news_search": _SERVER,
    "mcp__serper__google_search": _SERVER,
    "mcp__serper__scrape": _SERVER,
    "mcp__fetch__fetch": _SERVER,
    # MCP → custom tools
    "mcp__firecrawl__firecrawl_scrape": "firecrawl_scrape",
    "mcp__firecrawl__firecrawl_crawl": "firecrawl_scrape",
    "mcp__firecrawl__firecrawl_extract": "firecrawl_scrape",
    "mcp__github__search_code": "github_search",
    "mcp__github__get_file_contents": "github_search",
    "mcp__github__create_pull_request": "run_bash",
    "mcp__github__list_issues": "github_search",
    "mcp__github__list_code_scanning_alerts": "github_search",
    "mcp__github__list_secret_scanning_alerts": "github_search",
    # mem0 — skipped
    "mcp__mem0__add_memory": None,
    "mcp__mem0__search_memories": None,
    # Edit → write_file
    "Edit": "write_file",
}

_ALL_TOOLS: dict[str, dict] = {
    "serper_search": TOOL_SERPER_SEARCH,
    "firecrawl_scrape": TOOL_FIRECRAWL_SCRAPE,
    "github_search": TOOL_GITHUB_SEARCH,
    "read_file": TOOL_READ_FILE,
    "write_file": TOOL_WRITE_FILE,
    "run_bash": TOOL_RUN_BASH,
    "glob": TOOL_GLOB,
    "grep": TOOL_GREP,
}


def cli_tools_to_sdk(cli_tools: list[str]) -> list[dict]:
    """Convert CLI tool names to SDK tool definitions.

    Server tools (web_search, web_fetch) are always included.
    Custom tools are added based on the CLI tool names.
    """
    sdk_tools: list[dict] = []
    seen: set[str] = set()
    needs_server_tools = False

    for cli_name in cli_tools:
        sdk_name = _CLI_TO_SDK.get(cli_name)
        if sdk_name is None:
            continue
        if sdk_name == _SERVER:
            needs_server_tools = True
            continue
        if sdk_name not in seen:
            tool_def = _ALL_TOOLS.get(sdk_name)
            if tool_def:
                sdk_tools.append(tool_def)
                seen.add(sdk_name)

    # Always include server tools (web_search + web_fetch) for search workers
    # or when explicitly requested
    if needs_server_tools or not sdk_tools:
        # Prepend server tools so Claude sees them first
        server = [SERVER_WEB_SEARCH, SERVER_WEB_FETCH]
        sdk_tools = server + sdk_tools

    return sdk_tools


def domain_to_sdk_tools(domain: str) -> list[dict]:
    """Get SDK tool definitions for a worker domain."""
    from src.tools import DOMAIN_CLAUDE_TOOLS
    cli_tools = DOMAIN_CLAUDE_TOOLS.get(domain, ["WebSearch", "WebFetch"])
    return cli_tools_to_sdk(cli_tools)
