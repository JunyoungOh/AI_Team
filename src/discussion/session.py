"""Discussion session — WebSocket runner for AI Discussion mode.

Mirrors sim_runner.SimSession but runs the discussion graph instead.

Cancellation strategy:
  1. disc_stop received → set _cancelled + cancel _graph_task immediately
  2. _run_discussion catches CancelledError → kills orphan subprocesses
  3. Always sends disc_complete so the frontend can clean up
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from src.config.settings import get_settings
from src.discussion.config import DiscussionConfig, Participant, HumanParticipant, CloneConfig
from src.discussion.graph import build_discussion_pipeline
from src.discussion.state import DiscussionState
from src.utils.claude_code import get_pids_by_session, cleanup_specific_pids, set_session_tag


class DiscussionSession:
    """One WebSocket connection for a discussion session."""

    def __init__(self, ws, user_id: str = ""):
        self.ws = ws
        self._graph_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._cancelled = False
        self._session_id: str = ""
        self._user_id: str = user_id
        self._human_input_future: asyncio.Future | None = None

    async def run(self):
        """Main loop: wait for config, run discussion graph."""
        import logging
        _log = logging.getLogger(__name__)
        _log.info("disc_session_start: user=%s", self._user_id or "(anonymous)")

        await self._send({"type": "disc_init", "data": {"status": "ready"}})

        # Heartbeat during idle
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Wait for discussion config from browser
        config = None
        config_error = ""
        while not self._cancelled:
            try:
                msg = await self.ws.receive_json()
            except Exception as recv_err:
                _log.warning("disc_receive_error: %s", recv_err)
                break
            if msg.get("type") == "disc_start":
                _log.info("disc_start_received: topic=%s, participants=%d",
                          msg.get("data", {}).get("topic", "?")[:30],
                          len(msg.get("data", {}).get("participants", [])))
                config, config_error = self._parse_config(msg.get("data", {}))
                if config:
                    _log.info("disc_config_ok: %s style=%s", config.topic[:30], config.style)
                else:
                    _log.warning("disc_config_fail: %s", config_error)
                break
            elif msg.get("type") == "disc_stop":
                break

        if not config:
            if config_error:
                # Config was received but invalid — inform frontend
                await self._send({
                    "type": "error",
                    "data": {"message": config_error},
                })
                await self._send({
                    "type": "disc_complete",
                    "data": {"session_id": "", "cancelled": True},
                })
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            return

        # Run discussion graph
        self._graph_task = asyncio.create_task(self._run_discussion(config))
        listen_task = asyncio.create_task(self._listen_for_input())

        try:
            await self._graph_task
        except asyncio.CancelledError:
            # Normal cancellation path (stop button or home navigation)
            pass
        except Exception as e:
            await self._send({"type": "error", "data": {"message": str(e)}})
        finally:
            listen_task.cancel()
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            try:
                await listen_task
            except asyncio.CancelledError:
                pass

    def _parse_config(self, data: dict) -> tuple[DiscussionConfig | None, str]:
        """Parse and validate discussion config from browser message.

        Returns (config, error_message). error_message is empty on success.
        """
        try:
            participants = []
            for i, p in enumerate(data.get("participants", [])):
                clone_data = p.get("clone_config")
                clone_config = None
                if clone_data:
                    clone_config = CloneConfig(
                        web_search=clone_data.get("web_search", True),
                        files=clone_data.get("files", []),
                    )
                persona_id_val = p.get("persona_id")
                participants.append(Participant(
                    id=f"agent_{chr(97 + i)}",
                    name=p.get("name", f"Agent {i+1}"),
                    persona=p.get("persona", "일반 참가자"),
                    color=p.get("color", f"#{hash(p.get('name', '')) % 0xFFFFFF:06x}"),
                    clone_config=clone_config,
                    persona_id=persona_id_val,
                ))
                if persona_id_val and self._user_id:
                    try:
                        from src.persona.models import PersonaDB
                        pdb = PersonaDB.instance()
                        saved = pdb.get_usable(persona_id_val, user_id=self._user_id)
                        if saved:
                            participants[-1] = participants[-1].model_copy(
                                update={"persona": saved["persona_text"], "persona_id": persona_id_val}
                            )
                        else:
                            import logging
                            logging.getLogger(__name__).warning(
                                "persona_id_invalid: %s (user=%s)", persona_id_val, self._user_id
                            )
                            name = p.get("name", "Unknown")
                            return None, f"페르소나 '{name}'을(를) 찾을 수 없습니다. 삭제되었거나 권한이 없습니다."
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).error("persona_db_error: %s", e)
                        return None, "페르소나 데이터베이스 오류가 발생했습니다."
                elif persona_id_val and not self._user_id:
                    return None, "페르소나 클로닝 모드는 로그인이 필요합니다."
            if len(participants) < 2:
                return None, "참가자가 2명 이상이어야 합니다."

            human = data.get("human_participant")
            human_participant = None
            if human:
                human_participant = HumanParticipant(
                    name=human.get("name", "사용자"),
                    persona=human.get("persona", ""),
                )

            return DiscussionConfig(
                topic=data.get("topic", ""),
                participants=participants,
                style=data.get("style", "free"),
                time_limit_min=int(data.get("time_limit_min", 15)),
                human_participant=human_participant,
            ), ""
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("parse_config_error: %s", e)
            return None, "토론 설정 파싱에 실패했습니다."

    async def _run_discussion(self, config: DiscussionConfig):
        """Execute the discussion graph and stream events."""
        import logging
        _log = logging.getLogger(__name__)

        self._session_id = str(uuid.uuid4())[:8]
        self._config = config
        set_session_tag(f"disc_{self._session_id}")

        # Checkpointer is optional — discussion runs start-to-finish, no resume.
        # If SQLite fails (e.g. Railway ephemeral FS), run without checkpointing.
        checkpointer = None
        _checkpointer_ctx = None
        try:
            from src.engine import SqliteCheckpointer
            settings = get_settings()
            db_path = settings.checkpoint_db_path
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            _checkpointer_ctx = SqliteCheckpointer(db_path)
            checkpointer = await _checkpointer_ctx.__aenter__()
            _log.info("discussion_checkpointer: sqlite OK (%s)", db_path)
        except Exception as cp_err:
            _log.warning("discussion_checkpointer_fallback: %s — running without checkpoint", cp_err)
            checkpointer = None
            _checkpointer_ctx = None

        thread_id = f"disc_{self._user_id}_{self._session_id}" if self._user_id else f"disc_{self._session_id}"
        graph_config = {"configurable": {"thread_id": thread_id, "session": self}}

        initial: DiscussionState = {
            "config": config,
            "utterances": [],
            "current_round": 0,
            "phase": "setup",
            "start_time": 0.0,
            "cancelled": False,
            "time_limit_sec": config.time_limit_min * 60,
            "moderator_instruction": "",
            "next_speaker_id": "",
            "final_report_html": "",
            "report_file_path": "",
            "session_id": self._session_id,
            "needs_search": False,
            "human_input_pending": False,
        }

        cancelled_mid_stream = False
        try:
            app = build_discussion_pipeline(checkpointer=checkpointer)
            _log.info("discussion_graph_compiled: session=%s", self._session_id)

            async for event in app.astream(initial, config=graph_config):
                if self._cancelled:
                    cancelled_mid_stream = True
                    break
                for node_name, update in event.items():
                    if not isinstance(update, dict):
                        continue
                    await self._emit_discussion_event(node_name, update)
        except asyncio.CancelledError:
            cancelled_mid_stream = True
            self._kill_session_subprocesses()
        except Exception as exc:
            cancelled_mid_stream = True
            _log.error("discussion_graph_error: %s", exc, exc_info=True)
            await self._send({
                "type": "error",
                "data": {"message": f"토론 중 오류가 발생했습니다: {exc}"},
            })
        finally:
            # Send completion FIRST — before any cleanup that might hang
            await self._send({
                "type": "disc_complete",
                "data": {
                    "session_id": self._session_id,
                    "cancelled": cancelled_mid_stream,
                },
            })
            # Clean up checkpointer (with timeout to prevent hang)
            if _checkpointer_ctx is not None:
                try:
                    await asyncio.wait_for(
                        _checkpointer_ctx.__aexit__(None, None, None),
                        timeout=5,
                    )
                except Exception:
                    pass

    def _kill_session_subprocesses(self) -> None:
        """Kill only this session's subprocesses (safe for concurrent mode).

        Uses session tagging to identify PIDs belonging to this discussion session,
        so AI Company subprocesses running concurrently are never affected.
        """
        tag = f"disc_{self._session_id}" if self._session_id else ""
        if not tag:
            return
        pids = get_pids_by_session(tag)
        if pids:
            cleanup_specific_pids(pids)

    async def _emit_discussion_event(self, node_name: str, update: dict):
        """Translate graph node updates to WebSocket events."""
        # Phase changes
        if "phase" in update:
            await self._send({
                "type": "disc_phase",
                "data": {"phase": update["phase"], "node": node_name},
            })

        # New utterances
        for u in update.get("utterances", []):
            await self._send({
                "type": "disc_utterance",
                "data": {
                    "round": u.get("round", 0),
                    "speaker_id": u.get("speaker_id", ""),
                    "speaker_name": u.get("speaker_name", ""),
                    "content": u.get("content", ""),
                    "timestamp": u.get("timestamp", time.time()),
                },
            })

        # Moderator instruction (who speaks next)
        if "next_speaker_id" in update and update["next_speaker_id"]:
            await self._send({
                "type": "disc_moderator",
                "data": {
                    "next_speaker_id": update["next_speaker_id"],
                    "instruction": update.get("moderator_instruction", ""),
                },
            })

        # Round update
        if "current_round" in update:
            await self._send({
                "type": "disc_round",
                "data": {"round": update["current_round"]},
            })

        # Final report
        if update.get("final_report_html"):
            saved_path = update.get("report_file_path", "")
            # Convert filesystem path to URL path for download
            download_url = ""
            if saved_path:
                # data/reports/disc_xxx/report.html → /reports/disc_xxx/report.html
                import re
                m = re.search(r'(reports/disc_[^/]+/report\.html)', saved_path)
                download_url = f"/{m.group(1)}" if m else ""
            await self._send({
                "type": "disc_report",
                "data": {
                    "html": update["final_report_html"],
                    "saved_path": saved_path,
                    "download_url": download_url,
                },
            })
            # Save to user's report history (24h retention)
            if self._user_id and download_url and hasattr(self, '_config'):
                try:
                    from src.auth.models import UserDB
                    config = self._config
                    UserDB.get().save_discussion_report(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        topic=config.topic,
                        participants=[p.name for p in config.participants],
                        style=config.style,
                        file_path=download_url,
                    )
                except Exception:
                    pass  # Non-critical: report display still works

    async def wait_for_human_input(self, timeout: float = 120) -> str | None:
        """Block until user sends disc_human_input. Returns None on timeout."""
        self._human_input_future = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(self._human_input_future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._human_input_future = None

    async def _listen_for_input(self):
        """Listen for stop commands and human input from browser."""
        while not self._cancelled:
            try:
                msg = await self.ws.receive_json()
                msg_type = msg.get("type")
                if msg_type == "disc_stop":
                    self._cancelled = True
                    # Cancel pending human input future so human_turn doesn't hang
                    if self._human_input_future and not self._human_input_future.done():
                        self._human_input_future.cancel()
                    if self._graph_task and not self._graph_task.done():
                        self._graph_task.cancel()
                    break
                elif msg_type == "disc_human_input":
                    content = msg.get("data", {}).get("content", "").strip()
                    if content and self._human_input_future and not self._human_input_future.done():
                        self._human_input_future.set_result(content)
            except Exception:
                break

    async def _heartbeat_loop(self):
        try:
            while not self._cancelled:
                await asyncio.sleep(15)
                await self._send({"type": "heartbeat", "ts": time.time()})
        except asyncio.CancelledError:
            pass

    def cancel(self):
        """Called on WebSocket disconnect (e.g. home navigation, tab close)."""
        self._cancelled = True
        # Cancel pending human input future so human_turn node doesn't hang
        if self._human_input_future and not self._human_input_future.done():
            self._human_input_future.cancel()
        if self._graph_task and not self._graph_task.done():
            self._graph_task.cancel()
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        # Kill any active claude subprocesses for this session
        self._kill_session_subprocesses()

    async def _send(self, data: dict):
        try:
            await self.ws.send_json(data)
        except Exception:
            self._cancelled = True
