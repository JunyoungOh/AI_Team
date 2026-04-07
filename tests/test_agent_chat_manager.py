import pytest
from src.agent_mode.agent_chat_manager import AgentChatManager


@pytest.fixture
def manager(tmp_path):
    return AgentChatManager(session_id="test_session", summary_base_dir=tmp_path)


class TestAgentChatManager:
    def test_set_active_agent(self, manager):
        manager.set_active_agent("backend_developer")
        assert manager.active_agent_id == "backend_developer"

    def test_switch_agent_triggers_summary(self, manager):
        manager.set_active_agent("backend_developer")
        manager._conversations["backend_developer"] = [
            {"role": "user", "content": "API 만들어줘"},
            {"role": "assistant", "content": "네, REST API를 설계하겠습니다."},
        ]
        manager.set_active_agent("researcher")
        assert manager.active_agent_id == "researcher"
        # Verify summary was stored
        summaries = manager._summary_store.get_summaries("backend_developer")
        assert len(summaries) == 1

    def test_add_message(self, manager):
        manager.set_active_agent("backend_developer")
        manager.add_message("backend_developer", "user", "hello")
        assert len(manager.get_conversation("backend_developer")) == 1

    def test_get_empty_conversation(self, manager):
        assert manager.get_conversation("nonexistent") == []

    def test_build_system_prompt(self, manager):
        prompt = manager.build_system_prompt("backend_developer")
        assert "민준" in prompt
        assert "{context}" in prompt  # placeholder for ChatEngine

    def test_on_disconnect_saves_summary(self, manager):
        manager.set_active_agent("backend_developer")
        manager.add_message("backend_developer", "user", "API 만들어줘")
        manager.add_message("backend_developer", "assistant", "네, 설계하겠습니다.")
        manager.on_disconnect()
        summaries = manager._summary_store.get_summaries("backend_developer")
        assert len(summaries) == 1
