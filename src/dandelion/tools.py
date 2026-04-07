"""Tool schemas for Dandelion supervisor stage.

Supervisors use Anthropic server-side tools (web_search, web_fetch).
These are executed by Anthropic's servers — no local executor needed.
"""
from __future__ import annotations

from src.utils.tool_definitions import SERVER_WEB_SEARCH, SERVER_WEB_FETCH, SERVER_TOOL_TYPES

SUPERVISOR_TOOLS: list[dict] = [SERVER_WEB_SEARCH, SERVER_WEB_FETCH]


def is_server_tool_result(block_type: str) -> bool:
    """Check if a content block is a server tool result (skip in executor)."""
    return block_type in SERVER_TOOL_TYPES
