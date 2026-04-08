"""Setup node — validates config and initializes state."""

from __future__ import annotations

import time
import uuid

from src.discussion.state import HUMAN_SPEAKER_ID, DiscussionState


def discussion_setup(state: DiscussionState) -> dict:
    """Initialize discussion state from config.

    Generates one Claude CLI session UUID per AI participant. The first call
    in opening_speak creates the session via ``--session-id``; subsequent
    speak calls reuse it via ``--resume`` so persona + topic stay cached and
    each participant remembers their own prior turns.
    """
    config = state["config"]
    participant_sessions = {
        p.id: str(uuid.uuid4())
        for p in config.participants
        if p.id != HUMAN_SPEAKER_ID
    }
    return {
        "phase": "opening",
        "current_round": 0,
        "start_time": time.time(),
        "time_limit_sec": config.time_limit_min * 60,
        "cancelled": False,
        "utterances": [],
        "final_report_html": "",
        "moderator_instruction": "",
        "next_speaker_id": "",
        "participant_sessions": participant_sessions,
    }
