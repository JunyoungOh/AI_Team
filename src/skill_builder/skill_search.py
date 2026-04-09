"""기존 Claude Code 스킬을 검색하여 "이미 만들어진 것" 여부를 판단.

검색 소스:
  1. ~/.claude/skills/          — 사용자 머신에 설치된 스킬
  2. ~/.claude/plugins/install-counts-cache.json — 정량 필터 기준

필터 순서:
  1) skill-tab- 프리픽스 제외 (앱이 만든 것은 이미 레지스트리에 있음)
  2) trusted_marketplaces.json의 min_unique_installs 임계값 필터
  3) description·name에 대한 단순 키워드 매칭
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_CONFIG_PATH = Path(__file__).parent / "trusted_marketplaces.json"


@dataclass
class SkillCandidate:
    slug: str
    name: str
    description: str
    source_marketplace: Optional[str]
    unique_installs: int
    skill_md_path: Path


class SkillSearchIndex:
    def __init__(
        self,
        candidates: List[SkillCandidate],
        min_installs: int,
    ) -> None:
        self._candidates = candidates
        self._min_installs = min_installs

    @classmethod
    def load(cls) -> "SkillSearchIndex":
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        min_installs = cfg.get("min_unique_installs", 1000)

        home = Path(os.environ.get("HOME", "~")).expanduser()
        skills_dir = home / ".claude" / "skills"
        counts_path = home / ".claude" / "plugins" / "install-counts-cache.json"

        install_counts = cls._load_install_counts(counts_path)

        candidates: List[SkillCandidate] = []
        if skills_dir.exists():
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                if skill_dir.name.startswith("skill-tab-"):
                    continue  # 앱이 만든 스킬은 레지스트리에서 관리
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                name, desc = cls._parse_frontmatter(skill_md)
                installs = install_counts.get(skill_dir.name, 0)
                candidates.append(
                    SkillCandidate(
                        slug=skill_dir.name,
                        name=name,
                        description=desc,
                        source_marketplace=None,
                        unique_installs=installs,
                        skill_md_path=skill_md,
                    )
                )
        return cls(candidates=candidates, min_installs=min_installs)

    def search(self, query: str) -> List[SkillCandidate]:
        terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
        if not terms:
            return []
        matches: list[tuple[int, SkillCandidate]] = []
        for c in self._candidates:
            if c.unique_installs < self._min_installs:
                continue
            haystack = f"{c.name} {c.description}".lower()
            score = sum(1 for t in terms if t in haystack)
            if score > 0:
                matches.append((score, c))
        matches.sort(key=lambda x: (-x[0], -x[1].unique_installs))
        return [c for _, c in matches]

    @staticmethod
    def _load_install_counts(path: Path) -> dict:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, int] = {}
        for entry in raw.get("counts", []):
            plugin = entry.get("plugin", "")
            # "name@marketplace" 형태에서 name만 추출
            slug = plugin.split("@", 1)[0]
            result[slug] = entry.get("unique_installs", 0)
        return result

    @staticmethod
    def _parse_frontmatter(path: Path) -> tuple[str, str]:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return path.parent.name, ""
        parts = text.split("---", 2)
        if len(parts) < 3:
            return path.parent.name, ""
        fm = parts[1]
        name = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        desc = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
        return (
            name.group(1).strip() if name else path.parent.name,
            desc.group(1).strip() if desc else "",
        )
