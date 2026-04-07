"""Defensive programming guards -- loop limits, timeouts, state validation, error handling."""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any

from langchain_core.messages import AIMessage

from src.engine import InterruptRequest

from src.utils.logging import get_logger


# ── Exceptions ──────────────────────────────────────

class LoopLimitExceeded(Exception):
    """Raised when a loop iteration limit is exceeded."""

    def __init__(self, loop_name: str, current: int, limit: int):
        self.loop_name = loop_name
        self.current = current
        self.limit = limit
        super().__init__(f"Loop limit exceeded: {loop_name} ({current}/{limit})")


# ── Loop Guards ─────────────────────────────────────

def check_loop_limit(state: dict, counter_key: str, limit: int) -> int:
    """Read a loop counter from state.iteration_counts.

    Returns the current count. Raises LoopLimitExceeded if >= limit.
    """
    counts = state.get("iteration_counts", {})
    current = counts.get(counter_key, 0)
    if current >= limit:
        raise LoopLimitExceeded(counter_key, current, limit)
    return current


def increment_loop_counter(state: dict, counter_key: str) -> dict:
    """Return a new iteration_counts dict with counter_key incremented.

    Usage in nodes:
        return {"iteration_counts": increment_loop_counter(state, "ceo_rejections"), ...}
    """
    counts = dict(state.get("iteration_counts", {}))
    counts[counter_key] = counts.get(counter_key, 0) + 1
    return counts


# ── Safe Parallel Execution ─────────────────────────

async def safe_gather(
    coros: list,
    timeout_seconds: int = 300,
    description: str = "parallel_tasks",
    max_concurrency: int | None = None,
) -> list[tuple[bool, Any]]:
    """Run coroutines in parallel with per-task timeout and error isolation.

    Args:
        coros: List of coroutines to execute.
        timeout_seconds: Per-task timeout in seconds.
        description: Label for logging.
        max_concurrency: Max tasks running simultaneously (prevents API rate
            limits when many workers call Claude Code at once).  ``None``
            reads from ``get_settings().max_parallel_api_calls``.

    Returns list of (success: bool, result_or_error) tuples.
    Never raises -- all errors are captured per-task.
    """
    from src.config.settings import get_settings

    logger = get_logger(agent_id="parallel")
    limit = max_concurrency or get_settings().max_parallel_api_calls
    semaphore = asyncio.Semaphore(limit)

    async def _safe_run(coro, index: int):
        async with semaphore:
            try:
                result = await asyncio.wait_for(coro, timeout=timeout_seconds)
                return (True, result)
            except asyncio.TimeoutError:
                logger.error("parallel_task_timeout", index=index, desc=description)
                return (False, TimeoutError(f"Task {index} timed out ({timeout_seconds}s)"))
            except Exception as e:
                logger.error("parallel_task_failed", index=index, error=str(e), desc=description)
                return (False, e)

    return await asyncio.gather(*[_safe_run(c, i) for i, c in enumerate(coros)])


# ── Node Error Handler Decorator ────────────────────

def node_error_handler(node_name: str):
    """Decorator that wraps a node function with error handling.

    On exception: returns {error_message, phase: "error", messages}.
    On phase == "error": returns empty dict (pass-through).
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state: dict) -> dict:
            # Pass-through if already in error state
            if state.get("phase") == "error":
                return {}

            # Register step start for pipeline progress panel
            # (called at node START so progress is visible during execution)
            try:
                from src.utils.progress import get_step_tracker, NODE_LABELS
                label = NODE_LABELS.get(node_name, node_name)
                sid = state.get("session_id", "") if isinstance(state, dict) else ""
                get_step_tracker(sid).begin_step(node_name, label)
            except Exception:
                pass  # Progress tracking is non-critical

            # Track node execution time for metrics panel
            try:
                from src.utils.execution_tracker import get_exec_tracker
                get_exec_tracker().node_start(node_name)
            except Exception:
                pass  # Metrics tracking is non-critical

            try:
                result = fn(state)
                try:
                    from src.utils.execution_tracker import get_exec_tracker
                    get_exec_tracker().node_end(node_name)
                except Exception:
                    pass
                return result
            except InterruptRequest:
                try:
                    from src.utils.execution_tracker import get_exec_tracker
                    get_exec_tracker().node_end(node_name)
                except Exception:
                    pass
                raise  # Let LangGraph handle interrupt flow
            except Exception as e:
                logger = get_logger(agent_id=node_name)
                # Full details at DEBUG level only (not streamed to clients)
                err_detail = str(e)[:300]
                logger.error("node_failed", node=node_name, error=err_detail, error_type=type(e).__name__)
                safe_msg = f"{type(e).__name__}: {err_detail}"
                return {
                    "error_message": f"[{node_name}] {safe_msg}: check logs for details",
                    "phase": "error",
                    "messages": [
                        AIMessage(
                            content=f"[system error] {node_name} node failed ({safe_msg}). "
                            "See server logs for details."
                        )
                    ],
                }

        return wrapper
    return decorator


# ── Safe Type Conversion ────────────────────────────

def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert to int, returning default on failure."""
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_json_loads(text: str, default: Any = None) -> Any:
    """Safely parse JSON, returning default on failure."""
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default
