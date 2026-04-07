# src/agent_mode/agent_chat_config.py
"""Configuration for agent-mode ChatEngine instances."""
from __future__ import annotations

from src.config.personas import get_worker_persona, get_worker_name

AGENT_SYSTEM_TEMPLATE = """당신은 {agent_name}입니다. {role}으로서 사용자의 요청에 응답합니다.

전문 분야: {expertise}

{context}

이전 작업 이력:
{history_context}"""


def build_agent_system_prompt(worker_id: str, history_context: str = "") -> str:
    """Build system prompt template for ChatEngine.
    Returns a string with a {context} placeholder that ChatEngine.format() will fill.
    """
    persona = get_worker_persona(worker_id)
    name = get_worker_name(worker_id)
    role = persona.get("role") or worker_id.replace("_", " ").title()
    expertise = persona.get("expertise") or f"{worker_id} 도메인 전문가"
    return AGENT_SYSTEM_TEMPLATE.format(
        agent_name=name,
        role=role,
        expertise=expertise,
        context="{context}",  # kept for ChatEngine's .format(context=...) call
        history_context=history_context or "없음",
    )
