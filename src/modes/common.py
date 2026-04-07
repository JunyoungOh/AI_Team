"""Common interfaces shared by all execution modes."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# API-native tool names (web_search/web_fetch are Anthropic server-side tools)
TOOL_CATEGORY_MAP: dict[str, list[str]] = {
    "research": ["web_search", "web_fetch", "firecrawl_scrape"],
    "verify": ["web_search"],
    "none": [],
}


class ModeParticipant(BaseModel):
    """A participant in a mode execution with persona and tool access."""

    name: str
    persona: str
    role: str
    tool_category: Literal["research", "verify", "none"] = "none"


class ModeResult(BaseModel):
    """Common result wrapper returned by all execution modes."""

    mode: Literal["roundtable", "adversarial", "workshop", "relay"]
    summary: str
    result_html: str
    quality_score: float = Field(ge=0, le=10)
    roundtable: dict[str, Any] | None = None
    adversarial: dict[str, Any] | None = None
    workshop: dict[str, Any] | None = None
    relay: dict[str, Any] | None = None


import asyncio

_mode_event_queues: dict[str, asyncio.Queue] = {}

def get_mode_event_queue(session_id: str) -> asyncio.Queue:
    if session_id not in _mode_event_queues:
        _mode_event_queues[session_id] = asyncio.Queue()
    return _mode_event_queues[session_id]

def emit_mode_event(session_id: str, event: dict):
    queue = _mode_event_queues.get(session_id)
    if queue:
        queue.put_nowait(event)

def cleanup_mode_event_queue(session_id: str):
    _mode_event_queues.pop(session_id, None)
