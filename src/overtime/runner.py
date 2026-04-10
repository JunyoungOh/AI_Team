"""야근팀 실행 엔진 — 목표 달성까지 반복 iteration.

각 iteration:
  1. 싱글 세션으로 정보 수집 → raw_{n}.md 저장
  2. 평가 세션으로 달성률 판단
  3. score >= 90 이면 최종 보고서 생성, 아니면 다음 iteration

파일 시스템이 iteration 간 메모리 역할.
NOTE: asyncio.create_subprocess_exec 사용하여 shell injection 방지.
      모든 CLI 인자는 배열로 전달됨 (shell=False).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from src.config.settings import get_settings
from src.modes.common import emit_mode_event
from src.overtime.prompts import (
    build_evaluation_prompt,
    build_final_report_prompt,
    build_iteration_prompt,
)
from src.utils.logging import get_logger

_logger = get_logger(agent_id="overtime_runner")

_OVERTIME_TOOLS = [
    "WebSearch", "WebFetch", "Read", "Write",
    "Bash", "Glob", "Grep", "Agent",
    "mcp__firecrawl__firecrawl_scrape",
]

_EVAL_TOOLS = ["Read", "Glob"]

SCORE_THRESHOLD = 90
RATE_LIMIT_COOLDOWN_DEFAULT = 300  # 기본 5분 대기

# rate limit 감지 키워드
_RATE_LIMIT_SIGNALS = ["rate_limit", "rate limit", "overloaded", "429", "quota"]


class RateLimitError(Exception):
    """CLI 세션이 rate limit에 걸렸을 때 발생."""
    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


def _parse_cooldown_seconds(error_text: str) -> int:
    """에러 메시지에서 대기 시간을 추출. 못 찾으면 기본값 반환.

    CLI 에러에 "retry after X seconds", "reset in X minutes",
    "try again in Xm Ys" 등의 패턴이 있을 수 있음.
    """
    import re
    # "retry after 300 seconds" 또는 "after 300s"
    m = re.search(r"(?:retry|wait|after)\s+(\d+)\s*(?:s|sec|seconds)", error_text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # "in 5 minutes" 또는 "in 5m"
    m = re.search(r"in\s+(\d+)\s*(?:m|min|minutes)", error_text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60
    # "reset at HH:MM" (UTC 또는 로컬)
    m = re.search(r"reset\s+(?:at\s+)?(\d{1,2}):(\d{2})", error_text, re.IGNORECASE)
    if m:
        from datetime import datetime, timedelta
        now = datetime.now()
        reset_time = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
        if reset_time <= now:
            reset_time += timedelta(days=1)
        wait = (reset_time - now).total_seconds()
        return max(60, int(wait))  # 최소 1분
    return RATE_LIMIT_COOLDOWN_DEFAULT


async def _run_cli_session(
    system_prompt: str,
    user_prompt: str,
    tools: list[str],
    session_id: str,
    model: str = "sonnet",
    max_turns: int = 60,
    timeout: int = 420,
) -> str:
    """CLI subprocess를 실행하고 결과를 반환. rate limit 시 RateLimitError."""
    from src.utils.claude_code import (
        _register_process,
        _unregister_process,
        _kill_process_tree,
    )

    # asyncio.create_subprocess_exec: 인자가 배열로 전달되어 shell injection 방지
    cmd = [
        "claude", "-p", user_prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--max-turns", str(max_turns),
        "--append-system-prompt", system_prompt,
        "--allowedTools", ",".join(tools),
        "--permission-mode", "auto",
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=os.getcwd(),
        start_new_session=True,
        env=env,
    )
    _register_process(proc)

    full_text = ""
    tool_count = 0
    start = time.time()

    _TOOL_LABELS = {
        "WebSearch": "검색", "WebFetch": "수집",
        "Agent": "에이전트", "Write": "저장",
        "Read": "읽기", "Bash": "실행",
    }

    try:
        async with asyncio.timeout(timeout):
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text":
                            full_text += block["text"]
                        elif block.get("type") == "tool_use":
                            tool_count += 1
                            tool_name = block.get("name", "")
                            label = _TOOL_LABELS.get(tool_name, tool_name)
                            emit_mode_event(session_id, {
                                "type": "overtime_activity",
                                "data": {"tool": tool_name, "label": label, "count": tool_count},
                            })

                elif event.get("type") == "result":
                    result_text = event.get("result", "")
                    is_error = event.get("is_error", False)
                    subtype = event.get("subtype", "")

                    # rate limit 감지
                    if is_error:
                        error_lower = (result_text + subtype).lower()
                        for signal in _RATE_LIMIT_SIGNALS:
                            if signal in error_lower:
                                _logger.warning("overtime_rate_limited", result=result_text[:200])
                                raise RateLimitError(result_text[:200])

                    if not full_text and result_text:
                        full_text = result_text

    except TimeoutError:
        _logger.warning("overtime_session_timeout", elapsed=round(time.time() - start, 1))
        await _kill_process_tree(proc)
    except RateLimitError:
        await _kill_process_tree(proc)
        raise  # 상위에서 처리
    finally:
        _unregister_process(proc)

    return full_text


def _parse_eval_json(text: str) -> dict:
    """평가 결과에서 eval_json 블록을 추출."""
    marker = "```eval_json"
    idx = text.find(marker)
    if idx == -1:
        return {"score": 0, "summary": "평가 파싱 실패", "gaps": [], "recommendation": ""}
    start = idx + len(marker)
    end = text.find("```", start)
    if end == -1:
        return {"score": 0, "summary": "평가 파싱 실패", "gaps": [], "recommendation": ""}
    try:
        return json.loads(text[start:end].strip())
    except json.JSONDecodeError:
        return {"score": 0, "summary": "평가 JSON 파싱 실패", "gaps": [], "recommendation": ""}


async def run_overtime(
    task: str,
    strategy: dict | None,
    goal: str,
    session_id: str,
    user_id: str = "",
    max_iterations: int = 5,
    overtime_id: str = "",
    file_context: str = "",  # NEW
) -> str:
    """야근팀 전체 루프 실행. 완료 시 report_dir 반환."""
    from src.company_builder.storage import update_overtime_iteration

    settings = get_settings()
    work_dir = f"data/overtime/{session_id}"
    report_dir = f"data/reports/{session_id}"
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    previous_eval = None
    iteration = 0

    effective_task = task
    if file_context:
        effective_task = task + "\n\n" + file_context

    for iteration in range(1, max_iterations + 1):
        _logger.info("overtime_iteration_start", iteration=iteration, session_id=session_id)

        emit_mode_event(session_id, {
            "type": "overtime_iteration",
            "data": {
                "action": "start",
                "iteration": iteration,
                "max_iterations": max_iterations,
            },
        })

        # 1. 수집 세션 (rate limit 시 대기 후 재시도)
        system, user = build_iteration_prompt(
            task=effective_task, strategy=strategy, goal=goal,
            iteration=iteration, work_dir=work_dir,
            previous_eval=previous_eval,
        )

        iter_start = time.time()
        for attempt in range(3):  # 최대 3회 재시도
            try:
                await _run_cli_session(
                    system_prompt=system, user_prompt=user,
                    tools=_OVERTIME_TOOLS, session_id=session_id,
                    model=settings.worker_model, max_turns=60, timeout=420,
                )
                break  # 성공
            except RateLimitError as e:
                cooldown = _parse_cooldown_seconds(e.message)
                cooldown_min = cooldown // 60
                _logger.warning("overtime_rate_limit_hit", iteration=iteration, attempt=attempt + 1, cooldown_s=cooldown)
                emit_mode_event(session_id, {
                    "type": "overtime_iteration",
                    "data": {
                        "action": "rate_limited",
                        "iteration": iteration,
                        "message": f"⏸️ 사용량 한도 도달 — {cooldown_min}분 후 자동 재시도 ({attempt + 1}/3)",
                        "cooldown": cooldown,
                    },
                })
                if overtime_id and user_id:
                    update_overtime_iteration(user_id, overtime_id, {
                        "id": f"{iteration}_pause_{attempt}",
                        "action": "rate_limited",
                        "cooldown_s": cooldown,
                        "message": e.message,
                    }, status="paused")
                await asyncio.sleep(cooldown)
        iter_elapsed = round(time.time() - iter_start, 1)

        # 2. 평가 세션
        eval_system, eval_user = build_evaluation_prompt(work_dir, goal, iteration)
        try:
            eval_text = await _run_cli_session(
                system_prompt=eval_system, user_prompt=eval_user,
                tools=_EVAL_TOOLS, session_id=session_id,
                model=settings.worker_model, max_turns=10, timeout=120,
            )
        except RateLimitError as e:
            cooldown = _parse_cooldown_seconds(e.message)
            _logger.warning("overtime_eval_rate_limited", iteration=iteration, cooldown_s=cooldown)
            emit_mode_event(session_id, {
                "type": "overtime_iteration",
                "data": {"action": "rate_limited", "message": f"⏸️ 평가 중 한도 도달 — {cooldown // 60}분 후 재시도"},
            })
            await asyncio.sleep(cooldown)
            eval_text = await _run_cli_session(
                system_prompt=eval_system, user_prompt=eval_user,
                tools=_EVAL_TOOLS, session_id=session_id,
                model=settings.worker_model, max_turns=10, timeout=120,
            )

        eval_result = _parse_eval_json(eval_text)
        score = eval_result.get("score", 0)
        previous_eval = eval_result

        _logger.info("overtime_iteration_complete", iteration=iteration, score=score, elapsed_s=iter_elapsed)

        iter_record = {
            "id": iteration,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed_s": iter_elapsed,
            "score": score,
            "summary": eval_result.get("summary", ""),
            "gaps": eval_result.get("gaps", []),
        }

        status = "running"
        if score >= SCORE_THRESHOLD or iteration >= max_iterations:
            status = "finalizing"

        if overtime_id and user_id:
            update_overtime_iteration(user_id, overtime_id, iter_record, status)

        emit_mode_event(session_id, {
            "type": "overtime_iteration",
            "data": {
                "action": "scored",
                "iteration": iteration,
                "score": score,
                "summary": eval_result.get("summary", ""),
                "gaps": eval_result.get("gaps", []),
                "elapsed": iter_elapsed,
            },
        })

        if score >= SCORE_THRESHOLD:
            _logger.info("overtime_goal_reached", iteration=iteration, score=score)
            break

    # 3. 최종 보고서 생성
    emit_mode_event(session_id, {
        "type": "overtime_iteration",
        "data": {"action": "finalizing", "message": "최종 보고서 생성 중..."},
    })

    final_system, final_user = build_final_report_prompt(task, work_dir, report_dir)
    for attempt in range(3):
        try:
            await _run_cli_session(
                system_prompt=final_system, user_prompt=final_user,
                tools=_OVERTIME_TOOLS, session_id=session_id,
                model=settings.worker_model, max_turns=40, timeout=300,
            )
            break
        except RateLimitError as e:
            cooldown = _parse_cooldown_seconds(e.message)
            _logger.warning("overtime_final_report_rate_limited", attempt=attempt + 1, cooldown_s=cooldown)
            emit_mode_event(session_id, {
                "type": "overtime_iteration",
                "data": {"action": "rate_limited", "message": f"⏸️ 보고서 생성 중 한도 도달 — {cooldown // 60}분 후 재시도 ({attempt + 1}/3)"},
            })
            await asyncio.sleep(cooldown)

    # fallback
    report_path = Path(report_dir) / "results.html"
    if not report_path.exists():
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        raw_files = sorted(Path(work_dir).glob("raw_*.md"))
        combined = "\n\n---\n\n".join(
            f.read_text(encoding="utf-8") for f in raw_files if f.exists()
        )
        import html as html_mod
        report_path.write_text(
            '<html><head><meta charset="UTF-8"></head>'
            f'<body><pre>{html_mod.escape(combined[:50000])}</pre></body></html>',
            encoding="utf-8",
        )

    if overtime_id and user_id:
        update_overtime_iteration(
            user_id, overtime_id,
            {"id": "final", "action": "report_generated", "report_dir": report_dir},
            status="completed",
        )

    emit_mode_event(session_id, {
        "type": "overtime_iteration",
        "data": {
            "action": "completed",
            "report_path": f"/reports/{session_id}",
            "total_iterations": iteration,
        },
    })

    return report_dir
