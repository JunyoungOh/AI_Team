"""Haiku imagination agent — structured output via CLI bridge."""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel

from src.dandelion.schemas import Imagination
from src.dandelion.prompts.imaginer import build_imaginer_system, build_imaginer_user
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)

MODEL = "haiku"


class _ImaginationOutput(BaseModel):
    """Structured output schema for imaginer."""
    title: str
    content: str
    time_months: int


class Imaginer:
    """Calls Haiku once to generate a single future imagination via structured output."""

    def __init__(self, **_kwargs):
        """Accept and ignore legacy kwargs (e.g. client=) for compatibility."""
        self._bridge = get_bridge()

    async def imagine(
        self,
        theme_id: str,
        theme_name: str,
        theme_description: str,
        context_packet: str,
        agent_index: int,
    ) -> Imagination:
        system = build_imaginer_system(theme_name)
        user_msg = build_imaginer_user(theme_description, context_packet, agent_index)

        try:
            data: _ImaginationOutput = await self._bridge.structured_query(
                system_prompt=system,
                user_message=user_msg,
                output_schema=_ImaginationOutput,
                model=MODEL,
                allowed_tools=[],
                timeout=120,
            )

            title = data.title or "제목 없음"
            content = data.content or ""
            time_months = data.time_months or 12

            logger.error(
                "imaginer_ok agent=%d theme=%s title=%s content_len=%d",
                agent_index, theme_id, title[:30], len(content),
            )

            # Split content into summary (first 2 sentences) and detail (full)
            sentences = content.split(". ")
            summary = ". ".join(sentences[:2]) + "." if len(sentences) > 1 else content[:200]

            return Imagination(
                id=uuid.uuid4().hex[:12],
                theme_id=theme_id,
                title=title,
                summary=summary,
                detail=content,
                time_point=f"{time_months}개월 후",
                time_months=time_months,
            )

        except Exception as exc:
            logger.error("imaginer_failed agent=%d theme=%s error=%s type=%s", agent_index, theme_id, exc, type(exc).__name__)
            return self._fallback(theme_id, agent_index, str(exc))

    def _fallback(self, theme_id: str, agent_index: int, error: str) -> Imagination:
        return Imagination(
            id=uuid.uuid4().hex[:12],
            theme_id=theme_id,
            title="상상 생성 실패",
            summary=f"Agent #{agent_index + 1} 실패",
            detail=error,
            time_point="unknown",
            time_months=6,
        )
