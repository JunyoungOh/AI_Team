"""Date parsing utilities for deep research date constraint injection.

Extracts date ranges from Korean natural-language task descriptions and
builds prompt sections that enforce temporal boundaries in research output.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# ── Date range extraction patterns ──────────────────

# YYYY.MM.DD~YYYY.MM.DD  or  YYYY-MM-DD~YYYY-MM-DD
_FULL_RANGE_RE = re.compile(
    r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*[~\-–—]\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
)

# MM.DD~MM.DD (same year implied) or M월D일~M월D일
_SHORT_RANGE_RE = re.compile(
    r"(\d{1,2})[.\-/](\d{1,2})\s*[~\-–—]\s*(\d{1,2})[.\-/](\d{1,2})"
)

# YYYY년 M월 D일~M월 D일  (Korean style)
_KR_RANGE_RE = re.compile(
    r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일?\s*[~\-–—]\s*(\d{1,2})월\s*(\d{1,2})일?"
)

# YYYY년 M월 D일~D일 (same month)
_KR_SAME_MONTH_RE = re.compile(
    r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일?\s*[~\-–—]\s*(\d{1,2})일?"
)

# M월 N째주 / N월 N주차 patterns (week-based)
_WEEK_RE = re.compile(
    r"(\d{1,2})월\s*(\d)\s*(?:째주|주차|째\s*주)"
)

# YYYY.MM.DD~MM.DD (cross-month with year prefix)
_YEAR_PREFIX_RANGE_RE = re.compile(
    r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s*[~\-–—]\s*(\d{1,2})[.\-/](\d{1,2})"
)


def _safe_date(year: int, month: int, day: int) -> str | None:
    """Return ISO date string or None if invalid."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_date_range(text: str, today: date | None = None) -> tuple[str, str] | None:
    """Extract a date range from user task text.

    Recognizes patterns:
    - YYYY.MM.DD~YYYY.MM.DD
    - MM.DD~MM.DD (infers year from context)
    - YYYY년 M월 D일~M월 D일
    - YYYY년 M월 D일~D일
    - M월 N째주 (converts to Mon~Sun range)
    - YYYY.MM.DD~MM.DD

    Returns ("YYYY-MM-DD", "YYYY-MM-DD") or None.
    """
    today = today or date.today()

    # 1. Full range: YYYY.MM.DD~YYYY.MM.DD
    m = _FULL_RANGE_RE.search(text)
    if m:
        start = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        end = _safe_date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        if start and end:
            return (start, end)

    # 2. Year-prefix range: YYYY.MM.DD~MM.DD
    m = _YEAR_PREFIX_RANGE_RE.search(text)
    if m:
        year = int(m.group(1))
        start = _safe_date(year, int(m.group(2)), int(m.group(3)))
        end = _safe_date(year, int(m.group(4)), int(m.group(5)))
        if start and end:
            return (start, end)

    # 3. Korean range: YYYY년 M월 D일~M월 D일
    m = _KR_RANGE_RE.search(text)
    if m:
        year = int(m.group(1))
        start = _safe_date(year, int(m.group(2)), int(m.group(3)))
        end = _safe_date(year, int(m.group(4)), int(m.group(5)))
        if start and end:
            return (start, end)

    # 4. Korean same-month: YYYY년 M월 D일~D일
    m = _KR_SAME_MONTH_RE.search(text)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        start = _safe_date(year, month, int(m.group(3)))
        end = _safe_date(year, month, int(m.group(4)))
        if start and end:
            return (start, end)

    # 5. Short range: MM.DD~MM.DD (infer year)
    m = _SHORT_RANGE_RE.search(text)
    if m:
        # Check this isn't part of a YYYY.MM.DD pattern already matched
        prefix = text[:m.start()]
        if not re.search(r"\d{4}[.\-/]$", prefix):
            year = today.year
            start_month, start_day = int(m.group(1)), int(m.group(2))
            end_month, end_day = int(m.group(3)), int(m.group(4))
            # Handle year boundary (e.g., 12.28~01.03)
            start_year = year
            end_year = year
            if end_month < start_month:
                end_year = year + 1
            start = _safe_date(start_year, start_month, start_day)
            end = _safe_date(end_year, end_month, end_day)
            if start and end:
                return (start, end)

    # 6. Week pattern: N월 N째주
    m = _WEEK_RE.search(text)
    if m:
        month = int(m.group(1))
        week_num = int(m.group(2))
        # Infer year from task or use today's year
        year_match = re.search(r"(\d{4})년", text)
        year = int(year_match.group(1)) if year_match else today.year
        # Find the first day of the month, then calculate week start
        try:
            first_of_month = date(year, month, 1)
            # Week 1 starts on the first Monday on or before day 7
            # weekday(): Mon=0, Sun=6
            days_to_monday = (first_of_month.weekday()) % 7
            first_monday = first_of_month.toordinal() - days_to_monday
            week_start = date.fromordinal(first_monday + (week_num - 1) * 7)
            week_end = date.fromordinal(week_start.toordinal() + 6)
            return (week_start.isoformat(), week_end.isoformat())
        except (ValueError, OverflowError):
            pass

    return None


# ── News type detection ─────────────────────────────

_NEWS_KEYWORDS = (
    "뉴스", "동향", "트렌드", "소식", "이슈", "주간", "월간",
    "모니터링", "브리핑", "시황", "리뷰",
)


def is_news_type_task(user_task: str) -> bool:
    """Detect if the task is a news/trend monitoring request.

    Conservative criteria to avoid false positives:
    - (date range present + 1 news keyword) OR
    - (2+ news keywords)
    """
    lower = user_task.lower()
    keyword_count = sum(1 for kw in _NEWS_KEYWORDS if kw in lower)

    if keyword_count >= 2:
        return True
    if keyword_count >= 1 and extract_date_range(user_task) is not None:
        return True
    return False


# ── Prompt constraint builder ───────────────────────


def build_date_constraint(user_task: str, today: date | None = None) -> str:
    """Build a date constraint section for the deep research prompt.

    If a date range is found: strict temporal boundary with search guidance.
    If not: today's date + recent-year preference.
    """
    today = today or date.today()
    date_range = extract_date_range(user_task, today)

    if date_range:
        start, end = date_range
        return (
            f"## ⚠️ 날짜 범위 제한 (필수 준수)\n"
            f"- **조사 기간**: {start} ~ {end}\n"
            f"- 이 기간 내의 정보만 포함하세요. 기간 밖의 정보는 제외합니다.\n"
            f"- 검색어에 날짜/기간을 포함하세요 (예: \"2026년 2월\", \"February 2026\").\n"
            f"- 각 정보 항목에 출처 날짜를 명시하세요.\n"
            f"- 기간 외 정보가 필수 배경인 경우, \"[배경]\" 태그를 붙여 명확히 구분하세요."
        )
    else:
        today_str = today.isoformat()
        year = today.year
        return (
            f"## 날짜 기준\n"
            f"- 오늘 날짜: {today_str}\n"
            f"- 최신 데이터 우선 ({year - 1}-{year}년)\n"
            f"- 각 정보 항목에 출처 날짜를 명시하세요."
        )
