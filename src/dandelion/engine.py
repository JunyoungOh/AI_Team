"""DandelionEngine — orchestrates the multi-agent imagination pipeline."""
from __future__ import annotations

import asyncio
import json
import logging
import re

from src.utils.bridge_factory import get_bridge
from src.dandelion.schemas import (
    DandelionTree, Theme, ThemeAssignment, Seed, THEME_COLORS,
)
from src.dandelion.supervisor import ThemeSupervisor
from src.dandelion.imaginer import Imaginer
from src.dandelion.prompts.ceo import CLARIFY_SYSTEM, THEME_DECISION_SYSTEM, build_ceo_user_message

logger = logging.getLogger(__name__)

CEO_MODEL = "sonnet"

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Strip markdown code fences (```json ... ```) from LLM responses."""
    m = _CODE_FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


class DandelionEngine:
    """Orchestrates: CEO → Researcher → Imaginers (real-time seed streaming)."""

    def __init__(self, ws):
        self._ws = ws
        self._cancelled = False
        self._semaphore = asyncio.Semaphore(10)
        self._bridge = get_bridge()
        # Supervisor/Imaginer now use CLI bridge internally
        self._supervisor = ThemeSupervisor()
        self._imaginer = Imaginer()

    def cancel(self):
        self._cancelled = True

    async def clarify(self, query: str, files: list[str]) -> list[str]:
        """Stage 0: Generate clarifying questions for the user."""
        user_msg = build_ceo_user_message(query, files)

        raw = await self._bridge.raw_query(
            system_prompt=CLARIFY_SYSTEM,
            user_message=user_msg,
            model=CEO_MODEL,
            allowed_tools=[],
            timeout=120,
        )

        text = _strip_code_fence(raw)
        try:
            data = json.loads(text)
            return data.get("questions", [])
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("clarify_failed: %s", exc)
            return []

    async def _progress(self, step: int, label: str, current: int = 0, total: int = 0):
        """Send progress update to frontend."""
        try:
            await self._ws.send_json({
                "type": "progress",
                "step": step,
                "label": label,
                "current": current,
                "total": total,
            })
        except Exception:
            pass

    async def run(self, query: str, files: list[str], clarify_answers: dict[str, str] | None = None) -> DandelionTree | None:
        """Run the full dandelion pipeline. Returns a DandelionTree on success."""
        from datetime import datetime

        # Stage 1: Theme decision
        await self._progress(1, "테마 결정 중...")
        assignment = await self._decide_themes(query, files, clarify_answers)
        themes_dicts = [t.to_ws_dict() for t in assignment.themes]
        await self._ws.send_json({"type": "themes", "themes": themes_dicts})

        if self._cancelled:
            return None

        # Stage 2: Single researcher collects data
        await self._progress(2, "데이터 수집 중...")
        research_packet = await self._supervisor.research(assignment.themes, assignment.common_context)
        logger.info("research_done packet_len=%d", len(research_packet))

        if self._cancelled:
            return None

        # Stage 3: Haiku imagination — seeds stream in real-time as each completes
        self._imagine_done = 0
        self._collected_seeds: list[Seed] = []
        await self._progress(3, "상상 중...", 0, 40)
        results = await asyncio.gather(*[
            self._run_theme_imaginations(theme, research_packet)
            for theme in assignment.themes
        ], return_exceptions=True)

        # Report errors for failed themes
        for theme, result in zip(assignment.themes, results):
            if isinstance(result, Exception):
                logger.error("theme_failed theme=%s error=%s", theme.id, result)
                try:
                    await self._ws.send_json({
                        "type": "theme_error",
                        "theme_id": theme.id,
                        "message": str(result),
                    })
                except Exception:
                    pass

        tree = DandelionTree(
            query=query,
            themes=assignment.themes,
            seeds=self._collected_seeds,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

        await self._ws.send_json({"type": "complete"})
        return tree

    async def _decide_themes(self, query: str, files: list[str], clarify_answers: dict[str, str] | None = None) -> ThemeAssignment:
        """Stage 1: Sonnet decides 4 themes."""
        user_msg = build_ceo_user_message(query, files)
        if clarify_answers:
            user_msg += "\n\n--- 사용자 추가 답변 ---\n"
            for idx, answer in sorted(clarify_answers.items(), key=lambda x: int(x[0])):
                user_msg += f"Q{int(idx)+1}: {answer}\n"

        raw = await self._bridge.raw_query(
            system_prompt=THEME_DECISION_SYSTEM,
            user_message=user_msg,
            model=CEO_MODEL,
            allowed_tools=[],
            timeout=120,
        )

        text = _strip_code_fence(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"테마 결정 응답 파싱 실패: {exc}\nResponse: {text[:500]}") from exc

        themes = []
        for i, t in enumerate(data["themes"][:4]):
            themes.append(Theme(
                id=f"theme_{i}",
                name=t["name"],
                color=THEME_COLORS[i],
                description=t["description"],
            ))

        for j in range(len(themes), 4):
            themes.append(Theme(
                id=f"theme_{j}",
                name=f"추가 관점 {j+1}",
                color=THEME_COLORS[j],
                description="자동 생성된 보조 테마",
            ))

        return ThemeAssignment(
            themes=themes,
            common_context=data.get("common_context", query),
            user_query=query,
        )

    async def _run_theme_imaginations(self, theme: Theme, research_packet: str):
        """Run 10 Haiku imaginers for one theme — each seed streamed immediately."""
        if self._cancelled:
            return

        await asyncio.gather(*[
            self._imagine_and_stream(theme, research_packet, i)
            for i in range(10)
        ])

    async def _imagine_and_stream(self, theme: Theme, research_packet: str, index: int):
        """Imagine one future and stream the seed to frontend immediately."""
        await asyncio.sleep(index * 0.2)  # Stagger to avoid burst
        async with self._semaphore:
            if self._cancelled:
                return

            imagination = await self._imaginer.imagine(
                theme_id=theme.id,
                theme_name=theme.name,
                theme_description=theme.description,
                context_packet=research_packet,
                agent_index=index,
            )

            self._imagine_done += 1
            await self._progress(3, "상상 중...", self._imagine_done, 40)

            # Skip failed imaginations
            if imagination.title == "상상 생성 실패":
                return

            # Convert Imagination → Seed and stream immediately
            seed = Seed(
                id=imagination.id,
                theme_id=imagination.theme_id,
                title=imagination.title,
                summary=imagination.summary,
                detail=imagination.detail,
                time_months=imagination.time_months,
                weight=1,
                source_count=1,
            )

            self._collected_seeds.append(seed)

            try:
                await self._ws.send_json({
                    "type": "seed",
                    "theme_id": theme.id,
                    "seed": seed.to_ws_dict(),
                })
            except Exception:
                pass
