"""Execution metrics collector for cost/time tracking.

Tracks per-node and per-worker execution time, model usage, tier distribution,
and cache statistics. Generates a summary report at session end.

Thread-safe: workers update from async threads, main loop reads for display.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class WorkerMetric:
    """Metrics for a single worker execution."""

    worker_domain: str
    worker_id: str
    stage: int = 0                # Stage number (0 for flat parallel)
    tier: int = 1                 # Execution tier (0=cached, 1=full, 2=degraded, 3=skip)
    model: str = "sonnet"
    started_at: float = 0.0
    finished_at: float = 0.0
    success: bool = False
    cached: bool = False
    has_predecessor: bool = False
    reflection_result: str = ""   # "skipped"|"pass"|"fail_repaired"|"fail_kept_original"

    @property
    def duration_seconds(self) -> float:
        if self.finished_at > 0 and self.started_at > 0:
            return self.finished_at - self.started_at
        return 0.0


@dataclass
class NodeMetric:
    """Metrics for a single graph node execution."""

    node_name: str
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration_seconds(self) -> float:
        if self.finished_at > 0 and self.started_at > 0:
            return self.finished_at - self.started_at
        return 0.0


class ExecutionTracker:
    """Thread-safe execution metrics collector (session singleton)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: list[WorkerMetric] = []
        self._nodes: list[NodeMetric] = []
        self._session_start: float = time.time()
        self._cache_stats: dict = {}

    # ── Node tracking ──────────────────────────────────

    def node_start(self, node_name: str) -> None:
        with self._lock:
            self._nodes.append(NodeMetric(node_name=node_name, started_at=time.time()))

    def node_end(self, node_name: str) -> None:
        with self._lock:
            for n in reversed(self._nodes):
                if n.node_name == node_name and n.finished_at == 0.0:
                    n.finished_at = time.time()
                    break

    # ── Worker tracking ────────────────────────────────

    def worker_start(
        self,
        worker_domain: str,
        worker_id: str = "",
        stage: int = 0,
        model: str = "sonnet",
        has_predecessor: bool = False,
    ) -> None:
        with self._lock:
            self._workers.append(WorkerMetric(
                worker_domain=worker_domain,
                worker_id=worker_id,
                stage=stage,
                model=model,
                started_at=time.time(),
                has_predecessor=has_predecessor,
            ))

    def worker_end(
        self,
        worker_domain: str,
        tier: int = 1,
        success: bool = True,
        cached: bool = False,
    ) -> None:
        with self._lock:
            for w in reversed(self._workers):
                if w.worker_domain == worker_domain and w.finished_at == 0.0:
                    w.finished_at = time.time()
                    w.tier = tier
                    w.success = success
                    w.cached = cached
                    break

    def set_cache_stats(self, stats: dict) -> None:
        with self._lock:
            self._cache_stats = dict(stats)

    def set_reflection_result(self, worker_domain: str, result: str) -> None:
        """Set reflection result for the most recent matching worker."""
        with self._lock:
            for w in reversed(self._workers):
                if w.worker_domain == worker_domain:
                    w.reflection_result = result
                    break

    # ── Summary report ─────────────────────────────────

    def summary(self) -> dict:
        """Generate execution summary dict for state persistence and display."""
        with self._lock:
            total_time = time.time() - self._session_start

            worker_metrics = [
                {
                    "domain": w.worker_domain,
                    "duration_s": round(w.duration_seconds, 1),
                    "tier": w.tier,
                    "model": w.model,
                    "success": w.success,
                    "cached": w.cached,
                    "stage": w.stage,
                    "reflection": w.reflection_result,
                }
                for w in self._workers
            ]

            node_metrics = [
                {
                    "node": n.node_name,
                    "duration_s": round(n.duration_seconds, 1),
                }
                for n in self._nodes
            ]

            durations = [w.duration_seconds for w in self._workers if w.duration_seconds > 0]
            tier_counts: dict[str, int] = {}
            for w in self._workers:
                key = f"tier_{w.tier}"
                tier_counts[key] = tier_counts.get(key, 0) + 1

            return {
                "total_session_seconds": round(total_time, 1),
                "worker_count": len(self._workers),
                "workers": worker_metrics,
                "nodes": node_metrics,
                "tier_distribution": tier_counts,
                "avg_worker_duration_s": round(
                    sum(durations) / max(1, len(durations)), 1
                ),
                "max_worker_duration_s": round(max(durations, default=0), 1),
                "cache_stats": self._cache_stats,
            }


# ── Module-level singleton ─────────────────────────────

_exec_tracker: ExecutionTracker | None = None
_exec_lock = threading.Lock()


def get_exec_tracker() -> ExecutionTracker:
    """Get or create the global execution tracker."""
    global _exec_tracker
    if _exec_tracker is None:
        with _exec_lock:
            if _exec_tracker is None:
                _exec_tracker = ExecutionTracker()
    return _exec_tracker


def reset_exec_tracker() -> ExecutionTracker:
    """Reset and return a fresh execution tracker (for new sessions)."""
    global _exec_tracker
    with _exec_lock:
        _exec_tracker = ExecutionTracker()
    return _exec_tracker
