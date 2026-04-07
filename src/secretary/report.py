"""Instant report generator — converts chat context into HTML reports.

Uses Claude Code subprocess to synthesize conversation history and
background task results into a standalone HTML report.

Safety: All subprocess calls use asyncio.create_subprocess_exec with
argument lists (not shell strings) — no shell injection risk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from src.utils.claude_code import _register_process, _unregister_process, set_session_tag

logger = logging.getLogger(__name__)

_REPORTS_DIR = Path(__file__).parents[2] / "data" / "reports"

_REPORT_SYSTEM_PROMPT = """\
당신은 리포트 생성 전문가입니다.

## 임무
사용자의 대화 내용과 백그라운드 태스크 결과를 바탕으로
깔끔한 HTML 리포트를 생성하세요.

## HTML 요구사항
- 완전한 자체 완결형 HTML (외부 CSS/JS 없이)
- 다크 테마 (#0d1117 배경, #e6edf3 텍스트)
- 반응형 레이아웃 (max-width: 800px, 중앙 정렬)
- 한국어 기본, 영어 혼용 가능
- 섹션: 제목, 요약, 본문, 결론
- 마크다운 스타일: 헤더, 리스트, 코드블록, 표 사용

## 금지
- 도구(Tool) 사용 금지
- bkit, PDCA 등 메타데이터 출력 금지

## 출력
HTML 코드만 출력하세요. 마크다운 코드펜스나 설명 없이 순수 HTML만 반환.
"""


class ReportGenerator:
    """Generates HTML reports from chat history and task results."""

    def __init__(self, session_tag: str, session_id: str):
        self._session_tag = session_tag
        self._session_id = session_id
        self._report_dir = _REPORTS_DIR / f"sec_{session_id}"
        self._report_dir.mkdir(parents=True, exist_ok=True)

    async def generate(self, content: str, history: list, task_results: dict, ws) -> str | None:
        """Generate an HTML report and save it.

        Args:
            content: User's report request
            history: Chat history messages
            task_results: Background task summaries {task_id: summary}
            ws: WebSocket to stream progress

        Returns:
            Relative report path or None on failure.
        """
        set_session_tag(self._session_tag)

        # Build context for the report prompt
        context_parts = ["## 대화 내용"]
        for msg in history[-30:]:
            role = "User" if msg.role == "user" else "Assistant"
            context_parts.append(f"[{role}] {msg.content}")

        if task_results:
            context_parts.append("\n## 백그라운드 태스크 결과")
            for tid, summary in task_results.items():
                context_parts.append(f"[Task {tid}] {summary}")

        full_context = "\n\n".join(context_parts)
        user_prompt = (
            f"{_REPORT_SYSTEM_PROMPT}\n\n"
            f"사용자 요청: {content}\n\n"
            f"{full_context}\n\n"
            f"위 내용을 바탕으로 자체 완결형 HTML 리포트를 생성하세요. "
            f"<html>로 시작하고 </html>로 끝나는 순수 HTML만 출력하세요."
        )

        msg_id = f"rpt_{int(time.time() * 1000) % 1_000_000:06d}"
        try:
            await ws.send_json({
                "type": "sec_stream",
                "data": {"token": "📝 리포트를 생성 중입니다...", "done": False},
            })
        except Exception:
            pass

        # All arguments passed as list — safe from injection
        cmd = [
            "claude", "-p", user_prompt,
            "--output-format", "json",
            "--model", "sonnet",
            "--max-turns", "3",
        ]

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/tmp",
                start_new_session=True,
                env=env,
            )
            _register_process(proc)
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
            finally:
                _unregister_process(proc)

            text = stdout.decode("utf-8", errors="replace").strip()
            result = json.loads(text)
            raw = result.get("result", "")

            if isinstance(raw, str):
                raw = raw.strip()
                # Strip markdown code fences
                if raw.startswith("```"):
                    raw = "\n".join(raw.split("\n")[1:])
                if raw.endswith("```"):
                    raw = raw.rsplit("```", 1)[0]
                raw = raw.strip()

            # Extract HTML block from response
            html = raw
            if "<html" in raw.lower():
                start = raw.lower().find("<html")
                end = raw.lower().find("</html>")
                if end > start:
                    html = raw[start:end + 7]

            if not html or "<html" not in html.lower():
                raise ValueError("Generated content is not valid HTML")

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"report_{timestamp}.html"
            report_path = self._report_dir / filename
            report_path.write_text(html, encoding="utf-8")

            relative_path = f"/reports/sec_{self._session_id}/{filename}"

            await ws.send_json({
                "type": "sec_stream",
                "data": {"token": f"\n📄 리포트가 생성되었습니다: [{filename}]({relative_path})", "done": False},
            })
            await ws.send_json({
                "type": "sec_stream",
                "data": {"token": "", "done": True, "message_id": msg_id},
            })
            await ws.send_json({
                "type": "sec_report",
                "data": {"saved_path": relative_path, "filename": filename},
            })

            return relative_path

        except Exception as e:
            import traceback
            logger.warning("report_generate_error: %s", e)
            err_detail = f"{type(e).__name__}: {e}"
            try:
                await ws.send_json({
                    "type": "sec_stream",
                    "data": {"token": f"\n리포트 생성 중 오류가 발생했습니다: {err_detail}", "done": False},
                })
                await ws.send_json({
                    "type": "sec_stream",
                    "data": {"token": "", "done": True, "message_id": msg_id},
                })
            except Exception:
                pass
            return None
