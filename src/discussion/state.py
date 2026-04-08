"""Discussion graph state definition."""

from __future__ import annotations

from typing import TypedDict

from src.discussion.config import DiscussionConfig

HUMAN_SPEAKER_ID = "__human__"


class Utterance(TypedDict):
    """A single discussion utterance."""

    round: int
    speaker_id: str
    speaker_name: str
    content: str
    timestamp: float


class DiscussionState(TypedDict):
    """LangGraph state for a discussion session."""

    config: DiscussionConfig
    utterances: list[Utterance]
    current_round: int
    phase: str                   # "setup" | "persona_building" | "opening" | "discussing" | "closing" | "report" | "done"
    start_time: float
    cancelled: bool
    time_limit_sec: int
    moderator_instruction: str   # Current instruction from moderator to speaker
    next_speaker_id: str         # Who should speak next
    final_report_html: str       # Generated HTML report
    report_file_path: str        # Saved report file path (empty if not saved)
    session_id: str
    human_input_pending: bool        # human_turn 대기 상태
    participant_sessions: dict[str, str]  # participant_id → Claude CLI session UUID (per-discussion, new each run)
