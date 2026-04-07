"""Unit tests for Sonnet supervisor — collection and consolidation."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.dandelion.supervisor import ThemeSupervisor
from src.dandelion.schemas import Theme, Imagination, Seed, THEME_COLORS


def _make_theme(idx: int = 0) -> Theme:
    return Theme(id=f"theme_{idx}", name="AI 교육", color=THEME_COLORS[idx], description="desc")


def _make_imagination(title: str, time_months: int = 6) -> Imagination:
    return Imagination(
        id="i1", theme_id="theme_0", title=title, summary="s",
        detail="d", reasoning="r", time_point="2026", time_months=time_months,
    )


@pytest.mark.asyncio
async def test_supervisor_research_returns_packet():
    mock_response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Research findings about all themes..."
    mock_response.content = [text_block]
    mock_response.stop_reason = "end_turn"

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    sup = ThemeSupervisor(client=mock_client)
    themes = [_make_theme(i) for i in range(4)]
    packet = await sup.research(themes, "common context")

    assert "Research findings" in packet
    mock_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_supervisor_consolidate_returns_seeds():
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "structured_output"
    tool_block.input = {
        "seeds": [
            {
                "title": "AI 튜터",
                "content": "AI 튜터가 교육을 혁신한다. 근거는 시장 성장세.",
                "time_months": 6,
                "weight": 3,
            }
        ]
    }
    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.stop_reason = "end_turn"

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    sup = ThemeSupervisor(client=mock_client)
    imaginations = [_make_imagination(f"T{i}") for i in range(10)]
    seeds = await sup.consolidate(_make_theme(), imaginations)

    assert len(seeds) >= 1
    assert isinstance(seeds[0], Seed)
    assert seeds[0].weight == 3
    assert seeds[0].source_count == 3


@pytest.mark.asyncio
async def test_supervisor_consolidate_empty_seeds():
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "structured_output"
    tool_block.input = {"seeds": []}
    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.stop_reason = "end_turn"

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    sup = ThemeSupervisor(client=mock_client)
    imaginations = [_make_imagination("T")]
    seeds = await sup.consolidate(_make_theme(), imaginations)
    assert len(seeds) >= 1


@pytest.mark.asyncio
async def test_supervisor_consolidate_all_haiku_failed():
    mock_client = AsyncMock()
    sup = ThemeSupervisor(client=mock_client)
    imaginations = [
        Imagination(
            id=f"i{i}", theme_id="theme_0", title="상상 생성 실패",
            summary="fail", detail="err", reasoning="N/A",
            time_point="unknown", time_months=6,
        )
        for i in range(10)
    ]
    seeds = await sup.consolidate(_make_theme(), imaginations)
    assert len(seeds) == 1
    mock_client.messages.create.assert_not_called()
