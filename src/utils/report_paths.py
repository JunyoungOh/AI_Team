"""Shared helpers for constructing report output directories.

Every mode (instant, single-session, overtime, overtime-dev, upgrade, ...)
used to write artifacts to ``data/reports/{session_id}/`` which produced
cryptic, flat folders where multiple modes collided. This module replaces
that with ``{slug_of_title}_{production_date}`` so folders are human
readable at a glance and mode outputs naturally separate by content.

The three rule helpers — ``slugify_title``, ``_format_date``,
``_resolve_collision`` — are the only places to edit when tuning how
folders look. The orchestration function ``build_report_dir`` wires them
together and is the single entry point every writer should call.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config.settings import get_settings


_FALLBACK_PREFIX = "report"


# ─────────────────────────────────────────────────────────────────────────
# USER-OWNED RULES
#
# Only these three helpers define how folder names look. Everything below
# is orchestration that does not care about the format.
# ─────────────────────────────────────────────────────────────────────────


def slugify_title(title: str, max_len: int = 50) -> str:
    """Convert a free-form title into a filesystem-safe folder slug.

    TODO(user): Implement the slug rules that shape every report folder
    name across the app. This is the single most visible decision in the
    refactor — every output folder will carry whatever this function
    returns.

    Requirements to weigh:

    - Korean characters: keep as-is (UX: reads natively in Finder) or
      romanize (portability: avoids filesystem encoding issues on some
      CI/remote boxes)?
    - Forbidden characters on cross-platform filesystems:
      ``< > : " / \\ | ? *`` and leading/trailing dots/spaces.
    - Whitespace collapsing: ``space -> '_'`` (terminal-friendly) vs
      ``space -> '-'`` (web-url-friendly).
    - Max length: default 50 chars. Truncate at word boundary or hard
      slice? Very long Korean titles become unreadable past ~30 chars.
    - Empty/garbage input: return ``""`` — the caller will substitute a
      neutral fallback so the app never crashes on bad input.

    Reference implementation to adapt (has slug logic but no date):
        src/graphs/nodes/single_session.py:152 (``_build_report_dir``)

    Args:
        title: The free-form task/query string from the user.
        max_len: Maximum slug length in characters.

    Returns:
        A safe slug, or empty string if nothing usable remains.
    """
    import re
    name = title.strip()[:max_len]
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name).strip('_')
    return name


def _format_date(date: datetime) -> str:
    """Format the production-date suffix glued onto each folder name.

    Default: compact ``YYYYMMDD`` because it sorts lexicographically and
    plays well with terminal globs. Change to ``YYYY-MM-DD`` or
    ``YYYY_MM_DD`` if you prefer readability over sort-stability.
    """
    return date.strftime("%Y%m%d")


def _resolve_collision(candidate: Path, session_id: Optional[str]) -> Path:
    """Return a non-colliding Path when ``candidate`` already exists.

    Default strategy: first try session_id[:6] suffix (stable, traceable
    back to the session that produced it); if that also collides, fall
    back to an incremental counter ``_2``, ``_3``, ...
    """
    if session_id:
        tagged = candidate.with_name(f"{candidate.name}_{session_id[:6]}")
        if not tagged.exists():
            return tagged

    counter = 2
    while True:
        numbered = candidate.with_name(f"{candidate.name}_{counter}")
        if not numbered.exists():
            return numbered
        counter += 1


# ─────────────────────────────────────────────────────────────────────────
# Orchestration — callers should use ``build_report_dir`` only.
# ─────────────────────────────────────────────────────────────────────────


def build_report_dir(
    title: str,
    session_id: Optional[str] = None,
    *,
    date: Optional[datetime] = None,
    base_dir: Optional[Path | str] = None,
    create: bool = True,
) -> Path:
    """Build (and optionally create) a report folder named by title+date.

    The folder name is ``{slugify_title(title)}_{_format_date(date)}``
    under ``base_dir``. If the slug comes back empty, a neutral prefix is
    used so the app never crashes on empty input. If the path already
    exists, ``_resolve_collision`` picks a non-colliding variant.

    Args:
        title: Human-readable report title — usually the original user
            task/query string.
        session_id: Optional session identifier used only for collision
            resolution (does not normally appear in the folder name).
        date: Production datetime; defaults to ``datetime.now()``.
        base_dir: Parent directory; defaults to
            ``get_settings().report_output_dir``.
        create: If True, the returned directory is created on disk.

    Returns:
        Absolute ``Path`` to the selected (and optionally created) folder.
    """
    base = Path(base_dir) if base_dir else Path(get_settings().report_output_dir)
    when = date or datetime.now()

    slug = slugify_title(title or "")
    if not slug:
        slug = _FALLBACK_PREFIX

    folder_name = f"{slug}_{_format_date(when)}"
    candidate = base / folder_name

    if candidate.exists():
        candidate = _resolve_collision(candidate, session_id)

    if create:
        candidate.mkdir(parents=True, exist_ok=True)

    return candidate
