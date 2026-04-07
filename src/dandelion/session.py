"""DandelionSession — WebSocket handler for Dandelion Foresight."""
from __future__ import annotations

import asyncio
import logging
import uuid

from src.dandelion.engine import DandelionEngine
from src.dandelion.report import generate_html_report
from src.dandelion.schemas import DandelionTree

logger = logging.getLogger(__name__)

# In-memory store: report_id → (html_str, created_date_str)
# Cleaned up daily by the server startup hook.
_report_store: dict[str, tuple[str, str]] = {}


def store_report(tree: DandelionTree) -> str:
    """Generate HTML report, store it in memory, return report_id."""
    from datetime import date

    report_id = uuid.uuid4().hex[:12]
    html = generate_html_report(tree)
    _report_store[report_id] = (html, date.today().isoformat())
    return report_id


def get_report(report_id: str) -> str | None:
    """Return stored HTML if the report exists and was created today."""
    from datetime import date

    entry = _report_store.get(report_id)
    if not entry:
        return None
    html, created_date = entry
    if created_date != date.today().isoformat():
        # Expired — clean it up
        _report_store.pop(report_id, None)
        return None
    return html


def cleanup_expired_reports():
    """Remove all reports not from today."""
    from datetime import date

    today = date.today().isoformat()
    expired = [rid for rid, (_, d) in _report_store.items() if d != today]
    for rid in expired:
        del _report_store[rid]
    if expired:
        logger.info("dandelion_report_cleanup removed=%d remaining=%d", len(expired), len(_report_store))


class DandelionSession:
    """Manages one WebSocket connection for a Dandelion session."""

    def __init__(self, ws, user_id: str = ""):
        self.ws = ws
        self._user_id = user_id
        self._engine = DandelionEngine(ws=ws)
        self._run_task: asyncio.Task | None = None
        self._pending_query: str = ""
        self._pending_files: list[str] = []
        self._last_tree: DandelionTree | None = None

    def cancel(self):
        self._engine.cancel()
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()

    async def run(self):
        """Listen for messages and drive the pipeline."""
        while True:
            msg = await self.ws.receive_json()
            msg_type = msg.get("type")

            if msg_type == "start":
                query = msg.get("query", "")
                files = msg.get("files", [])

                if not query.strip():
                    await self.ws.send_json({
                        "type": "error",
                        "message": "질문을 입력해주세요.",
                    })
                    continue

                # Store query for after clarification
                self._pending_query = query
                self._pending_files = files

                # Stage 0: Generate clarifying questions
                try:
                    questions = await self._engine.clarify(query, files)
                    if questions:
                        await self.ws.send_json({
                            "type": "clarify",
                            "questions": questions,
                        })
                        # Wait for clarify_response before proceeding
                        continue
                    else:
                        # No questions generated — proceed directly
                        await self._run_pipeline()
                except Exception as exc:
                    logger.error("clarify_failed: %s", exc)
                    # Fallback: skip clarification, run directly
                    await self._run_pipeline()

            elif msg_type == "clarify_response":
                answers = msg.get("answers", {})
                await self._run_pipeline(clarify_answers=answers)

            elif msg_type == "skip_clarify":
                # User chose to skip clarification
                await self._run_pipeline()

            elif msg_type == "export_report":
                await self._handle_export()

    async def _run_pipeline(self, clarify_answers: dict[str, str] | None = None):
        """Run the full imagination pipeline."""
        try:
            tree = await self._engine.run(
                self._pending_query,
                self._pending_files,
                clarify_answers=clarify_answers,
            )
            self._last_tree = tree
        except Exception as exc:
            logger.error("dandelion_run_failed: %s", exc)
            try:
                await self.ws.send_json({
                    "type": "error",
                    "message": f"파이프라인 실행 실패: {str(exc)}",
                })
            except Exception:
                pass

        # Reset engine for potential next run
        self._engine = DandelionEngine(ws=self.ws)
        self._pending_query = ""
        self._pending_files = []

    async def _handle_export(self):
        """Generate HTML report and send download URL to client."""
        if not self._last_tree or not self._last_tree.seeds:
            await self.ws.send_json({
                "type": "export_error",
                "message": "내보낼 결과가 없습니다. 먼저 상상을 실행해주세요.",
            })
            return

        try:
            report_id = store_report(self._last_tree)
            await self.ws.send_json({
                "type": "export_ready",
                "report_id": report_id,
                "url": f"/api/dandelion/report/{report_id}",
            })
        except Exception as exc:
            logger.error("export_failed: %s", exc)
            await self.ws.send_json({
                "type": "export_error",
                "message": f"리포트 생성 실패: {str(exc)}",
            })
