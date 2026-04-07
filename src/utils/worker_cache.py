"""Worker result in-memory cache (session scope).

Prevents duplicate execution of workers with identical (domain, plan_content)
within the same session. Cross-session caching is delegated to mem0.

Cache key: sha256(worker_domain + stable_plan_fields)
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass

from src.utils.logging import get_logger

logger = get_logger(agent_id="worker_cache")


@dataclass
class CacheEntry:
    result_json: str
    worker_domain: str
    cache_key: str
    created_at: float
    hit_count: int = 0


class WorkerResultCache:
    """Thread-safe in-memory worker result cache."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _make_key(worker_domain: str, plan_json: str) -> str:
        """Build cache key from domain + stable plan content hash."""
        try:
            plan_data = json.loads(plan_json)
            # Only use fields that determine work output (exclude worker_id, etc.)
            stable = {
                "task_title": plan_data.get("task_title", ""),
                "objective": plan_data.get("objective", ""),
                "steps": plan_data.get("steps", []),
            }
            content = json.dumps(stable, sort_keys=True, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            content = plan_json
        digest = hashlib.sha256(f"{worker_domain}:{content}".encode()).hexdigest()[:16]
        return f"{worker_domain}:{digest}"

    def get(self, worker_domain: str, plan_json: str) -> str | None:
        """Look up cached result. Returns result JSON on hit, None on miss."""
        key = self._make_key(worker_domain, plan_json)
        with self._lock:
            entry = self._store.get(key)
            if entry and (time.time() - entry.created_at) < self._ttl:
                entry.hit_count += 1
                self._hits += 1
                logger.info("cache_hit", domain=worker_domain, key=key)
                return entry.result_json
            if entry:
                del self._store[key]  # TTL expired
            self._misses += 1
            return None

    def put(self, worker_domain: str, plan_json: str, result_json: str) -> None:
        """Store a completed worker result in cache."""
        key = self._make_key(worker_domain, plan_json)
        with self._lock:
            self._store[key] = CacheEntry(
                result_json=result_json,
                worker_domain=worker_domain,
                cache_key=key,
                created_at=time.time(),
            )
            logger.info("cache_put", domain=worker_domain, key=key)

    def stats(self) -> dict:
        """Return cache statistics."""
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate_pct": round((self._hits / max(1, total)) * 100, 1),
            }
