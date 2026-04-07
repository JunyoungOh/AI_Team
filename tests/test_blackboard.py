"""Tests for Blackboard — inter-worker information sharing."""

import json

from src.utils.blackboard import Blackboard, BlackboardEntry, extract_blackboard_entry


def test_post_and_read():
    """Posted entries are readable for later stages."""
    bb = Blackboard()
    entry = BlackboardEntry(
        worker_domain="researcher",
        worker_id="w1",
        stage=0,
        shared_artifacts=["market size: $50B"],
        warnings=["data from 2023, may be outdated"],
    )
    bb.post(entry)

    # Stage 1 can read stage 0 entries
    text = bb.format_for_prompt(current_stage=1)
    assert "researcher" in text
    assert "market size: $50B" in text
    assert "data from 2023" in text


def test_same_stage_not_visible():
    """Entries from the same stage are not included in prompt."""
    bb = Blackboard()
    bb.post(BlackboardEntry(
        worker_domain="researcher", worker_id="w1", stage=0,
        shared_artifacts=["artifact"],
    ))

    # Stage 0 cannot see its own entries
    text = bb.format_for_prompt(current_stage=0)
    assert text == ""


def test_multiple_stages():
    """Entries accumulate across stages."""
    bb = Blackboard()
    bb.post(BlackboardEntry(
        worker_domain="researcher", worker_id="w1", stage=0,
        shared_artifacts=["stage0 result"],
    ))
    bb.post(BlackboardEntry(
        worker_domain="architect", worker_id="w2", stage=1,
        shared_artifacts=["stage1 result"],
    ))

    # Stage 2 sees both stage 0 and stage 1
    text = bb.format_for_prompt(current_stage=2)
    assert "stage0 result" in text
    assert "stage1 result" in text


def test_truncation():
    """Long blackboard content is truncated."""
    bb = Blackboard(max_context_chars=100)
    bb.post(BlackboardEntry(
        worker_domain="researcher", worker_id="w1", stage=0,
        shared_artifacts=["x" * 200],
    ))

    text = bb.format_for_prompt(current_stage=1)
    assert len(text) <= 120  # 100 + truncation marker
    assert "이하 생략" in text


def test_stats():
    """Stats summarize blackboard contents."""
    bb = Blackboard()
    bb.post(BlackboardEntry(
        worker_domain="a", worker_id="w1", stage=0,
        shared_artifacts=["a1", "a2"], warnings=["w1"],
    ))
    bb.post(BlackboardEntry(
        worker_domain="b", worker_id="w2", stage=1,
        shared_artifacts=["b1"], open_questions=["q1"],
    ))

    stats = bb.stats()
    assert stats["total_entries"] == 2
    assert stats["stages_covered"] == 2
    assert stats["total_artifacts"] == 3
    assert stats["total_warnings"] == 1
    assert stats["total_questions"] == 1


def test_extract_blackboard_entry_from_result():
    """extract_blackboard_entry parses WorkerResult JSON."""
    result_json = json.dumps({
        "result_summary": "Analysis complete",
        "deliverables": ["report.md", "data.csv"],
        "issues_encountered": ["missing Q4 data"],
        "completion_percentage": 85,
    })
    worker = {"worker_domain": "data_analyst", "worker_id": "w3"}

    entry = extract_blackboard_entry(worker, result_json, stage=0)
    assert entry is not None
    assert entry.worker_domain == "data_analyst"
    assert entry.stage == 0
    assert "report.md" in entry.shared_artifacts
    assert "missing Q4 data" in entry.warnings


def test_extract_blackboard_entry_empty_result():
    """Empty deliverables and issues returns None."""
    result_json = json.dumps({
        "result_summary": "done",
        "deliverables": [],
        "issues_encountered": [],
        "completion_percentage": 100,
    })
    worker = {"worker_domain": "researcher", "worker_id": "w1"}

    entry = extract_blackboard_entry(worker, result_json, stage=0)
    assert entry is None


def test_extract_blackboard_entry_invalid_json():
    """Invalid JSON returns None without error."""
    entry = extract_blackboard_entry(
        {"worker_domain": "x"}, "not json", stage=0
    )
    assert entry is None
