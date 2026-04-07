"""Deduplication utilities for report findings, summaries, and deliverables.

Uses difflib.SequenceMatcher (stdlib) for similarity comparison.
No external dependencies required.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def _normalize(text: str) -> str:
    """텍스트 정규화: 소문자, 공백 통합, 구두점 제거."""
    text = unicodedata.normalize("NFC", text.strip().lower())
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _similarity(a: str, b: str) -> float:
    """두 문자열의 유사도 (0.0~1.0). 빈 문자열이면 0.

    SequenceMatcher ratio와 단어 겹침률(Jaccard) 중 높은 값을 반환.
    숫자 표기 차이(75,000 vs 7만5천)에도 단어 겹침으로 보완.
    """
    if not a or not b:
        return 0.0
    seq_ratio = SequenceMatcher(None, a, b).ratio()
    # 단어 기반 Jaccard 유사도
    words_a = set(a.split())
    words_b = set(b.split())
    union = words_a | words_b
    jaccard = len(words_a & words_b) / len(union) if union else 0.0
    return max(seq_ratio, jaccard)


def deduplicate_findings(
    findings: list[dict],
    threshold: float = 0.75,
) -> list[dict]:
    """labeled_findings 중복 제거.

    같은 category 내에서 content 유사도가 threshold 이상이면 중복으로 판단.
    importance가 높은 쪽을 보존하고, 같으면 source가 있는 쪽 우선.

    Args:
        findings: [{"content": str, "category": str, "importance": int, "source": str, ...}]
        threshold: 유사도 임계값 (기본 0.75)

    Returns:
        중복 제거된 findings 리스트 (importance 내림차순 정렬 유지)
    """
    if len(findings) <= 1:
        return list(findings)

    # importance 내림차순 정렬 (높은 것부터 처리 → 자연스럽게 보존)
    sorted_findings = sorted(
        findings, key=lambda f: f.get("importance", 1), reverse=True,
    )

    kept: list[dict] = []
    kept_normalized: list[str] = []

    for f in sorted_findings:
        content = f.get("content", "")
        norm = _normalize(content)
        if not norm:
            continue

        is_dup = False
        for existing_norm in kept_normalized:
            if _similarity(norm, existing_norm) >= threshold:
                is_dup = True
                break

        if not is_dup:
            kept.append(f)
            kept_normalized.append(norm)

    return kept


def deduplicate_summaries(summaries: list[str], threshold: float = 0.65) -> list[str]:
    """상세 분석 요약문 중복 제거.

    워커별 result_summary가 거의 동일한 내용을 반복하는 경우 제거.
    더 긴 요약을 우선 보존 (정보량이 많으므로).

    Args:
        summaries: 요약 문자열 리스트
        threshold: 유사도 임계값 (기본 0.65, 요약은 표현이 다양해서 낮게)

    Returns:
        중복 제거된 요약 리스트
    """
    if len(summaries) <= 1:
        return list(summaries)

    # 긴 것부터 처리 (정보량 우선)
    sorted_sums = sorted(summaries, key=len, reverse=True)

    kept: list[str] = []
    kept_normalized: list[str] = []

    for s in sorted_sums:
        norm = _normalize(s)
        if not norm or len(norm) < 10:
            continue

        is_dup = False
        for existing_norm in kept_normalized:
            # 부분문자열 체크: 짧은 요약이 긴 요약에 포함되면 중복
            if norm in existing_norm:
                is_dup = True
                break
            if _similarity(norm, existing_norm) >= threshold:
                is_dup = True
                break

        if not is_dup:
            kept.append(s)
            kept_normalized.append(norm)

    return kept


def deduplicate_deliverables(
    deliverables: list[str],
    threshold: float = 0.80,
) -> list[str]:
    """산출물 목록 중복 제거.

    기존 exact match를 정규화된 유사도 비교로 개선.

    Args:
        deliverables: 산출물 문자열 리스트
        threshold: 유사도 임계값 (기본 0.80, 산출물은 구체적이라 높게)

    Returns:
        중복 제거된 산출물 리스트 (원본 순서 유지)
    """
    if len(deliverables) <= 1:
        return list(deliverables)

    kept: list[str] = []
    kept_normalized: list[str] = []

    for d in deliverables:
        d_str = str(d).strip()
        if not d_str:
            continue
        norm = _normalize(d_str)
        if not norm:
            continue

        is_dup = False
        for existing_norm in kept_normalized:
            if norm == existing_norm:
                is_dup = True
                break
            if _similarity(norm, existing_norm) >= threshold:
                is_dup = True
                break

        if not is_dup:
            kept.append(d_str)
            kept_normalized.append(norm)

    return kept
