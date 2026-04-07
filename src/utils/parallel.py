"""Parallel execution utilities.

Provides :func:`run_async` — a helper to execute coroutines safely from
both sync and async calling contexts, and :func:`compute_stagger_delay` —
dynamic stagger that scales with task count.
"""

from __future__ import annotations

import atexit
import asyncio
from typing import Any
from concurrent.futures import ThreadPoolExecutor

# Module-level thread pool, reused across all run_async calls
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _shutdown_executor() -> None:
    """atexit handler: shut down the thread pool without waiting."""
    _EXECUTOR.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_executor)


def compute_stagger_delay(task_count: int) -> float:
    """Compute per-task stagger delay, scaling with task count.

    Reduced delay for small counts (≤2) to avoid unnecessary waiting:
      1-2 tasks:  0.3s  (minimal contention)
      ≤3 tasks:   1.0s  (base, within concurrency limit)
       6 tasks:   2.0s
       9 tasks:   3.0s
    """
    from src.config.settings import get_settings
    settings = get_settings()
    base = settings.parallel_stagger_delay
    max_concurrent = settings.max_parallel_api_calls
    # Small task counts: minimal stagger (no API contention risk)
    if task_count <= 2:
        return 0.3
    if task_count <= max_concurrent:
        return base
    return base * (task_count / max_concurrent)


def run_async(coro: Any, timeout_ceiling: int | None = None) -> Any:
    """Run a coroutine safely from both sync and async calling contexts.

    Args:
        coro: Coroutine to execute.
        timeout_ceiling: Override for the hard ceiling (seconds).
                         Defaults to max_total_staged_timeout + 120 (capped at 1800).

    When called from a sync context (no running event loop), uses asyncio.run().
    When called from an async context (event loop already running, e.g., the
    headless scheduler runner), submits to a shared thread pool to avoid RuntimeError.
    """
    import threading

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop — safe to use asyncio.run()
        return asyncio.run(coro)

    # Already in async context — run in shared thread pool.
    # Timeout prevents indefinite blocking if the thread pool is saturated.
    import concurrent.futures

    print(f"[run_async] Event loop detected in thread={threading.current_thread().name}, submitting to executor")

    if timeout_ceiling is None:
        from src.config.settings import get_settings
        settings = get_settings()
        timeout_ceiling = min(settings.max_total_staged_timeout + 120, 1800)

    future = _EXECUTOR.submit(asyncio.run, coro)
    try:
        return future.result(timeout=timeout_ceiling)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise TimeoutError(f"run_async: thread pool task exceeded {timeout_ceiling}s hard limit")
