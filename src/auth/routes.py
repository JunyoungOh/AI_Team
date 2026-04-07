"""Auth API routes — login, register, logout, admin endpoints.

All routes return JSON. The auth cookie name is "hq_token" (JWT).
First registrant is auto-approved as admin. Subsequent users need admin approval.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.auth.models import UserDB
from src.auth.security import (
    create_token,
    get_active_entry_code,
    hash_password,
    verify_entry_code,
    verify_password,
    verify_token,
)
from src.config.settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_COOKIE = "hq_token"
_COOKIE_MAX_AGE = 86400 * 7  # 7 days


def _set_token_cookie(response: JSONResponse, token: str) -> JSONResponse:
    response.set_cookie(_COOKIE, token, httponly=True, samesite="lax", max_age=_COOKIE_MAX_AGE)
    return response


def get_current_user(request: Request) -> dict | None:
    """Extract and verify JWT from cookie. Returns payload or None."""
    token = request.cookies.get(_COOKIE, "")
    if not token:
        return None
    return verify_token(token)


# ── Public endpoints ──────────────────────────────

@router.post("/login")
async def login(request: Request):
    """Login with entry_code + username + password."""
    body = await request.json()
    entry_code = body.get("entry_code", "")
    username = body.get("username", "").strip()
    password = body.get("password", "")

    # Step 1: Entry code
    if not verify_entry_code(entry_code):
        return JSONResponse({"ok": False, "error": "입장코드가 올바르지 않습니다."}, status_code=401)

    # Step 2: DB user lookup
    db = UserDB.get()
    user = db.get_by_username(username)
    if not user or not verify_password(password, user.password_hash):
        return JSONResponse({"ok": False, "error": "아이디 또는 비밀번호가 올바르지 않습니다."}, status_code=401)

    if user.status == "pending":
        return JSONResponse({"ok": False, "error": "승인 대기 중입니다. 관리자 승인 후 로그인할 수 있습니다."}, status_code=403)
    if user.status in ("rejected", "disabled"):
        return JSONResponse({"ok": False, "error": "비활성화된 계정입니다."}, status_code=403)

    db.update_last_login(user.id)
    token = create_token(user.id, user.username, user.display_name, role=user.role, company_name=user.company_name, ceo_name=user.ceo_name, visible_modes=user.get_visible_modes_list())
    resp = JSONResponse({"ok": True, "user": user.to_public_dict()})
    return _set_token_cookie(resp, token)


@router.post("/register")
async def register(request: Request):
    """Register a new account. First user becomes admin (auto-approved)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청입니다."}, status_code=400)

    entry_code = body.get("entry_code", "")
    username = body.get("username", "").strip()
    password = body.get("password", "")
    display_name = body.get("display_name", "").strip()

    if not verify_entry_code(entry_code):
        return JSONResponse({"ok": False, "error": "입장코드가 올바르지 않습니다."}, status_code=401)

    if not username or not password or not display_name:
        return JSONResponse({"ok": False, "error": "모든 항목을 입력해주세요."}, status_code=400)

    # Email domain restriction
    allowed_domain = get_settings().allowed_email_domain
    if allowed_domain:
        if "@" not in username or not username.endswith(f"@{allowed_domain}"):
            return JSONResponse({"ok": False, "error": f"@{allowed_domain} 이메일만 가입할 수 있습니다."}, status_code=400)

    if len(password) < 6:
        return JSONResponse({"ok": False, "error": "비밀번호는 6자 이상이어야 합니다."}, status_code=400)

    try:
        db = UserDB.get()
        if db.username_exists(username):
            return JSONResponse({"ok": False, "error": "이미 사용 중인 아이디입니다."}, status_code=409)

        pw_hash = hash_password(password)

        # First user → auto-approved admin
        if not db.has_any_admin():
            user = db.create_user(username, pw_hash, display_name, role="admin", status="approved")
            return JSONResponse({
                "ok": True,
                "message": "관리자 계정으로 등록되었습니다. 바로 로그인하세요.",
                "auto_admin": True,
            }, status_code=201)
        user = db.create_user(username, pw_hash, display_name)
        return JSONResponse({"ok": True, "message": "가입 신청이 완료되었습니다. 관리자 승인을 기다려주세요."}, status_code=201)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": f"서버 오류: {exc}"}, status_code=500)


@router.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_COOKIE)
    return resp


@router.put("/company-name")
async def set_company_name(request: Request):
    """Set or update the user's company name."""
    payload = get_current_user(request)
    if not payload:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청입니다."}, status_code=400)
    name = body.get("company_name", "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "회사 이름을 입력해주세요."}, status_code=400)
    if len(name) > 30:
        return JSONResponse({"ok": False, "error": "회사 이름은 30자 이내로 입력해주세요."}, status_code=400)
    db = UserDB.get()
    if not db.update_company_name(payload["sub"], name):
        return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)
    user = db.get_by_id(payload["sub"])
    token = create_token(user.id, user.username, user.display_name, role=user.role, company_name=user.company_name, ceo_name=user.ceo_name, visible_modes=user.get_visible_modes_list())
    resp = JSONResponse({"ok": True, "company_name": name})
    return _set_token_cookie(resp, token)


