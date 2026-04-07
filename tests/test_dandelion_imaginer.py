"""Unit tests for Haiku imaginer — uses mock API with tool_use responses."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.dandelion.imaginer import Imaginer
from src.dandelion.schemas import Imagination


@pytest.mark.asyncio
async def test_imaginer_returns_imagination():
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "structured_output"
    tool_block.input = {
        "title": "AI 교사 대체",
        "content": "2027년까지 AI가 초등 수학 교육의 70%를 담당하게 된다. 현재 AI 튜터 시장 성장세가 연 40% 이상이며, 정부의 디지털 교육 전환 정책이 이를 가속화한다.",
        "time_months": 11,
    }
    mock_response = MagicMock()
    mock_response.content = [tool_block]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    imaginer = Imaginer(client=mock_client)
    result = await imaginer.imagine(
        theme_id="theme_0",
        theme_name="AI 교육",
        theme_description="AI가 교육을 바꾸는 방식",
        context_packet="AI 튜터 시장이 급성장 중",
        agent_index=0,
    )

    assert isinstance(result, Imagination)
    assert result.theme_id == "theme_0"
    assert result.title == "AI 교사 대체"
    assert result.time_months == 11
    assert len(result.detail) > 0
    assert len(result.summary) > 0
    mock_client.messages.create.assert_called_once()


@pytest.mark.asyncio
async def test_imaginer_handles_no_tool_call():
    mock_response = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "some text"
    mock_response.content = [text_block]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    imaginer = Imaginer(client=mock_client)
    result = await imaginer.imagine(
        theme_id="theme_0",
        theme_name="T",
        theme_description="D",
        context_packet="C",
        agent_index=0,
    )
    assert isinstance(result, Imagination)
    assert result.title == "상상 생성 실패"
