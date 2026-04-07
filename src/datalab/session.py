"""DataLab session — 1-shot WebSocket handler with Zero-Retention cleanup."""
from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import uuid

from src.config.settings import get_settings
from src.datalab.engine import JarvisEngine
from src.datalab.security import (
    create_session_dir,
    cleanup_session,
    SECURITY_BANNER,
)

logger = logging.getLogger(__name__)


class DataLabSession:
    """One WebSocket connection for a DataLab session."""

    def __init__(self, ws, user_id: str = ""):
        self.ws = ws
        self._user_id = user_id
        self._session_id = f"datalab_{uuid.uuid4().hex[:12]}"
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
        self._engine = JarvisEngine(
            session_dir=self._session_dir,
            ws=ws,
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
            "type": "datalab_init",
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

            if msg_type == "datalab_stop":
                break
            elif msg_type == "datalab_message":
                content = msg.get("data", {}).get("content", "")
                if content:
                    await self._engine.send_message(content)
            elif msg_type == "datalab_upload":
                filename = msg.get("data", {}).get("filename", "")
                if filename:
                    self._uploaded_files.append(filename)
                    await self._engine.send_message(
                        f"[시스템] 파일 업로드 완료: {filename}\n"
                        f"지시: read_uploaded_data로 이 파일의 구조만 확인하세요. "
                        f"run_python은 절대 실행하지 마세요. "
                        f"구조를 요약한 뒤 반드시 '추가하실 데이터가 있으신가요?'라고 물어보고 멈추세요."
                    )
            elif msg_type == "datalab_analyze":
                # 실제 업로드된 파일 목록을 JARVIS에게 전달
                from pathlib import Path as _P
                uploads_path = _P(self._session_dir) / "uploads"
                actual_files = []
                if uploads_path.exists():
                    actual_files = [f.name for f in uploads_path.iterdir() if f.is_file()]
                file_list = "\n".join(f"  - {f}" for f in actual_files) if actual_files else "  (없음)"
                await self._engine.send_message(
                    "[시스템] 인터랙티브 HTML 대시보드를 생성하세요.\n\n"
                    f"업로드된 파일:\n{file_list}\n\n"
                    "지시사항:\n"
                    "- 이미 read_uploaded_data로 파악한 데이터 구조/미리보기를 기반으로 대시보드 생성\n"
                    "- run_python 호출 금지. export_file 호출 금지. 도구 호출 없이 바로 HTML 작성\n"
                    "- ```html 코드 블록 안에 Chart.js CDN + 인라인 데이터로 완전한 대시보드 HTML 작성\n"
                    "- 프론트엔드가 ```html 블록을 자동으로 대시보드로 렌더링합니다"
                )

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
        logger.info(f"DataLab session {self._session_id} cleaned up")

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
        ttl_seconds = settings.datalab_session_ttl_minutes * 60
        try:
            while not self._cancelled:
                await asyncio.sleep(60)
                if time.time() - self._last_activity > ttl_seconds:
                    logger.info(f"DataLab session {self._session_id} TTL expired")
                    self._cancelled = True
                    try:
                        await self._send({
                            "type": "datalab_complete",
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
