"""Stage-level Blackboard for inter-worker information sharing.

Blackboard pattern: completed workers post shared artifacts, warnings, and
open questions. Next-stage workers read the full board before execution.

Difference from predecessor_context:
  - predecessor_context: only direct dependency results
  - blackboard: ALL completed workers' shared notes (regardless of dependency)
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from src.utils.logging import get_logger

logger = get_logger(agent_id="blackboard")


@dataclass
class BlackboardEntry:
    """A single worker's shared note on the blackboard."""

    worker_domain: str
    worker_id: str
    stage: int
    shared_artifacts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)


class Blackboard:
    """Thread-safe stage-level blackboard (session scope)."""

    def __init__(self, max_context_chars: int = 2000) -> None:
        self._lock = threading.Lock()
        self._entries: list[BlackboardEntry] = []
        self._max_chars = max_context_chars

    def post(self, entry: BlackboardEntry) -> None:
        """Post a worker's shared note to the blackboard."""
        with self._lock:
            self._entries.append(entry)
            logger.info(
                "blackboard_post",
                domain=entry.worker_domain,
                stage=entry.stage,
                artifacts=len(entry.shared_artifacts),
                warnings=len(entry.warnings),
            )

    def format_for_prompt(self, current_stage: int) -> str:
        """Format all entries from previous stages as prompt text.

        Only includes entries from stages < current_stage.
        Truncates to max_context_chars to avoid prompt bloat.
        """
        with self._lock:
            entries = [e for e in self._entries if e.stage < current_stage]

        if not entries:
            return ""

        lines = ["## 팀 공유 보드 (이전 단계 워커들의 공유 정보)\n"]

        for e in entries:
            lines.append(f"### [{e.worker_domain}] (Stage {e.stage})")

            if e.shared_artifacts:
                lines.append("**공유 산출물:**")
                for a in e.shared_artifacts[:5]:
                    lines.append(f"  - {a}")

            if e.warnings:
                lines.append("**주의사항:**")
                for w in e.warnings[:3]:
                    lines.append(f"  - {w}")

            if e.open_questions:
                lines.append("**미해결 질문:**")
                for q in e.open_questions[:3]:
                    lines.append(f"  - {q}")

            lines.append("")

        result = "\n".join(lines)

        # Truncate if too long
        if len(result) > self._max_chars:
            result = result[: self._max_chars - 20] + "\n\n[... 이하 생략]"

        return result

    def stats(self) -> dict:
        """Return blackboard statistics."""
        with self._lock:
            return {
                "total_entries": len(self._entries),
                "stages_covered": len({e.stage for e in self._entries}),
                "total_artifacts": sum(len(e.shared_artifacts) for e in self._entries),
                "total_warnings": sum(len(e.warnings) for e in self._entries),
                "total_questions": sum(len(e.open_questions) for e in self._entries),
            }


# ── Pipeline-level Blackboard (P-E-R 루프 간 결과 보존) ──────────

