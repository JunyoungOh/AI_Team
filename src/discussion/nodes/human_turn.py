"""Human turn node — blocks for user input via WebSocket."""

from __future__ import annotations

import logging
import time

# config는 엔진이 자동으로 dict를 전달한다
RunnableConfig = dict

from src.discussion.config import HumanParticipant
from src.discussion.state import DiscussionState, Utterance, HUMAN_SPEAKER_ID

logger = logging.getLogger(__name__)

HUMAN_TURN_TIMEOUT = 120  # seconds


def _make_human_utterance(
    content: str | None,
    human: HumanParticipant,
    current_round: int,
) -> Utterance:
    """Create utterance from human input. None content = skip/timeout."""
    if content is None:
        content = f"({human.name}님이 이번 턴을 패스했습니다)"
    return Utterance(
        round=current_round,
        speaker_id=HUMAN_SPEAKER_ID,
        speaker_name=human.name,
        content=content.strip(),
        timestamp=time.time(),
    )


async def human_turn(state: DiscussionState, config: RunnableConfig) -> dict:
    """Wait for human input via WebSocket, then produce utterance."""
    disc_config = state["config"]
    human = disc_config.human_participant
    if not human:
        logger.warning("human_turn called but no human_participant configured")
        return {"current_round": state["current_round"] + 1}

    # Session is passed via LangGraph configurable (NOT state — state is serialised by checkpointer)
    session = config["configurable"].get("session") if config else None
    if not session:
        logger.error("human_turn: no session reference in configurable")
        utterance = _make_human_utterance(None, human, state["current_round"])
        return {
            "utterances": [utterance],
            "current_round": state["current_round"] + 1,
            "human_input_pending": False,
        }

    # Emit human turn event to frontend
    await session._send({
        "type": "disc_human_turn",
        "data": {
            "instruction": state.get("moderator_instruction", ""),
            "timeout_sec": HUMAN_TURN_TIMEOUT,
        },
    })

    # Wait for input — handle cancellation (stop button, WS disconnect)
    import asyncio
    try:
        content = await session.wait_for_human_input(timeout=HUMAN_TURN_TIMEOUT)
    except asyncio.CancelledError:
        logger.info("human_turn cancelled during wait")
        content = None

    utterance = _make_human_utterance(content, human, state["current_round"])

    return {
        "utterances": [utterance],
        "current_round": state["current_round"] + 1,
        "human_input_pending": False,
    }
