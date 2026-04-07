import pytest
from src.agent_mode.mention_parser import MentionParser


@pytest.fixture
def parser():
    return MentionParser()


class TestParseMention:
    def test_name_mention(self, parser):
        result = parser.parse("@민준 API 만들어줘")
        assert result.agent_id == "backend_developer"
        assert result.message == "API 만들어줘"

    def test_position_mention(self, parser):
        result = parser.parse("@backend_developer REST API 설계해줘")
        assert result.agent_id == "backend_developer"
        assert result.message == "REST API 설계해줘"

    def test_partial_position_mention(self, parser):
        result = parser.parse("@backend 서버 코드 짜줘")
        assert result.agent_id == "backend_developer"

    def test_keyword_mention(self, parser):
        result = parser.parse("@백엔드 DB 스키마 설계해줘")
        assert result.agent_id == "backend_developer"

    def test_no_mention(self, parser):
        result = parser.parse("이거 어떻게 생각해?")
        assert result.agent_id is None
        assert result.message == "이거 어떻게 생각해?"

    def test_unknown_mention(self, parser):
        result = parser.parse("@없는사람 뭐해")
        assert result.agent_id is None
        assert result.message == "@없는사람 뭐해"


class TestAutocomplete:
    def test_empty_query(self, parser):
        candidates = parser.autocomplete("")
        assert len(candidates) == 39

    def test_name_query(self, parser):
        candidates = parser.autocomplete("민준")
        assert any(c["id"] == "backend_developer" for c in candidates)

    def test_partial_position_query(self, parser):
        candidates = parser.autocomplete("back")
        assert any(c["id"] == "backend_developer" for c in candidates)

    def test_keyword_query(self, parser):
        candidates = parser.autocomplete("백엔드")
        assert any(c["id"] == "backend_developer" for c in candidates)

    def test_domain_grouping(self, parser):
        candidates = parser.autocomplete("")
        domains = {c["domain"] for c in candidates}
        assert "engineering" in domains
        assert "research" in domains
