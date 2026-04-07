"""SQLite storage for scheduled jobs and execution history."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.scheduler.models import (
    ExecutionRecord,
    ExecutionStatus,
    JobStatus,
    PreContext,
    ScheduleConfig,
    ScheduledJob,
)


DEFAULT_DB_PATH = Path("data/scheduler.db")


class SchedulerStore:
    """SQLite-backed storage for scheduler state."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                job_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                user_task TEXT NOT NULL,
                schedule_json TEXT NOT NULL,
                pre_context_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                max_execution_time_seconds INTEGER DEFAULT 1800,
                tags_json TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS execution_records (
                execution_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TEXT,
                completed_at TEXT,
                duration_seconds REAL,
                final_report_json TEXT,
                final_state_summary_json TEXT,
                error_message TEXT,
                thread_id TEXT DEFAULT '',
                FOREIGN KEY (job_id) REFERENCES scheduled_jobs(job_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_exec_job_id ON execution_records(job_id);
            CREATE INDEX IF NOT EXISTS idx_exec_status ON execution_records(status);
            CREATE INDEX IF NOT EXISTS idx_exec_started ON execution_records(started_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON scheduled_jobs(status);
        """)
            conn.commit()

    # ── Job CRUD ──────────────────────────────────────

    def save_job(self, job: ScheduledJob) -> None:
        """Insert or replace a scheduled job."""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO scheduled_jobs
                   (job_id, name, description, user_task, schedule_json, pre_context_json,
                    status, created_at, updated_at, max_execution_time_seconds, tags_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.job_id,
                    job.name,
                    job.description,
                    job.user_task,
                    job.schedule.model_dump_json(),
                    job.pre_context.model_dump_json(),
                    job.status.value,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    job.max_execution_time_seconds,
                    json.dumps(job.tags),
                ),
            )
            conn.commit()

    def get_job(self, job_id: str) -> ScheduledJob | None:
        """Retrieve a job by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self, status: JobStatus | None = None) -> list[ScheduledJob]:
        """List jobs, optionally filtered by status."""
        conn = self._get_conn()
        if status:
            rows = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE status = ? ORDER BY created_at DESC",
                (status.value,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE status != 'deleted' ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_job_status(self, job_id: str, status: JobStatus) -> None:
        """Update the status of a job."""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE scheduled_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (status.value, datetime.now(timezone.utc).isoformat(), job_id),
            )
            conn.commit()

    def delete_job(self, job_id: str) -> None:
        """Hard-delete a job and its executions (via CASCADE)."""
        conn = self._get_conn()
        with self._lock:
            conn.execute("DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,))
            conn.commit()

    # ── Execution CRUD ────────────────────────────────

    def save_execution(self, record: ExecutionRecord) -> None:
        """Insert a new execution record."""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                """INSERT INTO execution_records
                   (execution_id, job_id, status, started_at, completed_at,
                    duration_seconds, final_report_json, final_state_summary_json,
                    error_message, thread_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.execution_id,
                    record.job_id,
                    record.status.value,
                    record.started_at.isoformat() if record.started_at else None,
                    record.completed_at.isoformat() if record.completed_at else None,
                    record.duration_seconds,
                    json.dumps(record.final_report) if record.final_report else None,
                    json.dumps(record.final_state_summary) if record.final_state_summary else None,
                    record.error_message,
                    record.thread_id,
                ),
            )
            conn.commit()

    def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        """Retrieve an execution by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM execution_records WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        return self._row_to_execution(row) if row else None

    def list_executions(
        self,
        job_id: str | None = None,
        status: ExecutionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExecutionRecord]:
        """List executions with optional filters."""
        conn = self._get_conn()
        query = "SELECT * FROM execution_records WHERE 1=1"
        params: list[Any] = []
        if job_id:
            query += " AND job_id = ?"
            params.append(job_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_execution(r) for r in rows]

    _ALLOWED_EXECUTION_UPDATE_FIELDS = frozenset({
        "status", "error_message", "completed_at", "duration_seconds",
        "final_report_json", "final_state_summary_json",
    })

    def update_execution(self, execution_id: str, **fields: Any) -> None:
        """Update specific fields of an execution record.

        Only columns in ``_ALLOWED_EXECUTION_UPDATE_FIELDS`` are accepted.
        """
        if not fields:
            return
        invalid = set(fields) - self._ALLOWED_EXECUTION_UPDATE_FIELDS
        if invalid:
            raise ValueError(f"Disallowed update fields: {invalid}")
        conn = self._get_conn()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [execution_id]
        with self._lock:
            conn.execute(
                f"UPDATE execution_records SET {set_clause} WHERE execution_id = ?",
                values,
            )
            conn.commit()

    # ── Query helpers ─────────────────────────────────

    def get_last_execution(self, job_id: str) -> ExecutionRecord | None:
        """Get the most recent execution for a job."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM execution_records WHERE job_id = ? ORDER BY started_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return self._row_to_execution(row) if row else None

    def get_execution_stats(self, job_id: str) -> dict[str, Any]:
        """Get execution statistics for a job."""
        conn = self._get_conn()
        row = conn.execute(
            """SELECT
                COUNT(*) as total_runs,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status IN ('failed', 'timeout') THEN 1 ELSE 0 END) as failure_count,
                AVG(duration_seconds) as avg_duration,
                MAX(started_at) as last_run_at
            FROM execution_records WHERE job_id = ?""",
            (job_id,),
        ).fetchone()
        return {
            "total_runs": row["total_runs"],
            "success_count": row["success_count"] or 0,
            "failure_count": row["failure_count"] or 0,
            "avg_duration": round(row["avg_duration"], 1) if row["avg_duration"] else None,
            "last_run_at": row["last_run_at"],
        }

    def get_consecutive_failures(self, job_id: str) -> int:
        """Count consecutive recent failures for a job (for auto-pause logic)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT status FROM execution_records WHERE job_id = ? ORDER BY started_at DESC LIMIT 10",
            (job_id,),
        ).fetchall()
        count = 0
        for row in rows:
            if row["status"] in ("failed", "timeout"):
                count += 1
            else:
                break
        return count

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Private helpers ───────────────────────────────

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ScheduledJob:
        return ScheduledJob(
            job_id=row["job_id"],
            name=row["name"],
            description=row["description"],
            user_task=row["user_task"],
            schedule=ScheduleConfig.model_validate_json(row["schedule_json"]),
            pre_context=PreContext.model_validate_json(row["pre_context_json"]),
            status=JobStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            max_execution_time_seconds=row["max_execution_time_seconds"],
            tags=json.loads(row["tags_json"]),
        )

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=row["execution_id"],
            job_id=row["job_id"],
            status=ExecutionStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            duration_seconds=row["duration_seconds"],
            final_report=json.loads(row["final_report_json"]) if row["final_report_json"] else None,
            final_state_summary=json.loads(row["final_state_summary_json"]) if row["final_state_summary_json"] else None,
            error_message=row["error_message"],
            thread_id=row["thread_id"],
        )
