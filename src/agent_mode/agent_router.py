"""Route no-mention messages to the appropriate agent."""
from __future__ import annotations

import logging
from pydantic import BaseModel

from src.config.agent_registry import LEADER_DOMAINS
from src.config.personas import WORKER_NAMES

logger = logging.getLogger(__name__)

ROUTER_SYSTEM = """당신은 메시지 라우터입니다. 사용자 메시지를 가장 적합한 에이전트에게 전달하세요.
가용 에이전트: {agent_list}
직전 대화 에이전트: {last_agent_id}"""

ROUTER_TIMEOUT = 5.0
CONFIDENCE_THRESHOLD = 0.6

_NEW_TOPIC_MARKERS = {"다른", "새로", "이번에는", "그런데", "한편", "그리고", "또", "별도로"}


class RouterDecision(BaseModel):
    agent_id: str
    confidence: float
    reason: str


class AgentRouter:
    def __init__(self) -> None:
        self._agent_descriptions = self._build_agent_list()

    def _build_agent_list(self) -> str:
        lines = []
        for domain, info in LEADER_DOMAINS.items():
            for wid in info["worker_types"]:
                meta = WORKER_NAMES.get(wid, {})
                name = meta.get("name", wid)
                keywords = ", ".join(meta.get("keywords", []))
                lines.append(f"  {wid} ({name}): {keywords}")
        return "\n".join(lines)

    def check_continuity(self, message: str, last_agent_id: str | None) -> str | None:
        """Return last_agent_id if the message is a follow-up. Return None on topic change."""
        if not last_agent_id:
            return None
        first_word = message.split()[0] if message.strip() else ""
        if any(marker in first_word for marker in _NEW_TOPIC_MARKERS):
            return None
        for wid, meta in WORKER_NAMES.items():
            if wid == last_agent_id:
                continue
            for kw in meta.get("keywords", []):
                if kw in message:
                    return None
        return last_agent_id

    async def route(self, message: str, last_agent_id: str | None = None) -> RouterDecision | None:
        """Use Haiku to classify the message to an agent. Returns None on timeout/failure."""
        from src.utils.bridge_factory import get_bridge

        try:
            bridge = get_bridge()

            system = ROUTER_SYSTEM.format(
                agent_list=self._agent_descriptions,
                last_agent_id=last_agent_id or "없음",
            )

            result = await bridge.structured_query(
                system_prompt=system,
                user_message=message,
                output_schema=RouterDecision,
                model="haiku",
                allowed_tools=[],
                timeout=int(ROUTER_TIMEOUT),
            )

            return result

        except Exception as e:
            logger.warning("AgentRouter failed: %s", e)
            return None
