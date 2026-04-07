import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.agent_mode.agent_router import AgentRouter, RouterDecision


@pytest.fixture
def router():
    return AgentRouter()


class TestContinuityCheck:
    def test_continues_with_last_agent(self, router):
        result = router.check_continuity("그래 그거 해줘", last_agent_id="backend_developer")
        assert result == "backend_developer"

    def test_no_last_agent(self, router):
        result = router.check_continuity("API 만들어줘", last_agent_id=None)
        assert result is None

    def test_new_topic_marker_breaks_continuity(self, router):
        result = router.check_continuity("다른 주제인데 시장 조사해줘", last_agent_id="backend_developer")
        assert result is None

    def test_different_domain_keyword_breaks_continuity(self, router):
        result = router.check_continuity("보안 취약점 분석해줘", last_agent_id="backend_developer")
        assert result is None


class TestRouterDecisionModel:
    def test_valid_decision(self):
        d = RouterDecision(agent_id="researcher", confidence=0.9, reason="연구 관련")
        assert d.agent_id == "researcher"
        assert d.confidence == 0.9

    def test_low_confidence(self):
        d = RouterDecision(agent_id="researcher", confidence=0.3, reason="불확실")
        assert d.confidence < 0.6


class TestRouteAsync:
    @pytest.mark.asyncio
    async def test_route_returns_none_on_failure(self, router):
        with patch.dict("sys.modules", {"anthropic": MagicMock(
            AsyncAnthropic=MagicMock(side_effect=Exception("no key")),
        )}):
            result = await router.route("시장 조사해줘")
            assert result is None
