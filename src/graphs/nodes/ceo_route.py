"""Node 2: ceo_route - CEO analyzes task and selects execution mode.

No longer assembles leaders — CEO directly routes to questions or task decomposition.
"""

from langchain_core.messages import AIMessage

from src.agents.factory import create_ceo
from src.config.settings import get_settings
from src.utils.context import slice_for_ceo
from src.utils.guards import node_error_handler
from src.utils.logging import get_logger

_logger = get_logger(agent_id="ceo_route")


@node_error_handler("ceo_route")
def ceo_route_node(state: dict) -> dict:
    """CEO analyzes task and selects domains for team composition.

    Always uses team-based execution (like builder mode).
    CEO designs an ephemeral team that executes the task, then dissolves.
    """
    pre_context = state.get("pre_context") or {}

    # ── Team mode: skip CEO routing, use saved team domains ──
    if pre_context.get("team_agents"):
        pre_domains = pre_context.get("selected_domains", ["research"])
        team_name = pre_context.get("team_name", "")
        _logger.info("ceo_route_team_mode", team=team_name, domains=pre_domains)
        return {
            "selected_domains": pre_domains,
            "ceo_routing_rationale": f"사용자 정의 팀 '{team_name}' 구조를 그대로 사용합니다.",
            "estimated_complexity": "medium",
            "report_type": "general",
            "messages": [
                AIMessage(content=f"[CEO] 팀 '{team_name}' 로드 완료 — 도메인: {pre_domains}")
            ],
            "phase": "ceo_questions",
        }

    # ── Scheduled mode with pre-context ──
    if pre_context and state.get("execution_mode") == "scheduled":
        pre_domains = pre_context.get("selected_domains", ["research"])
        _logger.info("ceo_route_pre_routed", domains=pre_domains)
        return {
            "selected_domains": pre_domains,
            "ceo_routing_rationale": pre_context.get("background", ""),
            "estimated_complexity": pre_context.get("estimated_complexity", "medium"),
            "report_type": pre_context.get("report_type", "general"),
            "messages": [
                AIMessage(content=f"[CEO] Pre-routed (scheduled): domains={pre_domains}")
            ],
            "phase": "ceo_questions",
        }

    ceo = create_ceo()
    context = slice_for_ceo(state)
    decision = ceo.get_routing_decision(context)

    selected = decision.selected_domains or ["research"]

    _logger.info("team_composition", complexity=decision.estimated_complexity, domains=selected)
    return {
        "selected_domains": selected,
        "ceo_routing_rationale": decision.rationale,
        "estimated_complexity": decision.estimated_complexity,
        "report_type": decision.report_type,
        "messages": [
            AIMessage(
                content=f"[CEO] 팀 구성 — 도메인: {selected}\nRationale: {decision.rationale}"
            )
        ],
        "phase": "ceo_questions",
    }
