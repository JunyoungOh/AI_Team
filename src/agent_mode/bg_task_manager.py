"""Background task lifecycle management for AI Agent mode."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 3


@dataclass
class BackgroundTask:
    task_id: str
    agent_id: str
    description: str
    status: str = "running"  # running, completed, failed, cancelled
    progress: float = 0.0
    result_summary: str = ""
    asyncio_task: asyncio.Task | None = field(default=None, repr=False)


class BackgroundTaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == "running")

    def start_task(self, agent_id: str, description: str, ws) -> str | None:
        if self.active_count >= MAX_CONCURRENT:
            return None
        task_id = f"agnt_{uuid.uuid4().hex[:8]}"
        bg = BackgroundTask(task_id=task_id, agent_id=agent_id, description=description)
        self._tasks[task_id] = bg
        return task_id

    def set_asyncio_task(self, task_id: str, coro_task: asyncio.Task) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].asyncio_task = coro_task

    def cancel_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = "cancelled"
        if task.asyncio_task and not task.asyncio_task.done():
            task.asyncio_task.cancel()

    def complete_task(self, task_id: str, summary: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = "completed"
            task.result_summary = summary

    def fail_task(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = "failed"
            task.result_summary = error

    def update_progress(self, task_id: str, progress: float, summary: str = "") -> None:
        task = self._tasks.get(task_id)
        if task:
            task.progress = progress
            if summary:
                task.result_summary = summary

    def get_task(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def get_active_tasks(self) -> list[BackgroundTask]:
        return [t for t in self._tasks.values() if t.status == "running"]
