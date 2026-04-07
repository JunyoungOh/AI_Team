"""Unified bridge factory for local version (CLI-only mode).

Local 버전은 항상 ClaudeCodeBridge(CLI subprocess)를 사용한다.
AnthropicBridge(API 직접 호출)는 사용하지 않는다.

이 모듈은 두 가지 역할:
  1. get_bridge() — ClaudeCodeBridge 싱글턴 반환
  2. 공용 헬퍼 — API 버전의 AnthropicBridge에 있던 유틸리티 중
     다른 모듈에서 사용하는 것을 독립 함수로 제공
"""

from __future__ import annotations

import re
import threading
from typing import Any

from src.utils.claude_code import ClaudeCodeBridge

_bridge: ClaudeCodeBridge | None = None
_lock = threading.Lock()


# ── Model mapping ──────────────────────────────────

MODEL_MAP: dict[str, str] = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def resolve_model(alias: str) -> str:
    """모델 별칭(opus/sonnet/haiku)을 전체 모델 ID로 변환."""
    return MODEL_MAP.get(alias, alias)


# ── Bridge singleton ───────────────────────────────

def get_bridge() -> ClaudeCodeBridge:
    """ClaudeCodeBridge 싱글턴을 반환한다. Thread-safe."""
    global _bridge
    if _bridge is None:
        with _lock:
            if _bridge is None:
                _bridge = ClaudeCodeBridge()
    return _bridge


# ── Shared helpers (engineering/session 등에서 사용) ──

def prepare_cached_messages(messages: list[dict]) -> list[dict]:
    """마지막 user 메시지에 cache_control을 추가한다.

    CLI 모드에서는 프롬프트 캐싱이 CLI 내부에서 처리되므로
    이 함수는 no-op에 가깝지만, 코드 호환성을 위해 유지한다.
    """
    # CLI 모드에서는 캐싱을 Claude Code가 자체 관리하므로
    # 메시지를 그대로 반환해도 무방
    return list(messages)


def prune_old_tool_results(
    messages: list[dict],
    keep_recent: int = 3,
    summary_len: int = 200,
) -> None:
    """오래된 tool_result를 요약하여 컨텍스트를 줄인다 (in-place)."""
    tr_indices: list[int] = []
    for i, msg in enumerate(messages):
        if (
            msg.get("role") == "user"
            and isinstance(msg.get("content"), list)
            and any(
                item.get("type") == "tool_result"
                for item in msg["content"]
                if isinstance(item, dict)
            )
        ):
            tr_indices.append(i)

    if len(tr_indices) <= keep_recent:
        return

    for idx in tr_indices[:-keep_recent]:
        for item in messages[idx]["content"]:
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            raw = item.get("content", "")
            if isinstance(raw, str) and len(raw) > summary_len + 100:
                item["content"] = raw[:summary_len] + "\n...[이전 결과 요약됨]"


# ── Insight block stripping ────────────────────────

_INSIGHT_RE = re.compile(
    r'`?★\s*Insight[─\s]*`?\s*'
    r'.*?'
    r'`?[─]{10,}`?',
    re.DOTALL,
)


def strip_insight_blocks(text: str) -> str:
    """★ Insight 블록을 제거한다."""
    return _INSIGHT_RE.sub('', text).strip()


def clean_insight_from_dict(data: Any) -> Any:
    """dict 내 모든 문자열에서 ★ Insight 블록을 재귀적으로 제거한다."""
    if isinstance(data, str):
        return strip_insight_blocks(data)
    if isinstance(data, dict):
        return {k: clean_insight_from_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [clean_insight_from_dict(item) for item in data]
    return data
