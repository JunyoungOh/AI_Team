"""Mode injector — runs AI Company / AI Discussion graphs in the background.

Each injected task runs as an independent asyncio.Task with its own
session tag, so it can be cancelled without affecting the Secretary
chat loop or other background tasks.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from src.config.settings import get_settings
from src.utils.claude_code import set_session_tag, get_pids_by_session, cleanup_specific_pids

logger = logging.getLogger(__name__)

MAX_CONCURRENT_TASKS = 3

# Global registry so Discussion WS can find running injected sessions
_active_injectors: dict[str, "ModeInjector"] = {}


def get_injected_task(task_id: str) -> "BackgroundTask | None":
    """Find a running injected task by ID across all injectors."""
    for injector in _active_injectors.values():
        bg = injector.get_task(task_id)
        if bg:
            return bg
    return None


def get_injector_for_task(task_id: str) -> "ModeInjector | None":
    """Find the injector that owns a given task."""
    for injector in _active_injectors.values():
        if task_id in injector._tasks:
            return injector
    return None


class BackgroundTask:
    """Tracks a single background Company/Discussion execution."""

    __slots__ = (
        "task_id", "mode", "description", "status",
        "progress", "result_summary", "report_path",
        "started_at", "asyncio_task", "session_tag",
        "disc_events", "disc_config", "disc_subscribers",
        "company_events", "company_subscribers",
    )

    def __init__(self, task_id: str, mode: str, description: str, session_tag: str):
        self.task_id = task_id
        self.mode = mode
        self.description = description
        self.status = "running"
        self.progress = 0.0
        self.result_summary = ""
        self.report_path = ""
        self.started_at = time.time()
        self.asyncio_task: asyncio.Task | None = None
        self.session_tag = session_tag
        # Discussion event store + live subscribers
        self.disc_events: list[dict] = []
        self.disc_config: dict | None = None
        self.disc_subscribers: list = []  # list of WebSocket connections
        # Company event store + live subscribers
        self.company_events: list[dict] = []
        self.company_subscribers: list = []  # list of WebSocket connections


class ModeInjector:
    """Manages background AI Company / AI Discussion task executions."""

    def __init__(self, parent_session_tag: str):
        self._parent_tag = parent_session_tag
        self._tasks: dict[str, BackgroundTask] = {}
        _active_injectors[parent_session_tag] = self

    def cleanup(self):
        """Remove from global registry."""
        _active_injectors.pop(self._parent_tag, None)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == "running")

    async def inject_company(
        self,
        description: str,
        ws,
        pre_context: dict | None = None,
    ) -> str | None:
        """Launch an AI Company graph in the background.

        Args:
            description: Task description.
            ws: WebSocket connection.
            pre_context: If provided, run in scheduled mode (skip questioning).
                         Should contain domain_answers from Secretary clarification.

        Returns task_id on success, None if at capacity.
        """
        if self.active_count >= MAX_CONCURRENT_TASKS:
            return None

        task_id = f"inj_{uuid.uuid4().hex[:6]}"
        session_tag = f"sec_company_{task_id}"
        bg = BackgroundTask(task_id, "company", description, session_tag)
        _pre_context = pre_context

        async def _run():
            try:
                set_session_tag(session_tag)
                result = await self._run_company_graph(
                    description, task_id, ws, pre_context=_pre_context,
                )
                bg.status = "completed"
                bg.progress = 1.0
                bg.result_summary = result.get("summary", "")
                bg.report_path = result.get("report_path", "")

                await ws.send_json({
                    "type": "sec_task_complete",
                    "data": {
                        "task_id": task_id,
                        "summary": bg.result_summary,
                        "report_path": bg.report_path,
                    },
                })
            except asyncio.CancelledError:
                bg.status = "cancelled"
                self._kill_task_processes(session_tag)
            except Exception as e:
                logger.warning("inject_company_failed", task_id=task_id, error=str(e))
                bg.status = "failed"
                try:
                    await ws.send_json({
                        "type": "sec_task_complete",
                        "data": {
                            "task_id": task_id,
                            "summary": f"실행 실패: {e}",
                            "report_path": "",
                        },
                    })
                except Exception:
                    pass

        bg.asyncio_task = asyncio.create_task(_run())
        self._tasks[task_id] = bg
        return task_id

    async def inject_discussion(
        self,
        topic: str,
        ws,
        style: str = "debate",
        time_limit_min: int = 5,
        participants: list[dict] | None = None,
    ) -> str | None:
        """Launch an AI Discussion graph in the background.

        Args:
            topic: Discussion topic.
            ws: WebSocket connection.
            style: "free" | "debate" | "brainstorm".
            time_limit_min: Time limit in minutes.
            participants: List of {"name": str, "persona": str} dicts.

        Returns task_id on success, None if at capacity.
        """
        if self.active_count >= MAX_CONCURRENT_TASKS:
            return None

        task_id = f"inj_{uuid.uuid4().hex[:6]}"
        session_tag = f"sec_disc_{task_id}"
        bg = BackgroundTask(task_id, "discussion", topic, session_tag)

        _style = style
        _time = time_limit_min
        _parts = participants

        async def _run():
            try:
                set_session_tag(session_tag)
                result = await self._run_discussion_graph(
                    topic, task_id, ws,
                    style=_style,
                    time_limit_min=_time,
                    participants=_parts,
                )
                bg.status = "completed"
                bg.progress = 1.0
                bg.result_summary = result.get("summary", "")
                bg.report_path = result.get("report_path", "")

                await ws.send_json({
                    "type": "sec_task_complete",
                    "data": {
                        "task_id": task_id,
                        "summary": bg.result_summary,
                        "report_path": bg.report_path,
                    },
                })
            except asyncio.CancelledError:
                bg.status = "cancelled"
                self._kill_task_processes(session_tag)
            except Exception as e:
                logger.warning("inject_discussion_failed", task_id=task_id, error=str(e))
                bg.status = "failed"
                try:
                    await ws.send_json({
                        "type": "sec_task_complete",
                        "data": {
                            "task_id": task_id,
                            "summary": f"실행 실패: {e}",
                            "report_path": "",
                        },
                    })
                except Exception:
                    pass

        bg.asyncio_task = asyncio.create_task(_run())
        self._tasks[task_id] = bg
        return task_id

    async def cancel_task(self, task_id: str):
        """Cancel a running background task."""
        bg = self._tasks.get(task_id)
        if bg and bg.status == "running" and bg.asyncio_task:
            bg.asyncio_task.cancel()
            self._kill_task_processes(bg.session_tag)

    def cancel_all(self):
        """Cancel all running tasks (called on session disconnect)."""
        for bg in self._tasks.values():
            if bg.status == "running" and bg.asyncio_task:
                bg.asyncio_task.cancel()
                self._kill_task_processes(bg.session_tag)

    def get_task(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    async def subscribe_disc(self, task_id: str, ws) -> BackgroundTask | None:
        """Subscribe a WebSocket to a running discussion's live events.

        Replays all stored events first, then adds ws to live subscribers.
        Returns the BackgroundTask or None if not found.
        """
        bg = self._tasks.get(task_id)
        if not bg or bg.mode != "discussion":
            return None
        # Replay stored events
        for ev in bg.disc_events:
            try:
                await ws.send_json(ev)
            except Exception:
                return None
        # Register for live events
        bg.disc_subscribers.append(ws)
        return bg

    def unsubscribe_disc(self, task_id: str, ws):
        """Remove a WebSocket from live event subscribers."""
        bg = self._tasks.get(task_id)
        if bg and ws in bg.disc_subscribers:
            bg.disc_subscribers.remove(ws)

    async def subscribe_company(self, task_id: str, ws) -> BackgroundTask | None:
        """Subscribe a WebSocket to a running Company task's live events.

        Replays all stored events first, then adds ws to live subscribers.
        Returns the BackgroundTask or None if not found.
        """
        bg = self._tasks.get(task_id)
        if not bg or bg.mode != "company":
            return None
        # Replay stored events
        for ev in bg.company_events:
            try:
                await ws.send_json(ev)
            except Exception:
                return None
        # Register for live events
        bg.company_subscribers.append(ws)
        return bg

    def unsubscribe_company(self, task_id: str, ws):
        """Remove a WebSocket from Company event subscribers."""
        bg = self._tasks.get(task_id)
        if bg and ws in bg.company_subscribers:
            bg.company_subscribers.remove(ws)

    # ── Private: Graph execution ──────────────────────

    async def _run_company_graph(
        self, task: str, task_id: str, ws, pre_context: dict | None = None,
    ) -> dict:
        """Execute AI Company graph and extract results.

        If pre_context is provided, runs in 'scheduled' mode (skips questioning).
        Uses EventBridge to generate rich UI events for Company observer mode.
        """
        import math
        from dotenv import load_dotenv
        load_dotenv()

        from src.engine import SqliteCheckpointer
        from src.graphs.main_graph import build_pipeline
        from src.models.state import create_initial_state
        from src.ui.event_bridge import EventBridge
        from src.utils.execution_tracker import reset_exec_tracker
        from src.utils.progress import (
            get_tracker, get_step_tracker, compute_progress,
            NODE_LABELS, _NODE_TAU, _MAX_SIMULATED,
        )

        settings = get_settings()
        db_path = settings.checkpoint_db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        thread_id = f"sec_co_{task_id}"
        config = {"configurable": {"thread_id": thread_id}}
        reset_exec_tracker()

        # If pre_context given, use scheduled mode to skip questioning
        exec_mode = "scheduled" if pre_context else "interactive"

        bg = self._tasks.get(task_id)
        bridge = EventBridge()
        progress_poll_task: asyncio.Task | None = None
        step_poll_task: asyncio.Task | None = None

        async def _emit_company_event(ev: dict):
            """Store event and forward to all live Company subscribers."""
            if bg:
                bg.company_events.append(ev)
                dead = []
                for sub_ws in bg.company_subscribers:
                    try:
                        await sub_ws.send_json(ev)
                    except Exception:
                        dead.append(sub_ws)
                for d in dead:
                    bg.company_subscribers.remove(d)

        async def _poll_worker_progress():
            """Poll worker progress and emit events."""
            tracker = get_tracker(thread_id)
            try:
                # Wait for tracker activation
                for _ in range(240):
                    if tracker.is_active:
                        break
                    await asyncio.sleep(0.5)
                if not tracker.is_active:
                    return
                while tracker.is_active:
                    workers, elapsed = tracker.snapshot()
                    now = time.time()
                    for w in workers:
                        progress = compute_progress(w, now)
                        await _emit_company_event({
                            "type": "progress",
                            "ts": now,
                            "data": {
                                "character": w.domain,
                                "worker_id": w.worker_id or w.domain,
                                "progress": round(progress, 3),
                                "tier": w.tier,
                                "status": w.status.value,
                                "summary": w.summary,
                            },
                        })
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                pass

        async def _poll_step_progress(node_name: str, label: str, tau: float):
            """Poll step-level progress for long-running non-worker nodes."""
            start = time.time()
            try:
                while True:
                    elapsed = time.time() - start
                    progress = min(_MAX_SIMULATED, 1.0 - math.exp(-elapsed / tau))
                    await _emit_company_event({
                        "type": "step_progress",
                        "ts": time.time(),
                        "data": {
                            "node": node_name,
                            "label": label,
                            "progress": round(progress, 3),
                        },
                    })
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                await _emit_company_event({
                    "type": "step_progress",
                    "ts": time.time(),
                    "data": {"node": node_name, "label": label, "progress": 1.0},
                })

        # Store init event so observers get layout + characters on connect
        init_ev = {
            "type": "init",
            "data": {
                "layout": bridge.floor.get_layout(),
                "characters": bridge.characters.get_all_active(),
            },
        }
        await _emit_company_event(init_ev)

        get_step_tracker(thread_id).start_pipeline()

        async with SqliteCheckpointer(db_path) as checkpointer:
            app = build_pipeline(checkpointer=checkpointer)
            initial = create_initial_state(
                task,
                session_id=thread_id,
                execution_mode=exec_mode,
                pre_context=pre_context,
            )

            last_phase = ""
            async for event in app.astream(initial, config=config):
                for node_name, update in event.items():
                    if not isinstance(update, dict):
                        continue

                    # Stop step progress polling when non-worker node completes
                    if node_name in ("ceo_final_report", "worker_result_revision"):
                        if step_poll_task and not step_poll_task.done():
                            step_poll_task.cancel()
                            try:
                                await step_poll_task
                            except asyncio.CancelledError:
                                pass

                    # Start worker progress polling after leader decomposition or mode dispatch
                    phase = update.get("phase", "")
                    _MODE_SETUP_PHASES = {
                        "roundtable_setup", "adversarial_setup",
                        "workshop_setup", "relay_setup",
                    }
                    if node_name == "leader_task_decomposition" and phase == "leader_task_decomposition":
                        if progress_poll_task is None or progress_poll_task.done():
                            progress_poll_task = asyncio.create_task(_poll_worker_progress())
                    elif node_name == "mode_dispatch" and phase in _MODE_SETUP_PHASES:
                        if progress_poll_task is None or progress_poll_task.done():
                            progress_poll_task = asyncio.create_task(_poll_worker_progress())
                    elif node_name == "select_research_mode":
                        # breadth/deep research: tracker is started inside the node
                        if progress_poll_task is None or progress_poll_task.done():
                            progress_poll_task = asyncio.create_task(_poll_worker_progress())

                    # Translate via EventBridge (scene_change, hierarchy, char_spawn, message)
                    sim_events = bridge.translate(node_name, update)
                    for se in sim_events:
                        await _emit_company_event(se.to_dict())

                    # After mode execution nodes: stop polling, announce next step
                    _MODE_EXEC_NODES = {
                        "roundtable_execution", "adversarial_execution",
                        "workshop_execution", "relay_execution",
                    }
                    if node_name in _MODE_EXEC_NODES:
                        tracker = get_tracker(thread_id)
                        tracker.stop()
                        if progress_poll_task and not progress_poll_task.done():
                            progress_poll_task.cancel()
                        bridge._step += 1
                        bridge._current_node = "domain_analyst"
                        label = NODE_LABELS.get("domain_analyst", "domain_analyst")
                        await _emit_company_event({
                            "type": "scene_change",
                            "ts": time.time(),
                            "data": {"node": "domain_analyst", "label": label, "step": bridge._step},
                        })

                    # After worker_execution: announce ceo_final_report step
                    if node_name == "worker_execution":
                        tracker = get_tracker(thread_id)
                        tracker.stop()
                        if progress_poll_task and not progress_poll_task.done():
                            progress_poll_task.cancel()
                        # Announce next step
                        bridge._step += 1
                        bridge._current_node = "ceo_final_report"
                        label = NODE_LABELS.get("ceo_final_report", "ceo_final_report")
                        await _emit_company_event({
                            "type": "scene_change",
                            "ts": time.time(),
                            "data": {"node": "ceo_final_report", "label": label, "step": bridge._step},
                        })
                        tau = _NODE_TAU.get("ceo_final_report", 30)
                        if tau > 0:
                            if step_poll_task and not step_poll_task.done():
                                step_poll_task.cancel()
                            step_poll_task = asyncio.create_task(
                                _poll_step_progress("ceo_final_report", label, tau)
                            )

                    # Send phase progress to Secretary WS
                    if phase and phase != last_phase:
                        last_phase = phase
                        progress_val = self._phase_to_progress(phase)
                        if bg:
                            bg.progress = progress_val
                        try:
                            await ws.send_json({
                                "type": "sec_task_progress",
                                "data": {
                                    "task_id": task_id,
                                    "progress": progress_val,
                                    "status": phase,
                                },
                            })
                        except Exception:
                            pass

            # Cleanup polling tasks
            for t in (progress_poll_task, step_poll_task):
                if t and not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

            # Extract final results
            snapshot = await app.aget_state(config)
            values = snapshot.values if snapshot else {}
            report_path = values.get("report_file_path", "")
            summary = ""
            final_report = values.get("final_report", {})
            if isinstance(final_report, dict):
                summary = final_report.get("executive_summary", "")
            if not summary:
                summary = str(values.get("user_task", ""))[:500]

        # Emit completion event
        await _emit_company_event({
            "type": "complete",
            "data": {"session_id": thread_id, "report_path": report_path},
        })

        return {"summary": summary, "report_path": report_path}

    async def _run_discussion_graph(
        self,
        topic: str,
        task_id: str,
        ws,
        style: str = "debate",
        time_limit_min: int = 5,
        participants: list[dict] | None = None,
    ) -> dict:
        """Execute AI Discussion graph and extract results."""
        from src.engine import SqliteCheckpointer
        from src.discussion.config import DiscussionConfig, Participant
        from src.discussion.graph import build_discussion_pipeline
        from src.discussion.state import DiscussionState

        settings = get_settings()
        db_path = settings.checkpoint_db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Build participants from provided config or use defaults
        if participants:
            _ids = "abcdefgh"
            disc_participants = [
                Participant(
                    id=f"agent_{_ids[i]}",
                    name=p.get("name", f"참가자{i+1}"),
                    persona=p.get("persona", ""),
                )
                for i, p in enumerate(participants)
            ]
        else:
            disc_participants = [
                Participant(id="agent_a", name="찬성파", persona="주제에 긍정적인 관점"),
                Participant(id="agent_b", name="반대파", persona="주제에 비판적인 관점"),
                Participant(id="agent_c", name="중립파", persona="균형 잡힌 중립적 관점"),
            ]
        disc_config = DiscussionConfig(
            topic=topic,
            participants=disc_participants,
            style=style,
            time_limit_min=time_limit_min,
        )

        thread_id = f"sec_disc_{task_id}"
        config = {"configurable": {"thread_id": thread_id}}

        initial: DiscussionState = {
            "config": disc_config,
            "utterances": [],
            "current_round": 0,
            "phase": "setup",
            "start_time": 0.0,
            "cancelled": False,
            "time_limit_sec": disc_config.time_limit_min * 60,
            "moderator_instruction": "",
            "next_speaker_id": "",
            "final_report_html": "",
            "report_file_path": "",
            "session_id": task_id,
        }

        # Store config in BackgroundTask for subscribers
        bg = self._tasks.get(task_id)
        if bg:
            COLORS = ['#4A90D9', '#E94560', '#2ECC71', '#9B59B6', '#F39C12', '#1ABC9C']
            bg.disc_config = {
                "topic": topic,
                "style": style,
                "time_limit_min": time_limit_min,
                "started_at": time.time(),
                "participants": [
                    {"id": p.id, "name": p.name, "persona": p.persona, "color": COLORS[i % len(COLORS)]}
                    for i, p in enumerate(disc_participants)
                ],
            }
            # Send disc_config as first stored event
            config_ev = {"type": "disc_config", "data": bg.disc_config}
            bg.disc_events.append(config_ev)

        async def _emit_disc_event(ev: dict):
            """Store event and forward to all live subscribers."""
            if bg:
                bg.disc_events.append(ev)
                dead = []
                for sub_ws in bg.disc_subscribers:
                    try:
                        await sub_ws.send_json(ev)
                    except Exception:
                        dead.append(sub_ws)
                for d in dead:
                    bg.disc_subscribers.remove(d)

        async with SqliteCheckpointer(db_path) as checkpointer:
            app = build_discussion_pipeline(checkpointer=checkpointer)

            last_phase = ""
            async for event in app.astream(initial, config=config):
                for node_name, update in event.items():
                    if not isinstance(update, dict):
                        continue

                    # Phase changes
                    phase = update.get("phase", "")
                    if phase and phase != last_phase:
                        last_phase = phase
                        progress = {"setup": 0.1, "opening_speak": 0.2, "discussing": 0.5, "closing": 0.8, "done": 1.0}.get(phase, 0.3)
                        if bg:
                            bg.progress = progress
                        try:
                            await ws.send_json({
                                "type": "sec_task_progress",
                                "data": {
                                    "task_id": task_id,
                                    "progress": progress,
                                    "status": phase,
                                },
                            })
                        except Exception:
                            pass
                        await _emit_disc_event({"type": "disc_phase", "data": {"phase": phase, "node": node_name}})

                    # New utterances
                    for u in update.get("utterances", []):
                        await _emit_disc_event({
                            "type": "disc_utterance",
                            "data": {
                                "round": u.get("round", 0),
                                "speaker_id": u.get("speaker_id", ""),
                                "speaker_name": u.get("speaker_name", ""),
                                "content": u.get("content", ""),
                                "timestamp": u.get("timestamp", time.time()),
                            },
                        })

                    # Moderator instruction
                    if "next_speaker_id" in update and update["next_speaker_id"]:
                        await _emit_disc_event({
                            "type": "disc_moderator",
                            "data": {
                                "next_speaker_id": update["next_speaker_id"],
                                "instruction": update.get("moderator_instruction", ""),
                            },
                        })

                    # Round update
                    if "current_round" in update:
                        await _emit_disc_event({
                            "type": "disc_round",
                            "data": {"round": update["current_round"]},
                        })

                    # Final report
                    if update.get("final_report_html"):
                        await _emit_disc_event({
                            "type": "disc_report",
                            "data": {
                                "html": update["final_report_html"],
                                "saved_path": update.get("report_file_path", ""),
                            },
                        })

            # Extract results
            snapshot = await app.aget_state(config)
            values = snapshot.values if snapshot else {}
            report_html = values.get("final_report_html", "")
            report_path = values.get("report_file_path", "")

            # Generate summary from utterances
            utterances = values.get("utterances", [])
            n_parts = len(disc_participants)
            summary = f"토론 주제: {topic}\n참가자 {n_parts}명, {len(utterances)}개 발언"
            if report_html:
                summary += "\nHTML 리포트가 생성되었습니다."

        # Emit completion to subscribers
        await _emit_disc_event({
            "type": "disc_complete",
            "data": {"session_id": task_id, "cancelled": False},
        })

        return {"summary": summary, "report_path": report_path}

    @staticmethod
    def _phase_to_progress(phase: str) -> float:
        """Map Company graph phase to 0.0-1.0 progress."""
        mapping = {
            "intake": 0.05,
            "routing": 0.1,
            "questioning": 0.15,
            "planning": 0.25,
            "decomposition": 0.35,
            "execution": 0.5,
            "worker_execution": 0.6,
            "review": 0.8,
            "reporting": 0.9,
            "complete": 1.0,
        }
        return mapping.get(phase, 0.3)

    @staticmethod
    def _kill_task_processes(session_tag: str):
        pids = get_pids_by_session(session_tag)
        if pids:
            cleanup_specific_pids(pids)
