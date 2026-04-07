"""Calendar data models."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CalendarEvent:
    """A single calendar event."""

    title: str
    start: datetime
    end: datetime
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = ""
    location: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "description": self.description,
            "location": self.location,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CalendarEvent:
        return cls(
            event_id=d["event_id"],
            title=d["title"],
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]),
            description=d.get("description", ""),
            location=d.get("location", ""),
            created_at=d.get("created_at", 0.0),
        )

    def summary_line(self) -> str:
        date_str = self.start.strftime("%m/%d(%a)")
        time_str = f"{self.start.strftime('%H:%M')}~{self.end.strftime('%H:%M')}"
        return f"[{date_str} {time_str}] {self.title}"
