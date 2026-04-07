"""Test: 양방향 컨텍스트 손실 방지 — upstream context + result file save/read."""

import json
import os
import tempfile

from src.agents.worker import WorkerAgent
from src.graphs.nodes.worker_execution import _build_upstream_context, _save_result_file


# ── 하류: upstream context ──


def test_upstream_context_built_with_all_fields():
    """user_task + user_answers가 upstream context에 포함된다."""
    ctx = _build_upstream_context("원본 작업", user_answers=["답변1", "답변2"])
    assert "원본 작업" in ctx
    assert "답변1" in ctx


def test_upstream_context_empty_when_no_data():
    """빈 데이터에서도 에러 없이 최소 컨텍스트 반환."""
    ctx = _build_upstream_context("")
    assert "원본 요청" in ctx


def test_upstream_context_with_answers():
    """user_answers가 포함된다."""
    ctx = _build_upstream_context("작업", user_answers=["답변"])
    assert "답변" in ctx


def test_upstream_context_injected_into_worker_prompt():
    """upstream_context가 Worker 프롬프트에 삽입된다."""
    agent = WorkerAgent(agent_id="test", worker_domain="researcher")
    prompt = agent._build_execution_prompt(
        approved_plan='{"task_title":"테스트"}',
        upstream_context="- 원본 요청: 테스트 작업",
    )
    assert "상위 작업 맥락" in prompt
    assert "테스트 작업" in prompt


def test_upstream_context_absent_when_empty():
    """upstream_context가 비어있으면 블록 미생성."""
    agent = WorkerAgent(agent_id="test", worker_domain="researcher")
    prompt = agent._build_execution_prompt(
        approved_plan='{"task_title":"테스트"}',
        upstream_context="",
    )
    assert "상위 작업 맥락" not in prompt


# ── 상류: result file save ──


def test_save_result_file_creates_file():
    """실행 결과가 파일로 저장된다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = _save_result_file(
            '{"result_summary":"테스트 결과"}',
            "researcher_0", "session123", tmpdir,
        )
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert data["result_summary"] == "테스트 결과"


def test_save_result_file_creates_session_subdir():
    """세션 ID별 하위 디렉토리가 생성된다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = _save_result_file("{}", "w1", "sess_abc", tmpdir)
        assert "sess_abc" in path
        assert os.path.isdir(os.path.join(tmpdir, "sess_abc"))
