"""Unit tests for SchedulerStore SQLite CRUD operations."""

from datetime import datetime, timezone

import pytest

from src.scheduler.models import (
    ExecutionRecord,
    ExecutionStatus,
    JobStatus,
    PreContext,
    ScheduleConfig,
    ScheduleType,
    ScheduledJob,
)
from src.scheduler.store import SchedulerStore


@pytest.fixture
def store(tmp_db_path):
    """Create a SchedulerStore with a temporary database."""
    s = SchedulerStore(tmp_db_path)
    yield s
    s.close()


def _make_job(**overrides) -> ScheduledJob:
    """Create a sample ScheduledJob with defaults."""
    defaults = dict(
        job_id="test-job-001",
        name="Test Job",
        user_task="Analyze market trends",
        schedule=ScheduleConfig(
            schedule_type=ScheduleType.CRON,
            cron_expression="0 9 * * MON",
        ),
        pre_context=PreContext(background="Weekly analysis"),
    )
    defaults.update(overrides)
    return ScheduledJob(**defaults)


def _make_execution(job_id: str, **overrides) -> ExecutionRecord:
    """Create a sample ExecutionRecord."""
    defaults = dict(
        execution_id="exec-001",
        job_id=job_id,
        status=ExecutionStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_seconds=45.0,
    )
    defaults.update(overrides)
    return ExecutionRecord(**defaults)


# ── Job CRUD ─────────────────────────────────────


def test_save_and_get_job(store):
    job = _make_job()
    store.save_job(job)
    retrieved = store.get_job("test-job-001")
    assert retrieved is not None
    assert retrieved.name == "Test Job"
    assert retrieved.user_task == "Analyze market trends"
    assert retrieved.schedule.cron_expression == "0 9 * * MON"
    assert retrieved.pre_context.background == "Weekly analysis"


def test_get_nonexistent_job(store):
    assert store.get_job("nonexistent") is None


def test_list_jobs(store):
    store.save_job(_make_job(job_id="j1", name="Job 1"))
    store.save_job(_make_job(job_id="j2", name="Job 2"))
    store.save_job(_make_job(job_id="j3", name="Job 3", status=JobStatus.PAUSED))

    all_jobs = store.list_jobs()
    assert len(all_jobs) == 3

    active_jobs = store.list_jobs(status=JobStatus.ACTIVE)
    assert len(active_jobs) == 2

    paused_jobs = store.list_jobs(status=JobStatus.PAUSED)
    assert len(paused_jobs) == 1


def test_update_job_status(store):
    store.save_job(_make_job())
    store.update_job_status("test-job-001", JobStatus.PAUSED)
    job = store.get_job("test-job-001")
    assert job.status == JobStatus.PAUSED


def test_delete_job(store):
    store.save_job(_make_job())
    store.delete_job("test-job-001")
    assert store.get_job("test-job-001") is None


# ── Execution CRUD ───────────────────────────────


def test_save_and_get_execution(store):
    store.save_job(_make_job())
    record = _make_execution("test-job-001")
    store.save_execution(record)
    retrieved = store.get_execution("exec-001")
    assert retrieved is not None
    assert retrieved.job_id == "test-job-001"
    assert retrieved.status == ExecutionStatus.COMPLETED


def test_list_executions_by_job(store):
    store.save_job(_make_job())
    store.save_execution(_make_execution("test-job-001", execution_id="e1"))
    store.save_execution(_make_execution("test-job-001", execution_id="e2"))

    execs = store.list_executions(job_id="test-job-001")
    assert len(execs) == 2


def test_list_executions_by_status(store):
    store.save_job(_make_job())
    store.save_execution(_make_execution("test-job-001", execution_id="e1", status=ExecutionStatus.COMPLETED))
    store.save_execution(_make_execution("test-job-001", execution_id="e2", status=ExecutionStatus.FAILED))

    completed = store.list_executions(status=ExecutionStatus.COMPLETED)
    assert len(completed) == 1
    assert completed[0].execution_id == "e1"


def test_update_execution(store):
    store.save_job(_make_job())
    store.save_execution(_make_execution("test-job-001"))
    store.update_execution("exec-001", status="failed", error_message="Timeout")
    updated = store.get_execution("exec-001")
    assert updated.status == ExecutionStatus.FAILED
    assert updated.error_message == "Timeout"


# ── Query helpers ────────────────────────────────


def test_get_last_execution(store):
    store.save_job(_make_job())
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e1",
        started_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    ))
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e2",
        started_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
    ))
    last = store.get_last_execution("test-job-001")
    assert last.execution_id == "e2"


def test_get_execution_stats(store):
    store.save_job(_make_job())
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e1",
        status=ExecutionStatus.COMPLETED, duration_seconds=30.0,
    ))
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e2",
        status=ExecutionStatus.FAILED, duration_seconds=10.0,
    ))
    stats = store.get_execution_stats("test-job-001")
    assert stats["total_runs"] == 2
    assert stats["success_count"] == 1
    assert stats["failure_count"] == 1
    assert stats["avg_duration"] == 20.0


def test_get_consecutive_failures(store):
    store.save_job(_make_job())
    # Order: completed, failed, failed, failed (most recent first)
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e1",
        status=ExecutionStatus.COMPLETED,
        started_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    ))
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e2",
        status=ExecutionStatus.FAILED,
        started_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    ))
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e3",
        status=ExecutionStatus.FAILED,
        started_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
    ))
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e4",
        status=ExecutionStatus.FAILED,
        started_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
    ))
    assert store.get_consecutive_failures("test-job-001") == 3


def test_get_consecutive_failures_no_failures(store):
    store.save_job(_make_job())
    store.save_execution(_make_execution(
        "test-job-001", execution_id="e1",
        status=ExecutionStatus.COMPLETED,
    ))
    assert store.get_consecutive_failures("test-job-001") == 0


# ── FK Cascade ───────────────────────────────────


def test_fk_cascade_delete(store):
    store.save_job(_make_job())
    store.save_execution(_make_execution("test-job-001", execution_id="e1"))
    store.save_execution(_make_execution("test-job-001", execution_id="e2"))

    # Delete job should cascade to executions
    store.delete_job("test-job-001")
    assert store.get_execution("e1") is None
    assert store.get_execution("e2") is None
