"""Tests for PhaseEngine state machine (TDD)."""

from __future__ import annotations

import pytest

from src.engineering.phase_engine import PhaseEngine, PlanStep
from src.engineering.tools.executor import Phase


# ---------------------------------------------------------------------------
# PlanStep helpers
# ---------------------------------------------------------------------------

def make_steps(*descriptions: str) -> list[PlanStep]:
    """Create a list of PlanStep with sequential IDs, all pending."""
    return [PlanStep(id=i, description=desc) for i, desc in enumerate(descriptions)]


def done_steps(*descriptions: str) -> list[PlanStep]:
    """Create a list of PlanStep with all statuses set to 'done'."""
    return [PlanStep(id=i, description=desc, status="done") for i, desc in enumerate(descriptions)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_initial_phase_is_brainstorm(self):
        engine = PhaseEngine()
        assert engine.current_phase == Phase.BRAINSTORM


class TestAdvance:
    def test_advance_brainstorm_to_plan(self):
        engine = PhaseEngine()
        result = engine.advance()
        assert result == Phase.PLAN
        assert engine.current_phase == Phase.PLAN

    def test_advance_plan_to_implement(self):
        engine = PhaseEngine()
        engine.advance()  # -> PLAN
        result = engine.advance()  # -> IMPLEMENT
        assert result == Phase.IMPLEMENT
        assert engine.current_phase == Phase.IMPLEMENT

    def test_advance_requires_plan_steps_for_implement_to_verify(self):
        """Cannot advance from IMPLEMENT to VERIFY unless all steps are 'done'."""
        engine = PhaseEngine()
        engine.advance()  # -> PLAN
        engine.advance()  # -> IMPLEMENT

        # With no steps set, can_advance should be False
        assert engine.can_advance() is False

        # Set steps that are still pending
        engine.set_plan_steps(make_steps("Step A", "Step B"))
        assert engine.can_advance() is False

        # Mark only one done — still blocked
        engine.update_step(0, "done")
        assert engine.can_advance() is False

        # Mark all done — now allowed
        engine.update_step(1, "done")
        assert engine.can_advance() is True

        result = engine.advance()
        assert result == Phase.VERIFY

    def test_advance_verify_to_complete(self):
        engine = PhaseEngine()
        for _ in range(3):  # BRAINSTORM -> PLAN -> IMPLEMENT
            engine.advance()
        engine.set_plan_steps(done_steps("S1"))
        engine.advance()  # -> VERIFY
        result = engine.advance()  # -> COMPLETE
        assert result == Phase.COMPLETE

    def test_advance_at_complete_stays_at_complete(self):
        """advance() at COMPLETE returns COMPLETE without raising."""
        engine = PhaseEngine()
        for _ in range(3):
            engine.advance()
        engine.set_plan_steps(done_steps("S1"))
        engine.advance()  # -> VERIFY
        engine.advance()  # -> COMPLETE
        result = engine.advance()  # should not move further
        assert result == Phase.COMPLETE

    def test_advance_through_all_phases(self):
        engine = PhaseEngine()
        assert engine.current_phase == Phase.BRAINSTORM

        engine.advance()
        assert engine.current_phase == Phase.PLAN

        engine.advance()
        assert engine.current_phase == Phase.IMPLEMENT

        engine.set_plan_steps(done_steps("Write code", "Write tests"))
        engine.advance()
        assert engine.current_phase == Phase.VERIFY

        engine.advance()
        assert engine.current_phase == Phase.COMPLETE


class TestCanAdvance:
    def test_can_advance_from_brainstorm(self):
        engine = PhaseEngine()
        assert engine.can_advance() is True

    def test_can_advance_from_plan(self):
        engine = PhaseEngine()
        engine.advance()
        assert engine.can_advance() is True

    def test_can_advance_from_verify(self):
        engine = PhaseEngine()
        engine.advance()  # PLAN
        engine.advance()  # IMPLEMENT
        engine.set_plan_steps(done_steps("S1"))
        engine.advance()  # VERIFY
        assert engine.can_advance() is True

    def test_cannot_advance_from_complete(self):
        engine = PhaseEngine()
        engine.advance()  # PLAN
        engine.advance()  # IMPLEMENT
        engine.set_plan_steps(done_steps("S1"))
        engine.advance()  # VERIFY
        engine.advance()  # COMPLETE
        assert engine.can_advance() is False


class TestRewind:
    def test_rewind_to_brainstorm_from_implement(self):
        engine = PhaseEngine()
        engine.advance()  # PLAN
        engine.advance()  # IMPLEMENT
        result = engine.rewind(Phase.BRAINSTORM)
        assert result == Phase.BRAINSTORM
        assert engine.current_phase == Phase.BRAINSTORM

    def test_rewind_to_plan_from_implement(self):
        engine = PhaseEngine()
        engine.advance()  # PLAN
        engine.advance()  # IMPLEMENT
        result = engine.rewind(Phase.PLAN)
        assert result == Phase.PLAN
        assert engine.current_phase == Phase.PLAN

    def test_rewind_to_earlier_phase_only(self):
        """Rewinding to a later or equal phase raises ValueError."""
        engine = PhaseEngine()
        engine.advance()  # PLAN
        with pytest.raises(ValueError):
            engine.rewind(Phase.IMPLEMENT)

    def test_rewind_to_same_phase_raises(self):
        engine = PhaseEngine()
        engine.advance()  # PLAN
        with pytest.raises(ValueError):
            engine.rewind(Phase.PLAN)


class TestForceAdvance:
    def test_force_advance_from_implement_bypasses_step_check(self):
        """force_advance() ignores incomplete PlanSteps."""
        engine = PhaseEngine()
        engine.advance()  # PLAN
        engine.advance()  # IMPLEMENT

        engine.set_plan_steps(make_steps("Unfinished step"))
        assert engine.can_advance() is False

        result = engine.force_advance()
        assert result == Phase.VERIFY
        assert engine.current_phase == Phase.VERIFY

    def test_force_advance_at_complete_stays_at_complete(self):
        engine = PhaseEngine()
        engine.advance()  # PLAN
        engine.advance()  # IMPLEMENT
        engine.set_plan_steps(done_steps("S1"))
        engine.advance()  # VERIFY
        engine.advance()  # COMPLETE
        result = engine.force_advance()
        assert result == Phase.COMPLETE


class TestPlanSteps:
    def test_set_plan_steps(self):
        engine = PhaseEngine()
        steps = make_steps("Write tests", "Write code")
        engine.set_plan_steps(steps)
        summary = engine.get_plan_summary()
        assert summary["total"] == 2
        assert summary["pending"] == 2
        assert summary["done"] == 0
        assert summary["in_progress"] == 0

    def test_update_step_changes_status(self):
        engine = PhaseEngine()
        engine.set_plan_steps(make_steps("Alpha", "Beta", "Gamma"))
        engine.update_step(0, "in_progress")
        engine.update_step(1, "done")
        summary = engine.get_plan_summary()
        assert summary["in_progress"] == 1
        assert summary["done"] == 1
        assert summary["pending"] == 1

    def test_update_step_invalid_id_raises(self):
        engine = PhaseEngine()
        engine.set_plan_steps(make_steps("Only step"))
        with pytest.raises(KeyError):
            engine.update_step(99, "done")

    def test_get_plan_summary_correct_counts(self):
        engine = PhaseEngine()
        steps = [
            PlanStep(id=0, description="A", status="done"),
            PlanStep(id=1, description="B", status="done"),
            PlanStep(id=2, description="C", status="in_progress"),
            PlanStep(id=3, description="D", status="pending"),
            PlanStep(id=4, description="E", status="failed"),
        ]
        engine.set_plan_steps(steps)
        summary = engine.get_plan_summary()
        assert summary["total"] == 5
        assert summary["done"] == 2
        assert summary["in_progress"] == 1
        assert summary["pending"] == 1
        # failed is not counted in standard buckets but steps list is full
        assert len(summary["steps"]) == 5

    def test_get_plan_summary_steps_content(self):
        engine = PhaseEngine()
        engine.set_plan_steps([PlanStep(id=0, description="Do it", status="done")])
        summary = engine.get_plan_summary()
        assert summary["steps"][0] == {"id": 0, "description": "Do it", "status": "done"}

    def test_get_plan_summary_empty(self):
        engine = PhaseEngine()
        summary = engine.get_plan_summary()
        assert summary["total"] == 0
        assert summary["steps"] == []
