import pytest
from src.agent_mode.agent_summary_store import AgentSummaryStore


@pytest.fixture
def store(tmp_path):
    return AgentSummaryStore(base_dir=tmp_path)


class TestAgentSummaryStore:
    def test_add_summary(self, store):
        store.add_summary("backend_developer", "session_1", "REST API 5개 설계")
        summaries = store.get_summaries("backend_developer")
        assert len(summaries) == 1
        assert summaries[0]["summary"] == "REST API 5개 설계"

    def test_multiple_summaries(self, store):
        store.add_summary("backend_developer", "s1", "API 설계")
        store.add_summary("backend_developer", "s2", "인증 추가")
        summaries = store.get_summaries("backend_developer")
        assert len(summaries) == 2

    def test_empty_agent(self, store):
        summaries = store.get_summaries("nonexistent")
        assert summaries == []

    def test_get_recent_context(self, store):
        store.add_summary("backend_developer", "s1", "API 설계")
        store.add_summary("backend_developer", "s2", "인증 추가")
        ctx = store.get_recent_context("backend_developer", max_entries=1)
        assert "인증 추가" in ctx
        assert "API 설계" not in ctx

    def test_persistence(self, store, tmp_path):
        store.add_summary("researcher", "s1", "시장 조사 완료")
        store2 = AgentSummaryStore(base_dir=tmp_path)
        assert len(store2.get_summaries("researcher")) == 1
