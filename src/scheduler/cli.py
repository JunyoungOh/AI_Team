"""Schedule management CLI commands.

Provides subcommands for job registration, management, and daemon control.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.config.settings import Settings, get_settings
from src.scheduler.models import (
    JobStatus,
    PreContext,
    ScheduleConfig,
    ScheduledJob,
    ScheduleType,
)
from src.scheduler.service import SchedulerService

console = Console()


def build_schedule_parser(subparsers) -> None:
    """Add 'schedule' subcommand group to argparse."""
    sched = subparsers.add_parser("schedule", help="Manage scheduled jobs")
    sched_sub = sched.add_subparsers(dest="schedule_cmd")

    # schedule add
    add_p = sched_sub.add_parser("add", help="Register a new scheduled job")
    add_p.add_argument("--name", required=True, help="Job display name")
    add_p.add_argument("--task", required=True, help="User task instruction")
    add_p.add_argument("--description", default="", help="Job description")
    add_p.add_argument("--cron", help="Cron expression (e.g. '0 9 * * MON')")
    add_p.add_argument("--interval", type=int, help="Interval in seconds")
    add_p.add_argument("--context-file", help="Path to pre-context JSON file")
    add_p.add_argument("--timeout", type=int, default=1800, help="Max execution seconds (default: 1800)")
    add_p.add_argument("--tags", default="", help="Comma-separated tags")

    # schedule list
    sched_sub.add_parser("list", help="List all scheduled jobs")

    # schedule show <job_id>
    show_p = sched_sub.add_parser("show", help="Show job details")
    show_p.add_argument("job_id", help="Job ID")

    # schedule history
    hist_p = sched_sub.add_parser("history", help="Show execution history")
    hist_p.add_argument("--job-id", help="Filter by job ID")
    hist_p.add_argument("--limit", type=int, default=20, help="Number of records")

    # schedule pause/resume/remove/trigger <job_id>
    for cmd in ("pause", "resume", "remove", "trigger"):
        p = sched_sub.add_parser(cmd, help=f"{cmd.capitalize()} a job")
        p.add_argument("job_id", help="Job ID")

    # schedule start (daemon)
    sched_sub.add_parser("start", help="Start scheduler daemon")


def handle_schedule_command(args: argparse.Namespace) -> None:
    """Dispatch schedule subcommand."""
    cmd = args.schedule_cmd
    if not cmd:
        console.print("[red]Usage: enterprise-agent schedule <command>[/red]")
        console.print("Commands: add, list, show, history, pause, resume, remove, trigger, start")
        return

    settings = get_settings()
    dispatch = {
        "add": lambda: _cmd_add(args, settings),
        "list": lambda: _cmd_list(settings),
        "show": lambda: _cmd_show(args, settings),
        "history": lambda: _cmd_history(args, settings),
        "pause": lambda: _cmd_pause(args, settings),
        "resume": lambda: _cmd_resume(args, settings),
        "remove": lambda: _cmd_remove(args, settings),
        "trigger": lambda: _cmd_trigger(args, settings),
        "start": lambda: _cmd_start(settings),
    }
    handler = dispatch.get(cmd)
    if handler:
        handler()
    else:
        console.print(f"[red]Unknown schedule command: {cmd}[/red]")


def _cmd_add(args: argparse.Namespace, settings: Settings) -> None:
    """Register a new scheduled job."""
    if not args.cron and not args.interval:
        console.print("[red]--cron or --interval is required[/red]")
        return

    if args.cron:
        schedule = ScheduleConfig(
            schedule_type=ScheduleType.CRON,
            cron_expression=args.cron,
            timezone=settings.scheduler_timezone,
        )
    else:
        schedule = ScheduleConfig(
            schedule_type=ScheduleType.INTERVAL,
            interval_seconds=args.interval,
            timezone=settings.scheduler_timezone,
        )

    # Load pre-context
    pre_context = PreContext()
    if args.context_file:
        ctx_path = Path(args.context_file)
        if not ctx_path.exists():
            console.print(f"[red]Context file not found: {ctx_path}[/red]")
            return
        with open(ctx_path) as f:
            pre_context = PreContext(**json.load(f))

    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    job = ScheduledJob(
        name=args.name,
        description=args.description,
        user_task=args.task,
        schedule=schedule,
        pre_context=pre_context,
        max_execution_time_seconds=args.timeout,
        tags=tags,
    )

    from src.scheduler.store import SchedulerStore
    store = SchedulerStore(settings.scheduler_db_path)
    store.save_job(job)
    store.close()

    console.print(f"[green]Job registered:[/green] {job.job_id}")
    console.print(f"  Name: {job.name}")
    console.print(f"  Task: {job.user_task}")
    console.print(f"  Schedule: {args.cron or f'every {args.interval}s'}")


def _cmd_list(settings: Settings) -> None:
    """List all scheduled jobs."""
    from src.scheduler.store import SchedulerStore
    store = SchedulerStore(settings.scheduler_db_path)
    jobs = store.list_jobs()
    store.close()

    if not jobs:
        console.print("[dim]No scheduled jobs found.[/dim]")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Tags")

    for job in jobs:
        sched_str = job.schedule.cron_expression or f"every {job.schedule.interval_seconds}s"
        status_style = {
            "active": "green",
            "paused": "yellow",
            "deleted": "red",
        }.get(job.status.value, "white")
        table.add_row(
            job.job_id,
            job.name,
            sched_str,
            f"[{status_style}]{job.status.value}[/{status_style}]",
            ", ".join(job.tags) if job.tags else "",
        )

    console.print(table)


def _cmd_show(args: argparse.Namespace, settings: Settings) -> None:
    """Show detailed information about a job."""
    from src.scheduler.store import SchedulerStore
    store = SchedulerStore(settings.scheduler_db_path)
    job = store.get_job(args.job_id)
    stats = store.get_execution_stats(args.job_id)
    store.close()

    if not job:
        console.print(f"[red]Job not found: {args.job_id}[/red]")
        return

    sched_str = job.schedule.cron_expression or f"every {job.schedule.interval_seconds}s"
    console.print(f"\n[bold]Job: {job.name}[/bold] ({job.job_id})")
    console.print(f"  Description: {job.description or '-'}")
    console.print(f"  Task: {job.user_task}")
    console.print(f"  Schedule: {sched_str} ({job.schedule.timezone})")
    console.print(f"  Status: {job.status.value}")
    console.print(f"  Timeout: {job.max_execution_time_seconds}s")
    console.print(f"  Tags: {', '.join(job.tags) if job.tags else '-'}")
    console.print(f"  Created: {job.created_at}")

    if stats["total_runs"] > 0:
        console.print(f"\n  [bold]Execution Stats:[/bold]")
        console.print(f"    Total runs: {stats['total_runs']}")
        console.print(f"    Successes: {stats['success_count']}")
        console.print(f"    Failures: {stats['failure_count']}")
        avg = f"{stats['avg_duration']}s" if stats['avg_duration'] else "-"
        console.print(f"    Avg duration: {avg}")
        console.print(f"    Last run: {stats['last_run_at'] or '-'}")


def _cmd_history(args: argparse.Namespace, settings: Settings) -> None:
    """Show execution history."""
    from src.scheduler.store import SchedulerStore
    store = SchedulerStore(settings.scheduler_db_path)
    records = store.list_executions(job_id=args.job_id, limit=args.limit)
    store.close()

    if not records:
        console.print("[dim]No execution records found.[/dim]")
        return

    table = Table(title="Execution History")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Job ID", no_wrap=True)
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Started At")
    table.add_column("Error")

    for rec in records:
        status_style = {
            "completed": "green",
            "failed": "red",
            "timeout": "yellow",
            "running": "blue",
        }.get(rec.status.value, "white")
        duration = f"{rec.duration_seconds:.1f}s" if rec.duration_seconds else "-"
        started = str(rec.started_at)[:19] if rec.started_at else "-"
        error = (rec.error_message[:40] + "...") if rec.error_message and len(rec.error_message) > 40 else (rec.error_message or "")
        table.add_row(
            rec.execution_id,
            rec.job_id,
            f"[{status_style}]{rec.status.value}[/{status_style}]",
            duration,
            started,
            error,
        )

    console.print(table)


def _cmd_pause(args: argparse.Namespace, settings: Settings) -> None:
    from src.scheduler.store import SchedulerStore
    store = SchedulerStore(settings.scheduler_db_path)
    store.update_job_status(args.job_id, JobStatus.PAUSED)
    store.close()
    console.print(f"[yellow]Job paused: {args.job_id}[/yellow]")


def _cmd_resume(args: argparse.Namespace, settings: Settings) -> None:
    from src.scheduler.store import SchedulerStore
    store = SchedulerStore(settings.scheduler_db_path)
    store.update_job_status(args.job_id, JobStatus.ACTIVE)
    store.close()
    console.print(f"[green]Job resumed: {args.job_id}[/green]")


def _cmd_remove(args: argparse.Namespace, settings: Settings) -> None:
    from src.scheduler.store import SchedulerStore
    store = SchedulerStore(settings.scheduler_db_path)
    store.update_job_status(args.job_id, JobStatus.DELETED)
    store.close()
    console.print(f"[red]Job removed: {args.job_id}[/red]")


def _cmd_trigger(args: argparse.Namespace, settings: Settings) -> None:
    """Manually trigger a job execution."""
    service = SchedulerService(settings)
    service._recover_orphaned_executions()
    try:
        asyncio.run(service.trigger_now(args.job_id))
        console.print(f"[green]Job triggered: {args.job_id}[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
    finally:
        service.store.close()


def _cmd_start(settings: Settings) -> None:
    """Start the scheduler daemon."""
    console.print("[bold]Starting scheduler daemon...[/bold]")
    console.print(f"  Timezone: {settings.scheduler_timezone}")
    console.print(f"  DB: {settings.scheduler_db_path}")
    console.print("  Press Ctrl+C to stop\n")

    service = SchedulerService(settings)

    async def _run_daemon():
        await service.start()
        stop_event = asyncio.Event()

        def _signal_handler():
            console.print("\n[yellow]Shutting down...[/yellow]")
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        # Periodic status heartbeat (every 60s)
        async def _heartbeat():
            while True:
                await asyncio.sleep(60)
                _print_daemon_status(service)

        heartbeat = asyncio.create_task(_heartbeat())
        await stop_event.wait()
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        await service.stop()

    asyncio.run(_run_daemon())
    console.print("[green]Scheduler stopped.[/green]")


def _print_daemon_status(service: SchedulerService) -> None:
    """Print periodic daemon status summary."""
    from datetime import datetime

    try:
        jobs = service.store.list_jobs()
        active = sum(1 for j in jobs if j.status.value == "active")
        paused = sum(1 for j in jobs if j.status.value == "paused")
        last_execs = service.store.list_executions(limit=1)

        now = datetime.now().strftime("%H:%M:%S")
        parts = [f"[{now}]", f"활성: {active}개"]
        if paused:
            parts.append(f"일시정지: {paused}개")

        if last_execs:
            last = last_execs[0]
            status_color = {
                "completed": "green", "failed": "red",
                "timeout": "yellow", "running": "blue",
            }.get(last.status.value, "white")
            parts.append(
                f"마지막: [{status_color}]{last.status.value}[/{status_color}]"
            )

        console.print(f"[dim]{' | '.join(parts)}[/dim]")
    except Exception:
        pass  # Silently skip if DB is locked or unavailable
