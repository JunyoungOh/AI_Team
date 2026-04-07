"""Character definitions and dynamic creation from agent registry."""

from __future__ import annotations


# ── Fixed characters (always present) ──

FIXED_CHARACTERS: dict[str, dict] = {
    "ceo": {
        "id": "ceo",
        "name": "김도현",
        "title": "CEO",
        "emoji": "\U0001f454",  # 👔
        "color": "#4A90D9",
        "role": "ceo",
        "gender": "M",
        "home_room": "ceo_office",
    },
    "user": {
        "id": "user",
        "name": "You",
        "title": "Client",
        "emoji": "\U0001f9d1",  # 🧑
        "color": "#50C878",
        "role": "user",
        "gender": "U",
        "home_room": "reception",
    },
}

# ── Domain color mapping (extracted from former LEADER_PROFILES) ──

_DOMAIN_COLORS: dict[str, str] = {
    "engineering": "#E67E22",
    "research": "#9B59B6",
    "marketing": "#E74C3C",
    "operations": "#1ABC9C",
    "finance": "#F39C12",
    "hr": "#3498DB",
    "legal": "#95A5A6",
    "data": "#2ECC71",
    "product": "#E91E63",
    "security": "#607D8B",
}

# ── Worker emoji mapping ──

WORKER_PROFILES: dict[str, dict] = {
    "backend_developer":   {"emoji": "\u2328\ufe0f",     "name": "오진우",   "gender": "M"},
    "frontend_developer":  {"emoji": "\U0001f3a8",       "name": "김예린",   "gender": "F"},
    "devops_engineer":     {"emoji": "\U0001f527",       "name": "장현수",   "gender": "M"},
    "architect":           {"emoji": "\U0001f3d7\ufe0f", "name": "배소연",   "gender": "F"},
    "researcher":          {"emoji": "\U0001f4da",       "name": "홍석진",   "gender": "M"},
    "data_analyst":        {"emoji": "\U0001f4c8",       "name": "노하린",   "gender": "F"},
    "fact_checker":        {"emoji": "\u2705",           "name": "유건우",   "gender": "M"},
    "content_writer":      {"emoji": "\u270d\ufe0f",     "name": "문지현",   "gender": "F"},
    "strategist":          {"emoji": "\u265f\ufe0f",     "name": "조현태",   "gender": "M"},
    "designer":            {"emoji": "\U0001f58c\ufe0f", "name": "안서윤",   "gender": "F"},
    "market_researcher":   {"emoji": "\U0001f50d",       "name": "권민재",   "gender": "M"},
    "project_manager":     {"emoji": "\U0001f4cb",       "name": "송은채",   "gender": "F"},
    "process_analyst":     {"emoji": "\U0001f504",       "name": "황태영",   "gender": "M"},
    "financial_analyst":   {"emoji": "\U0001f4c9",       "name": "임가영",   "gender": "F"},
    "accountant":          {"emoji": "\U0001f9ee",       "name": "정우성",   "gender": "M"},
    "recruiter":           {"emoji": "\U0001f3af",       "name": "한수빈",   "gender": "F"},
    "training_specialist": {"emoji": "\U0001f4d6",       "name": "이동현",   "gender": "M"},
    "org_developer":       {"emoji": "\U0001f3e2",       "name": "차은지",   "gender": "F"},
    "legal_counsel":       {"emoji": "\U0001f4dc",       "name": "류승훈",   "gender": "M"},
    "compliance_officer":  {"emoji": "\U0001f50f",       "name": "강채원",   "gender": "F"},
    "ip_specialist":       {"emoji": "\U0001f4dd",       "name": "서정민",   "gender": "M"},
    "data_engineer":       {"emoji": "\U0001f5c4\ufe0f", "name": "박하영",   "gender": "F"},
    "ml_engineer":         {"emoji": "\U0001f916",       "name": "김시우",   "gender": "M"},
    "data_scientist":      {"emoji": "\U0001f9ea",       "name": "윤채아",   "gender": "F"},
    "product_manager":     {"emoji": "\U0001f3af",       "name": "이준서",   "gender": "M"},
    "ux_researcher":       {"emoji": "\U0001f465",       "name": "전소희",   "gender": "F"},
    "product_analyst":     {"emoji": "\U0001f4ca",       "name": "남도윤",   "gender": "M"},
    "security_analyst":    {"emoji": "\U0001f510",       "name": "고예나",   "gender": "F"},
    "compensation_analyst": {"emoji": "\U0001f4b5",      "name": "백지호",   "gender": "M"},
}


class CharacterManager:
    """Manage active characters during a session."""

    def __init__(self):
        # Start empty — CEO/You cards should only appear after pipeline starts
        self._active: dict[str, dict] = {}

    def activate_worker(self, worker_domain: str, parent_domain: str) -> dict:
        profile = WORKER_PROFILES.get(worker_domain, {"emoji": "\U0001f477", "name": worker_domain, "gender": "U"})
        color = _DOMAIN_COLORS.get(parent_domain, "#999")
        char = {
            "id": worker_domain,
            "name": profile["name"],
            "title": worker_domain.replace("_", " ").title(),
            "emoji": profile["emoji"],
            "color": color,
            "role": "worker",
            "gender": profile.get("gender", "U"),
            "domain": parent_domain,
            "home_room": "corridor_entrance",
        }
        self._active[worker_domain] = char
        return char

    def activate_deep_researcher(self, domain: str, index: int = 0, home_room: str = "research_lab") -> dict:
        """Spawn a Deep Researcher character for the given domain."""
        color = _DOMAIN_COLORS.get(domain, "#9B59B6")
        char_id = f"deep_researcher_{domain}_{index}"
        char = {
            "id": char_id,
            "name": "딥리서처",
            "title": f"{domain.title()} Deep Researcher",
            "emoji": "\U0001f9d1\u200d\U0001f4bb",  # 🧑‍💻
            "color": color,
            "role": "worker",
            "gender": "U",
            "domain": domain,
            "home_room": home_room,
        }
        self._active[char_id] = char
        return char

    def get_all_active(self) -> list[dict]:
        return list(self._active.values())

    def get(self, char_id: str) -> dict | None:
        return self._active.get(char_id)
