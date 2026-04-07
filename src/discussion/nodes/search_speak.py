"""Search-enabled speak node — participant speaks with web search tools."""

from __future__ import annotations

import logging
import time

# config는 엔진이 자동으로 dict를 전달한다
RunnableConfig = dict

from src.discussion.prompts.participant import PARTICIPANT_SYSTEM
from src.discussion.state import DiscussionState, Utterance
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)

SEARCH_TOOLS = [
    "WebSearch",                           # Anthropic server-side 웹검색
    "WebFetch",                            # Anthropic server-side 웹페이지 읽기
    "mcp__firecrawl__firecrawl_scrape",    # Firecrawl 폴백 스크래핑
]


def _format_conversation(utterances: list[dict]) -> str:
    if not utterances:
        return "(아직 발언 없음)"
    lines = []
    for u in utterances[-12:]:
        name = u.get("speaker_name", u.get("speaker_id", "?"))
        content = u.get("content", "")
        lines.append(f"[{name}] {content}")
    return "\n\n".join(lines)


async def search_speak(state: DiscussionState, config: RunnableConfig) -> dict:
    """Execute a participant's utterance with web search tools enabled."""
    disc_config = state["config"]
    speaker_id = state["next_speaker_id"]
    instruction = state["moderator_instruction"]

    participant = None
    for p in disc_config.participants:
        if p.id == speaker_id:
            participant = p
            break
    if not participant:
        return {"current_round": state["current_round"] + 1, "needs_search": False}

    # Emit search start event
    session = config["configurable"].get("session") if config else None
    if session:
        await session._send({
            "type": "disc_search_start",
            "data": {"speaker_id": speaker_id, "speaker_name": participant.name},
        })

    bridge = get_bridge()
    prompt = PARTICIPANT_SYSTEM.format(
        name=participant.name,
        persona=participant.persona,
        topic=disc_config.topic,
        conversation_so_far=_format_conversation(state["utterances"]),
        instruction=instruction,
    )

    try:
        text = await bridge.raw_query(
            system_prompt=prompt,
            user_message=instruction,
            model=disc_config.model_participant,
            allowed_tools=SEARCH_TOOLS,
            timeout=120,
            max_turns=3,
            effort="medium",
        )
    except Exception as e:
        logger.warning("search_speak_failed: %s (speaker=%s)", e, speaker_id)
        text = f"(검색 중 오류 발생 — {participant.name}의 발언을 가져오지 못했습니다)"
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
        "needs_search": False,
    }
