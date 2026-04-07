"""JSON file-based calendar store (Phase 1).

Stores events in data/calendar/events.json.
Designed to be swapped with Google Calendar API in Phase 4.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from src.secretary.calendar.models import CalendarEvent

logger = logging.getLogger(__name__)

_STORE_PATH = Path(__file__).parents[3] / "data" / "calendar" / "events.json"


class CalendarStore:
    """CRUD operations for calendar events backed by a JSON file."""

    def __init__(self, path: Path | None = None):
        self._path = path or _STORE_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._events: dict[str, CalendarEvent] = {}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for d in raw:
                    ev = CalendarEvent.from_dict(d)
                    self._events[ev.event_id] = ev
            except Exception as e:
                logger.warning("calendar_load_error: %s", e)

    def _save(self):
        data = [ev.to_dict() for ev in self._events.values()]
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def create(self, event: CalendarEvent) -> CalendarEvent:
        event.created_at = time.time()
        self._events[event.event_id] = event
        self._save()
        return event

    def get(self, event_id: str) -> CalendarEvent | None:
        return self._events.get(event_id)

    def list_range(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        results = []
        for ev in self._events.values():
            if ev.start < end and ev.end > start:
                results.append(ev)
        results.sort(key=lambda e: e.start)
        return results

    def list_date(self, date: datetime) -> list[CalendarEvent]:
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        return self.list_range(day_start, day_end)

    def list_week(self, ref: datetime | None = None) -> list[CalendarEvent]:
        ref = ref or datetime.now()
        monday = ref - timedelta(days=ref.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=7)
        return self.list_range(monday, sunday)

    def update(self, event_id: str, **kwargs) -> CalendarEvent | None:
        ev = self._events.get(event_id)
        if not ev:
            return None
        for k, v in kwargs.items():
            if hasattr(ev, k):
                setattr(ev, k, v)
        self._save()
        return ev

    def delete(self, event_id: str) -> bool:
        if event_id in self._events:
            del self._events[event_id]
            self._save()
            return True
        return False

    def find_conflicts(self, start: datetime, end: datetime, exclude_id: str = "") -> list[CalendarEvent]:
        conflicts = []
        for ev in self._events.values():
            if ev.event_id == exclude_id:
                continue
            if ev.start < end and ev.end > start:
                conflicts.append(ev)
        return conflicts

    def all_events(self) -> list[CalendarEvent]:
        return sorted(self._events.values(), key=lambda e: e.start)
