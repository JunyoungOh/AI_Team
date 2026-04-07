"""Tests for MetricsStore and MetricsExporter."""

import os
import tempfile

import pytest


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite DB path."""
    return str(tmp_path / "test_metrics.db")


@pytest.fixture
def store(tmp_db):
    from src.utils.metrics_store import MetricsStore
    return MetricsStore(db_path=tmp_db)


@pytest.fixture
def sample_summary():
    """A typical ExecutionTracker.summary() output."""
    return {
        "total_session_seconds": 520.3,
        "worker_count": 3,
        "avg_worker_duration_s": 160.5,
        "max_worker_duration_s": 280.0,
        "workers": [
            {
                "domain": "research_analyst",
                "model": "sonnet",
                "duration_s": 180.0,
                "tier": 1,
                "stage": 0,
                "success": True,
                "cached": False,
            },
            {
                "domain": "marketing_content_creator",
                "model": "sonnet",
                "duration_s": 280.0,
                "tier": 2,
                "stage": 0,
                "success": True,
                "cached": False,
            },
            {
                "domain": "tech_code_developer",
                "model": "opus",
                "duration_s": 21.5,
                "tier": 1,
                "stage": 0,
                "success": False,
                "cached": False,
            },
        ],
        "nodes": [
            {"node": "ceo_route", "duration_s": 15.2},
            {"node": "worker_execution", "duration_s": 300.1},
        ],
        "tier_distribution": {"tier_1": 2, "tier_2": 1},
        "cache_stats": {"hit_rate_pct": 0},
    }


# ── MetricsStore Tests ────────────────────────────────────


class TestMetricsStoreSave:
    def test_save_and_query_roundtrip(self, store, sample_summary):
        store.save_session(sample_summary, user_task="test task", session_id="sess-001")

        sessions = store.get_recent_sessions(limit=10)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-001"
        assert sessions[0]["user_task"] == "test task"
        assert sessions[0]["worker_count"] == 3

    def test_save_multiple_sessions(self, store, sample_summary):
        store.save_session(sample_summary, user_task="task 1", session_id="sess-001")
        store.save_session(sample_summary, user_task="task 2", session_id="sess-002")

        sessions = store.get_recent_sessions()
        assert len(sessions) == 2

    def test_save_duplicate_session_replaces(self, store, sample_summary):
        store.save_session(sample_summary, user_task="task v1", session_id="sess-001")
        store.save_session(sample_summary, user_task="task v2", session_id="sess-001")

        sessions = store.get_recent_sessions()
        assert len(sessions) == 1
        assert sessions[0]["user_task"] == "task v2"

    def test_save_empty_summary(self, store):
        store.save_session({}, user_task="", session_id="empty")
        sessions = store.get_recent_sessions()
        assert len(sessions) == 1
        assert sessions[0]["worker_count"] == 0


class TestMetricsStoreQuery:
    def test_empty_store_returns_empty(self, store):
        assert store.get_recent_sessions() == []
        assert store.get_domain_stats() == {}
        assert store.get_failure_patterns() == []

    def test_session_count(self, store, sample_summary):
        store.save_session(sample_summary, session_id="s1")
        store.save_session(sample_summary, session_id="s2")
        assert store.get_session_count(days=30) == 2

    def test_domain_stats_aggregation(self, store, sample_summary):
        store.save_session(sample_summary, session_id="s1")
        store.save_session(sample_summary, session_id="s2")

        stats = store.get_domain_stats()
        assert "research_analyst" in stats
        assert "marketing_content_creator" in stats

        research = stats["research_analyst"]
        assert research.session_count == 2
        assert research.avg_duration_s == 180.0
        assert research.success_rate == 1.0

        tech = stats["tech_code_developer"]
        assert tech.success_rate == 0.0  # Both sessions failed

    def test_domain_stats_timeout_rate(self, store, sample_summary):
        store.save_session(sample_summary, session_id="s1")

        stats = store.get_domain_stats()
        marketing = stats["marketing_content_creator"]
        assert marketing.timeout_rate == 1.0  # tier 2 = degraded

    def test_failure_patterns(self, store, sample_summary):
        store.save_session(sample_summary, session_id="s1")
        store.save_session(sample_summary, session_id="s2")

        patterns = store.get_failure_patterns()
        domains = [p.worker_domain for p in patterns]
        assert "tech_code_developer" in domains

        tech_pattern = next(p for p in patterns if p.worker_domain == "tech_code_developer")
        assert tech_pattern.failure_rate == 1.0
        assert tech_pattern.failure_count == 2

    def test_overall_stats(self, store, sample_summary):
        store.save_session(sample_summary, session_id="s1")

        overall = store.get_overall_stats()
        assert overall["session_count"] == 1
        assert overall["avg_duration_s"] == 520.3
        assert overall["total_workers"] == 3

    def test_overall_stats_empty(self, store):
        overall = store.get_overall_stats()
        assert overall["session_count"] == 0
        assert overall["success_rate"] == 0


class TestMetricsStoreConcurrency:
    def test_concurrent_saves(self, store, sample_summary):
        import threading

        errors = []

        def save(idx):
            try:
                store.save_session(sample_summary, session_id=f"concurrent-{idx}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=save, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        sessions = store.get_recent_sessions(limit=10)
        assert len(sessions) == 5


# ── MetricsExporter Tests ─────────────────────────────────


class TestMetricsExporter:
    def test_export_empty_store(self, store, tmp_path):
        from src.utils.metrics_exporter import MetricsExporter
        exporter = MetricsExporter(store)
        path = str(tmp_path / "report.md")
        result = exporter.export_report(path=path)

        assert result == path
        content = open(path).read()
        assert "# Execution Metrics Report" in content
        assert "No actionable improvements" in content

    def test_export_with_data(self, store, sample_summary, tmp_path):
        from src.utils.metrics_exporter import MetricsExporter

        store.save_session(sample_summary, session_id="s1")
        store.save_session(sample_summary, session_id="s2")
        store.save_session(sample_summary, session_id="s3")

        exporter = MetricsExporter(store)
        path = str(tmp_path / "report.md")
        exporter.export_report(path=path)

        content = open(path).read()
        assert "## Performance Summary" in content
        assert "## Domain Performance" in content
        assert "research_analyst" in content
        assert "marketing_content_creator" in content

    def test_export_replaces_file(self, store, sample_summary, tmp_path):
        from src.utils.metrics_exporter import MetricsExporter

        path = str(tmp_path / "report.md")
        exporter = MetricsExporter(store)

        # First export (empty)
        exporter.export_report(path=path)
        content1 = open(path).read()

        # Add data and export again
        store.save_session(sample_summary, session_id="s1")
        exporter.export_report(path=path)
        content2 = open(path).read()

        # Content should differ (replaced, not appended)
        assert content1 != content2
        # Should not contain duplicate headers
        assert content2.count("# Execution Metrics Report") == 1

    def test_export_includes_key_files_reference(self, store, tmp_path):
        from src.utils.metrics_exporter import MetricsExporter

        exporter = MetricsExporter(store)
        path = str(tmp_path / "report.md")
        exporter.export_report(path=path)

        content = open(path).read()
        assert "src/config/settings.py" in content
        assert "src/config/agent_registry.py" in content
        assert "src/tools/__init__.py" in content

    def test_improvements_need_minimum_sessions(self, store, sample_summary, tmp_path):
        from src.utils.metrics_exporter import MetricsExporter

        # Only 1 session — not enough for improvement suggestions
        store.save_session(sample_summary, session_id="s1")

        exporter = MetricsExporter(store)
        path = str(tmp_path / "report.md")
        exporter.export_report(path=path)

        content = open(path).read()
        assert "No actionable improvements" in content

    def test_failure_patterns_in_report(self, store, sample_summary, tmp_path):
        from src.utils.metrics_exporter import MetricsExporter

        for i in range(5):
            store.save_session(sample_summary, session_id=f"s{i}")

        exporter = MetricsExporter(store)
        path = str(tmp_path / "report.md")
        exporter.export_report(path=path)

        content = open(path).read()
        assert "## Failure Patterns" in content
        assert "tech_code_developer" in content
