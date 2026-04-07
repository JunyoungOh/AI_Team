"""Sonnet supervisor — data collection and imagination consolidation via CLI bridge."""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel

from src.dandelion.schemas import Theme, Imagination, Seed
from src.dandelion.prompts.supervisor import (
    build_research_system,
    build_consolidation_system,
    build_consolidation_user,
)
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)

MODEL = "sonnet"


# ── Pydantic schema for consolidation structured output ──

class _SeedOutput(BaseModel):
    title: str
    content: str
    time_months: int
    weight: int


class _ConsolidationOutput(BaseModel):
    seeds: list[_SeedOutput]


class ThemeSupervisor:
    """Sonnet-powered supervisor for one theme's data collection + consolidation."""

    def __init__(self, **_kwargs):
        """Accept and ignore legacy kwargs (e.g. client=) for compatibility."""
        self._bridge = get_bridge()

    async def research(self, themes: list[Theme], common_context: str) -> str:
        """Stage 2: Single research pass covering all themes.

        Uses CLI bridge with web search tools for research.
        """
        themes_dicts = [{"name": t.name, "description": t.description} for t in themes]
        system = build_research_system(themes_dicts, common_context)

        try:
            packet = await self._bridge.raw_query(
                system_prompt=system,
                user_message="Research all themes and produce a comprehensive research packet.",
                model=MODEL,
                allowed_tools=[
                    "mcp__brave-search__brave_web_search",
                    "WebFetch",
                ],
                timeout=300,
            )

            packet = packet.strip() if packet.strip() else common_context
            logger.error(
                "researcher_done packet_len=%d",
                len(packet),
            )
            return packet
        except Exception as exc:
            logger.error("researcher_failed error=%s", exc)
            return common_context

    async def consolidate(self, theme: Theme, imaginations: list[Imagination]) -> list[Seed]:
        """Stage 4: Consolidate imaginations into deduplicated seeds."""
        system = build_consolidation_system(theme.name)
        img_dicts = [img.model_dump() for img in imaginations if img.title != "상상 생성 실패"]

        if not img_dicts:
            logger.error("consolidation_skip theme=%s reason=all_failed", theme.id)
            return [self._fallback_seed(theme)]

        user_msg = build_consolidation_user(img_dicts)
        logger.error("consolidation_start theme=%s input_count=%d user_msg_len=%d", theme.id, len(img_dicts), len(user_msg))

        try:
            data: _ConsolidationOutput = await self._bridge.structured_query(
                system_prompt=system,
                user_message=user_msg,
                output_schema=_ConsolidationOutput,
                model=MODEL,
                allowed_tools=[],
                timeout=120,
            )

            raw_seeds = data.seeds
            if not raw_seeds:
                logger.error("consolidation_empty theme=%s", theme.id)
                return [self._fallback_seed(theme)]

            seeds = []
            for s in raw_seeds:
                content = s.content
                sentences = content.split(". ")
                summary = ". ".join(sentences[:2]) + "." if len(sentences) > 1 else content[:200]
                weight = s.weight

                seeds.append(Seed(
                    id=uuid.uuid4().hex[:12],
                    theme_id=theme.id,
                    title=s.title or "제목 없음",
                    summary=summary,
                    detail=content,
                    time_months=s.time_months,
                    weight=weight,
                    source_count=weight,
                ))

            logger.error("consolidation_ok theme=%s seeds=%d", theme.id, len(seeds))
            if not seeds:
                return [self._fallback_seed(theme)]
            return seeds

        except Exception as exc:
            logger.error("consolidation_failed theme=%s error=%s type=%s", theme.id, exc, type(exc).__name__)
            return [self._fallback_seed(theme)]

    def _fallback_seed(self, theme: Theme) -> Seed:
        return Seed(
            id=uuid.uuid4().hex[:12],
            theme_id=theme.id,
            title="통합 상상",
            summary="이 테마에서 AI들의 상상을 종합한 결과입니다.",
            detail="상세 내용을 생성하지 못했습니다.",
            time_months=12,
            weight=1,
            source_count=1,
        )
