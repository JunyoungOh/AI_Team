"""Persona Workshop REST API routes.

Endpoints:
  GET    /api/personas                  — list user's personas
  GET    /api/personas/{persona_id}     — get one (ownership check)
  POST   /api/personas/search-preview   — web search + sufficiency → preview_token
  POST   /api/personas                  — create from preview_token
  PUT    /api/personas/{persona_id}     — update (ownership check)
  DELETE /api/personas/{persona_id}     — delete (ownership check)
  POST   /api/personas/upload           — file upload for persona building
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from src.auth.routes import get_current_user
from src.config.settings import get_settings
from src.persona.models import MAX_PERSONAS_PER_USER, PersonaDB
from src.persona.prompts import PERSONA_SYNTHESIS_SYSTEM, SUFFICIENCY_SYSTEM

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/personas", tags=["personas"])

# ── Upload settings ────────────────────────────────
UPLOAD_ALLOWED_EXT = {".txt", ".md", ".pdf"}
UPLOAD_MAX_SIZE = 5 * 1024 * 1024  # 5 MB per file
UPLOAD_MAX_TOTAL = 20 * 1024 * 1024  # 20 MB total
UPLOAD_BASE = Path("/tmp/persona-uploads")


def _get_bridge():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


def _require_auth(request: Request) -> dict | None:
    """Return user payload or None."""
    return get_current_user(request)


# ── List personas ──────────────────────────────────

@router.get("")
async def list_personas(request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    db = PersonaDB.instance()
    items = db.list(user_id=user["sub"])
    return JSONResponse({"ok": True, "personas": items})


# ── List shared personas (from other users) ───────

@router.get("/shared")
async def list_shared(request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    db = PersonaDB.instance()
    return JSONResponse({"ok": True, "personas": db.list_shared(user["sub"])})


# ── List usable personas (own + shared-enabled) ───

@router.get("/usable")
async def list_usable(request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    db = PersonaDB.instance()
    return JSONResponse({"ok": True, "personas": db.list_usable(user["sub"])})


# ── Get single persona ─────────────────────────────

@router.get("/{persona_id}")
async def get_persona(persona_id: str, request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    db = PersonaDB.instance()
    item = db.get(persona_id, user_id=user["sub"])
    if not item:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True, "persona": item})


# ── Search preview (web search + sufficiency) ──────

@router.post("/search-preview")
async def search_preview(request: Request):
    """Web search + sufficiency check. Returns preview_token."""
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청입니다."}, status_code=400)

    name = body.get("name", "").strip()
    keywords = body.get("keywords", "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "인물 이름을 입력해주세요."}, status_code=400)

    search_query = f"{name} {keywords}".strip() if keywords else name

    # Build search prompt — leverage keywords for disambiguation
    search_system = (
        "주어진 인물에 대해 다음 정보를 웹에서 **광범위하게** 검색하여 상세히 정리하세요:\n"
        "1. 경력 궤적 (주요 커리어, 소속 조직, 역할 변천)\n"
        "2. 인생 전환점 (관점이 바뀐 계기, 결정적 성공/실패)\n"
        "3. 공개 발언, 인터뷰, 관점\n"
        "4. 말투와 커뮤니케이션 스타일\n"
        "5. 전문 분야와 최근 입장\n\n"
        "## 검색 규칙\n"
        "- web_search를 **최소 5회 이상** 다양한 쿼리로 반복하세요. "
        "검색어를 바꿔가며 (이름+회사, 이름+인터뷰, 이름+강연, 이름+기사 등) 폭넓게 수집하세요.\n"
        "- 유망한 검색 결과가 나오면 web_fetch로 해당 페이지를 직접 읽으세요. "
        "web_fetch가 실패하면 firecrawl_scrape를 사용하세요.\n"
        "- 키워드가 제공되면 이름+키워드를 조합하여 동명이인을 구분하세요.\n"
        "- 출처가 20건 이상이면 이상적입니다. 검색 결과가 부족하면 쿼리를 변경해서 더 검색하세요."
    )

    # Build user message — clearly separate name and disambiguation context
    user_parts = [f"인물: {name}"]
    if keywords:
        user_parts.append(f"인물 특정 키워드: {keywords}")
        user_parts.append(f"(이 키워드로 동명이인을 구분하세요. 예: 소속 회사, 직책, 전문 분야)")
    user_parts.append(f"검색 쿼리: {search_query}")

    # Step 1: Web search
    bridge = _get_bridge()
    try:
        search_results = await bridge.raw_query(
            system_prompt=search_system,
            user_message="\n".join(user_parts),
            model="sonnet",
            allowed_tools=["WebSearch", "WebFetch", "mcp__firecrawl__firecrawl_scrape"],
            timeout=300,
            max_turns=12,
            effort="medium",
        )
    except Exception as e:
        import traceback
        logger.error("persona_search_failed: %s (name=%s)\n%s", e, name, traceback.format_exc())
        search_results = ""
    finally:
        await bridge.close()

    # Step 2: Sufficiency check
    sufficiency = {"sufficient": False, "summary": "", "source_count": 0}
    if search_results:
        bridge2 = _get_bridge()
        try:
            raw = await bridge2.raw_query(
                system_prompt=SUFFICIENCY_SYSTEM,
                user_message=f"인물: {name}\n\n검색 결과:\n{search_results}",
                model="sonnet",
                allowed_tools=[],
                timeout=30,
                effort="low",
            )
            # Parse JSON from response
            try:
                # Find JSON block in response
                text = raw.strip()
                if "```" in text:
                    # Extract from code block
                    start = text.find("{")
                    end = text.rfind("}") + 1
                    if start >= 0 and end > start:
                        text = text[start:end]
                sufficiency = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                sufficiency = {"sufficient": bool(search_results), "summary": raw[:500], "source_count": 0}
        except Exception as e:
            logger.warning("sufficiency_check_failed: %s", e)
        finally:
            await bridge2.close()

    # Store preview
    db = PersonaDB.instance()
    preview_data = {
        "name": name,
        "keywords": keywords,
        "search_results": search_results,
        "sufficiency": sufficiency,
    }
    token = db.store_preview(user["sub"], preview_data)

    # If search returned nothing at all, report as search failure
    if not search_results:
        return JSONResponse({
            "ok": True,
            "preview_token": token,
            "name": name,
            "sufficient": False,
            "summary": "웹검색에서 관련 정보를 찾지 못했습니다. '추가 정보 입력'으로 직접 데이터를 제공해주세요.",
            "source_count": 0,
            "search_failed": True,
        })

    return JSONResponse({
        "ok": True,
        "preview_token": token,
        "name": name,
        "sufficient": sufficiency.get("sufficient", False),
        "summary": sufficiency.get("summary", ""),
        "source_count": sufficiency.get("source_count", 0),
    })


# ── Create persona from preview ────────────────────

@router.post("")
async def create_persona(request: Request):
    """Create persona from preview_token (synthesize with LLM)."""
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청입니다."}, status_code=400)

    preview_token = body.get("preview_token", "").strip()
    if not preview_token:
        return JSONResponse({"ok": False, "error": "preview_token이 필요합니다."}, status_code=400)

    db = PersonaDB.instance()

    # Enforce persona limit
    if db.count(user["sub"]) >= MAX_PERSONAS_PER_USER:
        return JSONResponse(
            {"ok": False, "error": f"페르소나는 최대 {MAX_PERSONAS_PER_USER}개까지 생성할 수 있습니다."},
            status_code=400,
        )

    # Resolve and consume preview data (single use — prevents duplicate creation)
    preview = db.consume_preview(preview_token, user["sub"])
    if not preview:
        return JSONResponse({"ok": False, "error": "미리보기 데이터가 만료되었습니다."}, status_code=400)

    name = preview["name"]
    search_results = preview.get("search_results", "")
    sufficiency = preview.get("sufficiency", {})

    # Load uploaded files if provided
    file_ids = body.get("file_ids", [])
    uploaded_text = _load_uploaded_files(file_ids)

    # Synthesize persona
    data_parts = []
    if search_results:
        data_parts.append(f"## 웹검색 결과\n{search_results}")
    if uploaded_text:
        data_parts.append(f"## 업로드된 자료\n{uploaded_text}")

    combined_data = "\n\n".join(data_parts) if data_parts else "(데이터 없음)"

    bridge = _get_bridge()
    try:
        persona_text = await bridge.raw_query(
            system_prompt=PERSONA_SYNTHESIS_SYSTEM,
            user_message=f"인물: {name}\n\n{combined_data}",
            model="sonnet",
            allowed_tools=[],
            timeout=120,
            effort="medium",
        )
    except Exception as e:
        logger.error("persona_synthesis_failed: %s", e)
        return JSONResponse({"ok": False, "error": "페르소나 합성에 실패했습니다."}, status_code=500)
    finally:
        await bridge.close()

    # Generate concise card summary from synthesized persona
    from src.persona.prompts import CARD_SUMMARY_SYSTEM
    bridge3 = _get_bridge()
    try:
        summary = await bridge3.raw_query(
            system_prompt=CARD_SUMMARY_SYSTEM,
            user_message=persona_text,
            model="haiku",
            allowed_tools=[],
            timeout=15,
            effort="low",
        )
        summary = summary.strip().replace('"', '').replace("'", "")[:60]
    except Exception:
        summary = sufficiency.get("summary", "")[:60]
    finally:
        await bridge3.close()

    source = "mixed" if uploaded_text else "web"

    persona = db.create(
        user_id=user["sub"],
        name=name,
        summary=summary,
        persona_text=persona_text,
        source=source,
    )

    return JSONResponse({"ok": True, "persona": persona}, status_code=201)


# ── Toggle share ───────────────────────────────────

@router.put("/{persona_id}/share")
async def toggle_share_endpoint(persona_id: str, request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)
    shared = bool(body.get("shared", False))
    db = PersonaDB.instance()
    ok = db.toggle_share(persona_id, user["sub"], shared)
    if not ok:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True, "shared": shared})


# ── Toggle use ─────────────────────────────────────

@router.put("/{persona_id}/use")
async def toggle_use_endpoint(persona_id: str, request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_request"}, status_code=400)
    enabled = bool(body.get("enabled", False))
    db = PersonaDB.instance()
    db.toggle_use(user["sub"], persona_id, enabled)
    return JSONResponse({"ok": True, "enabled": enabled})


# ── Update persona ─────────────────────────────────

@router.put("/{persona_id}")
async def update_persona(persona_id: str, request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청입니다."}, status_code=400)

    allowed_fields = {"name", "summary", "persona_text", "avatar_url"}
    updates = {k: v for k, v in body.items() if k in allowed_fields and isinstance(v, str)}
    if not updates:
        return JSONResponse({"ok": False, "error": "수정할 항목이 없습니다."}, status_code=400)

    db = PersonaDB.instance()
    ok = db.update(persona_id, user_id=user["sub"], **updates)
    if not ok:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    item = db.get(persona_id, user_id=user["sub"])
    return JSONResponse({"ok": True, "persona": item})


# ── Delete persona ─────────────────────────────────

@router.delete("/{persona_id}")
async def delete_persona(persona_id: str, request: Request):
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)

    db = PersonaDB.instance()
    ok = db.delete(persona_id, user_id=user["sub"])
    if not ok:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True})


# ── File upload ────────────────────────────────────

@router.post("/upload")
async def upload_files(request: Request):
    """Upload files for persona building. Returns file IDs."""
    user = _require_auth(request)
    if not user:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)

    form = await request.form()
    session_id = str(uuid.uuid4())[:8]
    # Scope upload directory to user_id for ownership isolation
    upload_dir = UPLOAD_BASE / user["sub"] / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    total_size = 0

    for key in form:
        file = form[key]
        if not hasattr(file, "read"):
            continue

        filename = _sanitize_filename(file.filename or "upload")
        if not _validate_extension(filename):
            continue

        content = await file.read()
        total_size += len(content)
        if len(content) > UPLOAD_MAX_SIZE:
            continue
        if total_size > UPLOAD_MAX_TOTAL:
            break

        dest = upload_dir / filename
        # Path traversal protection
        if not str(dest.resolve()).startswith(str(upload_dir.resolve())):
            continue

        dest.write_bytes(content)
        uploaded.append({
            "file_id": f"{session_id}/{filename}",
            "filename": filename,
        })

    return JSONResponse({"ok": True, "files": uploaded, "session_id": session_id})


# ── Helpers ────────────────────────────────────────

def _sanitize_filename(name: str) -> str:
    """Strip path components, prevent traversal."""
    clean = Path(name).name
    return clean if clean else "upload"


def _validate_extension(name: str) -> bool:
    return Path(name).suffix.lower() in UPLOAD_ALLOWED_EXT


def _load_uploaded_files(file_ids: list[str]) -> str:
    """Load uploaded file contents by file_id list."""
    texts = []
    for fid in file_ids:
        try:
            # file_id = "{session_id}/{filename}"
            safe = Path(fid).name  # prevent traversal on second component
            parts = fid.split("/", 1)
            if len(parts) != 2:
                continue
            session_id, filename = parts[0], Path(parts[1]).name
            fp = UPLOAD_BASE / session_id / filename
            # Path traversal protection
            if not str(fp.resolve()).startswith(str(UPLOAD_BASE.resolve())):
                continue
            if fp.exists() and fp.stat().st_size < 5_000_000:
                texts.append(fp.read_text(encoding="utf-8", errors="replace")[:10000])
        except Exception as e:
            logger.warning("upload_read_failed: %s", e)
    return "\n\n---\n\n".join(texts) if texts else ""
