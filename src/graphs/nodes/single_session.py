"""Single CLI session execution node — streaming version.

명확화 질문 완료 후, 하나의 Claude Code CLI 세션에서
리서치 → 합성 → HTML 보고서 생성까지 전체 작업을 실행한다.

Secretary chat_engine.py의 스트리밍 패턴을 적용:
- subprocess stdout을 line-by-line으로 읽어 stream-json 파싱
- tool_use 이벤트를 mode event queue로 실시간 전달
- sim_runner가 queue를 폴링하여 WebSocket으로 브라우저에 전송

NOTE: asyncio.create_subprocess_exec를 사용하여 shell injection을 방지.
      모든 인자는 배열로 전달됨 (shell=False).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from langchain_core.messages import AIMessage

from src.config.settings import get_settings
from src.modes.common import emit_mode_event
from src.prompts.single_session_prompts import (
    SINGLE_SESSION_SYSTEM,
    build_execution_prompt,
)
from src.utils.logging import get_logger

_logger = get_logger(agent_id="single_session")

# 싱글 세션에서 사용할 도구 목록
_SESSION_TOOLS = [
    "WebSearch", "WebFetch", "Read", "Write",
    "Bash", "Glob", "Grep", "Agent",
    "mcp__firecrawl__firecrawl_scrape",
]

# 도구명 → 사용자 친화적 상태 메시지
_TOOL_STATUS = {
    "WebSearch": "🔍 웹 검색 중...",
    "WebFetch": "🌐 웹 페이지 수집 중...",
    "Agent": "🤖 서브에이전트 실행 중...",
    "Write": "📝 파일 작성 중...",
    "Read": "📄 파일 읽는 중...",
    "Bash": "⚙️ 명령 실행 중...",
    "Glob": "📂 파일 검색 중...",
    "Grep": "🔎 코드 검색 중...",
    "mcp__firecrawl__firecrawl_scrape": "🕷️ 웹 스크래핑 중...",
}


def _build_report_dir(user_task: str, session_id: str) -> str:
    """task 제목을 기반으로 보고서 폴더 경로를 생성."""
    import re
    # 제목에서 폴더명 생성 (최대 50자, 파일시스템 안전 문자만)
    name = user_task.strip()[:50]
    # 파일시스템에 안전하지 않은 문자 제거
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name).strip('_')
    if not name:
        name = session_id
    # 동일 이름 충돌 방지: 이미 존재하면 session_id 접미사
    base = f"data/reports/{name}"
    if Path(base).exists():
        base = f"data/reports/{name}_{session_id[:6]}"
    return base


def _extract_qa_context(state: dict) -> tuple[list[str], list[str]]:
    """state에서 명확화 질문과 사용자 답변을 추출."""
    questions = []
    answers = state.get("user_answers", [])

    raw_q = state.get("clarifying_questions", [])
    if isinstance(raw_q, list):
        for item in raw_q:
            if isinstance(item, dict):
                questions.append(item.get("question_text", str(item)))
            elif isinstance(item, str):
                questions.append(item)
    elif isinstance(raw_q, str):
        questions = [raw_q]

    return questions, answers


async def _stream_session(
    prompt: str,
    system_prompt: str,
    session_id: str,
    model: str,
    max_turns: int,
    timeout: int,
) -> str:
    """CLI subprocess를 스트리밍으로 실행하고 활동 이벤트를 emit."""
    from src.utils.claude_code import (
        _register_process,
        _unregister_process,
        _kill_process_tree,
        set_session_tag,
    )

    set_session_tag(f"single_{session_id}")

    # asyncio.create_subprocess_exec: 인자가 배열로 전달되어 shell injection 방지
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--max-turns", str(max_turns),
        "--append-system-prompt", system_prompt,
        "--allowedTools", ",".join(_SESSION_TOOLS),
        "--permission-mode", "auto",
    ]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cwd = os.getcwd()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        start_new_session=True,
        env=env,
    )
    _register_process(proc)

    full_text = ""
    tool_count = 0
    start_time = time.time()

    # 시작 이벤트
    emit_mode_event(session_id, {
        "type": "activity",
        "data": {
            "action": "started",
            "message": "🚀 AI 세션이 시작되었습니다",
            "elapsed": 0,
        },
    })

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

                event_type = event.get("type")
                elapsed = round(time.time() - start_time, 1)

                if event_type == "assistant":
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if not isinstance(block, dict):
                            continue

                        if block.get("type") == "text":
                            full_text += block["text"]

                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_count += 1
                            status = _TOOL_STATUS.get(tool_name, f"🔧 {tool_name}")

                            # Agent 도구는 description을 표시
                            detail = ""
                            if tool_name == "Agent":
                                inp = block.get("input", {})
                                detail = inp.get("description", inp.get("prompt", ""))[:80]

                            emit_mode_event(session_id, {
                                "type": "activity",
                                "data": {
                                    "action": "tool_use",
                                    "tool": tool_name,
                                    "message": status,
                                    "detail": detail,
                                    "elapsed": elapsed,
                                    "tool_count": tool_count,
                                },
                            })

                elif event_type == "result":
                    result_text = event.get("result", "")
                    if event.get("is_error"):
                        _logger.warning("single_session_result_error", error=result_text[:200])
                        if not full_text:
                            full_text = result_text
                    elif not full_text and result_text:
                        full_text = result_text

    except TimeoutError:
        elapsed = round(time.time() - start_time, 1)
        _logger.warning("single_session_timeout", elapsed_s=elapsed, timeout=timeout)
        emit_mode_event(session_id, {
            "type": "activity",
            "data": {
                "action": "timeout",
                "message": f"⏱️ 타임아웃 ({elapsed}초)",
                "elapsed": elapsed,
            },
        })
        await _kill_process_tree(proc)
    finally:
        _unregister_process(proc)

    elapsed = round(time.time() - start_time, 1)
    emit_mode_event(session_id, {
        "type": "activity",
        "data": {
            "action": "completed",
            "message": f"✅ 작업 완료 ({elapsed}초, 도구 {tool_count}회 사용)",
            "elapsed": elapsed,
            "tool_count": tool_count,
        },
    })

    return full_text


async def single_session_node(state: dict) -> dict:
    """싱글 CLI 세션으로 전체 작업을 실행하고 HTML 보고서를 생성한다.

    async 노드 — PipelineEngine._call_node이 iscoroutinefunction 감지하여
    await로 호출. 메인 이벤트 루프를 블로킹하지 않아 mode event drain이
    동시에 동작하여 활동 이벤트를 실시간 WebSocket 전송 가능.
    """
    if state.get("phase") == "error":
        return {}

    settings = get_settings()
    session_id = state.get("session_id", "default")
    user_task = state.get("user_task", "")

    # 폴더명: task 제목 기반 (안전한 파일명으로 변환)
    report_dir = _build_report_dir(user_task, session_id)
    questions, answers = _extract_qa_context(state)
    domains = state.get("selected_domains", ["research"])
    complexity = state.get("estimated_complexity", "low")

    # 전략 프리셋 로드
    strategy = None
    pre_context = state.get("pre_context") or {}
    # 1) pre_context에 strategy 객체가 직접 전달된 경우 (UI에서 전략 실행)
    if pre_context.get("strategy"):
        strategy = pre_context["strategy"]
        _logger.info("single_session_strategy_direct", strategy=strategy.get("name", ""))
    # 2) strategy_id로 storage에서 로드하는 경우
    elif pre_context.get("strategy_id"):
        from src.company_builder.storage import load_strategy
        user_id = state.get("user_id", "")
        strategy = load_strategy(user_id, pre_context["strategy_id"])
        if strategy:
            _logger.info("single_session_strategy_loaded", strategy=strategy.get("name", ""))

    _logger.info(
        "single_session_start",
        session_id=session_id,
        task=user_task[:80],
        domains=domains,
        complexity=complexity,
        strategy=strategy.get("name") if strategy else None,
    )

    # 출력 형식 (pre_context 또는 기본값)
    output_format = pre_context.get("output_format", "html")
    if strategy and strategy.get("output_format"):
        fmt_map = {"executive_report": "html", "summary": "markdown", "data_table": "csv", "presentation": "html"}
        output_format = fmt_map.get(strategy["output_format"], output_format)

    # Delta 비교 / Append 모드
    previous_report_path = pre_context.get("previous_report_path")
    output_mode = pre_context.get("output_mode", "replace")
    is_scheduled = state.get("execution_mode") == "scheduled"

    # 스케줄/Append 모드: 기존 보고서와 같은 디렉터리에 출력
    if previous_report_path:
        existing_dir = str(Path(previous_report_path).parent)
        if Path(existing_dir).exists():
            report_dir = existing_dir

    prompt = build_execution_prompt(
        user_task=user_task,
        user_answers=answers,
        clarifying_questions=questions,
        domains=domains,
        complexity=complexity,
        report_dir=report_dir,
        strategy=strategy,
        output_format=output_format,
        previous_report_path=previous_report_path,
        output_mode=output_mode,
        is_scheduled=is_scheduled,
    )

    timeout_map = {"high": 900, "medium": 600, "low": 420}
    max_turns_map = {"high": 100, "medium": 80, "low": 60}
    timeout = timeout_map.get(complexity, 600)
    max_turns = max_turns_map.get(complexity, 80)

    start_time = time.time()

    try:
        result = await _stream_session(
            prompt=prompt,
            system_prompt=SINGLE_SESSION_SYSTEM,
            session_id=session_id,
            model=settings.worker_model,
            max_turns=max_turns,
            timeout=timeout,
        )
        elapsed = time.time() - start_time
        _logger.info(
            "single_session_complete",
            session_id=session_id,
            elapsed_s=round(elapsed, 1),
            result_len=len(result),
        )
    except Exception as e:
        elapsed = time.time() - start_time
        _logger.error(
            "single_session_failed",
            session_id=session_id,
            elapsed_s=round(elapsed, 1),
            error=str(e)[:300],
        )
        result = ""

    import glob as glob_mod
    from src.prompts.single_session_prompts import OUTPUT_FORMAT_MAP
    output_filename = OUTPUT_FORMAT_MAP.get(output_format, OUTPUT_FORMAT_MAP["html"])["ext"]
    report_path = Path(report_dir) / output_filename
    # 스케줄 모드: 날짜 파일명(results_YYYY-MM-DD.html)도 확인
    if not report_path.exists():
        dated_files = sorted(glob_mod.glob(str(Path(report_dir) / "results_*.html")), reverse=True)
        if dated_files:
            report_path = Path(dated_files[0])
    # 형식이 HTML이 아닌 경우 해당 확장자도 확인
    if not report_path.exists() and output_format != "html":
        html_path = Path(report_dir) / "results.html"
        if html_path.exists():
            report_path = html_path
    if not report_path.exists():
        _logger.warning("single_session_no_report_file", session_id=session_id)
        Path(report_dir).mkdir(parents=True, exist_ok=True)
        fallback = Path(report_dir) / "results.html"
        fallback.write_text(
            _build_fallback_html(result, user_task, session_id),
            encoding="utf-8",
        )

    # PDF 후처리: HTML → PDF 변환
    if output_format == "pdf":
        html_file = Path(report_dir) / "results.html"
        if html_file.exists():
            try:
                from src.utils.pdf_converter import html_to_pdf_sync
                pdf_path = html_to_pdf_sync(str(html_file))
                if pdf_path:
                    _logger.info("single_session_pdf_generated", path=pdf_path)
            except Exception as e:
                _logger.warning("single_session_pdf_failed", error=str(e)[:200])

    # 저장된 파일 목록
    saved_files = [f.name for f in Path(report_dir).iterdir() if f.is_file()] if Path(report_dir).exists() else []
    files_info = ", ".join(saved_files) if saved_files else "없음"

    return {
        "report_file_path": report_dir,
        "phase": "user_review_results",
        "messages": [
            AIMessage(content=(
                f"작업 완료 ({round(elapsed, 1)}초)\n"
                f"📁 저장 위치: {report_dir}/\n"
                f"📎 파일: {files_info}"
            ))
        ],
    }


def _build_fallback_html(result: str, user_task: str, session_id: str) -> str:
    """CLI 세션이 HTML 파일을 직접 생성하지 못한 경우의 fallback."""
    from datetime import datetime
    import html as html_mod

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    escaped_task = html_mod.escape(user_task)
    escaped_result = html_mod.escape(result[:50000] if result else "(결과 없음)")

    return (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'<title>{escaped_task}</title>'
        '<style>'
        "body{font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif;"
        "max-width:900px;margin:0 auto;padding:40px 24px;color:#1a1a2e;background:#f4f6f9;line-height:1.7}"
        ".header{background:linear-gradient(135deg,#0f3460,#16213e);color:#fff;padding:40px;border-radius:12px;margin-bottom:24px}"
        ".header h1{font-size:24px;margin:0 0 8px}"
        ".header .meta{font-size:12px;opacity:0.6}"
        ".content{background:#fff;padding:32px;border-radius:12px;border:1px solid #e0e5ee;white-space:pre-wrap;font-size:14px}"
        '</style></head><body>'
        f'<div class="header"><h1>{escaped_task}</h1>'
        f'<div class="meta">Generated {generated_at} | Session {session_id}</div></div>'
        f'<div class="content">{escaped_result}</div>'
        '</body></html>'
    )
