"""Node: select_research_mode — 유저가 Breadth/Depth 리서치 모드를 선택."""

from src.utils.guards import node_error_handler
from src.utils.logging import get_logger

_logger = get_logger(agent_id="select_research_mode")


@node_error_handler("select_research_mode")
def select_research_mode_node(state: dict) -> dict:
    """Deep Research 모드 자동 선택.

    CEO가 이미 sub_queries를 생성했으므로 쿼리 수 기반으로 자동 결정:
    - 3개 이상 서브쿼리 → breadth (넓은 범위 검색)
    - 그 외 → depth (깊은 분석)
    Scheduled 모드는 pre_context에서 strategy를 가져옴.
    """
    if state.get("execution_mode") == "scheduled":
        pre_context = state.get("pre_context", {})
        strategy = pre_context.get("research_strategy", "depth")
        _logger.info("scheduled_research_mode", strategy=strategy)
    else:
        sub_queries = state.get("deep_research_sub_queries", [])
        strategy = "breadth" if len(sub_queries) >= 3 else "depth"
        _logger.info("auto_selected_research_mode", strategy=strategy, sub_query_count=len(sub_queries))

    if strategy not in ("breadth", "depth"):
        _logger.warning("invalid_strategy_fallback", received=strategy)
        strategy = "depth"

    return {
        "deep_research_strategy": strategy,
    }
