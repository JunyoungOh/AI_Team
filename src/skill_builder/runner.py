"""스킬 만들기 세션 오케스트레이터.

핵심 책임 (순서대로):
  1) "start" 메시지 수신 → 한국어 인사
  2) 기존 스킬 검색 → 결과 emit
  3) 사용자 선택 대기 ("new" 또는 "import:<slug>")
  4) "new"면 skill-creator 세션 resume 루프 진입
  5) 완료 후 검증 + 레지스트리 등록

세션 resume 루프 핵심:
  - raw_query(session_id=...)로 첫 턴 시작
  - Claude 응답을 사용자에게 전달
  - 사용자 답변(type="user_message") 수신
  - raw_query(resume=session_id, user_message=답변)으로 다음 턴
  - [SKILL_COMPLETE] 토큰이 응답에 나타날 때까지 반복
  - 최대 MAX_INTERVIEW_ROUNDS 라운드 제한
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.skill_builder.clarification_questions import build_entry_greeting
from src.skill_builder.skill_search import SkillSearchIndex

if TYPE_CHECKING:
    from fastapi import WebSocket


MAX_INTERVIEW_ROUNDS = 15
COMPLETION_TOKEN = "[SKILL_COMPLETE]"


def _slugify(text: str) -> str:
    """한국어/영어 혼합 입력을 ASCII slug로 변환.

    의미 있는 slug가 되려면 최소 3개의 알파벳 문자가 있어야 함.
    그렇지 않으면 (한국어 위주 입력) timestamp + short uuid fallback.
    """
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    alpha_count = sum(1 for c in ascii_only if c.isalpha())
    if ascii_only and alpha_count >= 3:
        return ascii_only[:40]
    ts = int(datetime.now(timezone.utc).timestamp())
    short = uuid.uuid4().hex[:6]
    return f"{ts}-{short}"


async def run_skill_builder_session(ws: "WebSocket") -> None:
    """단일 WebSocket 세션을 종료까지 처리."""
    # 1) start 메시지 대기
    msg = await ws.receive_json()
    if msg.get("type") != "start":
        await ws.send_json(
            {
                "type": "error",
                "data": {"message": "첫 메시지는 'start' 타입이어야 합니다"},
            }
        )
        return

    description = (msg.get("data") or {}).get("description", "").strip()
    workspace_files = (msg.get("data") or {}).get("workspace_files", [])
    if not description:
        await ws.send_json(
            {
                "type": "error",
                "data": {"message": "스킬 설명을 입력해주세요"},
            }
        )
        return

    # 2) 한국어 인사
    await ws.send_json(
        {
            "type": "greeting",
            "data": {"text": build_entry_greeting(description)},
        }
    )

    # 3) 기존 스킬 검색 — 자동 진행 (명확화 인터뷰는 skill-creator가 직접 수행)
    try:
        index = SkillSearchIndex.load()
        candidates = index.search(description)
    except Exception:
        candidates = []

    await ws.send_json(
        {
            "type": "search_results",
            "data": {
                "candidates": [
                    {
                        "slug": c.slug,
                        "name": c.name,
                        "description": c.description,
                        "unique_installs": c.unique_installs,
                    }
                    for c in candidates[:5]
                ],
            },
        }
    )

    # 4) 클라이언트가 자동으로 'new' choice를 보냄 (현재 단계에서는 import 미지원)
    choice_msg = await ws.receive_json()
    if choice_msg.get("type") == "cancel":
        return
    if choice_msg.get("type") != "choice":
        await ws.send_json(
            {
                "type": "error",
                "data": {"message": "선택 메시지(choice)를 기다리고 있었습니다"},
            }
        )
        return
    choice = (choice_msg.get("data") or {}).get("choice", "new")
    if choice != "new":
        await ws.send_json(
            {
                "type": "error",
                "data": {"message": "기존 스킬 가져오기는 다음 버전에서 지원 예정"},
            }
        )
        return

    # 5) 세션 resume 루프 — skill-creator가 자체 인터뷰부터 저장까지 한 세션에서 수행
    await _run_skill_creator_loop(ws, description, workspace_files=workspace_files)


async def _run_skill_creator_loop(
    ws: "WebSocket",
    description: str,
    workspace_files: list[str] | None = None,
) -> None:
    """skill-creator를 세션 resume 방식으로 다중 턴 호출.

    핵심 로직:
      - session_id를 한 번 생성
      - 첫 턴: raw_query(session_id=..., user_message=description)
      - 이후 N턴: 사용자가 ws로 user_message 보낼 때마다
                 raw_query(resume=session_id, user_message=답변)
      - 응답에 [SKILL_COMPLETE] 등장 또는 MAX_INTERVIEW_ROUNDS 초과 시 종료

    CLAUDE.md 누출 방지: allowed_tools가 모두 _BUILTIN_TOOLS이라
    _all_builtin()=True → skip_mcp=True → subprocess가 /tmp에서 실행.
    """
    from src.config.settings import get_settings
    from src.skill_builder.registry import SkillRecord, SkillRegistry
    from src.skill_builder.skill_creator_bridge import (
        build_handoff_system_prompt,
    )
    from src.utils.claude_code import ClaudeCodeBridge

    slug = _slugify(description)

    # 설치 위치: 프로젝트 내부 data/skills/installed/.
    # ~/.claude/skills/는 Claude Code의 시스템 보호 영역이라 headless
    # 모드에서 쓰기 권한 자동 승인이 안 됨 (TTY 없어서 prompt 거부됨).
    # data/skills/installed/는 우리 앱 영역이라 권한 문제 없음.
    install_root = Path.cwd() / "data" / "skills" / "installed"
    install_root.mkdir(parents=True, exist_ok=True)

    system_prompt = build_handoff_system_prompt(
        user_description=description,
        slug=slug,
        install_root=str(install_root),
    )

    bridge = ClaudeCodeBridge()
    settings = get_settings()
    session_id = str(uuid.uuid4())
    allowed = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
    extra_dirs = [str(install_root)]

    from src.utils.workspace import read_files_as_context

    effective_desc = description
    if workspace_files:
        file_ctx = read_files_as_context("skill", workspace_files)
        if file_ctx:
            effective_desc = description + "\n\n" + file_ctx

    # 첫 턴: 세션 시작
    try:
        response = await bridge.raw_query(
            system_prompt=system_prompt,
            user_message=effective_desc,
            model="sonnet",
            allowed_tools=allowed,
            max_turns=30,
            timeout=settings.planning_timeout,
            session_id=session_id,
            extra_dirs=extra_dirs,
        )
    except Exception as e:
        await ws.send_json(
            {
                "type": "error",
                "data": {"message": f"세션 시작 실패: {e}"},
            }
        )
        return

    await ws.send_json(
        {
            "type": "assistant_message",
            "data": {"text": response},
        }
    )

    # Resume 루프
    for round_num in range(MAX_INTERVIEW_ROUNDS):
        if COMPLETION_TOKEN in response:
            break

        # 사용자 답변 대기
        reply_msg = await ws.receive_json()
        if reply_msg.get("type") == "cancel":
            return
        if reply_msg.get("type") != "user_message":
            await ws.send_json(
                {
                    "type": "error",
                    "data": {
                        "message": "user_message 타입을 기다리고 있었습니다"
                    },
                }
            )
            return

        user_text = (reply_msg.get("data") or {}).get("text", "").strip()
        if not user_text:
            continue

        try:
            response = await bridge.raw_query(
                system_prompt=system_prompt,
                user_message=user_text,
                model="sonnet",
                allowed_tools=allowed,
                max_turns=30,
                timeout=settings.planning_timeout,
                resume=session_id,
                extra_dirs=extra_dirs,
            )
        except Exception as e:
            await ws.send_json(
                {
                    "type": "error",
                    "data": {
                        "message": (
                            f"세션 resume 실패 (라운드 {round_num + 1}): {e}"
                        ),
                    },
                }
            )
            return

        await ws.send_json(
            {
                "type": "assistant_message",
                "data": {"text": response},
            }
        )
    else:
        # for-else: break 없이 루프 종료 = 최대 라운드 초과
        await ws.send_json(
            {
                "type": "error",
                "data": {
                    "message": (
                        f"최대 대화 라운드({MAX_INTERVIEW_ROUNDS}) 초과. "
                        "스킬이 완성되지 못했습니다."
                    ),
                },
            }
        )
        return

    # 6) 검증: 스킬 파일 존재 + subagent는 존재하지 않음
    skill_path = install_root / f"skill-tab-{slug}"
    skill_md = skill_path / "SKILL.md"
    # subagent 누수 검사는 ~/.claude/agents/ 그대로 (handoff prompt가 거기 쓰지
    # 말라고 했지만, 실제로 거기 만들면 시스템 글로벌 자동 트리거 위험).
    subagent_path = Path.home() / ".claude" / "agents" / f"skill-tab-{slug}.md"

    if subagent_path.exists():
        await ws.send_json(
            {
                "type": "error",
                "data": {
                    "message": (
                        "skill-creator가 스킬 대신 subagent를 만들었습니다. "
                        "핸드오프 프롬프트 확인이 필요합니다."
                    ),
                },
            }
        )
        return

    if not skill_md.exists():
        await ws.send_json(
            {
                "type": "error",
                "data": {
                    "message": (
                        "완료 토큰은 받았지만 SKILL.md 파일이 없습니다. "
                        f"예상 경로: {skill_md}"
                    ),
                },
            }
        )
        return

    # 7) 레지스트리 등록
    reg = SkillRegistry(path=Path("data/skills/registry.json"))

    # skill_metadata.json에서 required_mcps 읽기 (없으면 빈 리스트)
    required_mcps: list[str] = []
    meta_path = skill_path / "skill_metadata.json"
    if meta_path.exists():
        try:
            import json as _json

            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            required_mcps = meta.get("required_mcps", []) or []
        except Exception:
            pass  # 파싱 실패는 무시 — 빈 리스트로 진행

    record = SkillRecord(
        slug=slug,
        name=description[:50],
        skill_path=str(skill_path),
        required_mcps=required_mcps,
        source="created",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        reg.add(record)
    except ValueError:
        # 동일 slug 충돌 (드문 경우) — timestamp suffix 재시도
        record.slug = (
            f"{slug}-{int(datetime.now(timezone.utc).timestamp())}"
        )
        reg.add(record)

    await ws.send_json(
        {
            "type": "created",
            "data": {
                "slug": record.slug,
                "skill_path": record.skill_path,
            },
        }
    )
