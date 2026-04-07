import pytest
from unittest.mock import AsyncMock
from src.agent_mode.bg_task_manager import BackgroundTaskManager, MAX_CONCURRENT


class TestBackgroundTaskManager:
    def test_max_concurrent(self):
        assert MAX_CONCURRENT == 3

    def test_start_task_returns_id(self):
        mgr = BackgroundTaskManager()
        ws = AsyncMock()
        task_id = mgr.start_task("backend_developer", "API 설계", ws)
        assert task_id is not None
        assert task_id.startswith("agnt_")

    def test_capacity_limit(self):
        mgr = BackgroundTaskManager()
        ws = AsyncMock()
        for _ in range(3):
            assert mgr.start_task("backend_developer", "task", ws) is not None
        assert mgr.start_task("backend_developer", "task", ws) is None

    def test_cancel_task(self):
        mgr = BackgroundTaskManager()
        ws = AsyncMock()
        task_id = mgr.start_task("backend_developer", "task", ws)
        mgr.cancel_task(task_id)
        assert mgr.get_task(task_id).status == "cancelled"

    def test_active_count(self):
        mgr = BackgroundTaskManager()
        ws = AsyncMock()
        assert mgr.active_count == 0
        mgr.start_task("backend_developer", "task", ws)
        assert mgr.active_count == 1
