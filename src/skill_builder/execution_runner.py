"""스킬 실행 오케스트레이터.

run_skill(slug, user_input, on_event):
  1) skill_loader로 컨텍스트 로드
  2) isolation_mode에 따라 cwd / allowed_tools 결정
  3) execution_streamer 호출
  4) 결과를 run_history에 저장
  5) RunRecord 반환

에러는 모두 잡아서 status="error" RunRecord로 저장 후 반환.
호출자(WS endpoint)는 예외를 처리할 필요 없이 record.status만 보면 된다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from src.config.settings import get_settings
from src.skill_builder.execution_streamer import stream_skill_execution
from src.skill_builder.run_history import RunRecord, save_run
from src.skill_builder.skill_loader import (
    IsolationMode,
    load_skill_for_execution,
)


_BUILTIN_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]


def _build_allowed_tools_for_mcps(required_mcps: list[str]) -> list[str]:
    """required_mcps를 mcp__<server>__<tool> 형식으로 변환.

    Claude Code의 --allowedTools는 정확한 도구명을 요구하므로 각 MCP 서버의
    잘 알려진 도구를 명시한다.

    주의: 플러그인으로 설치된 MCP는 네임스페이스가
    ``mcp__plugin_<ns>_<server>__<tool>`` 형태로 달라질 수 있다. 아래 맵은
    프로젝트 ``.mcp.json``에 직접 등록된 서버 기준이다. 새 MCP를 추가할
    때는 ``src/utils/tool_definitions.py``와 실제 스트림에서 관찰되는
    도구명으로 검증할 것.

    후속 과제: skill_metadata.json에 구체적 tool 이름(``required_tools``)
    필드를 추가하면 이 매핑이 불필요해진다.
    """
    known_tools_by_server = {
        "serper": ["mcp__serper__google_search"],
        "firecrawl": [
            "mcp__firecrawl__firecrawl_scrape",
            "mcp__firecrawl__firecrawl_search",
        ],
        "brave-search": ["mcp__brave-search__brave_web_search"],
        "github": [
            "mcp__github__search_code",
            "mcp__github__search_repositories",
        ],
        "mem0": [
            "mcp__mem0__search_memories",
            "mcp__mem0__add_memory",
        ],
        # context7는 플러그인 네임스페이스가 필요할 수 있어 초기 맵에서 제외.
        # required_mcps=["context7"] 스킬은 알려진 도구가 없어서 빈 리스트가
        # 반환되고, 호출자는 빌트인 도구만으로 진행한다.
    }
    out: list[str] = []
    for server in required_mcps:
        out.extend(known_tools_by_server.get(server, []))
    return out


def _build_system_prompt(skill_body: str) -> str:
    return (
        skill_body
        + "\n\n---\n\n## 실행 컨텍스트\n"
        + "사용자의 입력은 다음 user message로 전달됩니다. "
        + "이 스킬의 절차에 따라 작업을 수행하고 결과를 한국어로 반환하세요.\n"
    )


async def run_skill(
    *,
    slug: str,
    user_input: str,
    on_event: Callable[[dict], None],
    timeout: int = 600,
    model: str = "sonnet",
    runs_root: Optional[Path] = None,
) -> RunRecord:
    """스킬을 한 번 실행하고 RunRecord를 반환.

    예외는 내부에서 모두 잡아서 status="error" 레코드로 저장한다.
    """
    started = time.time()

    try:
        ctx = load_skill_for_execution(slug)
    except Exception as e:
        record = save_run(
            slug=slug,
            user_input=user_input,
            result_text="",
            status="error",
            tool_count=0,
            duration_seconds=round(time.time() - started, 2),
            error_message=str(e),
            runs_root=runs_root,
        )
        on_event({"action": "error", "message": f"스킬 로드 실패: {e}"})
        return record

    if ctx.isolation_mode == IsolationMode.ISOLATED:
        cwd = "/tmp"
        allowed_tools = list(_BUILTIN_TOOLS)
    else:
        cwd = os.getcwd()
        allowed_tools = list(_BUILTIN_TOOLS) + _build_allowed_tools_for_mcps(
            ctx.required_mcps
        )

    system_prompt = _build_system_prompt(ctx.skill_body)

    try:
        full_text, tool_count, timed_out = await stream_skill_execution(
            prompt=user_input or "스킬을 실행하세요",
            system_prompt=system_prompt,
            model=model,
            allowed_tools=allowed_tools,
            cwd=cwd,
            timeout=timeout,
            on_event=on_event,
            effort=get_settings().worker_effort,
        )
    except Exception as e:
        return save_run(
            slug=slug,
            user_input=user_input,
            result_text="",
            status="error",
            tool_count=0,
            duration_seconds=round(time.time() - started, 2),
            error_message=str(e),
            runs_root=runs_root,
        )

    status = "timeout" if timed_out else "completed"
    return save_run(
        slug=slug,
        user_input=user_input,
        result_text=full_text,
        status=status,
        tool_count=tool_count,
        duration_seconds=round(time.time() - started, 2),
        runs_root=runs_root,
    )
