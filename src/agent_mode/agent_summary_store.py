"""Per-agent summary accumulation store."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_BASE = Path(__file__).parents[2] / "data" / "agent_mode" / "summaries"


class AgentSummaryStore:
    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base = Path(base_dir) if base_dir else _DEFAULT_BASE
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, agent_id: str) -> Path:
        return self._base / f"{agent_id}.json"

    def _load(self, agent_id: str) -> list[dict]:
        p = self._path(agent_id)
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, agent_id: str, data: list[dict]) -> None:
        self._path(agent_id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_summary(self, agent_id: str, session_id: str, summary: str) -> None:
        data = self._load(agent_id)
        data.append({
            "session_id": session_id,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "summary": summary,
        })
        self._save(agent_id, data)

    def get_summaries(self, agent_id: str) -> list[dict]:
        return self._load(agent_id)

    def get_recent_context(self, agent_id: str, max_entries: int = 5) -> str:
        summaries = self._load(agent_id)
        recent = summaries[-max_entries:]
        if not recent:
            return ""
        lines = [f"[{s['date']}] {s['summary']}" for s in recent]
        return "\n".join(lines)
