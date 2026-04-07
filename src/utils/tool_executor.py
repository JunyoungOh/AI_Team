"""SDK tool_use response executor.

Builtin tools (WebSearch, Read, etc.) → self-implemented
MCP tools (Brave, Serper, etc.) → direct HTTP API calls
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from html.parser import HTMLParser
import io

import httpx

from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(agent_id="tool_executor")


class _TextExtractor(HTMLParser):
    """Minimal HTML → text extractor (strips scripts, styles, nav)."""

    _SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "header", "noscript"})

    def __init__(self) -> None:
        super().__init__()
        self.text = io.StringIO()
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.text.write(stripped + " ")


def _html_to_text(html: str, max_chars: int = 50000) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.text.getvalue()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text


class ToolExecutor:
    """Maps tool names to execution functions."""

    def __init__(self, working_dir: str | None = None) -> None:
        self._working_dir = working_dir or os.getcwd()
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def close(self) -> None:
        await self._http.aclose()

    async def execute(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool. Returns error string for unknown tools."""
        handler = _TOOL_MAP.get(tool_name)
        if not handler:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            return await handler(self, tool_input)
        except Exception as exc:
            logger.warning("tool_execution_error", tool=tool_name, error=str(exc))
            return f"Error executing {tool_name}: {str(exc)}"

    # ── Web search ─────────────────────────────

    async def _web_search(self, inp: dict) -> str:
        """Brave Search API with retry + Serper fallback on rate limit."""
        query = inp.get("query", "")
        api_key = get_settings().brave_api_key or os.getenv("BRAVE_API_KEY", "")
        if not api_key:
            return await self._serper_search(inp)  # fallback

        # Retry with backoff on 429
        for attempt in range(3):
            resp = await self._http.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                params={"q": query, "count": 10},
            )
            if resp.status_code == 429:
                if attempt < 2:
                    delay = (attempt + 1) * 2  # 2s, 4s
                    logger.warning("brave_rate_limit_retry", attempt=attempt + 1, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                # Final attempt failed — fallback to Serper
                logger.warning("brave_rate_limit_fallback_to_serper", query=query[:80])
                return await self._serper_search(inp)
            resp.raise_for_status()
            break

        data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:10]:
            title = item.get("title", "")
            url = item.get("url", "")
            desc = item.get("description", "")
            results.append(f"- [{title}]({url})\n  {desc}")

        return "\n".join(results) if results else "No results found."

    async def _web_fetch(self, inp: dict) -> str:
        """Fetch URL and extract text. Replaces WebFetch builtin."""
        url = inp.get("url", "")
        resp = await self._http.get(url)
        resp.raise_for_status()
        return _html_to_text(resp.text)

    # ── External APIs (MCP replacements) ───────

    async def _serper_search(self, inp: dict) -> str:
        """Serper Google Search API."""
        query = inp.get("query", "")
        api_key = get_settings().serper_api_key or os.getenv("SERPER_API_KEY", "")
        if not api_key:
            return "Error: SERPER_API_KEY not configured"

        resp = await self._http.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 10},
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("organic", [])[:10]:
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")
            results.append(f"- [{title}]({link})\n  {snippet}")

        return "\n".join(results) if results else "No results found."

    async def _firecrawl_scrape(self, inp: dict) -> str:
        """Firecrawl API for structured scraping."""
        url = inp.get("url", "")
        api_key = get_settings().firecrawl_api_key or os.getenv("FIRECRAWL_API_KEY", "")
        if not api_key:
            return await self._web_fetch(inp)  # fallback

        resp = await self._http.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"url": url, "formats": ["markdown"]},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("markdown", "")

    async def _github_search(self, inp: dict) -> str:
        """GitHub Code Search API."""
        query = inp.get("query", "")
        token = get_settings().github_personal_access_token or os.getenv(
            "GITHUB_PERSONAL_ACCESS_TOKEN", ""
        )
        if not token:
            return "Error: GITHUB_PERSONAL_ACCESS_TOKEN not configured"

        resp = await self._http.get(
            "https://api.github.com/search/code",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            params={"q": query, "per_page": 10},
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("items", [])[:10]:
            path = item.get("path", "")
            repo = item.get("repository", {}).get("full_name", "")
            results.append(f"- {repo}/{path}")

        return "\n".join(results) if results else "No results found."

    # ── Filesystem (dev workers) ───────────────

    async def _read_file(self, inp: dict) -> str:
        path = pathlib.Path(inp.get("file_path", ""))
        if not path.is_absolute():
            path = pathlib.Path(self._working_dir) / path
        if not path.exists():
            return f"Error: File not found: {path}"
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > 100000:
            text = text[:100000] + "\n...[truncated]"
        return text

    async def _write_file(self, inp: dict) -> str:
        path = pathlib.Path(inp.get("file_path", ""))
        content = inp.get("content", "")
        if not path.is_absolute():
            path = pathlib.Path(self._working_dir) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {path}"

    async def _run_bash(self, inp: dict) -> str:
        command = inp.get("command", "")
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._working_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode(errors="replace")
        if stderr:
            output += "\nSTDERR:\n" + stderr.decode(errors="replace")
        if len(output) > 50000:
            output = output[:50000] + "\n...[truncated]"
        return output

    async def _glob_search(self, inp: dict) -> str:
        pattern = inp.get("pattern", "**/*")
        base = pathlib.Path(self._working_dir)
        matches = sorted(str(p) for p in base.glob(pattern))[:100]
        return "\n".join(matches) if matches else "No files found."

    async def _grep_search(self, inp: dict) -> str:
        pattern = inp.get("pattern", "")
        path = inp.get("path", self._working_dir)
        command = f"grep -rn --include='*.py' '{pattern}' '{path}' | head -50"
        return await self._run_bash({"command": command})


# ── Tool name → handler mapping ──────────────

_TOOL_MAP: dict[str, object] = {
    "web_search": ToolExecutor._web_search,
    "web_fetch": ToolExecutor._web_fetch,
    "serper_search": ToolExecutor._serper_search,
    "firecrawl_scrape": ToolExecutor._firecrawl_scrape,
    "github_search": ToolExecutor._github_search,
    "read_file": ToolExecutor._read_file,
    "write_file": ToolExecutor._write_file,
    "run_bash": ToolExecutor._run_bash,
    "glob": ToolExecutor._glob_search,
    "grep": ToolExecutor._grep_search,
}
