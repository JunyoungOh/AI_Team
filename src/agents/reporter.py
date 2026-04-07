"""Reporter Agent - final report synthesis from validated worker results."""

from __future__ import annotations

import json

from src.agents.base import BaseAgent
from src.config.personas import REPORTER_PERSONA, format_persona_block
from src.config.settings import get_settings
from src.models.messages import CEOFinalReport
from src.prompts.reporter_prompts import FINAL_REPORT_SYSTEM


class ReporterAgent(BaseAgent):
    """Synthesizes validated worker results into a final report.

    Uses Sonnet by default — no strategic reasoning needed,
    just accurate data compilation from gap-analyzed results.
    """

    def __init__(self, agent_id: str, model: str = "sonnet") -> None:
        super().__init__(agent_id, model=model)
        self._settings = get_settings()
        self._persona_block = format_persona_block(REPORTER_PERSONA)

    def invoke(self, state: dict) -> dict:
        raise NotImplementedError("Use compile_final_report()")

    def compile_final_report(self, state: dict) -> dict:
        """Compile gap analyses and results into a final report.

        When consolidated_result is available (multi-leader case), uses it as
        primary source — avoids re-extracting from worker execution_result
        which is what leader_consolidation already did.
        """
        user_task = state["user_task"]
        system = self._format_prompt(
            FINAL_REPORT_SYSTEM,
            user_task=user_task,
            persona_block=self._persona_block,
        )

        report_context = self._summarize_results_from_workers(state.get("workers", []))

        result: CEOFinalReport = self._query(
            system_prompt=system,
            user_content=report_context,
            output_schema=CEOFinalReport,
            allowed_tools=[],
            timeout=self._settings.reporter_timeout,
            max_turns=self._settings.reporter_max_turns,
            effort=self._settings.reporter_effort,
        )
        self.logger.info("final_report_compiled")
        return {
            "final_report": result.model_dump(),
            "phase": "complete",
        }

    def _summarize_results_from_workers(self, workers: list[dict]) -> str:
        """Summarize results from flat workers list (2-tier architecture).

        Reporter receives task descriptions and actual findings as content.
        Agent names, status labels, and process details are excluded to
        prevent the Reporter LLM from echoing them in the final report.
        """
        max_summary = self._settings.max_result_chars_in_context
        max_deliverables = 15
        parts = []

        has_results = False
        for w in workers:
            w_domain = w.get("worker_domain", "unknown")
            raw_result = w.get("execution_result", "")
            task_desc = w.get("specific_instructions", "")

            # Parse execution_result (JSON string or dict)
            result_data = {}
            if isinstance(raw_result, dict):
                result_data = raw_result
            elif isinstance(raw_result, str) and raw_result.strip():
                try:
                    parsed = json.loads(raw_result)
                    if isinstance(parsed, dict):
                        result_data = parsed
                except (json.JSONDecodeError, TypeError):
                    result_data = {"result_summary": raw_result}

            # Extract key fields
            summary = str(result_data.get("result_summary", result_data.get("summary", "")))
            if len(summary) > max_summary:
                summary = summary[:max_summary] + "..."
            deliverables = result_data.get("deliverables", result_data.get("key_deliverables", []))
            if isinstance(deliverables, list):
                deliverables = deliverables[:max_deliverables]

            parts.append(f"## {w_domain}")
            if task_desc:
                parts.append(f"### 작업: {task_desc[:80]}")

            if summary:
                clean = self._strip_agent_references(summary)
                parts.append(f"결과: {clean}")
                has_results = True
            if deliverables:
                parts.append(f"산출물: {', '.join(str(d) for d in deliverables)}")
            file_paths = result_data.get("deliverable_files", [])
            if isinstance(file_paths, list) and file_paths:
                parts.append(f"Files: {', '.join(str(f) for f in file_paths[:5])}")

            parts.append("")  # blank line separator

        if not has_results:
            parts.append("유효한 결과를 확보하지 못함.")

        return "\n".join(parts)

    @staticmethod
    def _strip_agent_references(text: str) -> str:
        """Remove agent/worker self-references from result text.

        Strips patterns like 'deep_researcher는 ...', 'data_analyst가 ...'
        and tool-process descriptions to focus on actual findings.
        """
        import re
        # Remove leading agent name patterns: "agent_name는/가/은/이"
        text = re.sub(
            r'^(deep_researcher|researcher|data_analyst|analyst|reporter|'
            r'developer|designer|planner|strategist|writer)\s*[는가은이]\s*',
            '', text, flags=re.IGNORECASE,
        )
        return text
