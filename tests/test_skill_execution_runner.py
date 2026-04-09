"""execution_runner 단위 테스트 — 의존성 모킹으로 오케스트레이션 검증."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.skill_builder.execution_runner import run_skill
from src.skill_builder.skill_loader import (
    IsolationMode,
    SkillExecutionContext,
)


def _make_ctx(
    slug: str = "summarize",
    *,
    body: str = "# 3줄 요약\n\n사용자가 준 텍스트를 3줄로 요약합니다.",
    required_mcps=None,
) -> SkillExecutionContext:
    required_mcps = required_mcps or []
    return SkillExecutionContext(
        slug=slug,
        name="3줄 요약",
        skill_path=Path(f"/tmp/skill-tab-{slug}"),
        skill_body=body,
        required_mcps=required_mcps,
        isolation_mode=(
            IsolationMode.WITH_MCPS if required_mcps else IsolationMode.ISOLATED
        ),
    )


async def test_run_skill_isolated_uses_tmp_cwd_and_builtins(tmp_path) -> None:
    captured: dict = {}

    async def fake_streamer(**kwargs):
        captured.update(kwargs)
        kwargs["on_event"]({"action": "started", "elapsed": 0})
        kwargs["on_event"]({
            "action": "completed",
            "elapsed": 1.0,
            "tool_count": 0,
            "timed_out": False,
        })
        return ("결과 텍스트", 0, False)

    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        return_value=_make_ctx(),
    ), patch(
        "src.skill_builder.execution_runner.stream_skill_execution",
        side_effect=fake_streamer,
    ):
        record = await run_skill(
            slug="summarize",
            user_input="긴 글입니다",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert captured["cwd"] == "/tmp"
    assert "Read" in captured["allowed_tools"]
    assert "Write" in captured["allowed_tools"]
    assert not any(
        t.startswith("mcp__") for t in captured["allowed_tools"]
    )
    assert record.status == "completed"
    assert record.result_text == "결과 텍스트"


async def test_run_skill_with_mcps_uses_project_cwd(tmp_path) -> None:
    captured: dict = {}

    async def fake_streamer(**kwargs):
        captured.update(kwargs)
        kwargs["on_event"]({
            "action": "completed",
            "elapsed": 1.0,
            "tool_count": 1,
            "timed_out": False,
        })
        return ("ok", 1, False)

    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        return_value=_make_ctx(required_mcps=["serper", "firecrawl"]),
    ), patch(
        "src.skill_builder.execution_runner.stream_skill_execution",
        side_effect=fake_streamer,
    ):
        record = await run_skill(
            slug="summarize",
            user_input="검색 요청",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert captured["cwd"] != "/tmp"
    assert any("serper" in t for t in captured["allowed_tools"])
    assert any("firecrawl" in t for t in captured["allowed_tools"])
    assert record.status == "completed"


async def test_run_skill_streamer_failure_saved_as_error(tmp_path) -> None:
    async def fake_streamer(**kwargs):
        raise RuntimeError("CLI 충돌")

    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        return_value=_make_ctx(),
    ), patch(
        "src.skill_builder.execution_runner.stream_skill_execution",
        side_effect=fake_streamer,
    ):
        record = await run_skill(
            slug="summarize",
            user_input="x",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert record.status == "error"
    assert "CLI 충돌" in (record.error_message or "")


async def test_run_skill_loader_failure_saved_as_error(tmp_path) -> None:
    with patch(
        "src.skill_builder.execution_runner.load_skill_for_execution",
        side_effect=FileNotFoundError("SKILL.md missing"),
    ):
        record = await run_skill(
            slug="broken",
            user_input="x",
            on_event=lambda e: None,
            runs_root=tmp_path,
        )

    assert record.status == "error"
    assert "SKILL.md missing" in (record.error_message or "")
