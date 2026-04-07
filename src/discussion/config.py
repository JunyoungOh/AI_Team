"""Discussion configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CloneConfig(BaseModel):
    """Configuration for persona cloning."""
    web_search: bool = True           # 웹검색 자동 수집
    files: list[str] = []             # 업로드된 파일 경로


class Participant(BaseModel):
    """A discussion participant with persona."""

    id: str                          # "agent_a", "agent_b"...
    name: str                        # Display name
    persona: str                     # Detailed persona description
    color: str = "#888888"           # UI color
    emoji: str = "💬"                # UI emoji
    clone_config: CloneConfig | None = None  # None = 기존 직접 작성 방식
    persona_id: str | None = None  # 저장된 페르소나 참조


class HumanParticipant(BaseModel):
    """Real user participating in the discussion."""
    name: str
    persona: str = ""   # 선택사항 — 빈칸이면 자유 발언


class DiscussionConfig(BaseModel):
    """User-provided discussion settings."""

    topic: str
    participants: list[Participant] = Field(min_length=2, max_length=6)
    style: str = "free"              # "free" | "debate" | "brainstorm"
    time_limit_min: int = Field(default=15, ge=3, le=60)
    model_moderator: str = "sonnet"
    model_participant: str = "sonnet"
    human_participant: HumanParticipant | None = None
