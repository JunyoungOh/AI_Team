"""Unit tests for SchedulerService — trigger building and job management."""

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.scheduler.models import (
    JobStatus,
    ScheduleConfig,
    ScheduleType,
    ScheduledJob,
)
from src.scheduler.service import SchedulerService


def _make_cron_config():
    return ScheduleConfig(
        schedule_type=ScheduleType.CRON,
        cron_expression="0 9 * * MON",
    )


def _make_interval_config():
    return ScheduleConfig(
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=3600,
    )


# ── _build_trigger ───────────────────────────────


def test_build_cron_trigger():
    trigger = SchedulerService._build_trigger(_make_cron_config())
    assert isinstance(trigger, CronTrigger)


def test_build_interval_trigger():
    trigger = SchedulerService._build_trigger(_make_interval_config())
    assert isinstance(trigger, IntervalTrigger)


def test_build_trigger_cron_missing_expression():
    config = ScheduleConfig(schedule_type=ScheduleType.CRON, cron_expression=None)
    with pytest.raises(ValueError, match="cron_expression"):
        SchedulerService._build_trigger(config)


def test_build_trigger_interval_missing_seconds():
    config = ScheduleConfig(schedule_type=ScheduleType.INTERVAL, interval_seconds=None)
    with pytest.raises(ValueError, match="interval_seconds"):
        SchedulerService._build_trigger(config)


# ── Job lifecycle via store ──────────────────────


def test_add_and_pause_job(tmp_db_path):
    from src.config.settings import Settings

    settings = Settings(
        scheduler_db_path=tmp_db_path,
        checkpoint_db_path=tmp_db_path.replace("test.db", "cp.db"),
    )
    service = SchedulerService(settings)

    job = ScheduledJob(
        name="Test Job",
        user_task="Do analysis",
        schedule=_make_cron_config(),
    )
    service.add_job(job)

    # Verify job is saved
    retrieved = service.store.get_job(job.job_id)
    assert retrieved is not None
    assert retrieved.status == JobStatus.ACTIVE

    # Pause
    service.pause_job(job.job_id)
    paused = service.store.get_job(job.job_id)
    assert paused.status == JobStatus.PAUSED

    # Resume
    service.resume_job(job.job_id)
    resumed = service.store.get_job(job.job_id)
    assert resumed.status == JobStatus.ACTIVE

    # Remove (soft delete)
    service.remove_job(job.job_id)
    removed = service.store.get_job(job.job_id)
    assert removed.status == JobStatus.DELETED
