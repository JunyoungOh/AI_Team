"""Intent classifier — rule-based first pass, LLM fallback later."""

from __future__ import annotations

import re

# Intent types
CHAT = "chat"
INJECT_COMPANY = "inject_company"
INJECT_DISCUSSION = "inject_discussion"
CALENDAR = "calendar"
REPORT = "report"

_PATTERNS: dict[str, list[str]] = {
    INJECT_COMPANY: [
        "AI Company", "ai company", "회사 모드",
    ],
    INJECT_DISCUSSION: [
        "AI Discussion", "ai discussion", "토론 모드",
    ],
    CALENDAR: [
        "일정 잡아", "미팅 잡아", "스케줄 등록", "일정 등록",
        "일정 보여", "이번 주 일정", "내일 일정", "오늘 일정", "다음 주 일정",
        "일정 옮겨", "일정 변경", "일정 수정", "일정 취소", "일정 삭제",
        "캘린더", "calendar", "스케줄",
    ],
    REPORT: [
        "리포트", "보고서", "정리해줘", "요약해줘", "요약 좀",
        "report", "리포트로", "보고서로",
    ],
}


def classify_intent(message: str) -> str:
    """Classify user message intent via keyword matching.

    Returns one of: "chat", "inject_company", "inject_discussion",
    "calendar", or "report".
    """
    text = message.strip()
    text_lower = text.lower()

    # Existing patterns
    for intent, keywords in _PATTERNS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return intent
    return CHAT
