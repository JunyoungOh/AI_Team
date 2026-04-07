"""Interrupt mechanism — LangGraph의 interrupt() 대체."""

from __future__ import annotations

from typing import Any


class InterruptRequest(Exception):
    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__("Interrupt requested")


def request_interrupt(state: dict, payload: dict) -> Any:
    if "_resume_value" in state:
        return state["_resume_value"]
    raise InterruptRequest(payload)
