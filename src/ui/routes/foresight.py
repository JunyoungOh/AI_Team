"""AI Foresight + Dandelion mode routes — WebSocket + file upload/download."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from src.config.settings import get_settings
from src.datalab.security import validate_upload, validate_download_path
from src.foresight.session import ForesightSession
from src.dandelion.session import DandelionSession, cleanup_expired_reports, get_report

router = APIRouter()


def _membership_enabled() -> bool:
    return get_settings().membership_enabled


def _verify_ws_token(ws: WebSocket) -> dict | None:
    from src.auth.security import verify_token
    token = ws.query_params.get("token", "")
    return verify_token(token) if token else None


# ── Foresight ──

@router.websocket("/ws/foresight")
async def foresight_endpoint(ws: WebSocket):
    """WebSocket endpoint for AI Foresight sessions."""
    await ws.accept()

    user = _verify_ws_token(ws) if _membership_enabled() else None
    if _membership_enabled() and not user:
        await ws.send_json({"type": "error", "data": {"message": "인증이 필요합니다."}})
        await ws.close(code=4001)
        return

    user_id = user["sub"] if user else ""
    session = ForesightSession(ws, user_id=user_id)

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


@router.post("/api/foresight/upload")
async def foresight_upload(
    session_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload a file to a Foresight session."""
    content = await file.read()
    error = validate_upload(file.filename or "", len(content))
    if error:
        return JSONResponse({"error": error}, status_code=400)

    import tempfile
    from pathlib import Path
    upload_dir = Path(tempfile.gettempdir()) / "foresight" / session_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / (file.filename or "upload")
    dest.write_bytes(content)

    return JSONResponse({"status": "ok", "filename": file.filename})


@router.get("/api/foresight/download/{session_id}/{filename}")
async def foresight_download(session_id: str, filename: str):
    """Download a result from a Foresight session."""
    import tempfile
    resolved = validate_download_path(tempfile.gettempdir(), session_id, filename)
    if not resolved:
        return JSONResponse({"error": "파일을 찾을 수 없습니다"}, status_code=404)
    return FileResponse(path=str(resolved), filename=filename)


# ── Dandelion ──

@router.websocket("/ws/dandelion")
async def dandelion_endpoint(ws: WebSocket):
    """WebSocket endpoint for Dandelion Foresight sessions."""
    await ws.accept()

    user = _verify_ws_token(ws) if _membership_enabled() else None
    if _membership_enabled() and not user:
        await ws.send_json({"type": "error", "message": "인증이 필요합니다."})
        await ws.close(code=4001)
        return

    user_id = user["sub"] if user else ""
    session = DandelionSession(ws, user_id=user_id)

    try:
        await session.run()
    except WebSocketDisconnect:
        session.cancel()
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        session.cancel()


@router.get("/api/dandelion/report/{report_id}")
async def dandelion_report_download(report_id: str):
    """Serve an in-memory Dandelion HTML report. Expires at midnight."""
    html = get_report(report_id)
    if not html:
        return HTMLResponse("<p>리포트가 만료되었거나 존재하지 않습니다.</p>", status_code=404)
    return HTMLResponse(content=html, headers={
        "Content-Disposition": f'attachment; filename="dandelion-report-{report_id}.html"',
    })


async def dandelion_cleanup_startup():
    """Clean up expired dandelion reports on server start."""
    cleanup_expired_reports()
