"""Speak node — designated participant makes a statement."""

from __future__ import annotations

import logging
import time

from src.discussion.prompts.participant import PARTICIPANT_SYSTEM
from src.discussion.state import DiscussionState, Utterance
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)


def _format_conversation(utterances: list[dict]) -> str:
    if not utterances:
        return "(아직 발언 없음)"
    lines = []
    for u in utterances[-12:]:  # Last 12 utterances for context window
        name = u.get("speaker_name", u.get("speaker_id", "?"))
        content = u.get("content", "")
        lines.append(f"[{name}] {content}")
    return "\n\n".join(lines)


async def agent_speak(state: DiscussionState) -> dict:
    """Execute a single participant's utterance."""
    config = state["config"]
    speaker_id = state["next_speaker_id"]
    instruction = state["moderator_instruction"]

    # Find participant
    participant = None
    for p in config.participants:
        if p.id == speaker_id:
            participant = p
            break
    if not participant:
        return {"current_round": state["current_round"] + 1}

    bridge = get_bridge()
    prompt = PARTICIPANT_SYSTEM.format(
        name=participant.name,
        persona=participant.persona,
        topic=config.topic,
        conversation_so_far=_format_conversation(state["utterances"]),
        instruction=instruction,
    )

    try:
        text = await bridge.raw_query(
            system_prompt=prompt,
            user_message=instruction,
            model=config.model_participant,
            allowed_tools=[],
            timeout=90,
            max_turns=1,
            effort="medium",
        )
    except Exception as e:
        logger.warning("speak_llm_failed: %s (speaker=%s)", e, speaker_id)
        text = f"(기술적 문제로 {participant.name}의 발언을 가져오지 못했습니다)"
    finally:
        await bridge.close()

    utterance = Utterance(
        round=state["current_round"],
        speaker_id=speaker_id,
        speaker_name=participant.name,
        content=text.strip(),
        timestamp=time.time(),
    )

    return {
        "utterances": [utterance],
        "current_round": state["current_round"] + 1,
    }
