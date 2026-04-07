"""Pydantic validation models for domain plugin YAML files."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class WorkerConfig(BaseModel):
    """Configuration for a single worker type within a domain plugin."""

    name: str
    description: str = ""
    model: str = "sonnet"
    text_mode: bool = False
    tools: list[str] = []
    tool_category: str | None = None
    persona: dict[str, str] | None = None

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        if v not in ("haiku", "sonnet", "opus"):
            raise ValueError(f"Invalid model '{v}'. Must be haiku, sonnet, or opus.")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Worker name '{v}' must be alphanumeric (with underscores/hyphens).")
        return v


class DomainConfig(BaseModel):
    """Configuration for a domain plugin loaded from YAML."""

    domain: str
    description: str = ""
    workers: list[WorkerConfig]
    approval_threshold: int = 7

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"Domain name '{v}' must be alphanumeric (with underscores/hyphens).")
        return v

    @field_validator("workers")
    @classmethod
    def validate_workers_not_empty(cls, v: list[WorkerConfig]) -> list[WorkerConfig]:
        if not v:
            raise ValueError("Domain must have at least one worker.")
        return v
