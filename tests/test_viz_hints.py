"""Unit tests for visualization hints extraction and prompt building."""

import pytest


def test_extract_viz_hints_empty_findings():
    from src.prompts.visualization_guides import extract_viz_hints
    hints = extract_viz_hints([])
    assert hints.numeric_count == 0
    assert hints.comparison_pairs == 0
    assert hints.has_timeline is False
    assert hints.data_complexity == "low"
    assert hints.recommend_interactive is False


def test_extract_viz_hints_statistic_count():
    from src.prompts.visualization_guides import extract_viz_hints
    findings = [
        {"content": "매출 1조원", "category": "statistic", "importance": 5, "source": ""},
        {"content": "점유율 35%", "category": "statistic", "importance": 4, "source": ""},
        {"content": "시장 성장 분석", "category": "analysis", "importance": 3, "source": ""},
    ]
    hints = extract_viz_hints(findings)
    assert hints.numeric_count == 2


def test_extract_viz_hints_comparison_detection():
    from src.prompts.visualization_guides import extract_viz_hints
    findings = [
        {"content": "삼성 vs SK하이닉스 비교", "category": "analysis", "importance": 5, "source": ""},
        {"content": "A 대비 B가 20% 높음", "category": "statistic", "importance": 4, "source": ""},
        {"content": "기술 차이가 큼", "category": "fact", "importance": 3, "source": ""},
    ]
    hints = extract_viz_hints(findings)
    assert hints.comparison_pairs >= 3


def test_extract_viz_hints_timeline_detection():
    from src.prompts.visualization_guides import extract_viz_hints
    findings = [
        {"content": "2024년 1분기 매출 증가", "category": "statistic", "importance": 5, "source": ""},
        {"content": "Q3 실적 발표", "category": "fact", "importance": 3, "source": ""},
    ]
    hints = extract_viz_hints(findings)
    assert hints.has_timeline is True


def test_extract_viz_hints_high_complexity():
    from src.prompts.visualization_guides import extract_viz_hints
    findings = [
        {"content": f"수치 데이터 {i}", "category": "statistic", "importance": 3, "source": ""}
        for i in range(12)
    ]
    hints = extract_viz_hints(findings)
    assert hints.data_complexity == "high"
    assert hints.recommend_interactive is True


def test_extract_viz_hints_medium_complexity():
    from src.prompts.visualization_guides import extract_viz_hints
    findings = [
        {"content": f"수치 {i}", "category": "statistic", "importance": 3, "source": ""}
        for i in range(6)
    ]
    hints = extract_viz_hints(findings)
    assert hints.data_complexity == "medium"
    assert hints.recommend_interactive is False


def test_build_report_prompt_appends_guide():
    from src.prompts.visualization_guides import build_report_prompt
    result = build_report_prompt("Base prompt here.", "comparison")
    assert "Base prompt here." in result
    assert "비교" in result


def test_build_report_prompt_unknown_type_falls_back_to_general():
    from src.prompts.visualization_guides import build_report_prompt
    result = build_report_prompt("Base.", "nonexistent_type")
    assert "Base." in result


def test_build_report_prompt_with_viz_hints():
    from src.prompts.visualization_guides import build_report_prompt, extract_viz_hints
    findings = [
        {"content": "매출 1조", "category": "statistic", "importance": 5, "source": ""},
    ]
    hints = extract_viz_hints(findings)
    result = build_report_prompt("Base.", "market_research", viz_hints=hints)
    assert "시각화 힌트" in result
    assert "수치 데이터" in result


def test_build_report_prompt_no_viz_hints():
    from src.prompts.visualization_guides import build_report_prompt
    result = build_report_prompt("Base.", "technical")
    assert "시각화 힌트" not in result


def test_all_guides_have_no_unescaped_braces():
    from src.prompts.visualization_guides import VISUALIZATION_GUIDES
    for name, guide in VISUALIZATION_GUIDES.items():
        stripped = guide.replace("{{", "").replace("}}", "")
        assert "{" not in stripped, f"Guide '{name}' contains unescaped '{{'"
        assert "}" not in stripped, f"Guide '{name}' contains unescaped '}}'"


def test_to_prompt_section_format():
    from src.prompts.visualization_guides import extract_viz_hints
    findings = [
        {"content": "A vs B", "category": "statistic", "importance": 5, "source": ""},
    ]
    hints = extract_viz_hints(findings)
    section = hints.to_prompt_section()
    assert "## 시각화 힌트" in section
    assert "수치 데이터" in section
