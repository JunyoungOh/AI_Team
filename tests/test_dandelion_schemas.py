"""Unit tests for Dandelion data schemas."""
import pytest
from src.dandelion.schemas import (
    Theme, ThemeAssignment, Imagination, Seed, ThemeResult, DandelionTree,
    THEME_COLORS,
)


def test_theme_colors_has_four():
    assert len(THEME_COLORS) == 4
    assert all(c.startswith("#") for c in THEME_COLORS)


def test_theme_assignment_enforces_four_themes():
    themes = [
        Theme(id=f"theme_{i}", name=f"T{i}", color=THEME_COLORS[i], description=f"Desc {i}")
        for i in range(4)
    ]
    ta = ThemeAssignment(themes=themes, common_context="ctx", user_query="q?")
    assert len(ta.themes) == 4


def test_theme_assignment_rejects_wrong_count():
    themes = [
        Theme(id="theme_0", name="T0", color="#4FC3F7", description="d")
    ]
    with pytest.raises(ValueError):
        ThemeAssignment(themes=themes, common_context="ctx", user_query="q")


def test_seed_time_months_clamped():
    s = Seed(
        id="s1", theme_id="theme_0", title="T", summary="S",
        detail="D", reasoning="R", time_months=120, weight=1, source_count=1,
    )
    assert s.time_months == 60


def test_seed_time_months_min_clamped():
    s = Seed(
        id="s1", theme_id="theme_0", title="T", summary="S",
        detail="D", reasoning="R", time_months=-5, weight=1, source_count=1,
    )
    assert s.time_months == 1


def test_seed_to_ws_dict():
    s = Seed(
        id="s1", theme_id="theme_0", title="T", summary="S",
        detail="D", reasoning="R", time_months=6, weight=3, source_count=3,
    )
    d = s.to_ws_dict()
    assert d["id"] == "s1"
    assert d["weight"] == 3
    assert "theme_id" in d


def test_theme_result_minimum_one_seed():
    theme = Theme(id="theme_0", name="T", color="#4FC3F7", description="d")
    tr = ThemeResult(theme=theme, seeds=[])
    assert tr.seeds == []


def test_dandelion_tree_snapshot():
    themes = [
        Theme(id=f"theme_{i}", name=f"T{i}", color=THEME_COLORS[i], description=f"D{i}")
        for i in range(4)
    ]
    tree = DandelionTree(
        query="q?", themes=themes, seeds=[], created_at="2026-04-01T00:00:00",
    )
    assert tree.query == "q?"
    assert len(tree.themes) == 4
