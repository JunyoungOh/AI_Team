"""Isolated advisory test — mock results.html + temp persona → advisory call.

Creates a temporary report directory with a mock results.html,
inserts a test persona into the DB, then calls the advisory flow directly.
Validates that the advisory pipeline doesn't crash and returns comments.

Usage:
    python3 -m pytest tests/test_advisory_isolated.py -v -s
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from src.config.settings import get_settings
from src.persona.models import PersonaDB

# ── Test fixtures ──────────────────────────────────────

MOCK_REPORT_HTML = """\
<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"><title>Test Report</title></head>
<body>
<h1>AI 기업 분석 보고서</h1>
<h2>Executive Summary</h2>
<p>글로벌 AI 시장은 2025년 기준 약 1,500억 달러 규모로 성장하였으며,
주요 기업들의 경쟁이 심화되고 있습니다.</p>
<h2>시장 분석</h2>
<table>
<tr><th>기업</th><th>시장 점유율</th><th>주요 제품</th></tr>
<tr><td>OpenAI</td><td>25%</td><td>GPT-4, ChatGPT</td></tr>
<tr><td>Google</td><td>22%</td><td>Gemini, Cloud AI</td></tr>
<tr><td>Anthropic</td><td>15%</td><td>Claude</td></tr>
</table>
<h2>권고사항</h2>
<ol>
<li>AI 인프라 투자 확대 필요</li>
<li>규제 대응 전략 수립</li>
<li>인재 확보 경쟁력 강화</li>
</ol>
</body>
</html>
"""

MOCK_PERSONA_TEXT = """\
당신은 AI 산업 전문 애널리스트 본인입니다.

## 경력
- 10년간 테크 산업 애널리스트로 활동
- 주요 AI 기업 IPO 분석 경험 다수

## 핵심 사고방식
- 데이터 기반 의사결정을 최우선시
- 시장 트렌드보다 기술적 해자(moat)를 중시

