"""Agent factory for dynamic creation of CEO and workers."""

from __future__ import annotations

import threading

from src.agents.ceo import CEOAgent
from src.agents.worker import WorkerAgent
from src.config.agent_registry import (
    is_registered_worker,
    is_text_mode_worker,
)
from src.config.settings import get_settings
from src.tools import get_claude_tools_for_domain, get_tools_for_category
from src.utils.model_selector import select_worker_model


_counter_lock = threading.Lock()
_worker_counter: dict[str, int] = {}


def create_ceo() -> CEOAgent:
    return CEOAgent(agent_id="ceo-main-001", model=get_settings().ceo_model)


def create_worker(
    worker_domain: str,
    tool_category: str | None = None,
    estimated_complexity: str = "medium",
    has_dependencies: bool = False,
    stage: int = 0,
    worker_name: str = "",
    role_type: str = "executor",
) -> WorkerAgent:
    """Create a worker agent with tools resolved from tool_category.

    For instant workers, tools come from TOOL_CATEGORIES.
    Legacy pre-defined workers fall back to DOMAIN_CLAUDE_TOOLS.
    """
    with _counter_lock:
        _worker_counter.setdefault(worker_domain, 0)
        _worker_counter[worker_domain] += 1
        count = _worker_counter[worker_domain]
    agent_id = f"worker-{worker_domain}-{count:03d}"

    # Tool resolution: prefer tool_category, fall back to domain lookup
    if tool_category:
        allowed_tools = get_tools_for_category(tool_category)
    elif is_registered_worker(worker_domain):
        allowed_tools = get_claude_tools_for_domain(worker_domain)
    else:
        allowed_tools = get_tools_for_category(worker_domain)  # tool_category as domain

    # Adaptive model selection
    model = select_worker_model(
        worker_domain=worker_domain,
        estimated_complexity=estimated_complexity,
        has_tools=bool(allowed_tools),
        has_dependencies=has_dependencies,
        stage=stage,
    )

    return WorkerAgent(
        agent_id=agent_id,
        model=model,
        worker_domain=worker_domain,
        allowed_tools=allowed_tools,
        text_mode=is_text_mode_worker(worker_domain),
        tool_category=tool_category,
        worker_name=worker_name,
        role_type=role_type,
    )


def reset_counters() -> None:
    """Reset ID counters (useful for testing)."""
    with _counter_lock:
        _worker_counter.clear()
