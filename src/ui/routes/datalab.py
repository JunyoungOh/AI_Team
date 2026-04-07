"""AI DataLab mode routes — WebSocket + file upload/download."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from src.config.settings import get_settings
from src.datalab.session import DataLabSession
from src.datalab.security import validate_upload, validate_download_path

router = APIRouter()

# Active sessions for file upload/download reference
_datalab_sessions: dict[str, DataLabSession] = {}


def _membership_enabled() -> bool:
    return get_settings().membership_enabled


def _verify_ws_token(ws: WebSocket) -> dict | None:
    from src.auth.security import verify_token
    token = ws.query_params.get("token", "")
    return verify_token(token) if token else None


@router.websocket("/ws/datalab")
async def datalab_endpoint(ws: WebSocket):
    """WebSocket endpoint for AI DataLab sessions."""
    await ws.accept()

    user = _verify_ws_token(ws) if _membership_enabled() else None
    if _membership_enabled() and not user:
        await ws.send_json({"type": "error", "data": {"message": "인증이 필요합니다."}})
        await ws.close(code=4001)
        return

    user_id = user["sub"] if user else ""
    session = DataLabSession(ws, user_id=user_id)
    _datalab_sessions[session.session_id] = session

    try:
        await session.run()
    except WebSocketDisconnect:
        session.cancel()
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "data": {"message": str(e)}})
        except Exception:
            pass
        session.cancel()
    finally:
        _datalab_sessions.pop(session.session_id, None)


@router.post("/api/datalab/upload")
async def datalab_upload(
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a file to a DataLab session."""
    session = _datalab_sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "세션을 찾을 수 없습니다"}, status_code=404)

    content = await file.read()
    error = validate_upload(file.filename or "", len(content))
    if error:
        return JSONResponse({"error": error}, status_code=400)

    from pathlib import Path
    upload_path = Path(session.session_dir) / "uploads" / (file.filename or "upload")
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(content)

    return JSONResponse({"status": "ok", "filename": file.filename})


@router.get("/api/datalab/download/{session_id}/{filename:path}")
async def datalab_download(session_id: str, filename: str):
    """Download a result file from a DataLab session."""
    import tempfile
    session = _datalab_sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "세션이 만료되었습니다"}, status_code=403)

    resolved = validate_download_path(tempfile.gettempdir(), session_id, filename)
    if not resolved:
        return JSONResponse({"error": "파일을 찾을 수 없습니다"}, status_code=404)

    if filename.endswith(".html"):
        return FileResponse(path=str(resolved), media_type="text/html")
    return FileResponse(
        path=str(resolved),
        filename=filename,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
