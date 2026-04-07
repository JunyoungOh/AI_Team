"""Persistent execution metrics store (SQLite).

Accumulates per-session and per-worker metrics across runs.
Written by ceo_final_report_node at session end, queried by MetricsExporter
to generate data/metrics-report.md.

Thread-safe: uses a dedicated connection per call (no shared state).
"""

from __future__ import annotations

import sqlite3
import statistics
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DomainStats:
    domain: str
    session_count: int = 0
    avg_duration_s: float = 0.0
    p95_duration_s: float = 0.0
    timeout_rate: float = 0.0      # tier>=2 / total
    success_rate: float = 0.0
    model_usage: dict[str, int] = field(default_factory=dict)


@dataclass
class FailurePattern:
    worker_domain: str
    failure_count: int = 0
    total_count: int = 0
    failure_rate: float = 0.0
    top_errors: list[str] = field(default_factory=list)


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS session_metrics (
    session_id TEXT PRIMARY KEY,
    user_task TEXT,
    total_duration_s REAL,
    worker_count INTEGER,
    avg_worker_duration_s REAL,
    max_worker_duration_s REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS worker_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES session_metrics(session_id),
    worker_domain TEXT,
    model TEXT,
    duration_s REAL,
    tier INTEGER,
    stage INTEGER,
    success INTEGER,
    cached INTEGER
);

CREATE TABLE IF NOT EXISTS failure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    worker_domain TEXT,
    tier INTEGER,
    error_summary TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class MetricsStore:
    """SQLite-based persistent execution metrics store."""

    def __init__(self, db_path: str = "data/metrics.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(_CREATE_SQL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Save ──────────────────────────────────────────────

    def save_session(
        self,
        tracker_summary: dict,
        user_task: str = "",
        session_id: str = "",
    ) -> None:
        """Persist an ExecutionTracker.summary() dict to SQLite."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO session_metrics
                   (session_id, user_task, total_duration_s, worker_count,
                    avg_worker_duration_s, max_worker_duration_s)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    user_task,
                    tracker_summary.get("total_session_seconds", 0),
                    tracker_summary.get("worker_count", 0),
                    tracker_summary.get("avg_worker_duration_s", 0),
                    tracker_summary.get("max_worker_duration_s", 0),
                ),
            )

            for w in tracker_summary.get("workers", []):
                conn.execute(
                    """INSERT INTO worker_metrics
                       (session_id, worker_domain, model, duration_s,
                        tier, stage, success, cached)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        w.get("domain", ""),
                        w.get("model", "sonnet"),
                        w.get("duration_s", 0),
                        w.get("tier", 1),
                        w.get("stage", 0),
                        1 if w.get("success", True) else 0,
                        1 if w.get("cached", False) else 0,
                    ),
                )

                # Log failures
                if not w.get("success", True):
                    conn.execute(
                        """INSERT INTO failure_log
                           (session_id, worker_domain, tier, error_summary)
                           VALUES (?, ?, ?, ?)""",
                        (
                            session_id,
                            w.get("domain", ""),
                            w.get("tier", 1),
                            f"tier_{w.get('tier', 1)}_failure",
                        ),
                    )

    # ── Query ─────────────────────────────────────────────

    def get_recent_sessions(self, limit: int = 30) -> list[dict]:
        """Return the most recent N session summaries."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM session_metrics
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_session_count(self, days: int = 30) -> int:
        """Count sessions in the last N days."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM session_metrics
                   WHERE created_at >= datetime('now', ?)""",
                (f"-{days} days",),
            ).fetchone()
            return row["cnt"] if row else 0

    def get_domain_stats(self, days: int = 30) -> dict[str, DomainStats]:
        """Aggregate per-domain statistics over the last N days."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT worker_domain, model, duration_s, tier, success, cached
                   FROM worker_metrics wm
                   JOIN session_metrics sm ON wm.session_id = sm.session_id
                   WHERE sm.created_at >= datetime('now', ?)""",
                (f"-{days} days",),
            ).fetchall()

        # Group by domain
        domain_data: dict[str, list[dict]] = {}
        for r in rows:
            d = dict(r)
            domain_data.setdefault(d["worker_domain"], []).append(d)

        result: dict[str, DomainStats] = {}
        for domain, records in domain_data.items():
            durations = [r["duration_s"] for r in records if r["duration_s"] > 0]
            total = len(records)
            successes = sum(1 for r in records if r["success"])
            degraded = sum(1 for r in records if r["tier"] >= 2)

            model_counts: dict[str, int] = {}
            for r in records:
                model_counts[r["model"]] = model_counts.get(r["model"], 0) + 1

            stats = DomainStats(
                domain=domain,
                session_count=total,
                avg_duration_s=round(statistics.mean(durations), 1) if durations else 0,
                p95_duration_s=round(
                    sorted(durations)[int(len(durations) * 0.95)] if len(durations) >= 2
                    else (durations[0] if durations else 0),
                    1,
                ),
                timeout_rate=round(degraded / max(1, total), 2),
                success_rate=round(successes / max(1, total), 2),
                model_usage=model_counts,
            )
            result[domain] = stats

        return result

    def get_failure_patterns(self, days: int = 30) -> list[FailurePattern]:
        """Identify domains with repeated failures."""
        domain_stats = self.get_domain_stats(days)
        patterns = []

        for domain, stats in domain_stats.items():
            failure_count = stats.session_count - int(
                stats.success_rate * stats.session_count
            )
            if failure_count > 0:
                # Get top error summaries
                with self._connect() as conn:
                    error_rows = conn.execute(
                        """SELECT error_summary, COUNT(*) as cnt
                           FROM failure_log
                           WHERE worker_domain = ?
                             AND created_at >= datetime('now', ?)
                           GROUP BY error_summary
                           ORDER BY cnt DESC LIMIT 3""",
                        (domain, f"-{days} days"),
                    ).fetchall()

                patterns.append(FailurePattern(
                    worker_domain=domain,
                    failure_count=failure_count,
                    total_count=stats.session_count,
                    failure_rate=round(1.0 - stats.success_rate, 2),
                    top_errors=[r["error_summary"] for r in error_rows],
                ))

        patterns.sort(key=lambda p: p.failure_rate, reverse=True)
        return patterns

    def get_overall_stats(self, days: int = 30) -> dict:
        """Get high-level aggregate stats."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT
                       COUNT(*) as session_count,
                       AVG(total_duration_s) as avg_duration,
                       AVG(worker_count) as avg_workers
                   FROM session_metrics
                   WHERE created_at >= datetime('now', ?)""",
                (f"-{days} days",),
            ).fetchone()

            worker_row = conn.execute(
                """SELECT
                       COUNT(*) as total_workers,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                       SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) as cached
                   FROM worker_metrics wm
                   JOIN session_metrics sm ON wm.session_id = sm.session_id
                   WHERE sm.created_at >= datetime('now', ?)""",
                (f"-{days} days",),
            ).fetchone()

        session_count = row["session_count"] if row else 0
        total_workers = worker_row["total_workers"] or 0 if worker_row else 0
        successes = worker_row["successes"] or 0 if worker_row else 0
        cached = worker_row["cached"] or 0 if worker_row else 0

        return {
            "session_count": session_count,
            "avg_duration_s": round(row["avg_duration"] or 0, 1),
            "avg_workers": round(row["avg_workers"] or 0, 1),
            "total_workers": total_workers,
            "success_rate": round(successes / max(1, total_workers), 2),
            "cache_hit_rate": round(cached / max(1, total_workers), 2),
        }
