"""Company task preparation — CEO-level routing and clarification for Secretary.

Reuses CEO agent's domain routing and question generation logic so that
Secretary can gather user clarification before injecting into Company graph.

Flow:
  1. route_task() → domains, complexity, rationale
  2. generate_questions() → per-domain clarifying questions
  3. build_pre_context() → pre_context dict for scheduled execution
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.agents.ceo import CEOAgent
from src.config.agent_registry import get_leader_domains, get_domain_description
from src.config.settings import get_settings
from src.models.messages import CEORoutingDecision, CEOGeneratedQuestions

logger = logging.getLogger(__name__)


class CompanyPrep:
    """Prepares a Company task by running CEO routing + question generation."""

    def __init__(self):
        self._ceo = CEOAgent(agent_id="sec_ceo_prep", model=get_settings().ceo_model)

    async def route_task(self, task: str) -> CEORoutingDecision:
        """Run CEO domain routing on the task description.

        Returns routing decision with selected domains and complexity.
        Runs in executor to avoid blocking the event loop (sync _query).
        """
        state = {"user_task": task, "pre_context": {}}
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._ceo.get_routing_decision, state
        )
        logger.info(
            "company_prep_routed",
            domains=result.selected_domains,
            complexity=result.estimated_complexity,
            selected_mode=result.selected_mode,
        )
        return result

    async def generate_questions(
        self, task: str, selected_domains: list[str]
    ) -> CEOGeneratedQuestions:
        """Generate clarifying questions for the selected domains.

        Uses CEO's question generation prompt — same quality as full pipeline.
        """
        state = {"user_task": task, "pre_context": {}}
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._ceo.generate_all_questions, state, selected_domains
        )
        logger.info(
            "company_prep_questions",
            total=result.total_questions,
            domains=[q.domain for q in result.questions_by_domain],
        )
        return result

    @staticmethod
    def build_pre_context(
        domain_answers: dict[str, list[str]],
        routing_rationale: str = "",
        estimated_complexity: str = "medium",
        selected_domains: list[str] | None = None,
        selected_mode: str = "hierarchical",
    ) -> dict[str, Any]:
        """Build pre_context dict for scheduled Company execution.

        Includes routing results so ceo_route_node can skip re-routing.

        Args:
            domain_answers: {domain: [answer1, answer2, ...]} from user.
            routing_rationale: CEO's routing rationale string.
            estimated_complexity: "low" | "medium" | "high" from CEO routing.
            selected_domains: Domains selected by CEO routing.
            selected_mode: Execution mode selected by CEO.
        """
        return {
            "domain_answers": domain_answers,
            "background": routing_rationale,
            "default_answer": "사용자가 답변하지 않은 항목은 최선의 판단으로 진행하세요.",
            "selected_domains": selected_domains or list(domain_answers.keys()),
            "estimated_complexity": estimated_complexity,
            "selected_mode": selected_mode,
        }
