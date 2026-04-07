# tests/test_agent_session.py
import pytest
from unittest.mock import AsyncMock, patch
from src.agent_mode.session import AgentSession


class TestAgentSession:
    def test_init_creates_components(self):
        ws = AsyncMock()
        session = AgentSession(ws)
        assert session._mention_parser is not None
        assert session._router is not None
        assert session._chat_manager is not None
        assert session._bg_manager is not None

    def test_build_init_message(self):
        ws = AsyncMock()
        session = AgentSession(ws)
        msg = session._build_init_message()
        assert msg["type"] == "agent_init"
        assert "session_id" in msg
        assert "agents" in msg
        assert len(msg["agents"]) == 39
        agent = msg["agents"][0]
        assert "id" in agent
        assert "name" in agent
        assert "domain" in agent


class TestAgentSessionIntegration:
    @pytest.mark.asyncio
    async def test_full_message_flow(self):
        ws = AsyncMock()
        ws.receive_json = AsyncMock(side_effect=[
            {"type": "agent_message", "content": "@민준 hello"},
            {"type": "agent_stop"},
        ])
        session = AgentSession(ws)

        with patch.object(session, '_stream_response', new_callable=AsyncMock):
            await session.run()

        init_call = ws.send_json.call_args_list[0]
        assert init_call[0][0]["type"] == "agent_init"
        assert len(init_call[0][0]["agents"]) == 39

    @pytest.mark.asyncio
    async def test_bg_switch_at_capacity(self):
        ws = AsyncMock()
        session = AgentSession(ws)

        for _ in range(3):
            session._bg_manager.start_task("researcher", "task", ws)

        ws.receive_json = AsyncMock(side_effect=[
            {"type": "agent_bg_switch", "agent_id": "backend_developer"},
            {"type": "agent_stop"},
        ])

        await session.run()

        calls = [c[0][0] for c in ws.send_json.call_args_list]
        error_msgs = [c for c in calls if c.get("type") == "error"]
        assert len(error_msgs) == 1
