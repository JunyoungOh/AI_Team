"""Determines character movements and actions based on graph node transitions.

Each graph node has a "scene script" — either static (fixed movements/actions)
or dynamic (generated from workers state).

Lighting system: rooms default to dark; only active rooms are lit.
"""

from __future__ import annotations


# ── Scene scripts per node ──
# Each script can include:
#   movements: character move commands
#   actions: character action/state commands
#   lights_on: list of room_ids to illuminate
#   dynamic: True + generator: method name for runtime generation

SCENE_SCRIPTS: dict[str, dict] = {
    "intake": {
        "lights_on": ["ceo_office"],
        "movements": [],
        "actions": [
            {"character": "ceo", "action": "reading", "detail": "작업 지시서 확인 중..."},
        ],
    },
    "ceo_route": {
        "dynamic": True,
        "generator": "_gen_ceo_route",
    },
    "ceo_questions": {
        "lights_on": ["ceo_office"],
        "movements": [],
        "actions": [
            {"character": "ceo", "action": "writing", "detail": "명확화 질문 작성 중..."},
        ],
    },
    "await_user_answers": {
        "lights_on": ["ceo_office"],
        "movements": [],
        "actions": [
            {"character": "ceo", "action": "waiting", "detail": "사용자 답변 대기 중..."},
        ],
    },
    "leader_task_decomposition": {
        "dynamic": True,
        "generator": "_gen_task_decomposition",
    },
    "ceo_work_order": {
        "lights_on": ["ceo_office"],
        "movements": [],
        "actions": [
            {"character": "ceo", "action": "writing", "detail": "작업지시서 작성 중..."},
        ],
    },
    "worker_execution": {
        "dynamic": True,
        "generator": "_gen_worker_execution",
    },
    "user_review_results": {
        "lights_on": ["ceo_office"],
        "movements": [
            {"character": "ceo", "to_room": "ceo_office", "action": "walk"},
        ],
        "actions": [
            {"character": "ceo", "action": "presenting", "detail": "결과물을 사용자에게 보고 중..."},
        ],
    },
    "leader_consolidation": {
        "dynamic": True,
        "generator": "_gen_leader_consolidation",
    },
    "worker_result_revision": {
        "dynamic": True,
        "generator": "_gen_worker_execution",
    },
    "ceo_final_report": {
        "dynamic": True,
        "generator": "_gen_final_report",
    },
    "error_terminal": {
        "lights_on": ["ceo_office"],
        "movements": [],
        "actions": [
            {"character": "ceo", "action": "alert", "detail": "오류 발생!"},
        ],
    },
}


