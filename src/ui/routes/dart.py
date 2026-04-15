"""DART mode routes — WebSocket only."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.config.settings import get_settings
from src.dart.mcp_session import DartMcpSession

router = APIRouter()


def _membership_enabled() -> bool:
    return get_settings().membership_enabled


def _verify_ws_token(ws: WebSocket) -> dict | None:
    from src.auth.security import verify_token

    token = ws.query_params.get("token", "")
    return verify_token(token) if token else None


@router.websocket("/ws/dart")
async def dart_endpoint(ws: WebSocket):
    """WebSocket endpoint for the DART (전자공시) mode."""
    await ws.accept()

    user = _verify_ws_token(ws) if _membership_enabled() else None
    if _membership_enabled() and not user:
        await ws.send_json({"type": "dart_error", "data": {"message": "인증이 필요합니다."}})
        await ws.close(code=4001)
        return

    user_id = user["sub"] if user else ""
    session = DartMcpSession(ws, user_id=user_id)

    try:
        await session.run()
    except WebSocketDisconnect:
        session.cancel()
    except Exception as exc:  # noqa: BLE001
        try:
            await ws.send_json({"type": "dart_error", "data": {"message": str(exc)}})
        except Exception:  # noqa: BLE001
            pass
        session.cancel()
