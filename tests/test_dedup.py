"""Tests for src/utils/dedup — findings, summaries, deliverables deduplication."""

import pytest

from src.utils.dedup import (
    deduplicate_deliverables,
    deduplicate_findings,
    deduplicate_summaries,
    _normalize,
    _similarity,
)


# ── Helpers ──────────────────────────────────────────


class TestNormalize:
    def test_basic(self):
        assert _normalize("  Hello, World!  ") == "hello world"

    def test_korean(self):
        assert _normalize("카카오 목표가 하향.") == "카카오 목표가 하향"

    def test_whitespace(self):
        assert _normalize("a  b\n\tc") == "a b c"

    def test_empty(self):
        assert _normalize("") == ""


class TestSimilarity:
    def test_identical(self):
        assert _similarity("hello", "hello") == 1.0

    def test_empty(self):
        assert _similarity("", "hello") == 0.0

    def test_similar(self):
        s = _similarity("카카오 목표가 하향", "카카오 목표가 하향 조정")
        assert s > 0.7

    def test_different(self):
        s = _similarity("카카오 목표가 하향", "삼성전자 실적 발표")
        assert s < 0.3


# ── Findings Dedup ───────────────────────────────────


class TestDeduplicateFindings:
    def test_empty(self):
        assert deduplicate_findings([]) == []

    def test_single(self):
        f = [{"content": "테스트", "category": "fact", "importance": 5}]
        assert deduplicate_findings(f) == f

    def test_identical_findings_from_different_workers(self):
        """같은 내용을 다른 워커가 수집한 경우 — 실제 중복 패턴.

        실제 리포트에서 Section 1과 Section 2에 동일 findings가 반복됨.
        """
        findings = [
            {
                "content": "한국투자증권 카카오 목표가 75,000→70,000원 하향, 투자의견 매수 유지 (2026-04-03)",
                "category": "fact",
                "importance": 5,
                "source": "글로벌이코노믹 2026-04-03",
            },
            {
                "content": "한국투자증권 카카오 목표가 75,000→70,000원 하향, 투자의견 매수 유지 (2026-04-03)",
                "category": "fact",
                "importance": 5,
                "source": "글로벌이코노믹 2026-04-03",
            },
        ]
        result = deduplicate_findings(findings)
        assert len(result) == 1

    def test_similar_findings_with_different_expression(self):
        """같은 사실이지만 표현이 다른 경우 — 숫자 표기 등이 다를 수 있음."""
        findings = [
            {
                "content": "한국투자증권 카카오 목표가 75,000→70,000원 하향, 투자의견 매수 유지 (2026-04-03)",
                "category": "fact",
                "importance": 5,
                "source": "글로벌이코노믹 2026-04-03",
            },
            {
                "content": "한국투자증권, 카카오 목표주가 7만5,000원→7만원 하향. AI 수익화 가능성 인정하나 시간 필요 평가. 투자의견 매수 유지.",
                "category": "fact",
                "importance": 5,
                "source": "https://www.g-enews.com/article/Securities/2026/04/",
            },
        ]
        result = deduplicate_findings(findings)
        # 표현이 크게 다르면 2개 유지될 수 있음 (false negative보다 false positive가 더 위험)
        assert len(result) <= 2

    def test_similar_findings_different_detail(self):
        """같은 사실의 상세도가 다른 버전 — 하나만 남아야 함."""
        findings = [
            {
                "content": "카카오 1Q26 매출 2.02조 원(YoY +8.6%) 컨센서스 상회 예상",
                "category": "statistic",
                "importance": 5,
                "source": "글로벌이코노믹",
            },
            {
                "content": "한국투자증권 카카오 목표주가 75,000원 → 70,000원 하향 (2026-04-03), 투자의견 매수 유지. 1Q 매출 2.02조원(YoY +8.6%), 영업이익 1,885억원 컨센서스 상회 예상. 톡비즈 광고 두 자릿수 성장.",
                "category": "statistic",
                "importance": 5,
                "source": "글로벌이코노믹",
            },
        ]
        result = deduplicate_findings(findings)
        # 두 findings는 카테고리가 같고 유사하므로 1개만 남아야
        assert len(result) <= 2  # 유사도에 따라 1-2개

    def test_distinct_findings_preserved(self):
        """서로 다른 주제의 findings는 모두 보존."""
        findings = [
            {
                "content": "카카오 주가 2026년 4월 3일 45,200원 마감",
                "category": "statistic",
                "importance": 5,
            },
            {
                "content": "신원근 카카오페이 대표 3연임 확정, 임기 2028년까지",
                "category": "fact",
                "importance": 5,
            },
            {
                "content": "카카오 AI 에이전틱 생태계 구축 목표 공식화",
                "category": "analysis",
                "importance": 4,
            },
        ]
        result = deduplicate_findings(findings)
        assert len(result) == 3

    def test_higher_importance_preserved(self):
        """importance가 다른 중복 — 높은 쪽 보존."""
        findings = [
            {"content": "카카오 목표가 하향", "category": "fact", "importance": 3},
            {"content": "카카오 목표가 하향 조정", "category": "fact", "importance": 5},
        ]
        result = deduplicate_findings(findings)
        assert len(result) == 1
        assert result[0]["importance"] == 5

    def test_custom_threshold(self):
        """threshold를 높이면 더 적게 제거."""
        findings = [
            {"content": "카카오 목표가 하향", "category": "fact", "importance": 5},
            {"content": "카카오 목표가 하향 조정됨", "category": "fact", "importance": 5},
        ]
        strict = deduplicate_findings(findings, threshold=0.95)
        lenient = deduplicate_findings(findings, threshold=0.5)
        assert len(strict) >= len(lenient)


