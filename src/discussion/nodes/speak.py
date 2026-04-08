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


def _format_others_since_last_turn(utterances: list[dict], speaker_id: str) -> str:
    """Return only the utterances from OTHER participants since this speaker's
    last turn. The resumed CLI session already remembers this speaker's own
    history, so we just need to feed what they missed."""
    last_self_idx = -1
    for i in range(len(utterances) - 1, -1, -1):
        if utterances[i].get("speaker_id") == speaker_id:
            last_self_idx = i
            break
    others = utterances[last_self_idx + 1:]
    if not others:
        return "(다른 참가자의 새로운 발언이 없습니다.)"
    lines = []
    for u in others:
        name = u.get("speaker_name", u.get("speaker_id", "?"))
        content = u.get("content", "")
        lines.append(f"[{name}] {content}")
    return "\n\n".join(lines)


async def agent_speak(state: DiscussionState) -> dict:
    """Execute a single participant's utterance.

    Resumes the participant's persistent Claude CLI session (created in
    opening_speak) so the persona + topic system prompt is not re-sent. Only
    the new utterances from other participants and the moderator's
    instruction are passed for this turn.
    """
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
    sid = (state.get("participant_sessions") or {}).get(speaker_id)

    if sid:
        # Resume mode: persona is already in the session. Send only what
        # the speaker missed plus the moderator's instruction.
        others = _format_others_since_last_turn(state["utterances"], speaker_id)
        user_message = (
            f"이번 라운드 다른 참가자들의 발언:\n{others}\n\n"
            f"진행자 지시: {instruction}\n\n"
            "위 흐름을 이어 본인의 입장을 발언해 주세요."
        )
        query_kwargs = {
            "system_prompt": "",  # No re-injection; resumed session has it.
            "user_message": user_message,
            "resume": sid,
        }
    else:
        # Fallback path: no session was set up (shouldn't happen with new
        # setup node, but keeps backward compatibility).
        prompt = PARTICIPANT_SYSTEM.format(
            name=participant.name,
            persona=participant.persona,
            topic=config.topic,
            conversation_so_far=_format_conversation(state["utterances"]),
            instruction=instruction,
        )
        query_kwargs = {"system_prompt": prompt, "user_message": instruction}

    try:
        text = await bridge.raw_query(
            model=config.model_participant,
            # WebSearch + WebFetch are Claude Code built-ins (no MCP cold
            # start). Each speaker self-decides whether to fact-check
            # before responding — no moderator gatekeeping.
            allowed_tools=["WebSearch", "WebFetch"],
            timeout=120,
            max_turns=3,  # up to 2 tool calls + final answer
            effort="medium",
            **query_kwargs,
        )
    except Exception as e:
        logger.warning("speak_llm_failed: %s (speaker=%s)", e, speaker_id)
        text = f"(기술적 문제로 {participant.name}의 발언을 가져오지 못했습니다)"

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
