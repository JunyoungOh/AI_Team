"""Shared plan formatting utilities.

Extracted to break circular dependency between ceo.py and worker.py.
"""

from __future__ import annotations

import json


def format_plan_for_execution(plan_json: str) -> str:
    """Convert a JSON plan string into readable markdown for LLM consumption.

    Supports both Subtask format (objective + success_criteria) and
    legacy WorkerPlan format (steps + expected_output).
    """
    try:
        data = json.loads(plan_json)
    except (json.JSONDecodeError, TypeError):
        return plan_json  # Return as-is if not valid JSON

    lines = []

    # Subtask format (task decomposition)
    if "objective" in data:
        lines.append(f"# {data.get('task_title', 'Task')}")
        lines.append(f"\n## 목표\n{data['objective']}")
        if data.get("success_criteria"):
            lines.append("\n## 성공 기준")
            for c in data["success_criteria"]:
                lines.append(f"- {c}")
        if data.get("dependencies"):
            lines.append(f"\n## 의존성\n" + ", ".join(data["dependencies"]))
        return "\n".join(lines)

    # Legacy WorkerPlan format
    if data.get("plan_title"):
        lines.append(f"# {data['plan_title']}")
    if data.get("steps"):
        lines.append("\n## 실행 단계")
        for step in data["steps"]:
            lines.append(f"- {step}")
    if data.get("expected_output"):
        lines.append(f"\n## 예상 산출물\n{data['expected_output']}")
    if data.get("dependencies"):
        lines.append(f"\n## 의존성\n" + ", ".join(data["dependencies"]))
    return "\n".join(lines) if lines else plan_json
