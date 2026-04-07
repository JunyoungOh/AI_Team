"""Persona build node — clones real-person personas via web search + uploaded data."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

# config는 엔진이 자동으로 dict를 전달한다
RunnableConfig = dict

from src.discussion.config import DiscussionConfig, Participant
from src.discussion.prompts.persona_builder import PERSONA_BUILDER_SYSTEM
from src.discussion.state import DiscussionState
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)


def _load_uploaded_files(file_paths: list[str]) -> str:
    texts = []
    for fp in file_paths:
        try:
            p = Path(fp)
            if p.exists() and p.stat().st_size < 5_000_000:
                texts.append(p.read_text(encoding="utf-8", errors="replace")[:10000])
        except Exception as e:
            logger.warning("upload_read_failed: %s", e)
    return "\n\n---\n\n".join(texts) if texts else ""


async def _build_one(
    participant: Participant,
    topic: str,
    session=None,
    index: int = 0,
    total: int = 1,
) -> Participant:
    clone = participant.clone_config
    if not clone:
        return participant

    try:
        if session:
            await session._send({
                "type": "disc_persona_progress",
                "data": {"participant_name": participant.name, "stage": "searching", "index": index, "total": total},
            })

        search_results = ""
        if clone.web_search:
            bridge = get_bridge()
            try:
                search_results = await bridge.raw_query(
                    system_prompt="주어진 인물에 대한 공개 발언, 인터뷰, 관점을 검색하여 요약하세요.",
                    user_message=f"인물: {participant.name}\n토론 주제: {topic}",
                    model="sonnet",
                    allowed_tools=["WebSearch", "WebFetch", "mcp__firecrawl__firecrawl_scrape"],
                    timeout=60, max_turns=3, effort="medium",
                )
            except Exception as e:
                logger.warning("persona_search_failed: %s (name=%s)", e, participant.name)
            finally:
                await bridge.close()

        uploaded_text = _load_uploaded_files(clone.files)

        if session:
            await session._send({
                "type": "disc_persona_progress",
                "data": {"participant_name": participant.name, "stage": "synthesizing", "index": index, "total": total},
            })

        bridge = get_bridge()
        try:
            persona_prompt = await bridge.raw_query(
                system_prompt=PERSONA_BUILDER_SYSTEM,
                user_message=f"인물: {participant.name}\n토론 주제: {topic}\n웹검색 결과:\n{search_results}\n\n업로드 데이터:\n{uploaded_text}",
                model="sonnet", allowed_tools=[], timeout=60, max_turns=1, effort="medium",
            )
        finally:
            await bridge.close()

        if session:
            await session._send({
                "type": "disc_persona_progress",
                "data": {"participant_name": participant.name, "stage": "done", "index": index, "total": total},
            })

        return participant.model_copy(update={"persona": persona_prompt.strip()})

    except Exception as e:
        logger.warning("persona_build_failed: %s (name=%s)", e, participant.name)
        return participant


async def persona_build(state: DiscussionState, config: RunnableConfig) -> dict:
    disc_config = state["config"]
    session = config["configurable"].get("session") if config else None

    # Only clone participants with clone_config AND no persona_id (pre-built skips cloning)
    clone_indices = [i for i, p in enumerate(disc_config.participants)
                     if p.clone_config and not p.persona_id]

    if not clone_indices:
        return {"config": disc_config}

    clone_participants = [disc_config.participants[i] for i in clone_indices]
    results = await asyncio.gather(*[
        _build_one(p, disc_config.topic, session, idx, len(clone_participants))
        for idx, p in enumerate(clone_participants)
    ])

    # Preserve original participant order
    updated_participants = list(disc_config.participants)
    for i, orig_idx in enumerate(clone_indices):
        updated_participants[orig_idx] = results[i]

    updated_config = disc_config.model_copy(update={"participants": updated_participants})
    return {"config": updated_config, "phase": "opening"}
