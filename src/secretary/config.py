"""Secretary configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SecretaryConfig(BaseModel):
    """Secretary mode settings."""

    model: str = "sonnet"
    max_history_turns: int = Field(default=20, ge=4, le=100)
    response_timeout: int = Field(default=60, ge=10, le=300)
    compress_threshold: int = Field(default=24, ge=10, le=200)
