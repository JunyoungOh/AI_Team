"""Adaptive model selection for workers — capped at sonnet.

All workers use sonnet by default. Adaptive selection adjusts within
the sonnet tier (no opus/haiku for workers). Opus is reserved for
CEO routing and cross-domain review where classification accuracy matters.
"""

from __future__ import annotations

from src.config.agent_registry import WORKER_MODEL_OVERRIDES
from src.config.settings import get_settings
from src.utils.logging import get_logger

_logger = get_logger(agent_id="model_selector")


def select_worker_model(
    worker_domain: str,
    estimated_complexity: str = "medium",
    has_tools: bool = True,
    has_dependencies: bool = False,
    stage: int = 0,
) -> str:
    """Select optimal model for a worker — always sonnet.

    Priority:
        1. WORKER_MODEL_OVERRIDES (per worker type — if set)
        2. Default: sonnet (adaptive model disabled for workers to prevent
           opus timeouts on high-complexity and haiku quality issues on low)

    Args:
        worker_domain: Worker type name (e.g. "researcher", "backend_developer").
        estimated_complexity: CEO's assessment (logged, no longer affects model).
        has_tools: Whether this worker has MCP/builtin tools assigned.
        has_dependencies: Whether this worker depends on predecessor workers.
        stage: Execution stage (0 = no predecessors, higher = later stage).

    Returns:
        Model identifier string (always "sonnet" unless overridden).
    """
    # Priority 1: hardcoded override (currently empty, but supports plugin overrides)
    hardcoded = WORKER_MODEL_OVERRIDES.get(worker_domain)
    if hardcoded:
        return hardcoded

    # All workers use sonnet — fast enough to avoid timeouts,
    # capable enough for analysis/search/writing tasks.
    model = get_settings().worker_model  # "sonnet"

    _logger.debug(
        "model_selected",
        worker=worker_domain,
        complexity=estimated_complexity,
        has_tools=has_tools,
        has_deps=has_dependencies,
        stage=stage,
        final=model,
    )
    return model
