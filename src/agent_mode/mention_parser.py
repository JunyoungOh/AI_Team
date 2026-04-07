"""Parse @mentions and provide autocomplete candidates."""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.config.agent_registry import LEADER_DOMAINS
from src.config.personas import WORKER_NAMES


@dataclass
class ParseResult:
    agent_id: str | None
    message: str


class MentionParser:
    def __init__(self) -> None:
        self._entries = self._build_entries()

    def _build_entries(self) -> list[dict]:
        entries = []
        for domain, info in LEADER_DOMAINS.items():
            for wid in info["worker_types"]:
                meta = WORKER_NAMES.get(wid, {})
                entries.append({
                    "id": wid,
                    "name": meta.get("name", wid),
                    "domain": domain,
                    "keywords": meta.get("keywords", []),
                })
        return entries

    def parse(self, text: str) -> ParseResult:
        m = re.match(r"^@(\S+)\s*(.*)", text, re.DOTALL)
        if not m:
            return ParseResult(agent_id=None, message=text.strip())
        mention, rest = m.group(1), m.group(2).strip()
        agent_id = self._resolve(mention)
        if agent_id:
            return ParseResult(agent_id=agent_id, message=rest)
        return ParseResult(agent_id=None, message=text.strip())

    def _resolve(self, mention: str) -> str | None:
        q = mention.lower()
        for e in self._entries:
            if e["id"] == q:
                return e["id"]
        for e in self._entries:
            if e["name"] == mention:
                return e["id"]
        for e in self._entries:
            if e["id"].startswith(q):
                return e["id"]
        for e in self._entries:
            if any(q == kw or kw.startswith(q) for kw in e["keywords"]):
                return e["id"]
        return None

    def autocomplete(self, query: str) -> list[dict]:
        if not query:
            return sorted(self._entries, key=lambda e: (e["domain"], e["name"]))
        q = query.lower()
        matches = []
        for e in self._entries:
            if (q in e["id"] or q in e["name"]
                    or any(q in kw for kw in e["keywords"])):
                matches.append(e)
        return sorted(matches, key=lambda e: (e["domain"], e["name"]))
