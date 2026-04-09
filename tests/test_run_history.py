"""run_history 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skill_builder.run_history import (
    RunRecord,
    list_runs,
    load_run,
    save_run,
)


def test_save_then_list_returns_run(tmp_path: Path) -> None:
    rec = save_run(
        slug="summarize",
        user_input="긴 글...",
        result_text="3줄 요약 결과",
        status="completed",
        tool_count=5,
        duration_seconds=12.3,
        runs_root=tmp_path,
    )
    assert isinstance(rec, RunRecord)
    assert rec.slug == "summarize"
    assert rec.status == "completed"

    listed = list_runs("summarize", runs_root=tmp_path)
    assert len(listed) == 1
    assert listed[0].run_id == rec.run_id
    assert listed[0].user_input == "긴 글..."


def test_list_runs_sorts_newest_first(tmp_path: Path) -> None:
    import time

    save_run(
        slug="x",
        user_input="a",
        result_text="r1",
        status="completed",
        tool_count=0,
        duration_seconds=1.0,
        runs_root=tmp_path,
    )
    time.sleep(1.1)
    save_run(
        slug="x",
        user_input="b",
        result_text="r2",
        status="completed",
        tool_count=0,
        duration_seconds=1.0,
        runs_root=tmp_path,
    )
    listed = list_runs("x", runs_root=tmp_path)
    assert len(listed) == 2
    assert listed[0].user_input == "b"
    assert listed[1].user_input == "a"


def test_list_runs_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_runs("nope", runs_root=tmp_path) == []


def test_load_run_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_run("x", "1234567890-abcdefgh", runs_root=tmp_path)


def test_save_run_with_error_status(tmp_path: Path) -> None:
    rec = save_run(
        slug="x",
        user_input="y",
        result_text="",
        status="error",
        tool_count=0,
        duration_seconds=0.5,
        error_message="timeout",
        runs_root=tmp_path,
    )
    assert rec.status == "error"
    assert rec.error_message == "timeout"


def test_save_run_rejects_path_traversal_slug(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_run(
            slug="../escape",
            user_input="x",
            result_text="y",
            status="completed",
            tool_count=0,
            duration_seconds=1.0,
            runs_root=tmp_path,
        )
