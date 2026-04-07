"""Tests for user_review_results node — human-in-the-loop after worker execution."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.graphs.nodes.user_review_results import (
    _build_results_summary,
    _parse_response,
    _should_skip,
)


# ── _should_skip tests ──────────────────────────────────────


def _make_state(**overrides) -> dict:
    base = {
        "execution_mode": "interactive",
        "estimated_complexity": "medium",
        "active_leaders": [],
    }
    base.update(overrides)
    return base


class TestShouldSkip:
    @patch("src.graphs.nodes.user_review_results.get_settings")
    def test_skip_when_disabled(self, mock_gs):
        s = MagicMock()
        s.enable_user_review = False
        mock_gs.return_value = s
        assert _should_skip(_make_state()) is True

    @patch("src.graphs.nodes.user_review_results.get_settings")
    def test_skip_scheduled_mode(self, mock_gs):
        s = MagicMock()
        s.enable_user_review = True
        s.user_review_min_complexity = "medium"
        mock_gs.return_value = s
        assert _should_skip(_make_state(execution_mode="scheduled")) is True

    @patch("src.graphs.nodes.user_review_results.get_settings")
    def test_skip_low_complexity_no_longer_applies(self, mock_gs):
        """Complexity check was removed — low complexity is NOT auto-skipped."""
        s = MagicMock()
        s.enable_user_review = True
        mock_gs.return_value = s
        assert _should_skip(_make_state(estimated_complexity="low")) is False

    @patch("src.graphs.nodes.user_review_results.get_settings")
    def test_no_skip_medium_interactive(self, mock_gs):
        s = MagicMock()
        s.enable_user_review = True
        s.user_review_min_complexity = "medium"
        mock_gs.return_value = s
        assert _should_skip(_make_state()) is False

    @patch("src.graphs.nodes.user_review_results.get_settings")
    def test_no_skip_high_interactive(self, mock_gs):
        s = MagicMock()
        s.enable_user_review = True
        s.user_review_min_complexity = "medium"
        mock_gs.return_value = s
        assert _should_skip(_make_state(estimated_complexity="high")) is False


# ── _parse_response tests ────────────────────────────────────


class TestParseResponse:
    def test_confirm_words(self):
        for word in ["확인", "ok", "진행", "네", "yes", "y", ""]:
            assert _parse_response(word) == ("confirm", "")

    def test_abort_words(self):
        for word in ["중단", "abort", "stop", "skip", "스킵"]:
            assert _parse_response(word) == ("abort", "")

    def test_revision_feedback(self):
        action, feedback = _parse_response("backend 결과에 에러 핸들링 추가해줘")
        assert action == "revise"
        assert "backend" in feedback

    def test_dict_confirm(self):
        assert _parse_response({"action": "confirm"}) == ("confirm", "")

    def test_dict_revise(self):
        assert _parse_response({"action": "revise", "feedback": "fix it"}) == ("revise", "fix it")

    def test_dict_abort(self):
        assert _parse_response({"action": "abort"}) == ("abort", "")

    def test_list_input(self):
        assert _parse_response(["ok"]) == ("confirm", "")
        assert _parse_response(["중단"]) == ("abort", "")


# ── _build_results_summary tests ─────────────────────────────


class TestBuildResultsSummary:
    def test_empty_leaders(self):
        summary, details = _build_results_summary([])
        assert "총 0개" in summary
        assert details == []

    def test_completed_workers(self):
        result_json = json.dumps({
            "result_summary": "Analysis done",
            "completion_percentage": 95,
            "deliverable_files": ["/data/reports/test/results.html"],
        })
        workers = [{
            "worker_domain": "researcher",
            "status": "completed",
            "execution_result": result_json,
        }]
        summary, details = _build_results_summary(workers)
        assert "완료: 1" in summary
        assert "Analysis done" in summary
        assert "95%" in summary
        assert len(details) == 1
        assert details[0]["files"] == ["/data/reports/test/results.html"]

    def test_failed_workers(self):
        workers = [{
            "worker_domain": "backend_developer",
            "status": "failed",
            "execution_result": "",
        }]
        summary, details = _build_results_summary(workers)
        assert "실패: 1" in summary
        assert details[0]["status"] == "failed"
