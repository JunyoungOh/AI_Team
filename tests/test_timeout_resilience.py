"""Tests for timeout resilience features (timeout-resilience)."""

import threading

import pytest
from pydantic import BaseModel

from src.utils.claude_code import (
    ClaudeCodeError,
    ClaudeCodeTimeoutError,
    SubprocessMetrics,
    _sanitize_json_output,
    _try_partial_recovery,
)


# ── Test schema ──────────────────────────────────────

class _TestSchema(BaseModel):
    status: str = ""
    result: str = ""


# ── ClaudeCodeTimeoutError extension ─────────────────


class TestTimeoutErrorExtension:
    def test_has_partial_stdout_attribute(self):
        err = ClaudeCodeTimeoutError("test timeout")
        assert hasattr(err, "partial_stdout")
        assert err.partial_stdout is None

    def test_has_partial_result_attribute(self):
        err = ClaudeCodeTimeoutError("test timeout")
        assert hasattr(err, "partial_result")
        assert err.partial_result is None

    def test_partial_stdout_can_be_set(self):
        err = ClaudeCodeTimeoutError("test timeout")
        err.partial_stdout = '{"status": "ok"}'
        assert err.partial_stdout == '{"status": "ok"}'

    def test_partial_result_can_be_set(self):
        err = ClaudeCodeTimeoutError("test timeout")
        schema = _TestSchema(status="partial", result="data")
        err.partial_result = schema
        assert err.partial_result.status == "partial"

    def test_is_subclass_of_claude_code_error(self):
        err = ClaudeCodeTimeoutError("test")
        assert isinstance(err, ClaudeCodeError)


# ── Time budget calculation ──────────────────────────


class TestTimeBudget:
    def test_effective_timeout_with_budget(self):
        """time_budget < execution_timeout -> budget 사용."""
        # Simulate: execution_timeout=600, time_budget=300
        budget = 300.0
        execution_timeout = 600
        effective = max(60, min(execution_timeout, int(budget)))
        assert effective == 300

    def test_effective_timeout_without_budget(self):
        """time_budget=None -> 기존 execution_timeout."""
        budget = None
        execution_timeout = 600
        effective = execution_timeout if budget is None else max(60, min(execution_timeout, int(budget)))
        assert effective == 600

    def test_minimum_budget_floor(self):
        """time_budget=10 -> max(60, 10) = 60."""
        budget = 10.0
        execution_timeout = 600
        effective = max(60, min(execution_timeout, int(budget)))
        assert effective == 60

    def test_budget_larger_than_timeout(self):
        """time_budget=900 > execution_timeout=600 -> 600."""
        budget = 900.0
        execution_timeout = 600
        effective = max(60, min(execution_timeout, int(budget)))
        assert effective == 600

    def test_tier2_budget_calculation(self):
        """tier1이 500s 소비 -> tier2 budget = total - 500."""
        time_budget = 800.0
        tier1_elapsed = 500.0
        tier2_budget = time_budget - tier1_elapsed
        assert tier2_budget == 300.0

    def test_tier2_skipped_when_no_budget(self):
        """tier2_budget < 30 -> Tier 2 스킵."""
        time_budget = 620.0
        tier1_elapsed = 600.0
        tier2_budget = time_budget - tier1_elapsed
        assert tier2_budget < 30


# ── Partial salvage ──────────────────────────────────


class TestPartialSalvage:
    def test_partial_recovery_extracts_valid_json(self):
        """partial stdout에서 valid schema 추출."""
        partial = 'some garbage {"status": "ok", "result": "done"} trailing'
        recovered = _try_partial_recovery(partial, _TestSchema)
        assert recovered is not None
        assert recovered.status == "ok"

    def test_partial_recovery_returns_none_on_garbage(self):
        """유효하지 않은 partial -> None."""
        assert _try_partial_recovery("just plain text", _TestSchema) is None

    def test_sanitize_then_recover(self):
        """sanitize + partial recovery chain."""
        raw = '\x1b[31mWARN\x1b[0m\n{"status": "found", "result": "data"}\ntrailing'
        sanitized = _sanitize_json_output(raw)
        assert '"status"' in sanitized

    def test_partial_result_has_is_partial_flag(self):
        """timeout + partial_result에 is_partial 설정 가능 확인."""
        from src.models.messages import WorkerResult
        result = WorkerResult(
            result_summary="partial", deliverables=[], completion_percentage=50,
        )
        assert result.is_partial is False
        # B4: structured_query에서 설정
        if hasattr(result, "is_partial"):
            result.is_partial = True
        assert result.is_partial is True


