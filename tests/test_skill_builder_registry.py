"""SkillRegistry 단위 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.skill_builder.registry import SkillRecord, SkillRegistry


@pytest.fixture
def tmp_registry(tmp_path: Path) -> SkillRegistry:
    path = tmp_path / "registry.json"
    path.write_text("[]")
    return SkillRegistry(path=path)


def test_empty_registry_returns_empty_list(tmp_registry: SkillRegistry):
    assert tmp_registry.list_all() == []


def test_add_and_list(tmp_registry: SkillRegistry):
    record = SkillRecord(
        slug="news-summary",
        name="경쟁사 뉴스 요약",
        skill_path="~/.claude/skills/skill-tab-news-summary",
        required_mcps=["mcp__serper__google_search"],
        source="created",
        created_at="2026-04-09T10:00:00Z",
    )
    tmp_registry.add(record)
    listed = tmp_registry.list_all()
    assert len(listed) == 1
    assert listed[0].slug == "news-summary"
    assert listed[0].name == "경쟁사 뉴스 요약"
    assert listed[0].required_mcps == ["mcp__serper__google_search"]


def test_add_persists_to_disk(tmp_registry: SkillRegistry, tmp_path: Path):
    record = SkillRecord(
        slug="t1",
        name="t1",
        skill_path="/x",
        required_mcps=[],
        source="imported",
        created_at="2026-04-09T10:00:00Z",
    )
    tmp_registry.add(record)
    data = json.loads((tmp_path / "registry.json").read_text())
    assert len(data) == 1
    assert data[0]["slug"] == "t1"
    assert data[0]["source"] == "imported"


def test_duplicate_slug_raises(tmp_registry: SkillRegistry):
    record = SkillRecord(
        slug="dup",
        name="x",
        skill_path="/x",
        required_mcps=[],
        source="created",
        created_at="2026-04-09T10:00:00Z",
    )
    tmp_registry.add(record)
    with pytest.raises(ValueError, match="이미 존재"):
        tmp_registry.add(record)
