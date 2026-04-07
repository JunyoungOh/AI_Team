"""Pydantic models for the Dandelion Foresight pipeline."""
from __future__ import annotations

from pydantic import BaseModel, field_validator

THEME_COLORS = ["#4FC3F7", "#81C784", "#FFB74D", "#CE93D8"]


class Theme(BaseModel):
    id: str
    name: str
    color: str
    description: str

    def to_ws_dict(self) -> dict:
        return self.model_dump()


class ThemeAssignment(BaseModel):
    themes: list[Theme]
    common_context: str
    user_query: str

    @field_validator("themes")
    @classmethod
    def must_have_four(cls, v: list[Theme]) -> list[Theme]:
        if len(v) != 4:
            raise ValueError(f"Exactly 4 themes required, got {len(v)}")
        return v


class Imagination(BaseModel):
    id: str
    theme_id: str
    title: str
    summary: str
    detail: str
    time_point: str
    time_months: int


class Seed(BaseModel):
    id: str
    theme_id: str
    title: str
    summary: str
    detail: str
    time_months: int
    weight: int
    source_count: int

    @field_validator("time_months")
    @classmethod
    def clamp_time(cls, v: int) -> int:
        return max(1, min(v, 60))

    def to_ws_dict(self) -> dict:
        return self.model_dump()


class ThemeResult(BaseModel):
    theme: Theme
    seeds: list[Seed]


class DandelionTree(BaseModel):
    query: str
    themes: list[Theme]
    seeds: list[Seed]
    created_at: str
