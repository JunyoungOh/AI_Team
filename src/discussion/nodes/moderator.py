"""Moderator turn node — decides next speaker and instruction."""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel

from src.discussion.prompts.moderator import (
    MODERATOR_HUMAN_SECTION, MODERATOR_SYSTEM, STYLE_DESCRIPTIONS,
)
from src.discussion.state import HUMAN_SPEAKER_ID, DiscussionState
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)


class ModeratorDecision(BaseModel):
    next_speaker_id: str
    instruction: str
    reasoning: str = ""


def _format_participants(config) -> str:
    lines = [
        f"- {p.name} ({p.id}): {p.persona}"
        for p in config.participants
    ]
    # Include human participant so moderator knows to assign them turns
    if config.human_participant:
        hp = config.human_participant
        desc = hp.persona if hp.persona else "실제 사용자 (AI가 아님)"
        lines.append(f"- {hp.name} ({HUMAN_SPEAKER_ID}): {desc}")
    return "\n".join(lines)


def _format_conversation(utterances: list[dict]) -> str:
    if not utterances:
        return "(아직 발언 없음)"
    lines = []
    for u in utterances:
        name = u.get("speaker_name", u.get("speaker_id", "?"))
        content = u.get("content", "")
        lines.append(f"[{name}] {content}")
    return "\n\n".join(lines)


def _is_time_up(state: DiscussionState) -> bool:
    elapsed = time.time() - state["start_time"]
    return elapsed >= state["time_limit_sec"]


def _round_robin_fallback(state: DiscussionState) -> dict:
    """Fallback: pick next speaker via round-robin when LLM call fails."""
    config = state["config"]
    # Build speaker pool including human if participating
    speakers = [(p.id, p.name) for p in config.participants]
    if config.human_participant:
        speakers.append((HUMAN_SPEAKER_ID, config.human_participant.name))
    idx = state["current_round"] % len(speakers)
    speaker_id, _ = speakers[idx]
    return {
        "next_speaker_id": speaker_id,
        "moderator_instruction": f"{config.topic}에 대해 의견을 말씀해 주세요.",
    }


async def moderator_turn(state: DiscussionState) -> dict:
    """Moderator decides next speaker or ends discussion."""
    # Check time limit or cancellation
    if state.get("cancelled") or _is_time_up(state):
        return {"phase": "closing"}
    config = state["config"]
    bridge = get_bridge()

    base_prompt = MODERATOR_SYSTEM.format(
        participants_info=_format_participants(config),
        topic=config.topic,
        style_desc=STYLE_DESCRIPTIONS.get(config.style, STYLE_DESCRIPTIONS["free"]),
        conversation_so_far=_format_conversation(state["utterances"]),
    )
    # Dynamically append human section if a human participant is present
    extra = ""
    if config.human_participant:
        extra += MODERATOR_HUMAN_SECTION.format(human_name=config.human_participant.name)
    prompt = base_prompt + extra

    try:
        decision = await bridge.structured_query(
            system_prompt=prompt,
            user_message=f"라운드 {state['current_round']}: 다음 발언자를 지정하세요.",
            output_schema=ModeratorDecision,
            model=config.model_moderator,
            allowed_tools=[],
            timeout=90,
            max_turns=3,
            effort="medium",
        )
    except Exception as e:
        logger.warning("moderator_llm_failed: %s (round=%s)", e, state["current_round"])
        return _round_robin_fallback(state)
    finally:
        await bridge.close()

    # Validate speaker exists
    valid_ids = {p.id for p in config.participants}
    if config.human_participant:
        valid_ids.add(HUMAN_SPEAKER_ID)
    speaker_id = decision.next_speaker_id
    if speaker_id not in valid_ids:
        speaker_id = config.participants[state["current_round"] % len(config.participants)].id

    return {
        "next_speaker_id": speaker_id,
        "moderator_instruction": decision.instruction,
    }
