"""Floor plan: room definitions and dynamic room assignment."""

from __future__ import annotations


# ── Fixed rooms ──

ROOMS: dict[str, dict] = {
    "reception":     {"label": "접수대",       "floor": 1, "type": "reception"},
    "lobby":         {"label": "로비",         "floor": 1, "type": "lobby"},
    "ceo_office":    {"label": "CEO실",       "floor": 1, "type": "office"},
    "boardroom":     {"label": "대회의실",     "floor": 1, "type": "boardroom"},
    "research_lab":  {"label": "연구실",       "floor": 1, "type": "lab"},
}

# ── Dynamic meeting room pool ──

MEETING_ROOMS: list[dict] = [
    {"id": "mr_a", "label": "Meeting Room A"},
    {"id": "mr_b", "label": "Meeting Room B"},
    {"id": "mr_c", "label": "Meeting Room C"},
    {"id": "mr_d", "label": "Meeting Room D"},
    {"id": "mr_e", "label": "Meeting Room E"},
]


class FloorPlan:
    """Dynamic meeting room assignment manager."""

    def __init__(self):
        self._assignments: dict[str, str] = {}  # domain -> room_id
        self._available = list(MEETING_ROOMS)

    def assign_rooms(self, leaders: list[dict]) -> list[dict]:
        """Assign meeting rooms to active leader domains. Returns char_move events."""
        moves = []
        for leader in leaders:
            domain = leader["leader_domain"]
            if domain not in self._assignments and self._available:
                room = self._available.pop(0)
                self._assignments[domain] = room["id"]
                moves.append({
                    "character": f"{domain}_leader",
                    "to_room": room["id"],
                    "action": "walk",
                    "room_label": f"{room['label']} ({domain})",
                })
        return moves

    def compute_assignments(self, leaders: list[dict]) -> dict[str, str]:
        """Compute domain→room_id mapping. Used by EventBridge to send to frontend."""
        for leader in leaders:
            domain = leader["leader_domain"]
            if domain not in self._assignments and self._available:
                room = self._available.pop(0)
                self._assignments[domain] = room["id"]
        return dict(self._assignments)

    def get_room_for_domain(self, domain: str) -> str:
        return self._assignments.get(domain, "mr_a")

    def get_layout(self) -> dict:
        """Full layout info for frontend initialization."""
        all_rooms = {}
        for room_id, room in ROOMS.items():
            all_rooms[room_id] = {**room, "id": room_id}
        for room in MEETING_ROOMS:
            all_rooms[room["id"]] = {**room, "type": "meeting"}
        return {
            "rooms": all_rooms,
            "assignments": dict(self._assignments),
        }
