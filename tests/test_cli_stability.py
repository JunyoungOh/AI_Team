"""Tests for CLI subprocess stability features (cli-stability)."""

import json
import threading

import pytest
from pydantic import BaseModel

from src.utils.claude_code import (
    ErrorCategory,
    SubprocessMetrics,
    _CircuitBreaker,
    _sanitize_json_output,
    _try_partial_recovery,
    classify_error,
)


# ── B1: classify_error ───────────────────────────────


class TestClassifyError:
    def test_rate_limit_429(self):
        assert classify_error("429 too many requests", 1) == ErrorCategory.RATE_LIMIT

    def test_rate_limit_text(self):
        assert classify_error("rate limit exceeded", 0) == ErrorCategory.RATE_LIMIT

    def test_overloaded_529(self):
        assert classify_error("529 overloaded", 1) == ErrorCategory.OVERLOADED

    def test_server_error_500(self):
        assert classify_error("internal server error", 1) == ErrorCategory.SERVER_ERROR

    def test_auth_error(self):
        assert classify_error("unauthorized access", 1) == ErrorCategory.AUTH_ERROR

    def test_auth_error_forbidden(self):
        assert classify_error("forbidden", 1) == ErrorCategory.AUTH_ERROR

    def test_cli_error_flag(self):
        assert classify_error("unknown flag --bad", 1) == ErrorCategory.CLI_ERROR

    def test_cli_error_exit_code(self):
        assert classify_error("something happened", 1) == ErrorCategory.CLI_ERROR

    def test_unknown(self):
        assert classify_error("something happened", 0) == ErrorCategory.UNKNOWN


# ── A2: _sanitize_json_output ────────────────────────


class TestSanitizeJsonOutput:
    def test_clean_json_passthrough(self):
        assert _sanitize_json_output('{"ok":true}') == '{"ok":true}'

    def test_strip_bom(self):
        assert _sanitize_json_output('\ufeff{"ok":true}') == '{"ok":true}'

    def test_strip_ansi_codes(self):
        assert _sanitize_json_output('\x1b[31mWARN\x1b[0m\n{"ok":true}') == '{"ok":true}'

    def test_strip_warning_prefix(self):
        raw = "UserWarning: blah blah\n" + '{"result":"hi"}'
        assert _sanitize_json_output(raw) == '{"result":"hi"}'

    def test_strip_warning_suffix(self):
        raw = '{"a":1}\nsome trailing text'
        assert _sanitize_json_output(raw) == '{"a":1}'

    def test_nested_json(self):
        raw = '{"outer":{"inner":1}}'
        assert _sanitize_json_output(raw) == raw

    def test_no_json_returns_original(self):
        assert _sanitize_json_output("no json here") == "no json here"

    def test_prefix_and_suffix(self):
        raw = "prefix{\"a\":1}suffix"
        assert _sanitize_json_output(raw) == '{"a":1}'


# ── B4: _try_partial_recovery ────────────────────────


class _TestSchema(BaseModel):
    status: str = ""
    result: str = ""


class TestPartialRecovery:
    def test_extract_result_field(self):
        raw = 'garbage {"result": {"status": "ok", "result": "done"}} garbage'
        recovered = _try_partial_recovery(raw, _TestSchema)
        assert recovered is not None
        assert recovered.status == "ok"

    def test_extract_last_json_object(self):
        raw = 'warning\n{"status": "found", "result": "data"}\ntrailing'
        recovered = _try_partial_recovery(raw, _TestSchema)
        assert recovered is not None
        assert recovered.status == "found"

    def test_returns_none_on_garbage(self):
        assert _try_partial_recovery("just plain text", _TestSchema) is None


# ── B3: _CircuitBreaker ──────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = _CircuitBreaker()
        assert cb.can_proceed() is True

    def test_opens_after_threshold(self):
        cb = _CircuitBreaker()
        for _ in range(5):
            cb.record_failure()
        assert cb.can_proceed() is False

    def test_closes_on_success(self):
        cb = _CircuitBreaker()
        for _ in range(5):
            cb.record_failure()
        cb.record_success()
        assert cb.can_proceed() is True

    def test_below_threshold_stays_closed(self):
        cb = _CircuitBreaker()
        for _ in range(4):
            cb.record_failure()
        assert cb.can_proceed() is True


# ── D2: SubprocessMetrics ────────────────────────────


class TestSubprocessMetrics:
    def test_record_and_snapshot(self):
        # Use a fresh instance by resetting singleton
        m = SubprocessMetrics()
        initial = m.snapshot()["total"]
        m.record(1.0, success=True)
        m.record(2.0, success=False)
        m.record(3.0, success=False, timeout=True)
        snap = m.snapshot()
        assert snap["total"] >= initial + 3

    def test_thread_safety(self):
        m = SubprocessMetrics()
        errors = []

        def _record():
            try:
                for _ in range(100):
                    m.record(0.1, success=True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_record) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
