"""SkillSearchIndex + skill_creator_bridge 단위 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.skill_builder.skill_search import SkillSearchIndex


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch) -> Path:
    """가짜 ~/.claude 최소 구조.

    - existing-skill: 신뢰 스킬, install 5000
    - skill-tab-already-mine: 앱이 만든 스킬 (제외되어야 함)
    - obscure: 저인기 스킬, install 50 (임계값 미달로 제외되어야 함)
    """
    claude = tmp_path / ".claude"

    # 신뢰 스킬
    (claude / "skills" / "existing-skill").mkdir(parents=True)
    (claude / "skills" / "existing-skill" / "SKILL.md").write_text(
        "---\nname: existing-skill\n"
        "description: Summarize news articles from RSS feeds\n---\n",
        encoding="utf-8",
    )

    # 앱이 만든 스킬
    (claude / "skills" / "skill-tab-already-mine").mkdir()
    (claude / "skills" / "skill-tab-already-mine" / "SKILL.md").write_text(
        "---\nname: already-mine\ndescription: Should be filtered out\n---\n",
        encoding="utf-8",
    )

    # 저인기 스킬 — 실제 디렉토리 생성하여 min_installs 필터를 실제로 태움
    (claude / "skills" / "obscure").mkdir()
    (claude / "skills" / "obscure" / "SKILL.md").write_text(
        "---\nname: obscure\ndescription: Obscure skill with few installs\n---\n",
        encoding="utf-8",
    )

    (claude / "plugins").mkdir()
    (claude / "plugins" / "install-counts-cache.json").write_text(
        json.dumps(
            {
                "version": 1,
                "fetchedAt": "2026-04-01T00:00:00Z",
                "counts": [
                    {
                        "plugin": "existing-skill@claude-plugins-official",
                        "unique_installs": 5000,
                    },
                    {
                        "plugin": "obscure@other-market",
                        "unique_installs": 50,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return claude


def test_search_finds_installed_trusted_skill(fake_home):
    index = SkillSearchIndex.load()
    results = index.search("summarize news")
    assert len(results) >= 1
    assert any(r.slug == "existing-skill" for r in results)


def test_search_excludes_skill_tab_prefixed(fake_home):
    """앱이 만든 스킬은 검색 결과에 나와서는 안 됨 (이미 등록된 것이므로)."""
    index = SkillSearchIndex.load()
    results = index.search("anything")
    assert not any(r.slug.startswith("skill-tab-") for r in results)


def test_search_excludes_low_install_skills(fake_home):
    """min_unique_installs=1000 미만 스킬은 제외. obscure는 50."""
    index = SkillSearchIndex.load()
    results = index.search("obscure")
    assert not any(r.slug == "obscure" for r in results)


def test_search_returns_empty_when_no_match(fake_home):
    index = SkillSearchIndex.load()
    results = index.search("completely unrelated xyz123")
    assert results == []


def test_handoff_directive_contains_korean_and_skill_enforcement():
    from src.skill_builder.skill_creator_bridge import build_handoff_system_prompt

    prompt = build_handoff_system_prompt(
        user_description="경쟁사 뉴스 요약",
        slug="news-summary",
    )
    # 한국어 지시 포함
    assert "Korean" in prompt or "한국어" in prompt
    # 정확한 저장 경로에 slug가 치환됨
    assert "skill-tab-news-summary" in prompt
    # 미치환된 placeholder 잔존 금지
    assert "{slug}" not in prompt
    # skill vs subagent 구분 명시
    assert "subagent" in prompt.lower()
    # 사용자 설명 포함
    assert "경쟁사 뉴스 요약" in prompt
    # 완료 토큰 명시
    assert "SKILL_COMPLETE" in prompt
