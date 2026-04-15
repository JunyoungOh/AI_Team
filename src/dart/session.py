"""DartSession — one WebSocket connection to the DART (전자공시) mode."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from src.config.settings import get_settings
from src.dart.engine import DartEngine

logger = logging.getLogger(__name__)


class DartSession:
    def __init__(self, ws, user_id: str = "") -> None:
        self._ws = ws
        self._user_id = user_id
        self._session_id = f"dart_{uuid.uuid4().hex[:12]}"
        self._engine = DartEngine(ws)
        self._cancelled = False
        self._last_activity = time.time()
        self._heartbeat_task: asyncio.Task | None = None
        self._ttl_task: asyncio.Task | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    async def run(self) -> None:
        await self._send_init()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._ttl_task = asyncio.create_task(self._ttl_watchdog())
        try:
            await self._message_loop()
        finally:
            self._cleanup()

    async def _send_init(self) -> None:
        has_key = get_settings().dart_api_key != ""
        await self._send({
            "type": "dart_init",
            "data": {
                "session_id": self._session_id,
                "has_key": has_key,
                "security_banner": (
                    "금융감독원 전자공시시스템(Open DART)의 공식 API를 통해 공시자료와 "
                    "재무제표를 직접 조회합니다. 대화 내용은 서버에 저장되지 않으며, "
                    "세션 종료 시 즉시 파기됩니다."
                ),
            },
        })

    async def _message_loop(self) -> None:
        while not self._cancelled:
            try:
                msg = await self._ws.receive_json()
            except Exception:  # noqa: BLE001
                break

            self._last_activity = time.time()
            msg_type = msg.get("type", "")
            data = msg.get("data", {}) or {}

            if msg_type == "dart_stop":
                self._engine.cancel()
                continue
            if msg_type == "dart_set_mode":
                self._engine.set_mode(data.get("mode", "flash"))
                continue
            if msg_type == "dart_message":
                content = (data.get("content") or "").strip()
                if not content:
                    continue
                effort_mode = data.get("effort")
                if effort_mode:
                    self._engine.set_mode(effort_mode)
                try:
                    await self._engine.send_message(content)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("DartSession: engine error")
                    await self._send({
                        "type": "dart_error",
                        "data": {"message": f"엔진 오류: {exc}"},
                    })

    def cancel(self) -> None:
        self._cancelled = True
        self._engine.cancel()
        self._cleanup()

    def _cleanup(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ttl_task:
            self._ttl_task.cancel()
        logger.info("DART session %s cleaned up", self._session_id)

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._cancelled:
                await asyncio.sleep(15)
                await self._send({"type": "heartbeat", "ts": time.time()})
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass

    async def _ttl_watchdog(self) -> None:
        ttl_seconds = get_settings().dart_session_ttl_minutes * 60
        try:
            while not self._cancelled:
                await asyncio.sleep(60)
                if time.time() - self._last_activity > ttl_seconds:
                    logger.info("DART session %s TTL expired", self._session_id)
                    self._cancelled = True
                    try:
                        await self._send({
                            "type": "dart_error",
                            "data": {"message": "세션이 비활성으로 종료되었습니다."},
                        })
                    except Exception:  # noqa: BLE001
                        pass
                    break
        except asyncio.CancelledError:
            pass

    async def _send(self, data: dict) -> None:
        try:
            await self._ws.send_json(data)
        except Exception:  # noqa: BLE001
            self._cancelled = True