@router.put("/ceo-name")
async def set_ceo_name(request: Request):
    """Set the user's CEO name (one-time only)."""
    payload = get_current_user(request)
    if not payload:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    if payload.get("ceon"):
        return JSONResponse({"ok": False, "error": "CEO 이름은 변경할 수 없습니다."}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청입니다."}, status_code=400)
    name = body.get("ceo_name", "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "CEO 이름을 입력해주세요."}, status_code=400)
    if len(name) > 20:
        return JSONResponse({"ok": False, "error": "CEO 이름은 20자 이내로 입력해주세요."}, status_code=400)
    db = UserDB.get()
    if not db.update_ceo_name(payload["sub"], name):
        return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)
    user = db.get_by_id(payload["sub"])
    token = create_token(user.id, user.username, user.display_name, role=user.role, company_name=user.company_name, ceo_name=user.ceo_name, visible_modes=user.get_visible_modes_list())
    resp = JSONResponse({"ok": True, "ceo_name": name})
    return _set_token_cookie(resp, token)


@router.get("/me")
async def me(request: Request):
    """Get current user info from JWT."""
    payload = get_current_user(request)
    if not payload:
        return JSONResponse({"ok": False, "error": "not_authenticated"}, status_code=401)
    return JSONResponse({
        "ok": True,
        "user": {
            "id": payload["sub"],
            "username": payload["usr"],
            "display_name": payload["dn"],
            "role": payload["role"],
            "company_name": payload.get("cn"),
            "ceo_name": payload.get("ceon"),
            "visible_modes": payload.get("vm"),
        },
    })


# ── Admin endpoints (admin role required) ─────────

def _require_admin(request: Request) -> dict | None:
    """Returns payload if admin, else None."""
    payload = get_current_user(request)
    if payload and payload.get("role") == "admin":
        return payload
    return None


@router.get("/admin/users")
async def admin_list_users(request: Request):
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    users = db.list_all()
    return JSONResponse({"ok": True, "users": [u.to_public_dict() for u in users]})


@router.post("/admin/approve/{user_id}")
async def admin_approve(user_id: str, request: Request):
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    if db.update_status(user_id, "approved"):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.post("/admin/reject/{user_id}")
async def admin_reject(user_id: str, request: Request):
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    if db.update_status(user_id, "rejected"):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.post("/admin/disable/{user_id}")
async def admin_disable(user_id: str, request: Request):
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    if db.update_status(user_id, "disabled"):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.post("/admin/reactivate/{user_id}")
async def admin_reactivate(user_id: str, request: Request):
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    if db.update_status(user_id, "approved"):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.delete("/admin/users/{user_id}")
async def admin_delete(user_id: str, request: Request):
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    if db.delete_user(user_id):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.post("/admin/promote/{user_id}")
async def admin_promote(user_id: str, request: Request):
    """Promote a user to admin role."""
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    if db.update_role(user_id, "admin"):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.post("/admin/demote/{user_id}")
async def admin_demote(user_id: str, request: Request):
    """Demote an admin to regular user role."""
    payload = _require_admin(request)
    if not payload:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    if payload["sub"] == user_id:
        return JSONResponse({"ok": False, "error": "자신의 관리자 권한은 해제할 수 없습니다."}, status_code=400)
    db = UserDB.get()
    if db.update_role(user_id, "user"):
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.put("/admin/visible-modes/{user_id}")
async def admin_set_visible_modes(user_id: str, request: Request):
    """Set which modes are visible for a user. null/empty = all modes visible."""
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    import json
    body = await request.json()
    modes = body.get("visible_modes")  # list or null
    ALL_MODES = {"company", "discussion", "secretary", "datalab", "foresight", "engineering", "agent"}
    if modes is not None:
        if not isinstance(modes, list) or not all(m in ALL_MODES for m in modes):
            return JSONResponse({"ok": False, "error": f"유효한 모드: {sorted(ALL_MODES)}"}, status_code=400)
        modes_json = json.dumps(modes, ensure_ascii=False)
    else:
        modes_json = None
    db = UserDB.get()
    if db.update_visible_modes(user_id, modes_json):
        return JSONResponse({"ok": True, "visible_modes": modes})
    return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)


@router.get("/admin/entry-code")
async def admin_get_entry_code(request: Request):
    """Get current active entry code (masked for display)."""
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    code = get_active_entry_code()
    if not code:
        return JSONResponse({"ok": True, "entry_code": "", "masked": "(미설정)"})
    masked = code[:2] + "•" * max(0, len(code) - 4) + code[-2:] if len(code) > 4 else "•" * len(code)
    return JSONResponse({"ok": True, "entry_code": code, "masked": masked})


@router.post("/admin/entry-code")
async def admin_set_entry_code(request: Request):
    """Set a new entry code (stored in DB, takes priority over env var)."""
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    body = await request.json()
    new_code = body.get("entry_code", "").strip()
    if not new_code:
        return JSONResponse({"ok": False, "error": "입장코드를 입력해주세요."}, status_code=400)
    if len(new_code) < 4:
        return JSONResponse({"ok": False, "error": "입장코드는 4자 이상이어야 합니다."}, status_code=400)
    db = UserDB.get()
    db.set_config("entry_code", new_code)
    return JSONResponse({"ok": True, "message": "입장코드가 변경되었습니다."})


@router.post("/admin/reset-password/{user_id}")
async def admin_reset_password(user_id: str, request: Request):
    """Reset a user's password to a random temporary password."""
    if not _require_admin(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
    db = UserDB.get()
    user = db.get_by_id(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "user_not_found"}, status_code=404)
    temp_pw = secrets.token_urlsafe(8)
    db.update_password(user_id, hash_password(temp_pw))
    return JSONResponse({"ok": True, "temp_password": temp_pw, "username": user.username})
