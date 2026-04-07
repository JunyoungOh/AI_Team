"""State merging — LangGraph의 add_messages reducer 대체."""

from __future__ import annotations

from typing import Any

_APPEND_FIELDS: set[str] = {"messages", "utterances"}


def register_append_field(name: str) -> None:
    _APPEND_FIELDS.add(name)


def merge_state(base: dict, update: dict) -> dict:
    merged = {**base}
    for key, value in update.items():
        if key in _APPEND_FIELDS and key in base and isinstance(value, list) and isinstance(base[key], list):
            merged[key] = base[key] + value
        else:
            merged[key] = value
    return merged
