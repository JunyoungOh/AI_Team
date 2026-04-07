"""Setup node — validates config and initializes state."""

from __future__ import annotations

import time

from src.discussion.state import DiscussionState


def discussion_setup(state: DiscussionState) -> dict:
    """Initialize discussion state from config."""
    config = state["config"]
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
    }
