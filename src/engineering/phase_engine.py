"""Engineering mode — PhaseEngine state machine with PlanStep tracking.

The workflow follows a strict linear sequence::

    BRAINSTORM -> PLAN -> IMPLEMENT -> VERIFY -> COMPLETE

Transitions from IMPLEMENT to VERIFY require every :class:`PlanStep` to carry
a ``"done"`` status, ensuring the AI completes all planned work before moving
on.  :meth:`PhaseEngine.force_advance` bypasses this guard for resource-limit
scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.engineering.tools.executor import Phase


# ---------------------------------------------------------------------------
# PlanStep dataclass
# ---------------------------------------------------------------------------


@dataclass
class PlanStep:
    """A single unit of planned work within the IMPLEMENT phase.

    Parameters
    ----------
    id:
        Unique integer identifier within the plan.
    description:
        Human-readable description of the work item.
    status:
        One of ``"pending"``, ``"in_progress"``, ``"done"``, or ``"failed"``.
    """

    id: int
    description: str
    status: str = "pending"


# ---------------------------------------------------------------------------
# Phase ordering
# ---------------------------------------------------------------------------

_PHASE_ORDER: list[Phase] = [
    Phase.BRAINSTORM,
    Phase.PLAN,
    Phase.IMPLEMENT,
    Phase.VERIFY,
    Phase.COMPLETE,
]

_PHASE_INDEX: dict[Phase, int] = {phase: idx for idx, phase in enumerate(_PHASE_ORDER)}


# ---------------------------------------------------------------------------
# PhaseEngine
# ---------------------------------------------------------------------------


class PhaseEngine:
    """State machine that manages Engineering-mode workflow phases.

    Usage::

        engine = PhaseEngine()
        engine.advance()                       # BRAINSTORM -> PLAN
        engine.advance()                       # PLAN -> IMPLEMENT
        engine.set_plan_steps([
            PlanStep(id=0, description="Write tests"),
            PlanStep(id=1, description="Implement feature"),
        ])
        engine.update_step(0, "done")
        engine.update_step(1, "done")
        engine.advance()                       # IMPLEMENT -> VERIFY (all done)
        engine.advance()                       # VERIFY -> COMPLETE
    """

    def __init__(self) -> None:
        self._index: int = 0  # starts at BRAINSTORM
        self._steps: dict[int, PlanStep] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_phase(self) -> Phase:
        """Return the current workflow phase."""
        return _PHASE_ORDER[self._index]

    # ------------------------------------------------------------------
    # Transition guards
    # ------------------------------------------------------------------

    def can_advance(self) -> bool:
        """Return ``True`` when a forward transition is permitted.

        Rules
        -----
        - At ``COMPLETE``: always ``False`` (terminal state).
        - At ``IMPLEMENT``: ``True`` only when every :class:`PlanStep` has
          ``status == "done"`` and at least one step exists.
        - All other phases: always ``True``.
        """
        if self._index >= len(_PHASE_ORDER) - 1:
            return False
        if self.current_phase == Phase.IMPLEMENT:
            if not self._steps:
                return False
            return all(s.status == "done" for s in self._steps.values())
        return True

    # ------------------------------------------------------------------
    # Transition methods
    # ------------------------------------------------------------------

    def advance(self) -> Phase:
        """Move to the next phase if :meth:`can_advance` allows it.

        At ``COMPLETE`` (terminal), the method is a no-op and returns
        ``COMPLETE``.

        Returns
        -------
        Phase
            The phase after the call (new current phase).
        """
        if self.can_advance():
            self._index += 1
        return self.current_phase

    def force_advance(self) -> Phase:
        """Move to the next phase unconditionally, bypassing PlanStep checks.

        Intended for resource-limit or emergency scenarios.  At ``COMPLETE``
        this is a no-op.

        Returns
        -------
        Phase
            The phase after the call (new current phase).
        """
        if self._index < len(_PHASE_ORDER) - 1:
            self._index += 1
        return self.current_phase

    def rewind(self, target: Phase) -> Phase:
        """Go back to an *earlier* phase.

        Parameters
        ----------
        target:
            The phase to rewind to.  Must be strictly earlier than the
            current phase.

        Raises
        ------
        ValueError
            If *target* is the same as or later than the current phase.

        Returns
        -------
        Phase
            The phase after the call (equals *target*).
        """
        target_index = _PHASE_INDEX[target]
        if target_index >= self._index:
            raise ValueError(
                f"Cannot rewind to '{target.value}': it is not earlier than "
                f"the current phase '{self.current_phase.value}'."
            )
        self._index = target_index
        return self.current_phase

    # ------------------------------------------------------------------
    # PlanStep management
    # ------------------------------------------------------------------

    def set_plan_steps(self, steps: list[PlanStep]) -> None:
        """Replace the current plan steps.

        Typically called when entering the IMPLEMENT phase with a concrete
        task breakdown produced during PLAN.

        Parameters
        ----------
        steps:
            List of :class:`PlanStep` objects.  Duplicate IDs are overwritten
            by the last occurrence.
        """
        self._steps = {s.id: s for s in steps}

    def update_step(self, step_id: int, status: str) -> None:
        """Update the status of a plan step by its ID.

        Parameters
        ----------
        step_id:
            The :attr:`PlanStep.id` to update.
        status:
            New status value (``"pending"``, ``"in_progress"``, ``"done"``,
            or ``"failed"``).

        Raises
        ------
        KeyError
            If *step_id* does not match any registered step.
        """
        if step_id not in self._steps:
            raise KeyError(f"No PlanStep with id={step_id!r}")
        self._steps[step_id].status = status

    def get_plan_summary(self) -> dict:
        """Return a structured summary of the current plan steps.

        Returns
        -------
        dict
            ``{total, done, in_progress, pending, steps: [{id, description, status}]}``
        """
        steps_list = list(self._steps.values())
        return {
            "total": len(steps_list),
            "done": sum(1 for s in steps_list if s.status == "done"),
            "in_progress": sum(1 for s in steps_list if s.status == "in_progress"),
            "pending": sum(1 for s in steps_list if s.status == "pending"),
            "steps": [
                {"id": s.id, "description": s.description, "status": s.status}
                for s in steps_list
            ],
        }