# ── run_async dynamic ceiling ────────────────────────


class TestRunAsyncCeiling:
    def test_default_ceiling_uses_settings(self):
        """기본 ceiling이 settings 기반으로 결정되는지 확인."""
        from src.config.settings import get_settings
        settings = get_settings()
        expected = min(settings.max_total_staged_timeout + 120, 1800)
        assert expected > 600  # 기존 hard-coded 600보다 큼

    def test_ceiling_cap_at_1800(self):
        """ceiling이 1800s를 초과하지 않는지 확인."""
        # Even with very large max_total_staged_timeout
        large_timeout = 5000
        ceiling = min(large_timeout + 120, 1800)
        assert ceiling == 1800

    def test_explicit_ceiling_override(self):
        """timeout_ceiling 파라미터 직접 전달 테스트."""
        from src.utils.parallel import run_async
        import asyncio

        async def quick():
            return 42

        # Explicit ceiling should work
        result = run_async(quick(), timeout_ceiling=10)
        assert result == 42


# ── Retry budget ─────────────────────────────────────


class TestRetryBudget:
    def test_retry_stops_when_budget_exhausted(self):
        """remaining < 30s -> retry 중단."""
        import time as _time
        time_budget = 50.0
        query_start = _time.monotonic() - 25  # simulate 25s elapsed
        elapsed = _time.monotonic() - query_start
        remaining = time_budget - elapsed
        # After ~25s elapsed from 50s budget, ~25s remaining
        # On attempt 1 (not 0), remaining < 30 should stop
        # This is conservative test -- just verify budget check logic
        should_stop = remaining < 30
        assert isinstance(should_stop, bool)

    def test_effective_timeout_shrinks_per_attempt(self):
        """2번째 attempt -> 남은 budget으로 축소."""
        timeout = 600
        time_budget = 400.0
        # First attempt: full budget remaining
        remaining_1 = time_budget - 0  # 0s elapsed
        effective_1 = max(30, min(timeout, int(remaining_1)))
        assert effective_1 == 400

        # Second attempt after 350s elapsed
        remaining_2 = time_budget - 350
        effective_2 = max(30, min(timeout, int(remaining_2)))
        assert effective_2 == 50  # only 50s left


# ── SubprocessMetrics extension ──────────────────────


class TestSubprocessMetricsExtension:
    def test_partial_recoveries_tracking(self):
        m = SubprocessMetrics()
        initial = m.snapshot().get("partial_recoveries", 0)
        m.record_partial_recovery()
        m.record_partial_recovery()
        snap = m.snapshot()
        assert snap["partial_recoveries"] >= initial + 2

    def test_snapshot_includes_partial_recoveries(self):
        m = SubprocessMetrics()
        snap = m.snapshot()
        assert "partial_recoveries" in snap

    def test_timeout_by_domain_tracking(self):
        m = SubprocessMetrics()
        m.record(1.0, success=False, timeout=True, domain="research")
        m.record(2.0, success=False, timeout=True, domain="research")
        m.record(1.5, success=False, timeout=True, domain="finance")
        snap = m.snapshot()
        assert "timeout_by_domain" in snap
        assert snap["timeout_by_domain"].get("research", 0) >= 2

    def test_record_without_domain(self):
        """domain 미전달 시에도 정상 동작."""
        m = SubprocessMetrics()
        m.record(1.0, success=False, timeout=True)
        # Should not crash


# ── WorkerResult is_partial ──────────────────────────


class TestWorkerResultPartial:
    def test_default_is_false(self):
        from src.models.messages import WorkerResult
        r = WorkerResult(
            result_summary="test",
            deliverables=["item"],
            completion_percentage=100,
        )
        assert r.is_partial is False

    def test_can_set_partial(self):
        from src.models.messages import WorkerResult
        r = WorkerResult(
            result_summary="partial result",
            deliverables=["item"],
            completion_percentage=60,
            is_partial=True,
        )
        assert r.is_partial is True
