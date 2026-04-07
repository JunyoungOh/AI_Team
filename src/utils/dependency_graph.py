"""Dependency graph utilities for staged worker execution.

Pure functions — no side effects, independently testable.
Implements Kahn's algorithm for topological sorting of workers by dependencies.
"""

from __future__ import annotations

import json
from collections import deque

from src.utils.logging import get_logger

logger = get_logger(agent_id="dependency_graph")


class CircularDependencyError(Exception):
    """Raised when workers have circular dependencies."""

    def __init__(self, remaining: list[str]):
        self.remaining = remaining
        super().__init__(f"Circular dependency among: {remaining}")


def has_any_dependencies(workers: list[dict]) -> bool:
    """Quick check: does any worker declare a non-empty dependencies list?

    Returns False for empty worker lists or when no worker has dependencies,
    signaling the caller to use flat parallel execution.
    """
    for w in workers:
        deps = _get_dependencies(w)
        if deps:
            return True
    return False


def build_execution_stages(workers: list[dict]) -> list[list[int]]:
    """Sort workers into execution stages using Kahn's algorithm (topological sort).

    Args:
        workers: List of worker dicts. Each may contain a ``dependencies``
                 field (list of task_title strings) in its plan JSON.

    Returns:
        List of stages, where each stage is a list of worker indices.
        Workers within a stage have no inter-dependencies and can run in parallel.
        ``[[0], [1, 2], [3]]`` means: run worker 0 first, then 1 & 2, then 3.

    Raises:
        CircularDependencyError: If a dependency cycle is detected.

    Edge cases:
        - Empty workers list → ``[]``
        - Missing dependency references → warning log, dependency ignored
        - Duplicate task_titles → warning log, last occurrence wins
    """
    if not workers:
        return []

    n = len(workers)

    # Build title → index mapping
    title_to_idx: dict[str, int] = {}
    for i, w in enumerate(workers):
        title = _get_task_title(w)
        if title in title_to_idx:
            logger.warning("duplicate_task_title", title=title, prev_idx=title_to_idx[title], new_idx=i)
        title_to_idx[title] = i

    # Build adjacency list and in-degree counts
    # Edge: dependency → dependent (if A depends on B, edge is B→A)
    adj: list[list[int]] = [[] for _ in range(n)]
    in_degree = [0] * n

    for i, w in enumerate(workers):
        deps = _get_dependencies(w)
        for dep_title in deps:
            dep_idx = title_to_idx.get(dep_title)
            if dep_idx is None:
                logger.warning("missing_dependency_ref", worker_idx=i, missing_title=dep_title)
                continue
            adj[dep_idx].append(i)
            in_degree[i] += 1

    # Kahn's algorithm
    queue: deque[int] = deque()
    for i in range(n):
        if in_degree[i] == 0:
            queue.append(i)

    stages: list[list[int]] = []
    visited = 0

    while queue:
        # All nodes currently in the queue form one stage
        stage = list(queue)
        stages.append(stage)
        visited += len(stage)
        queue.clear()

        for idx in stage:
            for neighbor in adj[idx]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

    if visited < n:
        remaining = [_get_task_title(workers[i]) for i in range(n) if in_degree[i] > 0]
        raise CircularDependencyError(remaining)

    return stages


