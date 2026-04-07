"""Abstract base class for all agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from src.config.settings import get_settings
from src.utils.logging import get_logger
from src.utils.parallel import run_async


class BaseAgent(ABC):
    """Base class providing shared infrastructure for CEO/Leader/Worker agents."""

    def __init__(self, agent_id: str, model: str = "sonnet") -> None:
        self.agent_id = agent_id
        self.model = model
        from src.utils.bridge_factory import get_bridge
        self._bridge = get_bridge()
        self.logger = get_logger(agent_id=agent_id)

    @abstractmethod
    def invoke(self, state: dict) -> dict:
        """Process state and return a partial state update dict."""
        ...

    # ── Sync LLM calls ─────────────────────────────────

    def _query(
        self,
        system_prompt: str,
        user_content: str,
        output_schema: type[BaseModel],
        allowed_tools: list[str] | None = None,
        timeout: int | None = None,
        max_turns: int | None = None,
        model: str | None = None,
        effort: str | None = None,
        time_budget: float | None = None,
        progress_callback: Any | None = None,
    ) -> BaseModel:
        """Sync wrapper — delegates to async :meth:`_aquery` via :func:`run_async`."""
        return run_async(
            self._aquery(system_prompt, user_content, output_schema, allowed_tools, timeout, max_turns, model, effort, time_budget, progress_callback)
        )

    # ── Async LLM calls (for parallel execution) ──────

    async def _aquery(
        self,
        system_prompt: str,
        user_content: str,
        output_schema: type[BaseModel],
        allowed_tools: list[str] | None = None,
        timeout: int | None = None,
        max_turns: int | None = None,
        model: str | None = None,
        effort: str | None = None,
        time_budget: float | None = None,
        progress_callback: Any | None = None,
    ) -> BaseModel:
        """Async structured query via Claude Code."""
        settings = get_settings()
        effective_timeout = timeout or (
            settings.execution_timeout if allowed_tools else settings.llm_call_timeout
        )
        # Default effort: "low" for tool-free calls (CEO/Leader), from settings otherwise
        effective_effort = effort or (
            settings.default_effort if not allowed_tools else settings.worker_effort
        )
        result = await self._bridge.structured_query(
            system_prompt=system_prompt,
            user_message=user_content,
            output_schema=output_schema,
            model=model or self.model,
            allowed_tools=allowed_tools,
            timeout=effective_timeout,
            max_turns=max_turns,
            effort=effective_effort,
            time_budget=time_budget,
            progress_callback=progress_callback,
        )
        self.logger.info("claude_code_query_success", schema=output_schema.__name__)
        return result

    # ── Utilities ──────────────────────────────────────

    # Keys containing user-controlled text that must be escaped
    _USER_CONTROLLED_KEYS = {"user_task", "user_answers", "ceo_feedback", "previous_subtasks"}

    def _format_prompt(self, template: str, **kwargs: Any) -> str:
        # Escape braces only in user-controlled values to prevent format string injection
        safe_kwargs = {}
        for k, v in kwargs.items():
            if isinstance(v, str) and k in self._USER_CONTROLLED_KEYS:
                safe_kwargs[k] = v.replace("{", "{{").replace("}", "}}")
            else:
                safe_kwargs[k] = v
        return template.format(**safe_kwargs)
