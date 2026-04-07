"""LangSmith tracing configuration for Enterprise Agent System.

Provides:
- configure_tracing(): export env vars so LangGraph auto-traces
- get_run_config(): build config dict with tracing metadata
- traceable_llm(): decorator for LLM calls
"""

from __future__ import annotations

import os

from src.utils.logging import get_logger

_logger = get_logger(agent_id="tracing")

_tracing_active: bool = False


def configure_tracing() -> bool:
    """Read LangSmith settings and export to os.environ for LangGraph.

    LangGraph reads LANGCHAIN_TRACING_V2 / LANGCHAIN_API_KEY directly
    from os.environ (not from pydantic Settings), so we must bridge the gap.

    Returns True if tracing is active.
    """
    global _tracing_active
    from src.config.settings import get_settings

    settings = get_settings()

    if not settings.langchain_tracing_v2 or not settings.langchain_api_key:
        _tracing_active = False
        _logger.debug("langsmith_tracing_disabled")
        return False

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project

    _tracing_active = True
    _logger.info(
        "langsmith_tracing_configured",
        project=settings.langchain_project,
    )
    return True


def is_tracing_active() -> bool:
    """Check whether LangSmith tracing has been configured."""
    return _tracing_active


def get_run_config(
    thread_id: str,
    *,
    tags: list[str] | None = None,
    mode: str = "interactive",
) -> dict:
    """Build LangGraph config dict with optional tracing metadata.

    Args:
        thread_id: Session thread ID.
        tags: Additional tags for LangSmith filtering.
        mode: Execution mode ("interactive" or "scheduled").
    """
    config: dict = {"configurable": {"thread_id": thread_id}}

    if _tracing_active:
        run_tags = [mode]
        if tags:
            run_tags.extend(tags)
        config["run_name"] = f"enterprise-agent-{thread_id}"
        config["tags"] = run_tags
        config["metadata"] = {
            "session_id": thread_id,
            "mode": mode,
        }

    return config


# ── Decorator for LLM calls ─────────────────

try:
    from langsmith.run_helpers import traceable

    def traceable_llm(*, name: str = "sdk_query"):
        """Wrap an async method as a LangSmith 'llm' run."""
        return traceable(run_type="llm", name=name)

except ImportError:

    def traceable_llm(*, name: str = "sdk_query"):  # type: ignore[misc]
        """No-op decorator when langsmith is not installed."""
        def decorator(func):
            return func
        return decorator