def build_predecessor_context(
    workers: list[dict],
    completed_results: dict[int, str],
    current_idx: int,
) -> str:
    """Format completed predecessor results for injection into a worker's prompt.

    Only includes results from *direct* dependencies of the current worker,
    not all completed workers. This keeps the context focused and avoids noise.

    Args:
        workers: Full worker list.
        completed_results: Mapping of worker index → JSON result string.
        current_idx: Index of the worker about to execute.

    Returns:
        Formatted predecessor context string, or empty string if no
        direct dependencies have completed results.
    """
    deps = _get_dependencies(workers[current_idx])
    if not deps:
        return ""

    # Build title → index for lookups
    title_to_idx: dict[str, int] = {}
    for i, w in enumerate(workers):
        title = _get_task_title(w)
        title_to_idx[title] = i

    _MAX_FILE_CHARS_FOR_PREDECESSOR = 50000

    blocks: list[str] = []
    for dep_title in deps:
        dep_idx = title_to_idx.get(dep_title)
        if dep_idx is None:
            continue
        result_json = completed_results.get(dep_idx)
        if not result_json:
            continue

        # Try reading full result from file (lossless)
        dep_worker = workers[dep_idx]
        result_file = dep_worker.get("result_file_path", "")
        if result_file:
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    result_json = f.read(_MAX_FILE_CHARS_FOR_PREDECESSOR)
            except (OSError, IOError):
                pass  # Fall back to in-memory result_json

        # Parse result for readable formatting
        dep_domain = dep_worker.get("worker_domain", dep_title)
        dep_name = dep_worker.get("worker_name", "") or dep_domain
        summary, deliverables, completion_pct, issues = _extract_result_summary(result_json)
        if not summary:
            continue

        block = f"### [{dep_name}] {dep_title}\n"
        block += f"**결과 요약**: {summary}\n"
        if completion_pct is not None:
            block += f"**완료율**: {completion_pct}%\n"
        if deliverables:
            block += "**산출물**:\n"
            for d in deliverables[:5]:  # Cap at 5 deliverables
                block += f"- {d}\n"
        if issues:
            block += "**미완료/이슈**:\n"
            for iss in issues[:3]:  # Cap at 3 issues
                block += f"- {iss}\n"
        blocks.append(block)

    if not blocks:
        return ""

    return "\n".join(blocks)


# ── Internal helpers ────────────────────────────────


def _get_task_title(worker: dict) -> str:
    """Extract task_title from a worker dict, checking plan JSON if needed."""
    # Direct field (set during task decomposition)
    title = worker.get("task_title", "")
    if title:
        return title

    # Fallback: parse from plan JSON
    plan_str = worker.get("plan", "")
    if plan_str:
        try:
            plan_data = json.loads(plan_str) if isinstance(plan_str, str) else plan_str
            if isinstance(plan_data, dict):
                return plan_data.get("task_title", "") or plan_data.get("plan_title", "")
        except (json.JSONDecodeError, TypeError):
            pass

    # Last resort: use worker_domain
    return worker.get("worker_domain", f"worker_{id(worker)}")


def _get_dependencies(worker: dict) -> list[str]:
    """Extract dependencies list from a worker dict."""
    # Direct field
    deps = worker.get("dependencies", [])
    if deps:
        return deps if isinstance(deps, list) else []

    # Fallback: parse from plan JSON
    plan_str = worker.get("plan", "")
    if plan_str:
        try:
            plan_data = json.loads(plan_str) if isinstance(plan_str, str) else plan_str
            if isinstance(plan_data, dict):
                deps = plan_data.get("dependencies", [])
                return deps if isinstance(deps, list) else []
        except (json.JSONDecodeError, TypeError):
            pass

    return []


def _extract_result_summary(result_json: str) -> tuple[str, list[str], int | None, list[str]]:
    """Extract result fields from a worker result JSON string.

    Returns:
        (result_summary, deliverables, completion_percentage, issues_encountered)
    """
    try:
        data = json.loads(result_json) if isinstance(result_json, str) else result_json
        if isinstance(data, dict):
            summary = data.get("result_summary", "")
            deliverables = data.get("deliverables", [])
            completion_pct = data.get("completion_percentage")
            issues = data.get("issues_encountered", [])
            return (
                summary,
                deliverables if isinstance(deliverables, list) else [],
                int(completion_pct) if completion_pct is not None else None,
                issues if isinstance(issues, list) else [],
            )
    except (json.JSONDecodeError, TypeError):
        pass
    # If it's a plain string error message, use as-is
    if isinstance(result_json, str) and result_json.startswith("[Execution failed"):
        return "", [], None, []
    return str(result_json)[:200] if result_json else "", [], None, []
