"""Authentication utilities — password hashing, JWT tokens, entry code check.

JWT payload:
    {"sub": user_id, "usr": username, "dn": display_name, "role": "user"|"admin", "exp": ...}
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path

import jwt

from src.config.settings import get_settings

# ── JWT ───────────────────────────────────────────

_jwt_secret: str = ""


_JWT_SECRET_FILE = Path("data/.jwt_secret")


def _get_jwt_secret() -> str:
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret
    # 1) settings (env var)
    s = get_settings().jwt_secret
    if s:
        _jwt_secret = s
        return _jwt_secret
    # 2) persistent file
    if _JWT_SECRET_FILE.exists():
        _jwt_secret = _JWT_SECRET_FILE.read_text().strip()
        if _jwt_secret:
            return _jwt_secret
    # 3) generate and persist
    _jwt_secret = secrets.token_hex(32)
    _JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _JWT_SECRET_FILE.write_text(_jwt_secret)
    return _jwt_secret


def create_token(user_id: str, username: str, display_name: str, role: str = "user", company_name: str | None = None, ceo_name: str | None = None, visible_modes: list[str] | None = None, ttl: int = 604800) -> str:
    """Create a JWT token (default TTL: 7 days)."""
    payload = {
        "sub": user_id,
        "usr": username,
        "dn": display_name,
        "role": role,
        "cn": company_name,
        "ceon": ceo_name,
        "vm": visible_modes,
        "exp": int(time.time()) + ttl,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def verify_token(token: str) -> dict | None:
    """Verify and decode JWT. Returns payload dict or None."""
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Password hashing (SHA-256 + salt, no bcrypt dependency) ───

def hash_password(password: str) -> str:
    """Hash password with random salt using SHA-256 (PBKDF2-HMAC, 260k iterations)."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, dk_hex = stored_hash.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# ── Entry code ────────────────────────────────────

def get_active_entry_code() -> str:
    """Get the active entry code — DB override takes priority over env var."""
    from src.auth.models import UserDB
    db = UserDB.get()
    db_code = db.get_config("entry_code")
    if db_code:
        return db_code
    return get_settings().entry_code


def verify_entry_code(code: str) -> bool:
    """Check entry code. Returns True if code matches or no entry code is configured."""
    entry_code = get_active_entry_code()
    if not entry_code:
        return True  # No entry code configured → skip check
    return hmac.compare_digest(code, entry_code)


