"""Tests for All-Sonnet worker policy (#4) and text mode bifurcation (#6).

All workers use sonnet — WORKER_MODEL_OVERRIDES is empty by default.
Opus reserved for CEO/Leader only (prevents timeout issues).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.agent_registry import (
    WORKER_MODEL_OVERRIDES,
    WORKER_TEXT_MODE,
    get_worker_model,
    is_text_mode_worker,
)
from src.models.messages import WorkerResult


# ──────────────────────────────────────────
# #4: All-Sonnet worker model policy
# ──────────────────────────────────────────


class TestWorkerModelOverrides:
    """Test WORKER_MODEL_OVERRIDES — empty by default (all-sonnet)."""

    def test_overrides_empty_by_default(self):
        """No per-worker overrides — all workers use settings.worker_model."""
        assert len(WORKER_MODEL_OVERRIDES) == 0

    def test_get_worker_model_returns_none(self):
        """All workers return None (no override) → factory uses default sonnet."""
        assert get_worker_model("architect") is None
        assert get_worker_model("strategist") is None
        assert get_worker_model("fact_checker") is None
        assert get_worker_model("researcher") is None

    def test_get_worker_model_unknown(self):
        """Unknown worker types also return None."""
        assert get_worker_model("backend_developer") is None
        assert get_worker_model("unknown_type") is None

    @patch("src.agents.factory.get_settings")
    def test_factory_all_workers_use_settings_model(self, mock_settings):
        """All workers should get worker_model from factory."""
        settings = MagicMock()
        settings.worker_model = "opus"
        mock_settings.return_value = settings

        from src.agents.factory import create_worker, reset_counters
        reset_counters()

        for worker_type in ["architect", "researcher", "fact_checker", "designer"]:
            worker = create_worker(worker_type)
            assert worker.model == "opus", f"{worker_type} should be opus"

    @patch("src.agents.factory.get_settings")
    def test_factory_uses_default_when_no_override(self, mock_settings):
        """Workers without override use settings.worker_model."""
        settings = MagicMock()
        settings.worker_model = "opus"
        mock_settings.return_value = settings

        from src.agents.factory import create_worker, reset_counters
        reset_counters()

        worker = create_worker("researcher")
        assert worker.model == "opus"


# ──────────────────────────────────────────
# #6: Text mode bifurcation
# ──────────────────────────────────────────


class TestTextModeRegistry:
    """Test WORKER_TEXT_MODE configuration."""

    def test_text_mode_workers_defined(self):
        """Analysis-heavy workers should be in text mode."""
        assert "researcher" in WORKER_TEXT_MODE
        assert "data_analyst" in WORKER_TEXT_MODE
        assert "content_writer" in WORKER_TEXT_MODE
        assert "ux_researcher" in WORKER_TEXT_MODE
        assert "product_analyst" in WORKER_TEXT_MODE

    def test_non_text_mode_workers(self):
        """Development workers should NOT be in text mode."""
        assert "backend_developer" not in WORKER_TEXT_MODE
        assert "frontend_developer" not in WORKER_TEXT_MODE
        assert "architect" not in WORKER_TEXT_MODE

    def test_is_text_mode_worker(self):
        assert is_text_mode_worker("researcher") is True
        assert is_text_mode_worker("backend_developer") is False
        assert is_text_mode_worker("unknown_type") is False


class TestTextModeFactory:
    """Test that factory passes text_mode flag to WorkerAgent."""

    @patch("src.agents.factory.get_settings")
    def test_factory_sets_text_mode_true(self, mock_settings):
        settings = MagicMock()
        settings.worker_model = "sonnet"
        mock_settings.return_value = settings

        from src.agents.factory import create_worker, reset_counters
        reset_counters()

        worker = create_worker("researcher")
        assert worker.text_mode is True

    @patch("src.agents.factory.get_settings")
    def test_factory_sets_text_mode_false(self, mock_settings):
        settings = MagicMock()
        settings.worker_model = "sonnet"
        mock_settings.return_value = settings

        from src.agents.factory import create_worker, reset_counters
        reset_counters()

        worker = create_worker("backend_developer")
        assert worker.text_mode is False


class TestTextModeExecution:
    """Test text mode execution path in WorkerAgent."""

    def test_wrap_text_result_normal(self):
        """Raw text should be wrapped into WorkerResult."""
        from src.agents.worker import WorkerAgent

        text = "This is a detailed analysis result with many findings."
        result = WorkerAgent._wrap_text_result(text)

        assert isinstance(result, WorkerResult)
        assert result.result_summary == text
        assert result.completion_percentage == 80
        assert len(result.deliverables) == 1
        assert result.deliverables[0] == text

    def test_wrap_text_result_long(self):
        """Long text summary should be truncated to 800 chars."""
        from src.agents.worker import WorkerAgent

        text = "A" * 2000
        result = WorkerAgent._wrap_text_result(text)

        assert len(result.result_summary) <= 810  # 800 + "…"
        assert result.result_summary.endswith("…")
        assert result.deliverables[0] == text  # Full text preserved

    def test_wrap_text_result_empty(self):
        """Empty text should produce 0% completion."""
        from src.agents.worker import WorkerAgent

        result = WorkerAgent._wrap_text_result("")
        assert result.completion_percentage == 0
        assert result.result_summary == "No output produced."

    def test_aexecute_plan_text_mode(self):
        """Text mode worker should call SDK tool_use_query (or raw_query fallback)."""
        from src.agents.worker import WorkerAgent
        from src.utils.parallel import run_async

        worker = WorkerAgent(
            agent_id="test-worker-001",
            model="sonnet",
            worker_domain="researcher",
            allowed_tools=["WebSearch"],
            text_mode=True,
        )

        # Mock SDK path (tool_use_query returns text for text_mode)
        mock_sdk = MagicMock()
        mock_sdk.tool_use_query = AsyncMock(return_value="Research findings: market grew 15%")
        worker._sdk = mock_sdk

        # Mock bridge as fallback (should not be called when SDK succeeds)
        mock_bridge = MagicMock()
        mock_bridge.raw_query = AsyncMock(return_value="fallback")
        worker._bridge = mock_bridge

        result = run_async(worker.aexecute_plan(
            approved_plan='{"plan_title":"test","steps":["step1"],"expected_output":"report"}',
        ))

        assert isinstance(result, WorkerResult)
        assert "Research findings" in result.result_summary
        mock_sdk.tool_use_query.assert_awaited_once()

    def test_aexecute_plan_structured_mode(self):
        """Non-text-mode worker should call SDK tool_use_query via _aquery."""
        from src.agents.worker import WorkerAgent
        from src.utils.parallel import run_async

        worker = WorkerAgent(
            agent_id="test-worker-002",
            model="sonnet",
            worker_domain="backend_developer",
            allowed_tools=["Read", "Write", "Bash"],
            text_mode=False,
        )

        mock_result = WorkerResult(
            result_summary="Code written",
            deliverables=["src/main.py"],
            completion_percentage=100,
        )
        # Mock SDK path
        mock_sdk = MagicMock()
        mock_sdk.tool_use_query = AsyncMock(return_value=mock_result)
        worker._sdk = mock_sdk

        # Mock bridge fallback
        mock_bridge = MagicMock()
        mock_bridge.structured_query = AsyncMock(return_value=mock_result)
        worker._bridge = mock_bridge

        result = run_async(worker.aexecute_plan(
            approved_plan='{"plan_title":"test","steps":["step1"],"expected_output":"code"}',
        ))

        assert isinstance(result, WorkerResult)
        assert result.result_summary == "Code written"
        mock_sdk.tool_use_query.assert_awaited_once()
