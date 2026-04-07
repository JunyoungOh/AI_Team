"""CLI-based client (AnthropicClient 인터페이스 호환).

원본은 Anthropic SDK 직접 호출이었으나, Local 버전에서는
ClaudeCodeBridge를 통해 동일한 인터페이스를 제공한다.

호환 인터페이스:
  - structured_query() → bridge.structured_query()
  - text_query() → bridge.raw_query()
  - tool_use_query() → bridge.raw_query() + MCP 도구
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from src.config.settings import get_settings
from src.utils.bridge_factory import get_bridge
from src.utils.logging import get_logger

logger = get_logger(agent_id="anthropic_client")


# ── Metrics ─────────────────────────────────────

@dataclass
class ApiMetrics:
    """Thread-safe API call metrics."""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_errors: int = 0
    total_elapsed: float = 0.0

    def record(self, input_tokens: int, output_tokens: int, elapsed: float) -> None:
        with self._lock:
            self.total_calls += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_elapsed += elapsed

    def record_error(self) -> None:
        with self._lock:
            self.total_errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_calls": self.total_calls,
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "errors": self.total_errors,
                "avg_elapsed": self.total_elapsed / max(self.total_calls, 1),
            }


# ── Exceptions ─────────────────────────────────

class SdkQueryError(Exception):
    """Query failure."""
    pass


class MaxTurnsExceeded(SdkQueryError):
    """tool_use loop exceeded max_turns."""
    pass


# ── CLI tool name mapping (SDK custom tool → CLI/MCP tool) ──

_SDK_TO_CLI_TOOLS: dict[str, str] = {
    "serper_search": "mcp__serper__google_search",
    "firecrawl_scrape": "mcp__firecrawl__firecrawl_scrape",
    "firecrawl_crawl": "mcp__firecrawl__firecrawl_crawl",
    "github_search_code": "mcp__github__search_code",
    "github_get_file": "mcp__github__get_file_contents",
    "github_list_issues": "mcp__github__list_issues",
    # Server-side tools → CLI built-ins
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
}


def _resolve_cli_tools(sdk_tools: list[dict]) -> list[str]:
    """SDK 도구 정의 리스트에서 CLI 도구 이름 리스트를 추출한다."""
    cli_names: list[str] = []
    for tool in sdk_tools:
        name = tool.get("name", "")
        # Skip server-side tools (they're Anthropic API specific)
        tool_type = tool.get("type", "")
        if tool_type.startswith("web_search") or tool_type.startswith("web_fetch"):
            cli_names.append("WebSearch" if "search" in tool_type else "WebFetch")
            continue
        # Map SDK tool name to CLI/MCP name
        cli_name = _SDK_TO_CLI_TOOLS.get(name, name)
        if cli_name:
            cli_names.append(cli_name)
    return cli_names


# ── AnthropicClient (CLI-based) ──────────────

class AnthropicClient:
    """ClaudeCodeBridge 기반 클라이언트 (AnthropicClient 인터페이스 호환)."""

    _instance: AnthropicClient | None = None
    _init_lock = threading.Lock()

    def __new__(cls) -> AnthropicClient:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._bridge = get_bridge()
        self._metrics = ApiMetrics()
        self._initialized = True

    @property
    def metrics(self) -> ApiMetrics:
        return self._metrics

    # ── structured_query ─────────────────────

    async def structured_query(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: type[BaseModel],
        model: str = "sonnet",
        max_tokens: int | None = None,
        effort: str = "low",
        timeout: float = 120,
    ) -> BaseModel:
        """Pydantic 구조화 출력. bridge.structured_query() 위임."""
        start = time.monotonic()
        try:
            result = await self._bridge.structured_query(
                system_prompt=system_prompt,
                user_message=user_message,
                output_schema=output_schema,
                model=model,
                allowed_tools=[],
                timeout=int(timeout),
                effort=effort,
            )
            elapsed = time.monotonic() - start
            self._metrics.record(0, 0, elapsed)
            logger.info(
                "cli_structured_query",
                model=model,
                schema=output_schema.__name__,
                elapsed=f"{elapsed:.1f}s",
            )
            return result
        except Exception as exc:
            self._metrics.record_error()
            raise SdkQueryError(str(exc)) from exc

    # ── text_query ───────────────────────────

    async def text_query(
        self,
        system_prompt: str,
        user_message: str,
        model: str = "sonnet",
        max_tokens: int | None = None,
        effort: str = "low",
        timeout: float = 120,
    ) -> str:
        """텍스트 응답. bridge.raw_query() 위임."""
        start = time.monotonic()
        try:
            result = await self._bridge.raw_query(
                system_prompt=system_prompt,
                user_message=user_message,
                model=model,
                allowed_tools=[],
                timeout=int(timeout),
                effort=effort,
            )
            elapsed = time.monotonic() - start
            self._metrics.record(0, 0, elapsed)
            return result
        except Exception as exc:
            self._metrics.record_error()
            raise SdkQueryError(str(exc)) from exc

    # ── tool_use_query ───────────────────────

    async def tool_use_query(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        tool_executor: object,
        output_schema: type[BaseModel] | None = None,
        model: str = "sonnet",
        max_tokens: int = 8192,
        max_turns: int = 18,
        effort: str = "low",
        timeout: float = 600,
    ) -> BaseModel | str:
        """도구 사용 쿼리. CLI가 MCP 도구를 자동 실행한다.

        SDK 버전: 수동 tool_use 루프 (API call → tool execute → feed back)
        CLI 버전: --allowedTools로 MCP 도구를 전달, CLI가 자동 루프 실행

        tool_executor 인자는 호환성을 위해 유지하지만 CLI에서는 사용하지 않음.
        """
        start = time.monotonic()

        # SDK 도구 정의에서 CLI 도구 이름 추출
        cli_tools = _resolve_cli_tools(tools)
        # WebSearch, WebFetch 기본 추가
        for builtin in ("WebSearch", "WebFetch"):
            if builtin not in cli_tools:
                cli_tools.append(builtin)

        try:
            if output_schema:
                result = await self._bridge.structured_query(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    output_schema=output_schema,
                    model=model,
                    allowed_tools=cli_tools,
                    timeout=int(timeout),
                    max_turns=max_turns,
                    effort=effort,
                )
                elapsed = time.monotonic() - start
                self._metrics.record(0, 0, elapsed)
                return result
            else:
                raw = await self._bridge.raw_query(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    model=model,
                    allowed_tools=cli_tools,
                    max_turns=max_turns,
                    timeout=int(timeout),
                    effort=effort,
                )
                elapsed = time.monotonic() - start
                self._metrics.record(0, 0, elapsed)
                return raw

        except Exception as exc:
            self._metrics.record_error()
            raise SdkQueryError(str(exc)) from exc
