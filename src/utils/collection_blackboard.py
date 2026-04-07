"""Collection Blackboard — worker findings accumulator for Analyst & CEO.

Workers write LabeledFindings (tagged with worker_id).
Analyst reads all findings to judge data sufficiency.
CEO reads all findings directly for final report generation.

Thread-safe, importance-sorted, with per-worker partitioning.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from src.utils.dedup import deduplicate_findings
from src.utils.logging import get_logger

logger = get_logger(agent_id="collection_blackboard")


@dataclass
class BlackboardEntry:
    """Blackboard에 저장되는 개별 항목 — LabeledFinding + 메타데이터."""

    content: str = ""
    category: str = ""        # fact, statistic, quote, analysis, recommendation, risk, opportunity
    importance: int = 3        # 1-5 (5=핵심)
    source: str = ""
    worker_id: str = ""        # 어떤 워커가 수집했는지
    worker_domain: str = ""    # researcher, data_analyst, etc.


class CollectionBlackboard:
    """Thread-safe findings accumulator.

    Workers write findings tagged with their ID.
    Analyst reads all to judge sufficiency.
    CEO reads all for report generation.
    """

    def __init__(self, max_chars: int = 80000) -> None:
        self._lock = threading.Lock()
        self._entries: list[BlackboardEntry] = []
        self._max_chars = max_chars

    # ── Write ────────────────────────────────

    def write_findings(
        self,
        worker_id: str,
        worker_domain: str,
        findings: list[dict],
    ) -> int:
        """워커의 labeled_findings를 Blackboard에 기록. 추가된 항목 수 반환."""
        entries = []
        for f in findings:
            entries.append(BlackboardEntry(
                content=f.get("content", ""),
                category=f.get("category", ""),
                importance=f.get("importance", 3),
                source=f.get("source", ""),
                worker_id=worker_id,
                worker_domain=worker_domain,
            ))
        with self._lock:
            self._entries.extend(entries)
        count = len(entries)
        logger.info(f"[Blackboard] +{count} findings from {worker_id} ({worker_domain})")
        return count

    # ── Dedup ────────────────────────────────

    def _deduplicated_entries(self) -> list[BlackboardEntry]:
        """entries를 dict로 변환 → dedup → BlackboardEntry로 복원."""
        with self._lock:
            raw = list(self._entries)
        if len(raw) <= 1:
            return raw
        as_dicts = [
            {"content": e.content, "category": e.category,
             "importance": e.importance, "source": e.source,
             "worker_id": e.worker_id, "worker_domain": e.worker_domain}
            for e in raw
        ]
        deduped = deduplicate_findings(as_dicts)
        return [BlackboardEntry(**d) for d in deduped]

    # ── Read (Analyst용) ─────────────────────

    def read_for_analyst(self) -> str:
        """Analyst가 데이터 충분성을 판단하기 위한 전체 뷰.

        importance 순 정렬, 워커별 그룹핑, 카테고리 통계 포함.
        """
        entries = self._deduplicated_entries()

        if not entries:
            return "수집된 데이터 없음."

        # 카테고리별 통계
        cat_counts: dict[str, int] = {}
        worker_counts: dict[str, int] = {}
        for e in entries:
            cat_counts[e.category] = cat_counts.get(e.category, 0) + 1
            worker_counts[e.worker_id] = worker_counts.get(e.worker_id, 0) + 1

        lines = [
            f"## Blackboard 현황: 총 {len(entries)}건\n",
            "### 카테고리별 분포",
        ]
        for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {cat}: {cnt}건")

        lines.append("\n### 워커별 기여")
        for wid, cnt in sorted(worker_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {wid}: {cnt}건")

        # importance 5→1 순으로 정렬하여 핵심 데이터 먼저
        entries.sort(key=lambda x: -x.importance)

        lines.append("\n### 수집 데이터 (중요도순)\n")
        total_chars = 0
        for i, e in enumerate(entries):
            entry_text = (
                f"**[{e.category}|중요도{e.importance}]** {e.content}\n"
                f"  출처: {e.source or '없음'} | 워커: {e.worker_id}\n"
            )
            if total_chars + len(entry_text) > self._max_chars:
                lines.append(f"\n[... {len(entries) - i}건 추가 존재, 용량 제한으로 생략]")
                break
            lines.append(entry_text)
            total_chars += len(entry_text)

        return "\n".join(lines)

    # ── Read (CEO용) ─────────────────────────

    def read_for_ceo(self) -> str:
        """CEO가 최종 보고서를 작성하기 위한 구조화된 데이터 뷰.

        importance 4-5를 '핵심 데이터'로, 나머지를 '보충 데이터'로 분리.
        """
        entries = self._deduplicated_entries()

        if not entries:
            return "수집된 데이터 없음."

        critical = [e for e in entries if e.importance >= 4]
        supporting = [e for e in entries if e.importance < 4]
        critical.sort(key=lambda x: -x.importance)
        supporting.sort(key=lambda x: -x.importance)

        lines = [f"## 수집 데이터 총 {len(entries)}건 (핵심 {len(critical)}건 + 보충 {len(supporting)}건)\n"]

        lines.append("### 🔑 핵심 데이터 (importance 4-5)\n")
        total_chars = 0
        for e in critical:
            entry_text = (
                f"- **[{e.category}]** {e.content}\n"
                f"  출처: {e.source or '없음'}\n"
            )
            total_chars += len(entry_text)
            if total_chars > self._max_chars * 0.7:
                lines.append("[핵심 데이터 용량 제한 도달]")
                break
            lines.append(entry_text)

        lines.append("\n### 📎 보충 데이터 (importance 1-3)\n")
        for e in supporting:
            entry_text = f"- [{e.category}] {e.content} (출처: {e.source or '없음'})\n"
            total_chars += len(entry_text)
            if total_chars > self._max_chars:
                lines.append(f"[... 보충 데이터 {len(supporting)}건 중 일부 생략]")
                break
            lines.append(entry_text)

        return "\n".join(lines)

    # ── Read (요약) ──────────────────────────

    def get_summary(self) -> str:
        """간단한 통계 요약 (UI/로그용)."""
        with self._lock:
            total = len(self._entries)
            if total == 0:
                return "Blackboard: 비어있음"
            workers = set(e.worker_id for e in self._entries)
            critical = sum(1 for e in self._entries if e.importance >= 4)
            return f"Blackboard: {total}건 (핵심 {critical}건, 워커 {len(workers)}명)"

    # ── Query ────────────────────────────────

    def get_gaps_by_category(self) -> dict[str, int]:
        """카테고리별 수집량 — Analyst가 부족한 영역을 판단할 때 사용."""
        with self._lock:
            counts: dict[str, int] = {}
            for e in self._entries:
                counts[e.category] = counts.get(e.category, 0) + 1
            return counts

    def count_by_importance(self) -> dict[int, int]:
        """중요도별 항목 수."""
        with self._lock:
            counts: dict[int, int] = {}
            for e in self._entries:
                counts[e.importance] = counts.get(e.importance, 0) + 1
            return counts

    @property
    def total_entries(self) -> int:
        with self._lock:
            return len(self._entries)

    # ── Serialization ────────────────────────

    def serialize(self) -> dict:
        """LangGraph State에 저장 가능한 직렬화."""
        with self._lock:
            return {
                "entries": [
                    {
                        "content": e.content,
                        "category": e.category,
                        "importance": e.importance,
                        "source": e.source,
                        "worker_id": e.worker_id,
                        "worker_domain": e.worker_domain,
                    }
                    for e in self._entries
                ],
                "max_chars": self._max_chars,
            }

    @classmethod
    def deserialize(cls, data: dict | None) -> CollectionBlackboard:
        """LangGraph State에서 복원."""
        if not data or "entries" not in data:
            return cls()
        bb = cls(max_chars=data.get("max_chars", 80000))
        bb._entries = [
            BlackboardEntry(**entry) for entry in data["entries"]
        ]
        return bb

    def stats(self) -> dict:
        with self._lock:
            workers = {}
            for e in self._entries:
                workers[e.worker_id] = workers.get(e.worker_id, 0) + 1
            return {
                "total_entries": len(self._entries),
                "workers": workers,
                "categories": self.get_gaps_by_category(),
            }
