"""Node 1: intake - receive user task and move to CEO routing."""

from langchain_core.messages import HumanMessage

from src.utils.guards import node_error_handler


@node_error_handler("intake")
def intake_node(state: dict) -> dict:
    """Capture user task and transition to CEO routing phase."""
    return {
        "messages": [HumanMessage(content=state["user_task"])],
        "phase": "ceo_routing",
    }