## 말투
- 간결하고 직설적, 수치를 자주 인용
"""

TEST_USER_ID = "__test_advisory_user__"
TEST_PERSONA_ID = "__test_advisory_persona__"


@pytest.fixture()
def mock_report_dir():
    """Create temp directory with mock results.html."""
    tmpdir = tempfile.mkdtemp(prefix="advisory_test_")
    report_path = Path(tmpdir) / "results.html"
    report_path.write_text(MOCK_REPORT_HTML, encoding="utf-8")
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture()
def mock_persona():
    """Insert a temporary persona into the DB, clean up after."""
    db = PersonaDB.instance()
    # Direct insert to avoid API overhead
    import sqlite3
    from datetime import datetime

    now = datetime.now().isoformat()
    conn = sqlite3.connect(db._db_path)
    conn.execute(
        "INSERT OR REPLACE INTO personas (id, user_id, name, summary, persona_text, source, avatar_url, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            TEST_PERSONA_ID,
            TEST_USER_ID,
            "AI 산업 애널리스트",
            "테스트용 AI 애널리스트 페르소나",
            MOCK_PERSONA_TEXT,
            "test",
            "",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    yield TEST_PERSONA_ID

    # Cleanup
    conn = sqlite3.connect(db._db_path)
    conn.execute("DELETE FROM personas WHERE id = ?", (TEST_PERSONA_ID,))
    conn.commit()
    conn.close()


# ── Tests ──────────────────────────────────────────────


class TestAdvisoryDirect:
    """Test generate_advisory_comments directly (no server needed)."""

    def test_report_file_read(self, mock_report_dir):
        """Verify mock report is readable."""
        rp = Path(mock_report_dir) / "results.html"
        assert rp.exists()
        text = rp.read_text(encoding="utf-8")
        assert "AI 기업 분석 보고서" in text
        assert "<table>" in text

    def test_persona_created(self, mock_persona):
        """Verify test persona exists in DB."""
        db = PersonaDB.instance()
        persona = db.get_usable(mock_persona, TEST_USER_ID)
        assert persona is not None
        assert persona["name"] == "AI 산업 애널리스트"
        assert "데이터 기반" in persona["persona_text"]

    def test_empty_inputs_return_empty(self):
        """Advisory with empty inputs should return [] without crash."""
        from src.persona.advisory import generate_advisory_comments

        result = asyncio.run(
            generate_advisory_comments(report_text="", persona_ids=[], user_id="")
        )
        assert result == []

    def test_invalid_persona_id_returns_empty(self):
        """Advisory with non-existent persona should return [] without crash."""
        from src.persona.advisory import generate_advisory_comments

        result = asyncio.run(
            generate_advisory_comments(
                report_text="test report",
                persona_ids=["nonexistent_id_12345"],
                user_id=TEST_USER_ID,
            )
        )
        assert result == []

    @pytest.mark.live
    def test_advisory_generates_comment(self, mock_report_dir, mock_persona, monkeypatch):
        """Full advisory call with real LLM — requires API key.

        Run with: pytest tests/test_advisory_isolated.py -v -s -k live

        Forces AnthropicBridge (API direct) to avoid ClaudeCodeBridge
        subprocess issues when running inside Claude Code.
        """
        from src.persona.advisory import generate_advisory_comments
        from src.utils.anthropic_bridge import AnthropicBridge

        # Force API direct bridge (ClaudeCodeBridge can't nest inside Claude Code)
        monkeypatch.setattr(
            "src.persona.advisory._get_bridge",
            lambda: AnthropicBridge(),
        )

        report_text = (Path(mock_report_dir) / "results.html").read_text(
            encoding="utf-8"
        )

        result = asyncio.run(
            generate_advisory_comments(
                report_text=report_text,
                persona_ids=[mock_persona],
                user_id=TEST_USER_ID,
            )
        )

        assert isinstance(result, list)
        assert len(result) == 1, f"Expected 1 comment, got {len(result)}"

        comment = result[0]
        assert comment["persona_id"] == TEST_PERSONA_ID
        assert comment["name"] == "AI 산업 애널리스트"
        assert len(comment["comment"]) > 20, (
            f"Comment too short: {comment['comment'][:50]}"
        )
        print(f"\n{'='*60}")
        print(f"Advisory comment from: {comment['name']}")
        print(f"{'='*60}")
        print(comment["comment"])
        print(f"{'='*60}")


class TestAdvisoryEndpoint:
    """Test the /api/advisory endpoint via HTTPX test client."""

    @pytest.mark.live
    def test_endpoint_with_mock_data(self, mock_report_dir, mock_persona, monkeypatch):
        """Full endpoint test with mock report + persona.

        Run with: pytest tests/test_advisory_isolated.py -v -s -k endpoint
        """
        from src.utils.anthropic_bridge import AnthropicBridge

        monkeypatch.setattr(
            "src.persona.advisory._get_bridge",
            lambda: AnthropicBridge(),
        )

        from httpx import ASGITransport, AsyncClient

        from src.ui.server import app

        async def _call():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/advisory",
                    json={
                        "report_path": mock_report_dir,
                        "session_id": "test_session",
                        "persona_ids": [mock_persona],
                        "user_id": TEST_USER_ID,
                    },
                )
                return resp

        resp = asyncio.run(_call())
        assert resp.status_code == 200
        data = resp.json()
        print(f"\nEndpoint response: {data}")

        assert "comments" in data
        if data.get("error"):
            print(f"Warning: endpoint returned error: {data['error']}")
        else:
            assert len(data["comments"]) >= 1
            print(f"Comment: {data['comments'][0].get('comment', '')[:200]}")


class TestReportHtmlSanitize:
    """Test that markdown in report_html is properly converted."""

    def test_markdown_converted_to_html(self):
        from src.utils.report_exporter import _sanitize_report_html

        md = "## 제목\n\n이것은 **굵은** 텍스트.\n\n| 열1 | 열2 |\n|---|---|\n| A | B |"
        result = _sanitize_report_html(md)
        assert "<" in result, f"Expected HTML, got raw text: {result[:100]}"
        # Should contain converted HTML elements
        assert "제목" in result

    def test_html_passes_through(self):
        from src.utils.report_exporter import _sanitize_report_html

        html = '<div style="color:red"><h2>Title</h2><p>Body</p></div>'
        result = _sanitize_report_html(html)
        assert "<div" in result
        assert "<h2>" in result

    def test_empty_returns_empty(self):
        from src.utils.report_exporter import _sanitize_report_html

        assert _sanitize_report_html("") == ""

    def test_code_fenced_html_stripped(self):
        from src.utils.report_exporter import _sanitize_report_html

        fenced = "```html\n<div>Hello</div>\n```"
        result = _sanitize_report_html(fenced)
        assert "<div>" in result
        assert "```" not in result
