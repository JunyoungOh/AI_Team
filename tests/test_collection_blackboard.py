"""Tests for CollectionBlackboard — LabeledFinding-based data accumulator."""

from src.utils.collection_blackboard import BlackboardEntry, CollectionBlackboard


def _findings(n=3, importance=3, category="fact", worker_id="w1", worker_domain="researcher"):
    """Helper: create N findings as dicts (mimics JSON from WorkerResult)."""
    return [
        {
            "content": f"Finding {i}",
            "category": category,
            "importance": importance,
            "source": f"https://example.com/{i}",
        }
        for i in range(n)
    ]


class TestWriteFindings:
    def test_write_findings_basic(self):
        bb = CollectionBlackboard()
        count = bb.write_findings("w1", "researcher", _findings(3))
        assert count == 3
        assert bb.total_entries == 3

    def test_write_findings_multiple_workers(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", _findings(2))
        bb.write_findings("w2", "data_analyst", _findings(3))
        assert bb.total_entries == 5

    def test_write_empty_findings(self):
        bb = CollectionBlackboard()
        count = bb.write_findings("w1", "researcher", [])
        assert count == 0
        assert bb.total_entries == 0


class TestReadForAnalyst:
    def test_analyst_view_has_stats(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", _findings(3, category="fact"))
        bb.write_findings("w2", "researcher", _findings(2, category="statistic"))
        result = bb.read_for_analyst()
        assert "총 5건" in result
        assert "fact" in result
        assert "statistic" in result
        assert "w1" in result
        assert "w2" in result

    def test_analyst_view_empty(self):
        bb = CollectionBlackboard()
        result = bb.read_for_analyst()
        assert "없음" in result

    def test_analyst_importance_sorting(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", [
            {"content": "Low", "category": "fact", "importance": 1, "source": ""},
            {"content": "High", "category": "fact", "importance": 5, "source": ""},
        ])
        result = bb.read_for_analyst()
        high_pos = result.index("High")
        low_pos = result.index("Low")
        assert high_pos < low_pos


class TestReadForCEO:
    def test_ceo_view_separates_critical_and_supporting(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", [
            {"content": "Critical finding", "category": "statistic", "importance": 5, "source": ""},
            {"content": "Minor finding", "category": "fact", "importance": 2, "source": ""},
        ])
        result = bb.read_for_ceo()
        assert "핵심 데이터" in result
        assert "보충 데이터" in result
        assert "Critical finding" in result
        assert "Minor finding" in result

    def test_ceo_view_empty(self):
        bb = CollectionBlackboard()
        result = bb.read_for_ceo()
        assert "없음" in result


class TestCharLimit:
    def test_analyst_truncation(self):
        bb = CollectionBlackboard(max_chars=500)
        bb.write_findings("w1", "researcher", [
            {"content": "x" * 200, "category": "fact", "importance": 3, "source": ""}
            for _ in range(20)
        ])
        result = bb.read_for_analyst()
        assert "생략" in result


class TestSerialization:
    def test_serialize_and_deserialize(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", _findings(2))
        bb.write_findings("w2", "data_analyst", _findings(3))

        data = bb.serialize()
        restored = CollectionBlackboard.deserialize(data)

        assert restored.total_entries == 5
        assert "researcher" in restored.read_for_analyst()

    def test_deserialize_none(self):
        restored = CollectionBlackboard.deserialize(None)
        assert restored.total_entries == 0

    def test_deserialize_empty(self):
        restored = CollectionBlackboard.deserialize({})
        assert restored.total_entries == 0


class TestSummary:
    def test_summary_format(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", _findings(3, importance=5))
        summary = bb.get_summary()
        assert "3건" in summary
        assert "핵심 3건" in summary

    def test_summary_empty(self):
        bb = CollectionBlackboard()
        assert "비어있음" in bb.get_summary()


class TestStats:
    def test_stats_structure(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", _findings(2, category="fact"))
        bb.write_findings("w2", "researcher", _findings(1, category="statistic"))
        stats = bb.stats()
        assert stats["total_entries"] == 3
        assert "w1" in stats["workers"]
        assert "fact" in stats["categories"]


class TestGapsAndCounts:
    def test_gaps_by_category(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", _findings(3, category="fact"))
        bb.write_findings("w2", "researcher", _findings(2, category="statistic"))
        gaps = bb.get_gaps_by_category()
        assert gaps["fact"] == 3
        assert gaps["statistic"] == 2

    def test_count_by_importance(self):
        bb = CollectionBlackboard()
        bb.write_findings("w1", "researcher", [
            {"content": "a", "category": "fact", "importance": 5, "source": ""},
            {"content": "b", "category": "fact", "importance": 5, "source": ""},
            {"content": "c", "category": "fact", "importance": 3, "source": ""},
        ])
        counts = bb.count_by_importance()
        assert counts[5] == 2
        assert counts[3] == 1
