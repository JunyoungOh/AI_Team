"""Tests for format_persona_block multiline handling."""
import pytest
from src.config.personas import format_persona_block


class TestFormatPersonaBlock:
    """Test persona dict → prompt text conversion."""

    def test_single_line_values(self):
        """Existing single-line behavior should be preserved."""
        persona = {"role": "테스트 역할", "expertise": "테스트 전문성"}
        result = format_persona_block(persona)
        assert "- role: 테스트 역할" in result
        assert "- expertise: 테스트 전문성" in result

    def test_list_values(self):
        """List values should be joined with semicolons."""
        persona = {"skills": ["Python", "Go", "Rust"]}
        result = format_persona_block(persona)
        assert "- skills: Python; Go; Rust" in result

    def test_multiline_values_rendered_as_block(self):
        """Multiline strings should be rendered as labeled blocks, not inline."""
        persona = {
            "role": "팩트체커",
            "analysis_framework": "Step 1: 주장 분해\nStep 2: 출처 탐색\nStep 3: 교차 검증",
        }
        result = format_persona_block(persona)
        assert "- role: 팩트체커" in result
        assert "- analysis_framework:" not in result
        assert "### analysis_framework" in result
        assert "Step 1: 주장 분해" in result
        assert "Step 2: 출처 탐색" in result

    def test_empty_persona(self):
        """Empty dict should return empty string."""
        assert format_persona_block({}) == ""

    def test_mixed_types(self):
        """Dict with single-line, list, and multiline values."""
        persona = {
            "role": "분석가",
            "skills": ["통계", "시각화"],
            "analysis_framework": "Step 1: 수집\nStep 2: 분석",
        }
        result = format_persona_block(persona)
        assert "- role: 분석가" in result
        assert "- skills: 통계; 시각화" in result
        assert "### analysis_framework" in result


def test_agent_mode_role_expertise_preserved():
    """agent_mode requires .get('role') and .get('expertise') to work."""
    from src.config.personas import WORKER_PERSONAS
    for worker_id, persona in WORKER_PERSONAS.items():
        assert "role" in persona, f"{worker_id} missing 'role'"
        assert "expertise" in persona, f"{worker_id} missing 'expertise'"


def test_all_registered_workers_have_personas():
    """Every worker in agent_registry should have a persona."""
    from src.config.agent_registry import get_all_registered_workers
    from src.config.personas import WORKER_PERSONAS
    registered = get_all_registered_workers()
    # deep_researcher uses separate prompt system
    skip = {"deep_researcher"}
    for worker in registered - skip:
        assert worker in WORKER_PERSONAS, f"{worker} registered but has no persona"
