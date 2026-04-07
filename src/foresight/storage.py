"""
Environment persistence layer for AI Foresight mode.

Provides per-user file-based storage with quota enforcement.
All paths follow: {base_dir}/{user_id}/{name}.json
"""

import json
from pathlib import Path

DEFAULT_STORAGE_DIR = "data/foresight/environments"


def _sanitize_name(name: str) -> str:
    """Strip whitespace and replace path-traversal characters with underscores.

    Returns 'unnamed' if the result is empty.
    """
    sanitized = name.strip()
    for bad in ("/", "\\", ".."):
        sanitized = sanitized.replace(bad, "_")
    sanitized = sanitized.strip()
    return sanitized if sanitized else "unnamed"


def _user_dir(base_dir: str, user_id: str) -> Path:
    return Path(base_dir) / user_id


def _env_path(base_dir: str, user_id: str, name: str) -> Path:
    return _user_dir(base_dir, user_id) / f"{_sanitize_name(name)}.json"


def save_environment(base_dir: str, user_id: str, name: str, data: dict) -> str:
    """Save environment data as JSON under {base_dir}/{user_id}/{name}.json.

    Creates the user directory if it does not exist.
    Returns the absolute file path as a string.
    """
    path = _env_path(base_dir, user_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def load_environment(base_dir: str, user_id: str, name: str) -> dict | None:
    """Load an environment by name.

    Returns the parsed dict, or None if the file does not exist.
    """
    path = _env_path(base_dir, user_id, name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_environments(base_dir: str, user_id: str) -> list[str]:
    """Return a list of saved environment names (file stems) for the user.

    Returns an empty list if the user directory does not exist.
    """
    user_dir = _user_dir(base_dir, user_id)
    if not user_dir.exists():
        return []
    return [p.stem for p in user_dir.glob("*.json")]


def delete_environment(base_dir: str, user_id: str, name: str) -> bool:
    """Delete an environment file.

    Returns True if the file existed and was deleted, False otherwise.
    """
    path = _env_path(base_dir, user_id, name)
    if path.exists():
        path.unlink()
        return True
    return False


def get_user_storage_usage(base_dir: str, user_id: str) -> int:
    """Return the total bytes used by all of the user's environment files."""
    user_dir = _user_dir(base_dir, user_id)
    if not user_dir.exists():
        return 0
    return sum(p.stat().st_size for p in user_dir.glob("*.json"))


def check_storage_quota(base_dir: str, user_id: str, max_mb: float = 200) -> bool:
    """Return True if the user's storage usage is strictly below max_mb megabytes."""
    usage_bytes = get_user_storage_usage(base_dir, user_id)
    return usage_bytes < max_mb * 1024 * 1024
