"""Secretary session — WebSocket handler for AI Secretary mode.

Mirrors DiscussionSession pattern but implements a persistent chat loop
instead of a one-shot pipeline.

Message flow:
  Browser sends sec_message → classify intent → handle → stream response
  Browser sends sec_stop → clean shutdown
  Browser sends sec_cancel_task → cancel background task
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from src.secretary.calendar.manager import CalendarManager
from src.secretary.chat_engine import ChatEngine
from src.secretary.company_prep import CompanyPrep
from src.secretary.config import SecretaryConfig
from src.secretary.history_store import HistoryStore
from src.secretary.intent import (
    classify_intent,
    CHAT, INJECT_COMPANY, INJECT_DISCUSSION, CALENDAR, REPORT,
)
from src.secretary.mode_injector import ModeInjector
from src.secretary.report import ReportGenerator
from src.secretary.prompts.system import SECRETARY_SYSTEM
from src.utils.claude_code import get_pids_by_session, cleanup_specific_pids, set_session_tag

logger = logging.getLogger(__name__)


def _build_persona_prompt(persona_text: str | None) -> str | None:
    """Combine SECRETARY_SYSTEM + persona_text. Returns None if no persona."""
    if not persona_text:
        return None
    safe_persona = persona_text.replace("{", "{{").replace("}", "}}")
    return SECRETARY_SYSTEM + "\n\n## 당신의 인격\n" + safe_persona


class SecretarySession:
    """One WebSocket connection for a secretary chat session."""

    def __init__(self, ws, restore_session_id: str = "", user_id: str = ""):
        self.ws = ws
        self._cancelled = False
        self._user_id = user_id

        # Try to restore recent session or create new one
        if restore_session_id:
            self._session_id = restore_session_id
        else:
            recent = HistoryStore.find_recent_session(max_age_hours=4, user_id=user_id)
            self._session_id = recent if recent else str(uuid.uuid4())[:8]

        self._session_tag = f"sec_{self._session_id}"
        self._restored = False
        self._chat_engine = ChatEngine(
            config=SecretaryConfig(),
            session_tag=self._session_tag,
            session_id=self._session_id,
            user_id=user_id,
        )
        self._injector = ModeInjector(self._session_tag)
        self._calendar = CalendarManager(self._session_tag)
        self._reporter = ReportGenerator(self._session_tag, self._session_id)
        self._heartbeat_task: asyncio.Task | None = None

    async def run(self):
        """Main loop: restore history → init → chat loop."""
        set_session_tag(self._session_tag)

        # Restore persisted history
        restored_count = self._chat_engine.load_history()
        self._restored = restored_count > 0

        await self._send({
            "type": "sec_init",
            "data": {
                "status": "ready",
                "session_id": self._session_id,
                "restored": self._restored,
            },
        })

        # Send persisted character customization to frontend
        if self._user_id:
            from src.auth.models import UserDB
            char_json = UserDB.get().get_secretary_character(self._user_id)
            if char_json:
                import json
                await self._send({
                    "type": "sec_char_loaded",
                    "data": json.loads(char_json),
                })

        # Send restored messages to frontend for display
        if self._restored:
            await self._send_restored_history()

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            await self._chat_loop()
        finally:
            self._injector.cancel_all()
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _chat_loop(self):
        """Listen for user messages and respond."""
        while not self._cancelled:
            try:
                msg = await self.ws.receive_json()
            except Exception:
                break

            msg_type = msg.get("type")

            if msg_type == "sec_stop":
                break

            if msg_type == "sec_message":
                content = msg.get("data", {}).get("content", "").strip()
                if not content:
                    continue
                chat_mode = msg.get("data", {}).get("mode", "flash")
                await self._handle_message(content, chat_mode=chat_mode)

            elif msg_type == "sec_cancel_task":
                task_id = msg.get("data", {}).get("task_id", "")
                if task_id:
                    await self._injector.cancel_task(task_id)

            elif msg_type == "sec_disc_confirm":
                await self._handle_disc_confirm(msg.get("data", {}))

            elif msg_type == "sec_char_save":
                char_data = msg.get("data", {})
                if self._user_id:
                    import json
                    from src.auth.models import UserDB
                    UserDB.get().update_secretary_character(
                        self._user_id, json.dumps(char_data)
                    )
                await self._send({
                    "type": "sec_char_loaded",
                    "data": char_data,
                })

            elif msg_type == "sec_set_persona":
                persona_id = msg.get("data", {}).get("persona_id")
                await self._handle_set_persona(persona_id)

            elif msg_type == "sec_company_answers":
                await self._handle_company_answers(msg.get("data", {}))

    async def _handle_set_persona(self, persona_id: str | None):
        """Change secretary persona. Resets chat history."""
        persona_text = None
        name = "기본 AI 비서"
        avatar_url = ""

        if persona_id and self._user_id:
            try:
                from src.persona.models import PersonaDB
                db = PersonaDB.instance()
                persona = db.get_usable(persona_id, user_id=self._user_id)
                if persona and persona.get("persona_text"):
                    persona_text = persona["persona_text"]
                    name = persona["name"]
                    avatar_url = persona.get("avatar_url", "")
                else:
                    await self._send({
                        "type": "sec_persona_changed",
                        "data": {"name": "기본 AI 비서", "persona_id": None,
                                 "avatar_url": "", "warning": "페르소나를 찾을 수 없습니다"},
                    })
                    return
            except Exception as e:
                logger.warning("sec_set_persona_failed: %s", e)
                await self._send({
                    "type": "sec_persona_changed",
                    "data": {"name": "기본 AI 비서", "persona_id": None,
                             "avatar_url": "", "warning": "페르소나 로드 실패"},
                })
                return

        new_template = _build_persona_prompt(persona_text)
        self._chat_engine.reset(system_prompt_template=new_template)

        await self._send({
            "type": "sec_persona_changed",
            "data": {"name": name, "persona_id": persona_id,
                     "avatar_url": avatar_url, "warning": None},
        })

    async def _handle_message(self, content: str, chat_mode: str = "flash"):
        """Classify intent and route to appropriate handler."""
        intent = classify_intent(content)

        if intent == CHAT:
            await self._handle_chat(content, chat_mode=chat_mode)
        elif intent == INJECT_COMPANY:
            await self._handle_inject(content, "company")
        elif intent == INJECT_DISCUSSION:
            await self._handle_inject(content, "discussion")
        elif intent == CALENDAR:
            await self._handle_calendar(content)
        elif intent == REPORT:
            await self._handle_report(content)
        else:
            await self._handle_chat(content, chat_mode=chat_mode)

    async def _handle_chat(self, content: str, chat_mode: str = "flash"):
        """Handle normal chat — stream response from LLM."""
        # Flash: Haiku + low (no thinking) / Think: Sonnet + high
        if chat_mode == "think":
            self._chat_engine.config.model = "sonnet"
            effort = "high"
        else:
            self._chat_engine.config.model = "haiku"
            effort = "low"
        try:
            await self._chat_engine.stream_response(content, self.ws, effort=effort)
        except Exception as e:
            logger.warning("chat_handler_error: %s", e)
            await self._send({
                "type": "sec_stream",
                "data": {"token": f"죄송합니다, 오류가 발생했습니다: {e}", "done": False},
            })
            await self._send({
                "type": "sec_stream",
                "data": {"token": "", "done": True, "message_id": "err"},
            })

    async def _handle_inject(self, content: str, mode: str):
        """Inject task into AI Company or AI Discussion in the background."""
        if mode == "discussion":
            # Send setup form to frontend instead of immediate execution
            await self._send({
                "type": "sec_disc_setup",
                "data": {
                    "topic": content,
                    "style": "free",
                    "time_limit_min": 5,
                    "participants": [
                        {"name": "", "persona": ""},
                        {"name": "", "persona": ""},
                    ],
                },
            })
            return

        # AI Company — run CEO routing + question generation first
        if self._injector.active_count >= 3:
            msg = "동시 실행 가능한 태스크 수를 초과했습니다 (최대 3개). 기존 태스크가 완료된 후 다시 시도해주세요."
            await self._send({"type": "sec_stream", "data": {"token": msg, "done": False}})
            await self._send({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": "cap"}})
            return

        # Show "analyzing" indicator while CEO thinks
        await self._send({
            "type": "sec_company_prep_start",
            "data": {"task": content},
        })

        try:
            prep = CompanyPrep()
            routing = await prep.route_task(content)
            questions = await prep.generate_questions(content, routing.selected_domains)

            # Send question card to frontend
            await self._send({
                "type": "sec_company_questions",
                "data": {
                    "task": content,
                    "domains": routing.selected_domains,
                    "complexity": routing.estimated_complexity,
                    "selected_mode": routing.selected_mode,
                    "rationale": routing.rationale,
                    "questions": [
                        {"domain": q.domain, "questions": q.questions}
                        for q in questions.questions_by_domain
                    ],
                },
            })
        except Exception as e:
            logger.warning("company_prep_error: %s", e)
            # Fallback: inject immediately without clarification
            await self._send({
                "type": "sec_stream",
                "data": {"token": f"명확화 질문 생성 중 오류가 발생하여 바로 실행합니다: {e}", "done": False},
            })
            await self._send({
                "type": "sec_stream",
                "data": {"token": "", "done": True, "message_id": "prep_err"},
            })
            await self._launch_company_immediate(content)

    async def _launch_company_immediate(
        self, content: str, rationale: str = "", pre_context: dict | None = None,
        display_task: str = "",
    ):
        """Launch AI Company graph immediately (no clarification or fallback)."""
        task_id = await self._injector.inject_company(
            content, self.ws, pre_context=pre_context,
        )
        if not task_id:
            return

        # Use display_task (original task without Q&A) for UI, fall back to content
        short = display_task or content
        await self._send({
            "type": "sec_task_started",
            "data": {"task_id": task_id, "mode": "company", "description": short},
        })

        confirm = f"AI Company에 태스크를 전달했습니다: \"{short[:80]}\"\n백그라운드에서 실행 중이며, 완료되면 알려드리겠습니다."
        await self._send({"type": "sec_stream", "data": {"token": confirm, "done": False}})
        await self._send({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": f"inj_{task_id}"}})

        self._chat_engine.history.append(
            type("ChatMessage", (), {"role": "user", "content": content, "timestamp": time.time(), "message_id": ""})()
        )
        self._chat_engine.history.append(
            type("ChatMessage", (), {"role": "assistant", "content": confirm, "timestamp": time.time(), "message_id": f"inj_{task_id}"})()
        )

    async def _handle_company_answers(self, data: dict):
        """Handle user answers to Company clarification questions.

        Builds an enriched task description with Q&A context and launches Company.
        """
        task = data.get("task", "").strip()
        answers_by_domain = data.get("answers", {})  # {domain: [answer1, ...]}
        rationale = data.get("rationale", "")
        complexity = data.get("complexity", "medium")
        domains = data.get("domains", [])
        selected_mode = data.get("selected_mode", "hierarchical")

        if not task:
            await self._send({"type": "sec_stream", "data": {"token": "태스크 설명이 비어있습니다.", "done": False}})
            await self._send({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": "co_err"}})
            return

        # Build enriched task description with Q&A embedded
        enriched = task
        if answers_by_domain:
            qa_lines = ["\n\n--- 사전 확인 사항 ---"]
            questions_data = data.get("questions", {})  # {domain: [q1, q2, ...]}
            for domain, answers in answers_by_domain.items():
                domain_qs = questions_data.get(domain, [])
                for i, ans in enumerate(answers):
                    if ans.strip():
                        q_text = domain_qs[i] if i < len(domain_qs) else f"질문{i+1}"
                        qa_lines.append(f"[{domain}] Q: {q_text} → A: {ans}")
            if len(qa_lines) > 1:
                enriched = task + "\n".join(qa_lines)

        # Build pre_context for scheduled mode (includes routing results)
        pre_context = CompanyPrep.build_pre_context(
            domain_answers=answers_by_domain,
            routing_rationale=rationale,
            estimated_complexity=complexity,
            selected_domains=domains,
            selected_mode=selected_mode,
        )

        await self._launch_company_immediate(
            enriched, rationale, pre_context, display_task=task,
        )

    async def _handle_disc_confirm(self, data: dict):
        """Handle confirmed discussion settings from the setup form."""
        topic = data.get("topic", "").strip()
        style = data.get("style", "free")
        time_limit = data.get("time_limit_min", 5)
        participants_raw = data.get("participants", [])

        if not topic:
            await self._send({"type": "sec_stream", "data": {"token": "토론 주제가 비어있습니다.", "done": False}})
            await self._send({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": "disc_err"}})
            return

        if len(participants_raw) < 2:
            await self._send({"type": "sec_stream", "data": {"token": "참가자가 최소 2명 필요합니다.", "done": False}})
            await self._send({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": "disc_err"}})
            return

        if self._injector.active_count >= 3:
            await self._send({"type": "sec_stream", "data": {"token": "동시 실행 가능한 태스크 수를 초과했습니다 (최대 3개).", "done": False}})
            await self._send({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": "cap"}})
            return

        task_id = await self._injector.inject_discussion(
            topic=topic,
            ws=self.ws,
            style=style,
            time_limit_min=time_limit,
            participants=participants_raw,
        )

        if not task_id:
            return

        await self._send({
            "type": "sec_task_started",
            "data": {
                "task_id": task_id,
                "mode": "discussion",
                "description": topic,
            },
        })

        names = ", ".join(p.get("name", "?") for p in participants_raw)
        style_label = {"free": "자유토론", "debate": "찬반토론", "brainstorm": "브레인스토밍"}.get(style, style)
        confirm = (
            f"AI Discussion을 시작합니다!\n"
            f"📋 주제: {topic[:60]}\n"
            f"🎯 스타일: {style_label} | ⏱️ {time_limit}분 | 👥 {names}\n"
            f"백그라운드에서 실행 중이며, 완료되면 알려드리겠습니다."
        )
        await self._send({"type": "sec_stream", "data": {"token": confirm, "done": False}})
        await self._send({"type": "sec_stream", "data": {"token": "", "done": True, "message_id": f"inj_{task_id}"}})

        self._chat_engine.history.append(
            type("ChatMessage", (), {"role": "user", "content": f"[AI Discussion 요청] {topic}", "timestamp": time.time(), "message_id": ""})()
        )
        self._chat_engine.history.append(
            type("ChatMessage", (), {"role": "assistant", "content": confirm, "timestamp": time.time(), "message_id": f"inj_{task_id}"})()
        )

    async def _handle_calendar(self, content: str):
        """Handle calendar intent — parse and execute calendar action."""
        try:
            await self._calendar.handle(content, self.ws)
        except Exception as e:
            logger.warning("calendar_handler_error: %s", e)
            await self._send({
                "type": "sec_stream",
                "data": {"token": f"일정 처리 중 오류가 발생했습니다: {e}", "done": False},
            })
            await self._send({
                "type": "sec_stream",
                "data": {"token": "", "done": True, "message_id": "cal_err"},
            })

    async def _handle_report(self, content: str):
        """Handle report generation from chat history + task results."""
        # Collect background task summaries
        task_results = {}
        for bg in self._injector._tasks.values():
            if bg.status == "completed" and bg.result_summary:
                task_results[bg.task_id] = bg.result_summary

        try:
            await self._reporter.generate(
                content=content,
                history=self._chat_engine.history,
                task_results=task_results,
                ws=self.ws,
            )
        except Exception as e:
            logger.warning("report_handler_error: %s", e)
            await self._send({
                "type": "sec_stream",
                "data": {"token": f"리포트 생성 중 오류가 발생했습니다: {e}", "done": False},
            })
            await self._send({
                "type": "sec_stream",
                "data": {"token": "", "done": True, "message_id": "rpt_err"},
            })

    async def _send_restored_history(self):
        """Send persisted messages to frontend so UI shows previous conversation."""
        messages = []
        for msg in self._chat_engine.history:
            if msg.role == "system":
                continue
            messages.append({
                "role": msg.role,
                "content": msg.content,
                "message_id": msg.message_id,
                "timestamp": msg.timestamp,
            })
        if messages:
            await self._send({
                "type": "sec_history_restored",
                "data": {"messages": messages, "count": len(messages)},
            })

    def cancel(self):
        """Called on WebSocket disconnect."""
        self._cancelled = True
        self._injector.cancel_all()
        pids = get_pids_by_session(self._session_tag)
        if pids:
            cleanup_specific_pids(pids)

    async def _heartbeat_loop(self):
        try:
            while not self._cancelled:
                await asyncio.sleep(15)
                await self._send({"type": "heartbeat", "ts": time.time()})
        except asyncio.CancelledError:
            pass

    async def _send(self, data: dict):
        try:
            await self.ws.send_json(data)
        except Exception:
            self._cancelled = True