class PipelineBlackboard:
    """Planner-Executor-Reviewer 루프 횡단 결과 보존.

    각 루프 반복의 planner 결과, executor 결과, reviewer 판정을
    누적 보존하여 Reporter에게 손실 없이 전달한다.

    기존 Blackboard(스테이지 간 워커 공유)와는 별도 레벨의 추상화.
    """

    def __init__(self) -> None:
        self.loops: list[dict] = []
        self.event_log: list[dict] = []

    def record_event(self, event_type: str, iteration: int, summary: str = "") -> None:
        """이벤트 로그에 기록 (디버깅 + Reporter 참조용)."""
        import time
        self.event_log.append({
            "timestamp": time.time(),
            "event": event_type,
            "iteration": iteration,
            "summary": summary,
        })

    def record_loop_iteration(
        self,
        iteration: int,
        planner_result: dict,
        executor_results: list[dict],
        reviewer_passed: bool,
        reviewer_gaps: list[str],
        reviewer_critique: str = "",
        synthesizer_result: dict | None = None,
    ) -> None:
        """한 루프 반복의 전체 결과를 기록."""
        entry = {
            "iteration": iteration,
            "planner_result": {
                "worker_name": planner_result.get("worker_name", ""),
                "task_title": planner_result.get("task_title", ""),
                "execution_result": planner_result.get("execution_result", ""),
                "status": planner_result.get("status", ""),
            },
            "executor_results": [
                {
                    "worker_id": ex.get("worker_id", ""),
                    "worker_name": ex.get("worker_name", ""),
                    "worker_domain": ex.get("worker_domain", ""),
                    "task_title": ex.get("task_title", ""),
                    "execution_result": ex.get("execution_result", ""),
                    "status": ex.get("status", ""),
                }
                for ex in executor_results
            ],
            "reviewer_passed": reviewer_passed,
            "reviewer_gaps": reviewer_gaps,
            "reviewer_critique": reviewer_critique,
        }
        if synthesizer_result:
            entry["synthesizer_result"] = {
                "worker_name": synthesizer_result.get("worker_name", ""),
                "task_title": synthesizer_result.get("task_title", ""),
                "execution_result": synthesizer_result.get("execution_result", ""),
                "status": synthesizer_result.get("status", ""),
            }
        self.loops.append(entry)
        self.record_event(
            "loop_complete",
            iteration,
            f"passed={reviewer_passed}, gaps={len(reviewer_gaps)}",
        )

    def get_iteration_count(self) -> int:
        return len(self.loops)

    def get_gaps_for_replanning(self) -> str:
        """Reviewer 갭 + 이전 결과를 planner 재실행용으로 포맷."""
        if not self.loops:
            return ""

        last = self.loops[-1]
        parts = [f"## 이전 반복 (#{last['iteration'] + 1}) 결과\n"]

        # 이전 executor 결과 요약
        parts.append("### 실행자 결과 요약")
        for ex in last["executor_results"]:
            name = ex.get("worker_name") or ex.get("worker_domain", "?")
            result = ex.get("execution_result", "")
            # 결과 요약만 추출 (전체는 너무 김)
            summary = _extract_summary(result)
            parts.append(f"- **{name}**: {summary[:500]}")

        # Reviewer 갭
        if last["reviewer_gaps"]:
            parts.append("\n### 검증자 지적 사항 (보완 필요)")
            for gap in last["reviewer_gaps"]:
                parts.append(f"- ❌ {gap}")

        if last.get("reviewer_critique"):
            parts.append(f"\n### 검증자 의견\n{last['reviewer_critique'][:1000]}")

        return "\n".join(parts)

    def get_accumulated_context(self) -> str:
        """Reporter에게 전달할 전체 누적 컨텍스트."""
        if not self.loops:
            return ""

        parts = [f"## P-E-R 루프 실행 이력 ({len(self.loops)}회 반복)\n"]

        for loop in self.loops:
            iteration = loop["iteration"] + 1
            passed = "✅ PASS" if loop["reviewer_passed"] else "❌ FAIL"
            parts.append(f"### 반복 #{iteration} ({passed})")

            # Planner
            planner = loop.get("planner_result", {})
            if planner.get("execution_result"):
                planner_summary = _extract_summary(planner["execution_result"])
                parts.append(f"\n**기획자** ({planner.get('worker_name', '?')}):")
                parts.append(planner_summary[:2000])

            # Executors
            for ex in loop.get("executor_results", []):
                name = ex.get("worker_name") or ex.get("worker_domain", "?")
                result = ex.get("execution_result", "")
                summary = _extract_summary(result)
                parts.append(f"\n**실행자** ({name}):")
                parts.append(summary[:3000])

            # Synthesizer
            synth = loop.get("synthesizer_result")
            if synth and synth.get("execution_result"):
                synth_summary = _extract_summary(synth["execution_result"])
                parts.append(f"\n**합성자** ({synth.get('worker_name', '?')}):")
                parts.append(synth_summary[:5000])

            # Reviewer feedback
            if loop.get("reviewer_gaps"):
                parts.append("\n**검증자 지적:**")
                for gap in loop["reviewer_gaps"]:
                    parts.append(f"  - {gap}")

            parts.append("")  # blank line

        return "\n".join(parts)

    def to_dict(self) -> dict:
        """state에 저장 가능한 dict로 직렬화."""
        return {
            "loops": self.loops,
            "event_log": self.event_log,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineBlackboard":
        """state에서 복원."""
        bb = cls()
        bb.loops = data.get("loops", [])
        bb.event_log = data.get("event_log", [])
        return bb


def _extract_summary(result_json: str) -> str:
    """WorkerResult JSON 문자열에서 result_summary 추출."""
    if not result_json:
        return "(결과 없음)"
    if isinstance(result_json, dict):
        return str(result_json.get("result_summary", result_json.get("summary", str(result_json)[:500])))
    try:
        data = json.loads(result_json)
        if isinstance(data, dict):
            return data.get("result_summary", data.get("summary", str(data)[:500]))
        return str(data)[:500]
    except (json.JSONDecodeError, TypeError):
        return str(result_json)[:500]


def extract_blackboard_entry(
    worker: dict,
    result_json: str,
    stage: int,
) -> BlackboardEntry | None:
    """Extract a BlackboardEntry from a WorkerResult JSON.

    Maps WorkerResult fields:
      - deliverables → shared_artifacts
      - issues_encountered → warnings
    """
    try:
        data = json.loads(result_json)
    except (json.JSONDecodeError, TypeError):
        return None

    artifacts = data.get("deliverables", [])
    warnings = data.get("issues_encountered", [])

    if not artifacts and not warnings:
        return None

    return BlackboardEntry(
        worker_domain=worker.get("worker_domain", "unknown"),
        worker_id=worker.get("worker_id", ""),
        stage=stage,
        shared_artifacts=artifacts[:5],
        warnings=warnings[:3],
    )