class SceneDirector:
    """Translate node transitions into character movements and actions."""

    def __init__(self):
        self._workers: list[dict] = []
        self._room_assignments: dict[str, str] = {}  # domain -> room_id

    def set_room_assignments(self, assignments: dict[str, str]) -> None:
        self._room_assignments = assignments

    def plan_movements(self, node_name: str, update: dict) -> list[dict]:
        if "workers" in update:
            self._workers = update["workers"]

        script = SCENE_SCRIPTS.get(node_name, {})
        if script.get("dynamic"):
            generator = getattr(self, script["generator"])
            return generator(update)
        return list(script.get("movements", []))

    def plan_actions(self, node_name: str, update: dict) -> list[dict]:
        script = SCENE_SCRIPTS.get(node_name, {})
        if script.get("dynamic"):
            generator_name = script["generator"]
            actions_method = f"{generator_name}_actions"
            if hasattr(self, actions_method):
                return getattr(self, actions_method)(update)
            return []
        return list(script.get("actions", []))

    def plan_lights(self, node_name: str, update: dict) -> list[str]:
        """Return list of room_ids that should be lit for this node."""
        script = SCENE_SCRIPTS.get(node_name, {})
        if script.get("dynamic"):
            generator_name = script["generator"]
            lights_method = f"{generator_name}_lights"
            if hasattr(self, lights_method):
                return getattr(self, lights_method)(update)
            return ["ceo_office"]
        return list(script.get("lights_on", ["ceo_office"]))

    # ── Dynamic scene generators ──

    def _gen_ceo_route(self, update: dict) -> list[dict]:
        """After domain routing, workers walk to CEO office."""
        moves = []
        for i, worker in enumerate(self._workers):
            w_domain = worker.get("worker_domain", "unknown")
            moves.append({
                "character": w_domain,
                "to_room": "ceo_office",
                "action": "phone_then_walk",
                "delay_index": i,
            })
        return moves

    def _gen_ceo_route_actions(self, update: dict) -> list[dict]:
        actions = [{"character": "ceo", "action": "thinking", "detail": "적합한 팀을 선정하는 중..."}]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            actions.append({
                "character": w_domain,
                "action": "walking",
                "detail": f"{w_domain} CEO Office로 이동 중...",
            })
        return actions

    def _gen_ceo_route_lights(self, update: dict) -> list[str]:
        return ["ceo_office"]

    def _gen_task_decomposition(self, update: dict) -> list[dict]:
        """Workers walk to their assigned meeting rooms for task execution."""
        moves = []
        for i, worker in enumerate(self._workers):
            w_domain = worker.get("worker_domain", "unknown")
            room = self._room_assignments.get(w_domain, "mr_a")
            moves.append({
                "character": w_domain,
                "to_room": room,
                "action": "walk",
                "delay_index": i,
            })
        return moves

    def _gen_task_decomposition_actions(self, update: dict) -> list[dict]:
        actions = [{"character": "ceo", "action": "reviewing", "detail": "작업 분해 모니터링 중..."}]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            actions.append({
                "character": w_domain,
                "action": "presenting",
                "detail": f"{w_domain} 작업 분해 중...",
            })
        return actions

    def _gen_task_decomposition_lights(self, update: dict) -> list[str]:
        rooms = ["ceo_office"]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            room = self._room_assignments.get(w_domain, "mr_a")
            if room not in rooms:
                rooms.append(room)
        return rooms

    def _gen_leader_consolidation(self, update: dict) -> list[dict]:
        """All workers return to CEO office for consolidation."""
        moves = []
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            moves.append({
                "character": w_domain,
                "to_room": "ceo_office",
                "action": "walk",
            })
        return moves

    def _gen_leader_consolidation_actions(self, update: dict) -> list[dict]:
        actions = [{"character": "ceo", "action": "reviewing", "detail": "결과 통합 회의 중..."}]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            actions.append({
                "character": w_domain,
                "action": "presenting",
                "detail": f"{w_domain} 결과 보고 중...",
            })
        return actions

    def _gen_leader_consolidation_lights(self, update: dict) -> list[str]:
        return ["ceo_office"]

    def _gen_worker_execution(self, update: dict) -> list[dict]:
        """Workers enter from corridor edge to their rooms."""
        moves = []
        for idx, worker in enumerate(self._workers):
            w_domain = worker.get("worker_domain", "unknown")
            room = self._room_assignments.get(w_domain, "mr_a")
            moves.append({
                "character": w_domain,
                "to_room": room,
                "action": "enter_from_corridor",
                "delay_index": idx,
            })
        return moves

    def _gen_worker_execution_actions(self, update: dict) -> list[dict]:
        actions = [{"character": "ceo", "action": "reviewing", "detail": "워커 진행 상황 모니터링 중..."}]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            actions.append({
                "character": w_domain,
                "action": "writing",
                "detail": f"{w_domain} 작업 실행 중...",
            })
        return actions

    def _gen_worker_execution_lights(self, update: dict) -> list[str]:
        rooms = ["ceo_office"]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            room = self._room_assignments.get(w_domain, "mr_a")
            if room not in rooms:
                rooms.append(room)
        return rooms

    def _gen_final_report(self, update: dict) -> list[dict]:
        """Everyone gathers in the boardroom."""
        moves = [{"character": "ceo", "to_room": "boardroom", "action": "walk"}]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            moves.append({
                "character": w_domain,
                "to_room": "boardroom",
                "action": "walk",
            })
        return moves

    def _gen_final_report_actions(self, update: dict) -> list[dict]:
        actions = [{"character": "ceo", "action": "presenting", "detail": "최종 보고서 작성 중..."}]
        for worker in self._workers:
            w_domain = worker.get("worker_domain", "unknown")
            actions.append({
                "character": w_domain,
                "action": "presenting",
                "detail": f"{w_domain} 결과 보고 중...",
            })
        return actions

    def _gen_final_report_lights(self, update: dict) -> list[str]:
        return ["ceo_office", "boardroom"]
