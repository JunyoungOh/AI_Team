# src/agent_mode/agent_chat_manager.py
"""Manage per-agent conversations and execution modes."""
from __future__ import annotations

import logging
from pathlib import Path

from src.agent_mode.agent_summary_store import AgentSummaryStore
from src.agent_mode.agent_chat_config import build_agent_system_prompt
from src.config.personas import get_worker_name

logger = logging.getLogger(__name__)


class AgentChatManager:
    def __init__(self, session_id: str, summary_base_dir: Path | str | None = None) -> None:
        self._session_id = session_id
        self._summary_store = AgentSummaryStore(base_dir=summary_base_dir)
        self._conversations: dict[str, list[dict]] = {}
        self._active_agent_id: str | None = None

    @property
    def active_agent_id(self) -> str | None:
        return self._active_agent_id

    def set_active_agent(self, agent_id: str) -> None:
        if self._active_agent_id and self._active_agent_id != agent_id:
            self._mark_for_summary(self._active_agent_id)
        self._active_agent_id = agent_id
        if agent_id not in self._conversations:
            self._conversations[agent_id] = []

    def add_message(self, agent_id: str, role: str, content: str) -> None:
        if agent_id not in self._conversations:
            self._conversations[agent_id] = []
        self._conversations[agent_id].append({"role": role, "content": content})

    def get_conversation(self, agent_id: str) -> list[dict]:
        return self._conversations.get(agent_id, [])

    def build_system_prompt(self, agent_id: str) -> str:
        history_ctx = self._summary_store.get_recent_context(agent_id)
        return build_agent_system_prompt(agent_id, history_ctx)

    def get_agent_display(self, agent_id: str) -> dict:
        return {
            "agent_id": agent_id,
            "agent_name": get_worker_name(agent_id),
        }

    def _mark_for_summary(self, agent_id: str) -> None:
        conv = self._conversations.get(agent_id, [])
        if len(conv) < 2:
            return
        last_assistant = ""
        for msg in reversed(conv):
            if msg["role"] == "assistant":
                last_assistant = msg["content"][:200]
                break
        if last_assistant:
            self._summary_store.add_summary(agent_id, self._session_id, last_assistant)

    def on_disconnect(self) -> None:
        if self._active_agent_id:
            self._mark_for_summary(self._active_agent_id)
