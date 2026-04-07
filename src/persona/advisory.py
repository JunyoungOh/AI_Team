"""Advisory panel — persona comments on reports. Shared by Company + Foresight."""

from __future__ import annotations

import asyncio
import logging

from src.config.settings import get_settings
from src.persona.models import PersonaDB
from src.persona.prompts import ADVISORY_COMMENT_SYSTEM, SCENARIO_ROLEPLAY_SYSTEM

logger = logging.getLogger(__name__)


def _get_bridge():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


async def generate_advisory_comments(
    report_text: str,
    persona_ids: list[str],
    user_id: str,
) -> list[dict]:
    """Generate advisory comments from personas on a report. Parallel execution."""
    if not persona_ids or not report_text or not user_id:
        return []

    db = PersonaDB.instance()

    async def _comment(pid: str) -> dict | None:
        persona = db.get_usable(pid, user_id)
        if not persona or not persona.get("persona_text"):
            return None
        bridge = None
        try:
            bridge = _get_bridge()
            text = await bridge.raw_query(
                system_prompt=ADVISORY_COMMENT_SYSTEM.format(
                    persona_name=persona["name"],
                    persona_text=persona["persona_text"],
                ),
                user_message=f"보고서:\n{report_text[:16000]}",
                model="sonnet",
                allowed_tools=[],
                timeout=90,
                max_turns=1,
                effort="medium",
            )
            return {
                "persona_id": pid,
                "name": persona["name"],
                "avatar_url": persona.get("avatar_url", ""),
                "comment": text.strip(),
            }
        except Exception as e:
            logger.warning("advisory_comment_failed: %s (persona=%s)", e, pid)
            return None
        finally:
            if bridge and hasattr(bridge, 'close'):
                await bridge.close()

    results = await asyncio.gather(
        *[_comment(pid) for pid in persona_ids[:3]],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, dict)]


async def generate_scenario_roleplay(
    report_text: str,
    persona_ids: list[str],
    user_id: str,
) -> list[dict]:
    """Generate scenario roleplay responses from personas. Parallel execution."""
    if not persona_ids or not report_text or not user_id:
        return []

    db = PersonaDB.instance()

    async def _roleplay(pid: str) -> dict | None:
        persona = db.get_usable(pid, user_id)
        if not persona or not persona.get("persona_text"):
            return None
        bridge = None
        try:
            bridge = _get_bridge()
            text = await bridge.raw_query(
                system_prompt=SCENARIO_ROLEPLAY_SYSTEM.format(
                    persona_name=persona["name"],
                    persona_text=persona["persona_text"],
                ),
                user_message=f"미래 분석 리포트:\n{report_text[:8000]}",
                model="sonnet",
                allowed_tools=[],
                timeout=120,
                max_turns=1,
                effort="low",
            )
            return {
                "persona_id": pid,
                "name": persona["name"],
                "avatar_url": persona.get("avatar_url", ""),
                "roleplay": text.strip(),
            }
        except Exception as e:
            logger.warning("scenario_roleplay_failed: %s (persona=%s)", e, pid)
            return None
        finally:
            if bridge and hasattr(bridge, 'close'):
                await bridge.close()

    results = await asyncio.gather(
        *[_roleplay(pid) for pid in persona_ids[:3]],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, dict)]
