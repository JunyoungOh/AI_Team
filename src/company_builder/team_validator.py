"""Validate task-team fit before execution.

Two-layer validation:
1. Fast heuristic (keyword matching) — instant, no API cost
2. AI judgment (Claude haiku) — when heuristic is inconclusive
"""

from __future__ import annotations

from src.utils.logging import get_logger

_logger = get_logger(agent_id="team_validator")

# Category-task mapping heuristics
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "research": ["분석", "조사", "리서치", "동향", "시장", "트렌드", "보고서", "research", "analysis", "요약", "현황"],
    "data": ["데이터", "통계", "시각화", "대시보드", "data", "analytics"],
    "finance": ["재무", "투자", "주가", "환율", "매출", "finance", "financial", "펀드"],
    "development": ["개발", "코드", "API", "구현", "dev", "code", "프로그래밍"],
    "security": ["보안", "취약점", "CVE", "security", "해킹"],
    "legal": ["법률", "규제", "컴플라이언스", "legal", "특허"],
    "hr": ["인사", "채용", "조직", "hr", "hiring", "인재"],
}


def _heuristic_check(task: str, agents: list[dict]) -> str:
    """Returns 'fit', 'no_fit', or 'uncertain'."""
    categories = {a.get("tool_category", "") for a in agents}
    task_lower = task.lower()

    task_categories: set[str] = set()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in task_lower for kw in keywords):
            task_categories.add(cat)

    if not task_categories:
        return "uncertain"
    if task_categories & categories:
        return "fit"
    return "no_fit"


async def _ai_judge(task: str, agents: list[dict]) -> dict:
    """Use Claude haiku for fast AI judgment on task-team fit."""
    team_desc = "\n".join(
        f"- {a.get('name', '?')}: {a.get('role', '?')} (도구: {a.get('tool_category', '?')})"
        for a in agents
    )

    prompt = f"""팀 구성:
{team_desc}

업무: {task}

이 팀이 위 업무를 수행하기에 적합한가요?
- 적합하면 "FIT" 한 단어만 답하세요.
- 적합하지 않으면 "NO_FIT: [이유 한 문장]" 형식으로 답하세요."""

    try:
        from src.utils.bridge_factory import get_bridge
        bridge = get_bridge()

        response = await bridge.raw_query(
            system_prompt="당신은 팀-업무 적합성 판단 전문가입니다. 간결하게 답하세요.",
            user_message=prompt,
            model="haiku",
            allowed_tools=[],
            max_turns=1,
            timeout=15,
            effort="low",
        )
        response = response.strip()
        if response.startswith("FIT"):
            return {"fit": True, "reason": ""}
        elif response.startswith("NO_FIT"):
            reason = response.replace("NO_FIT:", "").strip()
            return {"fit": False, "reason": reason}
        # Ambiguous response — default to fit
        return {"fit": True, "reason": ""}
    except Exception as e:
        _logger.warning("ai_judge_failed", error=str(e))
        # AI unavailable — fall back to allowing execution
        return {"fit": True, "reason": ""}


async def validate_task_team_fit_async(
    task: str,
    agents: list[dict],
    saved_teams: list[dict] | None = None,
) -> dict:
    """Two-layer validation: heuristic first, AI if uncertain.

    Returns dict with fit, reason, suggestion, matching_team_id.
    """
    if not agents:
        return {
            "fit": False,
            "reason": "팀에 에이전트가 없습니다.",
            "suggestion": "새 팀을 만들어보세요.",
            "matching_team_id": "",
        }

    # Layer 1: fast heuristic
    heuristic = _heuristic_check(task, agents)
    if heuristic == "fit":
        return {"fit": True, "reason": "", "suggestion": "", "matching_team_id": ""}

    if heuristic == "uncertain":
        # Layer 2: AI judgment
        ai_result = await _ai_judge(task, agents)
        if ai_result["fit"]:
            return {"fit": True, "reason": "", "suggestion": "", "matching_team_id": ""}
        # AI says no fit — continue to suggestion logic below
        reason = ai_result.get("reason", "")
    else:
        reason = ""

    # No fit — check saved teams for better match
    matching_team_id = ""
    matching_team_name = ""
    if saved_teams:
        for team in saved_teams:
            team_cats = {a.get("tool_category", "") for a in team.get("agents", [])}
            task_lower = task.lower()
            task_cats: set[str] = set()
            for cat, keywords in _CATEGORY_KEYWORDS.items():
                if any(kw in task_lower for kw in keywords):
                    task_cats.add(cat)
            if task_cats & team_cats:
                matching_team_id = team.get("id", "")
                matching_team_name = team.get("name", "")
                break

    suggestion = reason if reason else "이 업무는 현재 팀의 전문 분야와 맞지 않습니다."
    if matching_team_id:
        suggestion += f" '{matching_team_name}' 팀이 더 적합할 수 있습니다."
    else:
        suggestion += " 새로운 팀을 만들어보시는 건 어떨까요?"

    return {
        "fit": False,
        "reason": reason,
        "suggestion": suggestion,
        "matching_team_id": matching_team_id,
    }
