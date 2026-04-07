"""Foresight session — WebSocket handler with heartbeat + TTL watchdog."""
from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import uuid

from src.config.settings import get_settings
from src.foresight.engine import SageEngine
from src.datalab.security import (
    create_session_dir,
    cleanup_session,
    SECURITY_BANNER,
)

logger = logging.getLogger(__name__)


class ForesightSession:
    """One WebSocket connection for a Foresight session."""

    def __init__(self, ws, user_id: str = ""):
        self.ws = ws
        self._user_id = user_id
        self._session_id = f"foresight_{uuid.uuid4().hex[:12]}"
        self._cancelled = False
        self._last_activity = time.time()
        self._heartbeat_task: asyncio.Task | None = None
        self._ttl_task: asyncio.Task | None = None

        # Create ephemeral session directory
        self._base_dir = tempfile.gettempdir()
        self._session_dir = str(
            create_session_dir(self._base_dir, self._session_id)
        )

        self._uploaded_files: list[str] = []
        self._clarify_task_text: str = ""
        self._advisory_persona_ids: list[str] = []
        self._advisory_roleplay: bool = False
        self._engine = SageEngine(
            session_dir=self._session_dir,
            ws=ws,
            user_id=user_id,
        )

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def session_dir(self) -> str:
        return self._session_dir

    async def run(self):
        """Main session loop."""
        await self._send_init()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._ttl_task = asyncio.create_task(self._ttl_watchdog())
        try:
            await self._message_loop()
        finally:
            self._cleanup()

    async def _send_init(self):
        await self._send({
            "type": "foresight_init",
            "data": {
                "session_id": self._session_id,
                "security_banner": SECURITY_BANNER,
            },
        })

    async def _message_loop(self):
        while not self._cancelled:
            try:
                msg = await self.ws.receive_json()
            except Exception:
                break

            self._last_activity = time.time()
            msg_type = msg.get("type", "")

            if msg_type == "foresight_stop":
                break
            elif msg_type == "foresight_set_advisory":
                self._advisory_persona_ids = msg.get("data", {}).get("persona_ids", [])
                self._advisory_roleplay = msg.get("data", {}).get("roleplay", True)
            elif msg_type == "foresight_message":
                content = msg.get("data", {}).get("content", "")
                if content:
                    await self._engine.send_message(content)
                    await self._run_advisory_if_ready()
            elif msg_type == "foresight_file_uploaded":
                filename = msg.get("data", {}).get("filename", "")
                path = msg.get("data", {}).get("path", "")
                if filename:
                    self.register_upload(filename, path)
                    await self._engine.send_message(
                        f"[시스템] 파일 업로드 완료: {filename}\n"
                        f"지시: read_uploaded_file로 이 파일의 구조만 확인하세요. "
                        f"run_python은 절대 실행하지 마세요. "
                        f"구조를 요약한 뒤 반드시 '추가하실 데이터가 있으신가요?'라고 물어보고 멈추세요."
                    )
                    await self._run_advisory_if_ready()
            elif msg_type == "foresight_node_click":
                node_id = msg.get("data", {}).get("node_id", "")
                if node_id:
                    await self._engine.send_message(
                        f"[타임라인 노드 클릭: {node_id}] "
                        f"이 지점에 대해 상세히 설명해주세요."
                    )
                    await self._run_advisory_if_ready()
            elif msg_type == "foresight_node_question":
                node_id = msg.get("data", {}).get("node_id", "")
                question = msg.get("data", {}).get("question", "")
                if node_id and question:
                    await self._engine.send_message(
                        f"[{node_id} 관련 질문] {question}"
                    )
                    await self._run_advisory_if_ready()
            elif msg_type == "foresight_clarify_request":
                task_text = msg.get("data", {}).get("task", "")
                if task_text:
                    self._clarify_task_text = task_text
                    await self._engine.send_message(
                        f"[명확화 질문 생성 요청]\n\n"
                        f"사용자의 예측 요청: {task_text}\n\n"
                        f"지시: generate_clarifications 도구를 사용하여 이 예측 요청의 "
                        f"범위, 시간축, 핵심 지표, 전제 조건 등을 확인하는 "
                        f"명확화 질문 2~5개를 생성하세요."
                    )
            elif msg_type == "foresight_clarification_response":
                answers = msg.get("data", {}).get("answers", {})
                task_text = self._clarify_task_text
                answer_lines = []
                for qid, answer in answers.items():
                    answer_lines.append(f"- {qid}: {answer}")
                clarification_context = "\n".join(answer_lines)
                await self._engine.send_message(
                    f"[태스크 분석 요청] {task_text}\n\n"
                    f"[명확화 답변]\n{clarification_context}\n\n"
                    f"지시: 위 예측 요청과 명확화 답변을 종합적으로 검토하여, "
                    f"analyze_requirements 도구를 사용해 이 예측에 필요한 "
                    f"사전 정보 항목을 분석하세요."
                )
                await self._run_advisory_if_ready()
            elif msg_type == "foresight_requirements_submit":
                data = msg.get("data", {})
                task_text = data.get("task", "")
                items = data.get("items", [])
                if task_text and items:
                    await self._handle_requirements_submit(task_text, items)
                    await self._run_advisory_if_ready()
            elif msg_type == "foresight_analysis_select":
                # 단일 모드: 시나리오 분기 예측만 지원
                await self._engine.send_message(
                    "[분석 실행] 시나리오 분기 예측을 시작합니다.\n\n"
                    "지시: run_ensemble_forecast 도구를 사용하여 앙상블 예측을 실행하세요. "
                    "환경 프로필에서 가장 중요한 예측 질문을 1-3개 도출하고, "
                    "각 질문에 대해 run_ensemble_forecast를 호출하세요. "
                    "앙상블 결과의 보정된 확률을 emit_timeline으로 시각화하세요."
                )
                await self._run_advisory_if_ready()

    async def _handle_requirements_submit(self, task_text: str, items: list, **_kw) -> None:
        """Process submitted requirements and start prediction."""

        parts = [f"[예측 태스크] {task_text}\n"]
        web_search_items = []

        for item in items:
            item_id = item.get("id", "")
            method = item.get("method", "web_search")
            label = item.get("label", item_id)

            if method == "text":
                content = item.get("content", "")
                parts.append(f"[사용자 제공 — {label}] {content}")
                await self._send({
                    "type": "foresight_requirement_status",
                    "data": {"id": item_id, "status": "done"},
                })
            elif method == "file":
                filename = item.get("filename", "")
                parts.append(f"[파일 첨부 — {label}] 파일명: {filename}")
                await self._send({
                    "type": "foresight_requirement_status",
                    "data": {"id": item_id, "status": "done"},
                })
            elif method == "web_search":
                web_search_items.append({"id": item_id, "label": label})
                await self._send({
                    "type": "foresight_requirement_status",
                    "data": {"id": item_id, "status": "searching"},
                })

        if web_search_items:
            search_directives = "\n".join(
                f"- {ws['label']}" for ws in web_search_items
            )
            parts.append(
                f"\n[웹검색 필요 항목]\n{search_directives}\n\n"
                f"지시: 위 웹검색 항목들을 web_search로 조사하세요. "
                f"각 항목의 검색이 완료될 때마다 emit_requirement_status 도구로 "
                f"해당 항목의 완료를 알려주세요 (item_id와 status='done'). "
                f"모든 정보가 수집되면 환경 프로필을 자동으로 생성하고 "
                f"compress_environment를 호출하여 타임라인 예측을 시작하세요."
            )
        else:
            # No web searches needed — all data provided by user
            parts.append(
                "\n지시: 사용자가 모든 정보를 직접 제공했습니다. "
                "위 데이터를 기반으로 환경 프로필을 생성하고 "
                "compress_environment를 호출하여 타임라인 예측을 시작하세요."
            )

        prompt = "\n\n".join(parts)
        await self._engine.send_message(prompt)

    async def _run_advisory_if_ready(self):
        """Check if report was generated and run advisory."""
        if not self._advisory_persona_ids:
            return
        if not hasattr(self._engine, '_last_report_filename') or not self._engine._last_report_filename:
            return

        filename = self._engine._last_report_filename
        self._engine._last_report_filename = None

        from pathlib import Path
        report_path = Path(self._session_dir) / filename
        if not report_path.exists():
            return
        report_text = report_path.read_text(encoding="utf-8")

        from src.persona.advisory import generate_advisory_comments, generate_scenario_roleplay

        comments = await generate_advisory_comments(
            report_text=report_text,
            persona_ids=self._advisory_persona_ids,
            user_id=self._user_id,
        )
        if comments:
            await self._send({
                "type": "foresight_advisory_comments",
                "data": {"comments": comments},
            })

        if self._advisory_roleplay:
            roleplay = await generate_scenario_roleplay(
                report_text=report_text,
                persona_ids=self._advisory_persona_ids,
                user_id=self._user_id,
            )
            if roleplay:
                await self._send({
                    "type": "foresight_roleplay",
                    "data": {"roleplay": roleplay},
                })

    def cancel(self):
        """Called on WebSocket disconnect."""
        self._cancelled = True
        self._engine.cancel()
        self._cleanup()

    def _cleanup(self):
        """Zero-Retention: delete all session data."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ttl_task:
            self._ttl_task.cancel()
        cleanup_session(self._base_dir, self._session_id)
        logger.info(f"Foresight session {self._session_id} cleaned up")

    async def _heartbeat_loop(self):
        try:
            while not self._cancelled:
                await asyncio.sleep(15)
                await self._send({"type": "heartbeat", "ts": time.time()})
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _ttl_watchdog(self):
        """Auto-cleanup if no activity for TTL period."""
        settings = get_settings()
        ttl_seconds = settings.foresight_session_ttl_minutes * 60
        try:
            while not self._cancelled:
                await asyncio.sleep(60)
                if time.time() - self._last_activity > ttl_seconds:
                    logger.info(f"Foresight session {self._session_id} TTL expired")
                    self._cancelled = True
                    try:
                        await self._send({
                            "type": "foresight_complete",
                            "data": {"message": "세션이 비활성으로 종료되었습니다. 모든 데이터가 삭제되었습니다."},
                        })
                    except Exception:
                        pass
                    break
        except asyncio.CancelledError:
            pass

    async def _send(self, data: dict):
        try:
            await self.ws.send_json(data)
        except Exception:
            self._cancelled = True

    def register_upload(self, filename: str, path: str) -> None:
        """Track an uploaded file (called from server upload endpoint)."""
        self._uploaded_files.append(filename)
        logger.info(
            "Foresight session %s: file registered — %s (%s)",
            self._session_id, filename, path,
        )
