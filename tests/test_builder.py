"""Tests for Company Builder — storage, validation, scheduler integration."""

import json
import shutil
import pytest
from pathlib import Path

from src.company_builder.builder_agent import _extract_team_json
from src.company_builder.storage import (
    save_company, load_company, list_companies, delete_company,
)
from src.company_builder.schedule_storage import (
    save_schedule, load_schedule, list_schedules, delete_schedule,
    toggle_schedule, _validate_cron,
)


TEST_USER = "_test_builder"


@pytest.fixture(autouse=True)
def cleanup_test_data():
    yield
    shutil.rmtree(f"data/users/{TEST_USER}", ignore_errors=True)


# ── Team JSON validation ────────────────────────


class TestExtractTeamJson:
    def test_valid_team(self):
        text = '```team_json\n{"agents": [{"id": "a1", "name": "R", "role": "Search", "tool_category": "research", "emoji": "X"}], "edges": []}\n```'
        result = _extract_team_json(text)
        assert result is not None
        assert len(result["agents"]) == 1
        assert result["agents"][0]["name"] == "R"

    def test_missing_fields_get_defaults(self):
        text = '```team_json\n{"agents": [{"id": "a1"}]}\n```'
        result = _extract_team_json(text)
        assert result["agents"][0]["name"] == "a1"
        assert result["agents"][0]["role"] == "AI 에이전트"
        assert result["agents"][0]["tool_category"] == "research"

    def test_invalid_category_defaults_to_research(self):
        text = '```team_json\n{"agents": [{"id": "a1", "tool_category": "invalid"}]}\n```'
        result = _extract_team_json(text)
        assert result["agents"][0]["tool_category"] == "research"

    def test_self_loop_removed(self):
        text = '```team_json\n{"agents": [{"id": "a1"}], "edges": [{"from": "a1", "to": "a1"}]}\n```'
        result = _extract_team_json(text)
        assert len(result["edges"]) == 0

    def test_dangling_edge_removed(self):
        text = '```team_json\n{"agents": [{"id": "a1"}], "edges": [{"from": "a1", "to": "nonexistent"}]}\n```'
        result = _extract_team_json(text)
        assert len(result["edges"]) == 0

    def test_circular_reference_clears_edges(self):
        text = '```team_json\n{"agents": [{"id": "a1"}, {"id": "a2"}], "edges": [{"from": "a1", "to": "a2"}, {"from": "a2", "to": "a1"}]}\n```'
        result = _extract_team_json(text)
        assert len(result["edges"]) == 0

    def test_valid_dag_preserved(self):
        text = '```team_json\n{"agents": [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}], "edges": [{"from": "a1", "to": "a2"}, {"from": "a1", "to": "a3"}]}\n```'
        result = _extract_team_json(text)
        assert len(result["edges"]) == 2

    def test_empty_agents_returns_none(self):
        text = '```team_json\n{"agents": []}\n```'
        assert _extract_team_json(text) is None

    def test_no_team_block_returns_none(self):
        assert _extract_team_json("Hello, no team here") is None

    def test_malformed_json_returns_none(self):
        text = '```team_json\n{invalid json}\n```'
        assert _extract_team_json(text) is None

    def test_auto_generates_missing_ids(self):
        text = '```team_json\n{"agents": [{"name": "Agent1"}, {"name": "Agent2"}]}\n```'
        result = _extract_team_json(text)
        assert result["agents"][0]["id"].startswith("agent_temp_")
        assert result["agents"][1]["id"].startswith("agent_temp_")
        assert result["agents"][0]["id"] != result["agents"][1]["id"]


# ── Company CRUD ────────────────────────────────


class TestCompanyStorage:
    def test_save_and_load(self):
        co = save_company(TEST_USER, {"name": "Test Team", "agents": [{"id": "a1"}]})
        assert co["id"].startswith("company_")
        loaded = load_company(TEST_USER, co["id"])
        assert loaded["name"] == "Test Team"

    def test_list_companies(self):
        save_company(TEST_USER, {"name": "Team A"})
        save_company(TEST_USER, {"name": "Team B"})
        companies = list_companies(TEST_USER)
        assert len(companies) >= 2

    def test_delete_company(self):
        co = save_company(TEST_USER, {"name": "To Delete"})
        assert delete_company(TEST_USER, co["id"]) is True
        assert load_company(TEST_USER, co["id"]) is None

    def test_update_existing_company(self):
        co = save_company(TEST_USER, {"name": "V1"})
        co["name"] = "V2"
        save_company(TEST_USER, co)
        loaded = load_company(TEST_USER, co["id"])
        assert loaded["name"] == "V2"


# ── Schedule CRUD ───────────────────────────────


class TestScheduleStorage:
    def test_save_and_load(self):
        sched = save_schedule(TEST_USER, {
            "company_id": "co_001",
            "task_description": "Weekly report",
            "cron_expression": "0 9 * * 1",
        })
        assert sched["id"].startswith("schedule_")
        loaded = load_schedule(TEST_USER, sched["id"])
        assert loaded["task_description"] == "Weekly report"

    def test_toggle_schedule(self):
        sched = save_schedule(TEST_USER, {
            "company_id": "co_001",
            "task_description": "Test",
            "cron_expression": "0 9 * * *",
        })
        toggled = toggle_schedule(TEST_USER, sched["id"], False)
        assert toggled["enabled"] is False

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError, match="Invalid cron"):
            save_schedule(TEST_USER, {
                "company_id": "co_001",
                "cron_expression": "not a cron",
            })

    def test_valid_cron_formats(self):
        assert _validate_cron("0 9 * * *") is True
        assert _validate_cron("*/5 * * * *") is True
        assert _validate_cron("0 9 1,15 * *") is True
        assert _validate_cron("0 9 * * 1-5") is True

    def test_invalid_cron_formats(self):
        assert _validate_cron("not valid") is False
        assert _validate_cron("0 9 * *") is False  # only 4 fields
        assert _validate_cron("") is False


# ── Scheduler integration ───────────────────────


class TestSchedulerIntegration:
    def test_team_injected_into_pre_context(self):
        from src.company_builder.scheduler import _to_scheduled_job

        co = save_company(TEST_USER, {
            "name": "Marketing",
            "agents": [
                {"id": "a1", "name": "Researcher", "role": "Data collection", "tool_category": "research"},
                {"id": "a2", "name": "Analyst", "role": "Analysis", "tool_category": "data"},
            ],
            "edges": [{"from": "a1", "to": "a2"}],
        })

        sched = {"id": "s1", "company_id": co["id"], "task_description": "Run", "cron_expression": "0 9 * * *"}
        job = _to_scheduled_job(sched, TEST_USER)

        assert "Researcher" in job.pre_context.background
        assert "Analyst" in job.pre_context.background
        assert "research" in job.pre_context.domain_answers
        assert "data" in job.pre_context.domain_answers

    def test_missing_company_graceful(self):
        from src.company_builder.scheduler import _to_scheduled_job

        sched = {"id": "s2", "company_id": "nonexistent", "task_description": "Run", "cron_expression": "0 9 * * *"}
        job = _to_scheduled_job(sched, TEST_USER)
        # Should not crash, just no team context
        assert "nonexistent" in job.user_task
