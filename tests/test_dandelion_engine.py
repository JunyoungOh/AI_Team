"""Unit tests for DandelionEngine orchestration."""
import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.dandelion.engine import DandelionEngine
from src.dandelion.schemas import THEME_COLORS


@pytest.mark.asyncio
async def test_engine_cancel():
    ws = AsyncMock()
    engine = DandelionEngine(ws=ws)
    assert not engine._cancelled
    engine.cancel()
    assert engine._cancelled


@pytest.mark.asyncio
async def test_engine_theme_color_assignment():
    """Engine should assign colors by index, not from LLM output."""
    ws = AsyncMock()
    engine = DandelionEngine(ws=ws)

    ceo_response = {
        "themes": [
            {"name": f"Theme {i}", "description": f"Desc {i}"}
            for i in range(4)
        ],
        "common_context": "test context",
    }

    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type="text", text=json.dumps(ceo_response))]

    with patch.object(engine, '_client') as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        assignment = await engine._decide_themes("test query", [])

    assert len(assignment.themes) == 4
    for i, theme in enumerate(assignment.themes):
        assert theme.color == THEME_COLORS[i]
        assert theme.id == f"theme_{i}"


@pytest.mark.asyncio
async def test_engine_sends_themes_message():
    """Engine should send themes WS message after CEO stage."""
    ws = AsyncMock()
    engine = DandelionEngine(ws=ws)

    ceo_response = {
        "themes": [
            {"name": f"T{i}", "description": f"D{i}"} for i in range(4)
        ],
        "common_context": "ctx",
    }
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type="text", text=json.dumps(ceo_response))]

    with patch.object(engine, '_client') as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        with patch.object(engine, '_run_theme_pipeline', new_callable=AsyncMock):
            await engine.run("test", [])

    calls = ws.send_json.call_args_list
    msgs = [c[0][0] for c in calls]
    # Should contain progress, themes, and complete messages
    types = [m["type"] for m in msgs]
    assert "progress" in types
    assert "themes" in types
    assert "complete" in types
    themes_msg = next(m for m in msgs if m["type"] == "themes")
    assert len(themes_msg["themes"]) == 4
