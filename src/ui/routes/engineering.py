"""AI Engineering mode routes — WebSocket + file upload/download."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from src.config.settings import get_settings

router = APIRouter()
_eng_log = logging.getLogger("engineering.ws")


def _membership_enabled() -> bool:
    return get_settings().membership_enabled


def _verify_ws_token(ws: WebSocket) -> dict | None:
    from src.auth.security import verify_token
    token = ws.query_params.get("token", "")
    return verify_token(token) if token else None


@router.websocket("/ws/eng")
async def websocket_engineering(ws: WebSocket):
    """WebSocket endpoint for AI Engineering sessions."""
    await ws.accept()

    user = _verify_ws_token(ws) if _membership_enabled() else None
    if _membership_enabled() and not user:
        await ws.send_json({"type": "error", "data": {"message": "인증이 필요합니다."}})
        await ws.close(code=4001)
        return

    user_id = user["sub"] if user else ""

    from src.engineering.session import EngineeringSession

    try:
        session = EngineeringSession(ws, user_id=user_id)
    except Exception as e:
        _eng_log.exception("EngineeringSession init failed: %s", e)
        await ws.send_json({"type": "eng_error", "data": {"message": f"세션 초기화 실패: {e}"}})
        await ws.close()
        return

    try:
        await session.run()
    except WebSocketDisconnect:
        await session.on_disconnect()
    except Exception as e:
        _eng_log.exception("EngineeringSession error: %s", e)
        try:
            await ws.send_json({"type": "eng_error", "data": {"message": str(e)}})
        except Exception:
            pass
        session.cancel()


@router.get("/api/eng/download/{session_id}")
async def download_engineering_project(session_id: str):
    """Download the zipped workspace for an Engineering session."""
    from src.engineering.workspace_manager import WorkspaceManager
    mgr = WorkspaceManager()
    ws = mgr.get_workspace(session_id)
    if not ws:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    zip_path = mgr.create_zip(session_id)
    return FileResponse(
        path=str(zip_path),
        filename=f"{session_id}.zip",
        media_type="application/zip",
    )


_ENG_MAX_FILE_SIZE = 10 * 1024 * 1024
_ENG_MAX_SESSION_UPLOAD = 50 * 1024 * 1024
_ENG_ALLOWED_EXTENSIONS: set[str] = {
    ".csv", ".xlsx", ".xls", ".json", ".txt", ".pdf",
    ".py", ".js", ".ts", ".html", ".css", ".md",
    ".xml", ".yaml", ".yml", ".env", ".sql",
    ".png", ".jpg", ".jpeg", ".gif", ".svg",
}


@router.post("/api/eng/upload/{session_id}")
async def upload_engineering_file(session_id: str, file: UploadFile = File(...)):
    """Upload a file to an Engineering session's workspace."""
    from pathlib import Path
    from src.engineering.workspace_manager import WorkspaceManager

    mgr = WorkspaceManager()
    content = await file.read()

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ENG_ALLOWED_EXTENSIONS:
        return JSONResponse({"error": f"허용되지 않는 파일 형식입니다: {ext or '(없음)'}"}, status_code=400)

    if len(content) > _ENG_MAX_FILE_SIZE:
        return JSONResponse({"error": f"파일 크기가 10MB를 초과합니다 ({len(content) / (1024*1024):.1f} MB)"}, status_code=400)

    ws = mgr.get_workspace(session_id)
    if not ws:
        ws = mgr.create(session_id)

    uploads_dir = ws / "uploads"
    uploads_dir.mkdir(exist_ok=True)

    existing_size = sum(f.stat().st_size for f in uploads_dir.rglob("*") if f.is_file())
    if existing_size + len(content) > _ENG_MAX_SESSION_UPLOAD:
        return JSONResponse({"error": "세션 업로드 총 용량(50MB)을 초과합니다."}, status_code=400)

    safe_name = Path(file.filename or "upload").name
    dest = uploads_dir / safe_name
    dest.write_bytes(content)

    return JSONResponse({"status": "ok", "filename": safe_name, "size": len(content), "path": f"uploads/{safe_name}"})
