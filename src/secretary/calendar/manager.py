"""Calendar manager — interprets natural language calendar requests via LLM.

Uses Claude Code subprocess to parse user intent into structured calendar
operations, then executes them against CalendarStore.

Note: All subprocess calls use asyncio.create_subprocess_exec with argument
lists (not shell strings), so there is no shell injection risk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta

from src.secretary.calendar.models import CalendarEvent
from src.secretary.calendar.store import CalendarStore
from src.utils.claude_code import _register_process, _unregister_process, set_session_tag

logger = logging.getLogger(__name__)

_CALENDAR_PARSE_TMPL = """\
You are a calendar JSON parser. Current time: {now}

User request: "{content}"

Parse this into a single JSON object with these fields:
- action: "create" or "query" or "update" or "delete"
- title: event title (for create/update)
- date: "YYYY-MM-DD" (convert relative dates to absolute)
- start_time: "HH:MM" (for create/update)
- end_time: "HH:MM" (for create/update, default: start_time + 1 hour)
- query_type: "today" or "tomorrow" or "this_week" or "next_week" (for query)
- target_title: title to find (for update/delete)

Respond with ONLY the JSON object, nothing else."""


class CalendarManager:
    """High-level calendar operations with natural language parsing."""

    def __init__(self, session_tag: str):
        self._session_tag = session_tag
        self._store = CalendarStore()

    async def handle(self, content: str, ws) -> None:
        """Parse calendar intent and execute action, streaming result to ws."""
        parsed = await self._parse_intent(content)
        if not parsed:
            await self._stream_reply(ws, "일정 요청을 이해하지 못했습니다. 다시 시도해주세요.")
            return

        action = parsed.get("action", "")
        try:
            if action == "create":
                await self._handle_create(parsed, ws)
            elif action == "query":
                await self._handle_query(parsed, ws)
            elif action == "update":
                await self._handle_update(parsed, ws)
            elif action == "delete":
                await self._handle_delete(parsed, ws)
            else:
                await self._stream_reply(ws, f"지원하지 않는 캘린더 작업입니다: {action}")
        except Exception as e:
            logger.warning("calendar_handle_error: %s", e)
            await self._stream_reply(ws, f"일정 처리 중 오류가 발생했습니다: {e}")

    async def _handle_create(self, parsed: dict, ws):
        date_str = parsed.get("date", "")
        start_time = parsed.get("start_time", "09:00")
        end_time = parsed.get("end_time", "")
        title = parsed.get("title", "제목 없음")

        start = datetime.fromisoformat(f"{date_str}T{start_time}")
        if end_time:
            end = datetime.fromisoformat(f"{date_str}T{end_time}")
        else:
            end = start + timedelta(hours=1)

        # Check conflicts
        conflicts = self._store.find_conflicts(start, end)
        conflict_warning = ""
        if conflicts:
            lines = [c.summary_line() for c in conflicts]
            conflict_warning = f"\n⚠️ 겹치는 일정이 있습니다:\n" + "\n".join(f"  - {l}" for l in lines) + "\n"

        event = CalendarEvent(title=title, start=start, end=end, description=parsed.get("description", ""))
        self._store.create(event)

        msg = f"📅 일정을 등록했습니다.\n{event.summary_line()}"
        if parsed.get("description"):
            msg += f"\n설명: {parsed['description']}"
        msg += conflict_warning

        await self._stream_reply(ws, msg)
        await self._send_calendar_event(ws, "created", event)

    async def _handle_query(self, parsed: dict, ws):
        query_type = parsed.get("query_type", "today")
        now = datetime.now()

        if query_type == "today":
            events = self._store.list_date(now)
            label = "오늘"
        elif query_type == "tomorrow":
            events = self._store.list_date(now + timedelta(days=1))
            label = "내일"
        elif query_type == "this_week":
            events = self._store.list_week(now)
            label = "이번 주"
        elif query_type == "next_week":
            events = self._store.list_week(now + timedelta(weeks=1))
            label = "다음 주"
        elif parsed.get("date"):
            target = datetime.fromisoformat(parsed["date"])
            events = self._store.list_date(target)
            label = target.strftime("%m월 %d일")
        else:
            events = self._store.list_week(now)
            label = "이번 주"

        if not events:
            await self._stream_reply(ws, f"📅 {label} 일정이 없습니다.")
        else:
            lines = [f"📅 {label} 일정 ({len(events)}건):"]
            for ev in events:
                lines.append(f"  • {ev.summary_line()}")
            await self._stream_reply(ws, "\n".join(lines))

    async def _handle_update(self, parsed: dict, ws):
        target_title = parsed.get("target_title", "")
        if not target_title:
            await self._stream_reply(ws, "수정할 일정을 지정해주세요.")
            return

        match = None
        for ev in self._store.all_events():
            if target_title in ev.title:
                match = ev
                break

        if not match:
            await self._stream_reply(ws, f"'{target_title}' 일정을 찾을 수 없습니다.")
            return

        updates = {}
        if parsed.get("title") and parsed["title"] != target_title:
            updates["title"] = parsed["title"]
        if parsed.get("date") and parsed.get("start_time"):
            updates["start"] = datetime.fromisoformat(f"{parsed['date']}T{parsed['start_time']}")
            end_time = parsed.get("end_time", "")
            if end_time:
                updates["end"] = datetime.fromisoformat(f"{parsed['date']}T{end_time}")
            else:
                updates["end"] = updates["start"] + timedelta(hours=1)
        elif parsed.get("start_time"):
            date_str = match.start.strftime("%Y-%m-%d")
            updates["start"] = datetime.fromisoformat(f"{date_str}T{parsed['start_time']}")
            end_time = parsed.get("end_time", "")
            if end_time:
                updates["end"] = datetime.fromisoformat(f"{date_str}T{end_time}")
            else:
                updates["end"] = updates["start"] + timedelta(hours=1)

        if not updates:
            await self._stream_reply(ws, "변경할 내용이 없습니다.")
            return

        updated = self._store.update(match.event_id, **updates)
        await self._stream_reply(ws, f"📅 일정을 수정했습니다.\n{updated.summary_line()}")
        await self._send_calendar_event(ws, "updated", updated)

    async def _handle_delete(self, parsed: dict, ws):
        target_title = parsed.get("target_title", "")
        if not target_title:
            await self._stream_reply(ws, "삭제할 일정을 지정해주세요.")
            return

        match = None
        for ev in self._store.all_events():
            if target_title in ev.title:
                match = ev
                break

        if not match:
            await self._stream_reply(ws, f"'{target_title}' 일정을 찾을 수 없습니다.")
            return

        self._store.delete(match.event_id)
        await self._stream_reply(ws, f"📅 일정을 삭제했습니다: {match.title}")
        await self._send_calendar_event(ws, "deleted", match)

    async def _parse_intent(self, content: str) -> dict | None:
        """Use Claude Code to parse natural language into structured calendar JSON.

        Uses create_subprocess_exec with argument list — safe from shell injection.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        full_prompt = _CALENDAR_PARSE_TMPL.format(now=now, content=content)

        set_session_tag(self._session_tag)
        cmd = [
            "claude", "-p", full_prompt,
            "--output-format", "json",
            "--model", "sonnet",
            "--max-turns", "1",
        ]

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp",
                start_new_session=True,
                env=env,
            )
            _register_process(proc)
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            finally:
                _unregister_process(proc)

            text = stdout.decode("utf-8", errors="replace").strip()
            result = json.loads(text)
            inner = result.get("result", "")
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, str):
                inner = inner.strip()
                # Strip markdown code fences
                if inner.startswith("```"):
                    inner = "\n".join(inner.split("\n")[1:])
                if inner.endswith("```"):
                    inner = inner.rsplit("```", 1)[0]
                inner = inner.strip()
                # Try to find JSON object in the text
                start = inner.find("{")
                end = inner.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(inner[start:end])
            return None
        except Exception as e:
            logger.warning("calendar_parse_error: %s", e)
            return None

    async def _stream_reply(self, ws, text: str):
        """Send a complete message as sec_stream events."""
        msg_id = f"cal_{int(time.time() * 1000) % 1_000_000:06d}"
        try:
            await ws.send_json({"type": "sec_stream", "data": {"token": text, "done": False}})
            await ws.send_json({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": msg_id}})
        except Exception:
            pass

    async def _send_calendar_event(self, ws, action: str, event: CalendarEvent):
        """Send sec_calendar event for frontend handling."""
        try:
            await ws.send_json({
                "type": "sec_calendar",
                "data": {"action": action, "event": event.to_dict()},
            })
        except Exception:
            pass
