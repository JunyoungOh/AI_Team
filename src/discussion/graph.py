"""Discussion pipeline — multi-agent debate graph.

Flow:
  setup → [persona_build?] → opening_prep → opening_speak
  → [moderator_turn ↔ agent_speak / human_turn] loop → report

agent_speak gives every participant access to WebSearch + WebFetch
(Claude Code built-in tools, no MCP cold start) so each speaker decides
when to fact-check on their own — no moderator gatekeeping.
"""

from __future__ import annotations

from src.engine import PipelineEngine

from src.discussion.nodes.human_turn import human_turn
from src.discussion.nodes.moderator import moderator_turn
from src.discussion.nodes.opening import discussion_opening_prep, discussion_opening_speak
from src.discussion.nodes.persona_build import persona_build
from src.discussion.nodes.report import discussion_report
from src.discussion.nodes.setup import discussion_setup
from src.discussion.nodes.speak import agent_speak
from src.discussion.state import HUMAN_SPEAKER_ID, DiscussionState


def route_after_moderator(state: DiscussionState) -> str:
    """Route: 3-way branch after moderator decision."""
    if state.get("phase") == "closing" or state.get("cancelled"):
        return "report"
    if state.get("next_speaker_id") == HUMAN_SPEAKER_ID:
        return "human_turn"
    return "agent_speak"


def _route_after_speak(state: DiscussionState) -> str:
    """Route: always go back to moderator for next turn decision."""
    return "moderator_turn"


def _route_after_opening(state: DiscussionState) -> str:
    """Route: if human opening is pending, go to human_turn; else moderator."""
    if state.get("next_speaker_id") == HUMAN_SPEAKER_ID:
        return "human_turn"
    return "moderator_turn"


def _route_after_setup(state: DiscussionState) -> str:
    """Route: if any participant has clone_config (and no persona_id), build personas first."""
    config = state["config"]
    # persona_id means pre-built persona — skip clone. Only clone if clone_config without persona_id.
    if any(p.clone_config and not p.persona_id for p in config.participants):
        return "persona_build"
    return "opening_prep"


def _build_discussion_engine() -> PipelineEngine:
    """Build the discussion PipelineEngine (uncompiled)."""
    engine = PipelineEngine()

    engine.add_node("setup", discussion_setup)
    engine.add_node("persona_build", persona_build)
    engine.add_node("opening_prep", discussion_opening_prep)
    engine.add_node("opening_speak", discussion_opening_speak)
    engine.add_node("moderator_turn", moderator_turn)
    engine.add_node("agent_speak", agent_speak)
    engine.add_node("human_turn", human_turn)
    engine.add_node("report", discussion_report)

    engine.set_entry("setup")
    engine.set_router("setup", _route_after_setup)
    engine.set_router("persona_build", lambda s: "opening_prep")
    engine.set_router("opening_prep", lambda s: "opening_speak")
    engine.set_router("opening_speak", _route_after_opening)
    engine.set_router("moderator_turn", route_after_moderator)
    engine.set_router("agent_speak", _route_after_speak)
    engine.set_router("human_turn", _route_after_speak)
    engine.set_router("report", lambda s: "__end__")

    return engine


def build_discussion_pipeline(checkpointer=None):
    """Compile the discussion pipeline with optional checkpointer."""
    engine = _build_discussion_engine()
    return engine.compile(checkpointer=checkpointer)
