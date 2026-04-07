"""Tests for ExecutionTracker — execution metrics collection."""

import time

from src.utils.execution_tracker import ExecutionTracker


def test_worker_tracking_basic():
    """Worker start/end records duration and tier."""
    tracker = ExecutionTracker()
    tracker.worker_start("researcher", worker_id="w1", model="sonnet")
    time.sleep(0.05)
    tracker.worker_end("researcher", tier=1, success=True)

    summary = tracker.summary()
    assert summary["worker_count"] == 1
    w = summary["workers"][0]
    assert w["domain"] == "researcher"
    assert w["tier"] == 1
    assert w["success"] is True
    assert w["cached"] is False
    assert w["duration_s"] >= 0.04


def test_worker_cached():
    """Cached workers are tracked with tier=0."""
    tracker = ExecutionTracker()
    tracker.worker_start("data_analyst", worker_id="w2")
    tracker.worker_end("data_analyst", tier=0, success=True, cached=True)

    summary = tracker.summary()
    w = summary["workers"][0]
    assert w["cached"] is True
    assert w["tier"] == 0
    assert summary["tier_distribution"] == {"tier_0": 1}


def test_node_tracking():
    """Node start/end records duration."""
    tracker = ExecutionTracker()
    tracker.node_start("ceo_route")
    time.sleep(0.05)
    tracker.node_end("ceo_route")

    summary = tracker.summary()
    assert len(summary["nodes"]) == 1
    assert summary["nodes"][0]["node"] == "ceo_route"
    assert summary["nodes"][0]["duration_s"] >= 0.04


def test_multiple_workers_stats():
    """Summary aggregates stats across multiple workers."""
    tracker = ExecutionTracker()

    tracker.worker_start("researcher", worker_id="w1", model="sonnet")
    time.sleep(0.02)
    tracker.worker_end("researcher", tier=1, success=True)

    tracker.worker_start("architect", worker_id="w2", model="opus")
    time.sleep(0.04)
    tracker.worker_end("architect", tier=1, success=True)

    tracker.worker_start("data_analyst", worker_id="w3", model="sonnet")
    tracker.worker_end("data_analyst", tier=2, success=False)

    summary = tracker.summary()
    assert summary["worker_count"] == 3
    assert summary["tier_distribution"]["tier_1"] == 2
    assert summary["tier_distribution"]["tier_2"] == 1
    assert summary["avg_worker_duration_s"] >= 0


def test_cache_stats_integration():
    """set_cache_stats merges cache data into summary."""
    tracker = ExecutionTracker()
    tracker.set_cache_stats({"hits": 2, "misses": 5, "hit_rate_pct": 28.6})

    summary = tracker.summary()
    assert summary["cache_stats"]["hits"] == 2
    assert summary["cache_stats"]["hit_rate_pct"] == 28.6