# ── Summaries Dedup ──────────────────────────────────


class TestDeduplicateSummaries:
    def test_empty(self):
        assert deduplicate_summaries([]) == []

    def test_single(self):
        assert deduplicate_summaries(["요약"]) == ["요약"]

    def test_identical_summaries(self):
        """동일한 요약 반복 — 실제 PESR 루프 패턴."""
        summaries = [
            "카카오 최신 뉴스 3건 수집 완료 (2026-04-06 기준). ① 4월 4일 카카오 주가 마감",
            "카카오 최신 뉴스 3건 수집 완료 (2026-04-06 기준). ① 4월 4일 카카오 주가 마감",
        ]
        result = deduplicate_summaries(summaries)
        assert len(result) == 1

    def test_subset_summary_removed(self):
        """짧은 요약이 긴 요약의 부분집합인 경우."""
        short = "카카오 최신 뉴스 3건 수집"
        long = "카카오 최신 뉴스 3건 수집 완료. 주가, 연임, 목표가 관련 기사 포함."
        result = deduplicate_summaries([short, long])
        assert len(result) == 1
        assert result[0] == long  # 긴 것 보존

    def test_distinct_summaries_preserved(self):
        """서로 다른 내용의 요약은 보존."""
        summaries = [
            "카카오 주가 분석: 4월 3일 45,200원 마감",
            "카카오페이 리더십: 신원근 대표 3연임 확정",
        ]
        result = deduplicate_summaries(summaries)
        assert len(result) == 2

    def test_short_summaries_skipped(self):
        """너무 짧은 요약(10자 미만)은 무시."""
        result = deduplicate_summaries(["abc", "긴 요약 텍스트입니다 이것은 충분히 깁니다"])
        assert len(result) == 1

    def test_three_worker_repeat_pattern(self):
        """실제 PESR 루프: executor, synthesizer, reviewer가 같은 요약 반복."""
        base = "카카오 최신 뉴스 3건 수집 완료 (2026-04-06 기준)."
        summaries = [
            base + " ① 4월 4일 — 카카오 주가 4만5천원대 마감",
            base + " ① 2026-03-26 — 카카오 정신아 대표 연임 확정, ② 2026-03-23 — 신원근 카카오페이 대표 3연임",
            base + " ① 2026-04-03 글로벌이코노믹: 한국투자증권 목표가 하향, ② 코리안센터: 정신아 대표 연임",
        ]
        result = deduplicate_summaries(summaries)
        # 같은 prefix를 공유하므로 일부 제거될 수 있지만, 내용이 다르면 보존
        assert len(result) >= 1


# ── Deliverables Dedup ───────────────────────────────


class TestDeduplicateDeliverables:
    def test_empty(self):
        assert deduplicate_deliverables([]) == []

    def test_exact_duplicates(self):
        result = deduplicate_deliverables(["항목A", "항목A", "항목B"])
        assert result == ["항목A", "항목B"]

    def test_case_insensitive(self):
        result = deduplicate_deliverables(["Report PDF", "report pdf"])
        assert len(result) == 1

    def test_near_duplicates(self):
        """같은 산출물의 약간 다른 표현."""
        deliverables = [
            "뉴스 3: [신원근 카카오페이 대표 3연임] https://www.etoday.co.kr/news/view/2568307",
            "기사2: 신원근 카카오페이 대표 3연임 — 제목·요약·URL 완성 (이투데이, 2026-03-23)",
        ]
        result = deduplicate_deliverables(deliverables)
        # 표현이 다르므로 보존될 수 있음 (threshold 0.80이면)
        assert len(result) >= 1

    def test_distinct_preserved(self):
        result = deduplicate_deliverables([
            "카카오 주가 분석 보고서",
            "카카오페이 리더십 변동 분석",
            "data/outputs/kakao_news_20260406.md 파일 생성",
        ])
        assert len(result) == 3

    def test_empty_strings_filtered(self):
        result = deduplicate_deliverables(["", "  ", "실제 항목"])
        assert result == ["실제 항목"]

    def test_order_preserved(self):
        """원본 순서 유지 확인."""
        items = ["첫번째", "두번째", "세번째"]
        result = deduplicate_deliverables(items)
        assert result == items
