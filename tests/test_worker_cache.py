"""Tests for WorkerResultCache — session-scope result caching."""

import json
import time

from src.utils.worker_cache import WorkerResultCache


def _make_plan(title: str = "research", objective: str = "do research") -> str:
    return json.dumps({
        "task_title": title,
        "objective": objective,
        "steps": ["step 1", "step 2"],
        "worker_type": "researcher",
    })


def _make_result(summary: str = "done") -> str:
    return json.dumps({
        "result_summary": summary,
        "deliverables": ["report.md"],
        "completion_percentage": 100,
    })


def test_cache_miss_then_hit():
    """First lookup is a miss, after put it's a hit."""
    cache = WorkerResultCache(ttl_seconds=60)
    plan = _make_plan()
    result = _make_result()

    assert cache.get("researcher", plan) is None  # miss

    cache.put("researcher", plan, result)
    cached = cache.get("researcher", plan)
    assert cached == result  # hit


def test_different_domain_no_hit():
    """Same plan but different domain is a separate cache entry."""
    cache = WorkerResultCache(ttl_seconds=60)
    plan = _make_plan()
    result = _make_result()

    cache.put("researcher", plan, result)
    assert cache.get("data_analyst", plan) is None


def test_different_plan_no_hit():
    """Same domain but different plan is a separate cache entry."""
    cache = WorkerResultCache(ttl_seconds=60)
    plan1 = _make_plan(title="research A")
    plan2 = _make_plan(title="research B")
    result = _make_result()

    cache.put("researcher", plan1, result)
    assert cache.get("researcher", plan2) is None


def test_ttl_expiration():
    """Cache entries expire after TTL."""
    cache = WorkerResultCache(ttl_seconds=0)  # Immediate expiration
    plan = _make_plan()
    result = _make_result()

    cache.put("researcher", plan, result)
    time.sleep(0.01)
    assert cache.get("researcher", plan) is None


def test_stats():
    """Stats track hits, misses, and size."""
    cache = WorkerResultCache(ttl_seconds=60)
    plan = _make_plan()
    result = _make_result()

    cache.get("researcher", plan)  # miss
    cache.put("researcher", plan, result)
    cache.get("researcher", plan)  # hit
    cache.get("researcher", plan)  # hit

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["size"] == 1
    assert stats["hit_rate_pct"] > 60


def test_non_json_plan_still_works():
    """Cache handles non-JSON plan strings gracefully."""
    cache = WorkerResultCache(ttl_seconds=60)
    plan = "just a plain text plan"
    result = _make_result()

    cache.put("researcher", plan, result)
    assert cache.get("researcher", plan) == result
