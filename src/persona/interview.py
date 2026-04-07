"""WebSocket interview handler for persona workshop.

Manages an interactive interview session over WebSocket to gather
persona-building data through guided questions (3-5 turns, max 7).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import WebSocket

from src.config.settings import get_settings
from src.persona.models import PersonaDB
from src.persona.prompts import INTERVIEWER_SYSTEM, PERSONA_SYNTHESIS_SYSTEM

logger = logging.getLogger(__name__)

MAX_INTERVIEW_TURNS = 7
WS_RECEIVE_TIMEOUT = 300  # 5 minutes
UPLOAD_BASE = Path("/tmp/persona-uploads")


def _get_bridge():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


class InterviewSession:
    """Stateful WebSocket interview session for persona building."""

    def __init__(self, ws: WebSocket, *, user_id: str = "") -> None:
        self._ws = ws
        self._user_id = user_id
        self._cancelled = False
        self._turn = 0
        self._qa_history: list[dict[str, str]] = []
        self._name = ""
        self._preview_data: dict | None = None
        self._uploaded_text = ""

    async def run(self) -> None:
        """Main session loop: wait for start → interview → finish."""
        # Step 1: Wait for interview_start message
        start_msg = await self._receive()
        if not start_msg or start_msg.get("type") != "interview_start":
            await self._send({"type": "error", "data": {"message": "interview_start 메시지가 필요합니다."}})
            return

        data = start_msg.get("data", {})
        self._name = data.get("name", "").strip()
        if not self._name:
            await self._send({"type": "error", "data": {"message": "인물 이름이 필요합니다."}})
            return

        # Load preview data if token provided
        preview_token = data.get("preview_token", "")
        if preview_token and self._user_id:
            db = PersonaDB.instance()
            self._preview_data = db.get_preview(preview_token, self._user_id)

        # Load uploaded files
        file_ids = data.get("file_ids", [])
        self._uploaded_text = self._load_files(file_ids)

        # Send ready acknowledgment
        await self._send({
            "type": "interview_ready",
            "data": {"name": self._name, "max_turns": MAX_INTERVIEW_TURNS},
        })

        # Step 2: Generate first question
        await self._ask_question()

        # Step 3: Interview loop
        while self._turn < MAX_INTERVIEW_TURNS and not self._cancelled:
            msg = await self._receive()
            if not msg:
                break

            msg_type = msg.get("type", "")

            if msg_type == "interview_answer":
                answer = msg.get("data", {}).get("answer", "").strip()
                if not answer:
                    continue
                self._qa_history.append({"q": self._last_question, "a": answer})
                self._turn += 1

                # Check if we should continue or finish
                if self._turn >= MAX_INTERVIEW_TURNS:
                    await self._send({
                        "type": "interview_max_reached",
                        "data": {"message": "최대 질문 횟수에 도달했습니다. 페르소나를 합성합니다."},
                    })
                    break
                # Generate next question
                await self._ask_question()

            elif msg_type == "interview_finish":
                break

            elif msg_type == "interview_skip":
                # Skip current question, ask another
                self._turn += 1
                if self._turn < MAX_INTERVIEW_TURNS:
                    await self._ask_question()
                else:
                    break

        # Step 4: Synthesize persona
        if not self._cancelled:
            await self._synthesize_and_save()

    async def _ask_question(self) -> None:
        """Generate and send the next interview question."""
        existing = self._build_existing_data()
        system = INTERVIEWER_SYSTEM.format(existing_data=existing)

        qa_context = ""
        if self._qa_history:
            parts = []
            for qa in self._qa_history:
                parts.append(f"Q: {qa['q']}\nA: {qa['a']}")
            qa_context = f"\n\n## 이전 질문/답변\n" + "\n\n".join(parts)

        bridge = _get_bridge()
        try:
            question = await bridge.raw_query(
                system_prompt=system,
                user_message=f"인물: {self._name}\n{qa_context}\n\n다음 질문을 생성하세요.",
                model="sonnet",
                allowed_tools=[],
                timeout=30,
                effort="low",
            )
        except Exception as e:
            logger.warning("interview_question_failed: %s", e)
            question = f"{self._name}에 대해 더 알려주세요."
        finally:
            await bridge.close()

        self._last_question = question.strip()
        await self._send({
            "type": "interview_question",
            "data": {
                "question": self._last_question,
                "turn": self._turn + 1,
                "max_turns": MAX_INTERVIEW_TURNS,
            },
        })

    async def _synthesize_and_save(self) -> None:
        """Synthesize persona from all collected data and save to DB."""
        await self._send({
            "type": "interview_synthesizing",
            "data": {"message": "페르소나를 합성하고 있습니다..."},
        })

        # Build synthesis input
        data_parts = []
        if self._preview_data and self._preview_data.get("search_results"):
            data_parts.append(f"## 웹검색 결과\n{self._preview_data['search_results']}")
        if self._uploaded_text:
            data_parts.append(f"## 업로드된 자료\n{self._uploaded_text}")
        if self._qa_history:
            qa_text = "\n\n".join(f"Q: {qa['q']}\nA: {qa['a']}" for qa in self._qa_history)
            data_parts.append(f"## 인터뷰 답변\n{qa_text}")

        combined = "\n\n".join(data_parts) if data_parts else "(데이터 없음)"

        bridge = _get_bridge()
        try:
            persona_text = await bridge.raw_query(
                system_prompt=PERSONA_SYNTHESIS_SYSTEM,
                user_message=f"인물: {self._name}\n\n{combined}",
                model="sonnet",
                allowed_tools=[],
                timeout=60,
                effort="medium",
            )
        except Exception as e:
            logger.error("persona_synthesis_failed: %s", e)
            await self._send({
                "type": "error",
                "data": {"message": "페르소나 합성에 실패했습니다."},
            })
            return
        finally:
            await bridge.close()

        # Determine source — spec: "web" | "interview" | "mixed"
        has_web = bool(self._preview_data and self._preview_data.get("search_results"))
        has_interview = bool(self._qa_history)
        has_upload = bool(self._uploaded_text)
        if (has_web and has_interview) or (has_web and has_upload) or (has_interview and has_upload):
            source = "mixed"
        elif has_web:
            source = "web"
        else:
            source = "interview"

        # Generate concise card summary
        from src.persona.prompts import CARD_SUMMARY_SYSTEM
        summary = ""
        bridge2 = _get_bridge()
        try:
            summary = await bridge2.raw_query(
                system_prompt=CARD_SUMMARY_SYSTEM,
                user_message=persona_text,
                model="haiku",
                allowed_tools=[],
                timeout=15,
                effort="low",
            )
            summary = summary.strip().replace('"', '').replace("'", "")[:60]
        except Exception:
            if self._preview_data and self._preview_data.get("sufficiency"):
                summary = self._preview_data["sufficiency"].get("summary", "")[:60]
        finally:
            await bridge2.close()

        # Save to DB
        db = PersonaDB.instance()
        persona = db.create(
            user_id=self._user_id,
            name=self._name,
            summary=summary,
            persona_text=persona_text,
            source=source,
        )

        await self._send({
            "type": "persona_ready",
            "data": {"persona": persona},
        })

    def _build_existing_data(self) -> str:
        """Build summary of already-collected data for the interviewer prompt."""
        parts = []
        if self._preview_data and self._preview_data.get("sufficiency", {}).get("summary"):
            parts.append(f"웹검색 요약: {self._preview_data['sufficiency']['summary']}")
        if self._uploaded_text:
            parts.append(f"업로드 자료: {self._uploaded_text[:500]}...")
        if self._qa_history:
            for qa in self._qa_history:
                parts.append(f"Q: {qa['q']}\nA: {qa['a']}")
        return "\n".join(parts) if parts else "(없음)"

    def _load_files(self, file_ids: list[str]) -> str:
        """Load uploaded file contents by file_id list with user-scoped traversal protection."""
        texts = []
        # Files are stored at UPLOAD_BASE/{user_id}/{session_id}/{filename}
        if not self._user_id:
            return ""  # No user_id → cannot resolve user-scoped uploads
        user_dir = UPLOAD_BASE / self._user_id
        for fid in file_ids:
            try:
                parts = fid.split("/", 1)
                if len(parts) != 2:
                    continue
                session_id, filename = parts[0], Path(parts[1]).name
                fp = user_dir / session_id / filename
                # Path traversal protection — must stay within user's upload dir
                if not str(fp.resolve()).startswith(str(user_dir.resolve())):
                    continue
                if fp.exists() and fp.stat().st_size < 5_000_000:
                    texts.append(fp.read_text(encoding="utf-8", errors="replace")[:10000])
            except Exception as e:
                logger.warning("upload_read_failed: %s", e)
        return "\n\n---\n\n".join(texts) if texts else ""

    async def _send(self, msg: dict) -> None:
        """Send JSON message, catching exceptions to set _cancelled."""
        try:
            await self._ws.send_json(msg)
        except Exception:
            self._cancelled = True

    async def _receive(self) -> dict | None:
        """Receive JSON message with timeout. Returns None on error/timeout."""
        try:
            msg = await asyncio.wait_for(
                self._ws.receive_json(),
                timeout=WS_RECEIVE_TIMEOUT,
            )
            return msg
        except asyncio.TimeoutError:
            await self._send({"type": "error", "data": {"message": "세션 시간이 초과되었습니다."}})
            self._cancelled = True
            return None
        except Exception:
            self._cancelled = True
            return None
