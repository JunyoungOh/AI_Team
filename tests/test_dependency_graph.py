"""Unit tests for dependency graph utilities."""

import json

import pytest

from src.utils.dependency_graph import (
    CircularDependencyError,
    build_execution_stages,
    build_predecessor_context,
    has_any_dependencies,
)


# ── Helpers ──────────────────────────────────────────────


def _worker(title: str, domain: str = "", deps: list[str] | None = None) -> dict:
    """Create a minimal worker dict for testing."""
    return {
        "task_title": title,
        "worker_domain": domain or title,
        "plan": json.dumps({"task_title": title}),
        "dependencies": deps or [],
    }


def _completed_result(summary: str, deliverables: list[str] | None = None) -> str:
    """Create a worker result JSON string."""
    return json.dumps({
        "result_summary": summary,
        "deliverables": deliverables or [],
        "issues_encountered": [],
        "completion_percentage": 100,
    })


# ── TestHasAnyDependencies ───────────────────────────────


class TestHasAnyDependencies:
    def test_no_dependencies(self):
        workers = [_worker("A"), _worker("B"), _worker("C")]
        assert has_any_dependencies(workers) is False

    def test_with_dependencies(self):
        workers = [_worker("A"), _worker("B", deps=["A"])]
        assert has_any_dependencies(workers) is True

    def test_empty_list(self):
        assert has_any_dependencies([]) is False


# ── TestBuildExecutionStages ─────────────────────────────


class TestBuildExecutionStages:
    def test_no_dependencies_single_stage(self):
        """All workers independent → single stage with all of them."""
        workers = [_worker("A"), _worker("B"), _worker("C")]
        stages = build_execution_stages(workers)
        assert len(stages) == 1
        assert sorted(stages[0]) == [0, 1, 2]

    def test_linear_chain(self):
        """A → B → C → 3 stages."""
        workers = [
            _worker("A"),
            _worker("B", deps=["A"]),
            _worker("C", deps=["B"]),
        ]
        stages = build_execution_stages(workers)
        assert stages == [[0], [1], [2]]

    def test_diamond_pattern(self):
        """A → B, C → D (diamond) → 3 stages."""
        workers = [
            _worker("A"),
            _worker("B", deps=["A"]),
            _worker("C", deps=["A"]),
            _worker("D", deps=["B", "C"]),
        ]
        stages = build_execution_stages(workers)
        assert len(stages) == 3
        assert stages[0] == [0]
        assert sorted(stages[1]) == [1, 2]
        assert stages[2] == [3]

    def test_mixed_some_with_deps(self):
        """Some workers have deps, some don't. Independent ones go to stage 0."""
        workers = [
            _worker("A"),
            _worker("B"),
            _worker("C", deps=["A"]),
        ]
        stages = build_execution_stages(workers)
        assert len(stages) == 2
        assert sorted(stages[0]) == [0, 1]  # A and B (no deps)
        assert stages[1] == [2]              # C depends on A

    def test_circular_dependency_raises(self):
        """Circular A → B → A should raise CircularDependencyError."""
        workers = [
            _worker("A", deps=["B"]),
            _worker("B", deps=["A"]),
        ]
        with pytest.raises(CircularDependencyError) as exc_info:
            build_execution_stages(workers)
        assert len(exc_info.value.remaining) == 2

    def test_missing_reference_ignored(self):
        """Missing dependency ref is warned and ignored — worker moves to earlier stage."""
        workers = [
            _worker("A"),
            _worker("B", deps=["nonexistent"]),
        ]
        stages = build_execution_stages(workers)
        # B's dep is ignored → both in stage 0
        assert len(stages) == 1
        assert sorted(stages[0]) == [0, 1]

    def test_single_worker(self):
        workers = [_worker("A")]
        stages = build_execution_stages(workers)
        assert stages == [[0]]

    def test_empty_workers(self):
        assert build_execution_stages([]) == []


# ── TestBuildPredecessorContext ───────────────────────────


class TestBuildPredecessorContext:
    def test_no_dependencies_empty_string(self):
        workers = [_worker("A"), _worker("B")]
        result = build_predecessor_context(workers, {}, 0)
        assert result == ""

    def test_completed_dependency_included(self):
        workers = [
            _worker("A", domain="architect"),
            _worker("B", domain="backend", deps=["A"]),
        ]
        completed = {0: _completed_result("API 설계 완료", ["REST API 스펙"])}
        result = build_predecessor_context(workers, completed, 1)
        assert "API 설계 완료" in result
        assert "REST API 스펙" in result
        assert "architect" in result

    def test_incomplete_dependency_excluded(self):
        workers = [
            _worker("A"),
            _worker("B", deps=["A"]),
        ]
        # No completed results
        result = build_predecessor_context(workers, {}, 1)
        assert result == ""

    def test_failed_dependency_excluded(self):
        workers = [
            _worker("A"),
            _worker("B", deps=["A"]),
        ]
        # Failed result (error string)
        completed = {0: "[Execution failed: timeout]"}
        result = build_predecessor_context(workers, completed, 1)
        # Failed results have no summary → excluded
        assert result == ""

    def test_only_direct_dependencies_included(self):
        """Only direct deps are included, not transitive ones."""
        workers = [
            _worker("A", domain="architect"),
            _worker("B", domain="backend", deps=["A"]),
            _worker("C", domain="devops", deps=["B"]),
        ]
        completed = {
            0: _completed_result("아키텍처 설계"),
            1: _completed_result("백엔드 구현"),
        }
        # C depends on B only, not A
        result = build_predecessor_context(workers, completed, 2)
        assert "백엔드 구현" in result
        assert "아키텍처 설계" not in result


# ── TestDependenciesFromPlanJSON ─────────────────────────


class TestDependenciesFromPlanJSON:
    """Test that dependencies can be extracted from plan JSON, not just direct fields."""

    def test_deps_from_plan_json(self):
        worker = {
            "worker_domain": "backend",
            "plan": json.dumps({
                "task_title": "Backend API",
                "dependencies": ["Architecture Design"],
            }),
        }
        assert has_any_dependencies([worker]) is True

    def test_title_from_plan_json(self):
        workers = [
            {"worker_domain": "arch", "plan": json.dumps({"task_title": "Architecture Design"})},
            {"worker_domain": "backend", "plan": json.dumps({
                "task_title": "Backend API",
                "dependencies": ["Architecture Design"],
            })},
        ]
        stages = build_execution_stages(workers)
        assert stages == [[0], [1]]
