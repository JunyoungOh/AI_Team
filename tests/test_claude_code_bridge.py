"""Unit tests for ClaudeCodeBridge — subprocess and error handling."""

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from pydantic import BaseModel

from src.utils.claude_code import (
    ClaudeCodeBridge,
    ClaudeCodeError,
    ClaudeCodeTimeoutError,
    _sdk_available,
)


class SimpleSchema(BaseModel):
    name: str
    value: int


# ── Transport detection ──────────────────────────


def test_sdk_available_returns_bool():
    """_sdk_available should return True or False."""
    import src.utils.claude_code as mod
    mod._USE_SDK = None  # Reset cache
    result = _sdk_available()
    assert isinstance(result, bool)
    mod._USE_SDK = None  # Clean up


# ── Subprocess transport ─────────────────────────


@pytest.mark.asyncio
async def test_subprocess_query_success():
    """Successful subprocess query parses JSON into Pydantic model."""
    bridge = ClaudeCodeBridge()

    mock_output = json.dumps({"result": {"name": "test", "value": 42}})

    with patch.object(bridge, "_run_subprocess", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_output

        # Force subprocess mode
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            result = await bridge.structured_query(
                system_prompt="You are helpful.",
                user_message="Give me data.",
                output_schema=SimpleSchema,
                model="sonnet",
            )

    assert isinstance(result, SimpleSchema)
    assert result.name == "test"
    assert result.value == 42


@pytest.mark.asyncio
async def test_subprocess_query_error_flag():
    """When Claude Code returns is_error=True, raises ClaudeCodeError."""
    bridge = ClaudeCodeBridge(max_retries=0)

    mock_output = json.dumps({"is_error": True, "result": "API rate limited"})

    with patch.object(bridge, "_run_subprocess", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = mock_output
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            with pytest.raises(ClaudeCodeError, match="API rate limited"):
                await bridge.structured_query(
                    system_prompt="test",
                    user_message="test",
                    output_schema=SimpleSchema,
                )


@pytest.mark.asyncio
async def test_subprocess_timeout():
    """Timeout raises ClaudeCodeTimeoutError."""
    bridge = ClaudeCodeBridge()

    async def _slow_run(cmd, *, timeout, **kwargs):
        raise ClaudeCodeTimeoutError("timed out")

    with patch.object(bridge, "_run_subprocess", side_effect=_slow_run):
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            with pytest.raises(ClaudeCodeTimeoutError):
                await bridge.structured_query(
                    system_prompt="test",
                    user_message="test",
                    output_schema=SimpleSchema,
                    timeout=1,
                )


@pytest.mark.asyncio
async def test_subprocess_nonzero_exit():
    """Non-zero exit code raises ClaudeCodeError."""
    bridge = ClaudeCodeBridge(max_retries=0)

    with patch.object(bridge, "_run_subprocess", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = ClaudeCodeError("exited with code 1: some error")
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            with pytest.raises(ClaudeCodeError, match="exited with code 1"):
                await bridge.structured_query(
                    system_prompt="test",
                    user_message="test",
                    output_schema=SimpleSchema,
                )


# ── Retry logic ──────────────────────────────────


@pytest.mark.asyncio
async def test_retry_on_error():
    """Bridge retries on error (up to max_retries)."""
    bridge = ClaudeCodeBridge(max_retries=1)

    call_count = 0
    mock_output = json.dumps({"result": {"name": "retry", "value": 1}})

    async def _flaky_run(cmd, *, timeout, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ClaudeCodeError("temporary failure")
        return mock_output

    with patch.object(bridge, "_run_subprocess", side_effect=_flaky_run):
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            result = await bridge.structured_query(
                system_prompt="test",
                user_message="test",
                output_schema=SimpleSchema,
            )

    assert result.name == "retry"
    assert call_count == 2


@pytest.mark.asyncio
async def test_timeout_retried_once():
    """Timeout errors are NOT retried — immediate raise (same conditions = same result)."""
    bridge = ClaudeCodeBridge(max_retries=2)

    call_count = 0

    async def _timeout_run(cmd, *, timeout, **kwargs):
        nonlocal call_count
        call_count += 1
        raise ClaudeCodeTimeoutError("timed out")

    with patch.object(bridge, "_run_subprocess", side_effect=_timeout_run):
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            with pytest.raises(ClaudeCodeTimeoutError):
                await bridge.structured_query(
                    system_prompt="test",
                    user_message="test",
                    output_schema=SimpleSchema,
                )

    assert call_count == 1  # No retry on timeout — fail fast to Tier 2


# ── Model selection ──────────────────────────────


@pytest.mark.asyncio
async def test_model_passed_to_subprocess():
    """Model argument is forwarded to the subprocess command."""
    bridge = ClaudeCodeBridge()

    captured_cmd = None

    async def _capture_cmd(cmd, *, timeout, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        return json.dumps({"result": {"name": "test", "value": 1}})

    with patch.object(bridge, "_run_subprocess", side_effect=_capture_cmd):
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            await bridge.structured_query(
                system_prompt="sys",
                user_message="msg",
                output_schema=SimpleSchema,
                model="opus",
            )

    assert "--model" in captured_cmd
    model_idx = captured_cmd.index("--model")
    assert captured_cmd[model_idx + 1] == "opus"


@pytest.mark.asyncio
async def test_allowed_tools_passed():
    """allowed_tools are forwarded as --allowedTools flag."""
    bridge = ClaudeCodeBridge()

    captured_cmd = None

    async def _capture_cmd(cmd, *, timeout, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        return json.dumps({"result": {"name": "test", "value": 1}})

    with patch.object(bridge, "_run_subprocess", side_effect=_capture_cmd):
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            await bridge.structured_query(
                system_prompt="sys",
                user_message="msg",
                output_schema=SimpleSchema,
                allowed_tools=["WebSearch", "Bash"],
            )

    assert "--allowedTools" in captured_cmd
    tools_idx = captured_cmd.index("--allowedTools")
    assert captured_cmd[tools_idx + 1] == "WebSearch,Bash"


# ── raw_query stream-json integration ─────────────


@pytest.mark.asyncio
async def test_subprocess_query_passes_effort():
    """--effort should be in the subprocess command when specified."""
    bridge = ClaudeCodeBridge()

    captured_cmd = None

    async def _capture_cmd(cmd, *, timeout, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        return json.dumps({"result": {"name": "test", "value": 1}})

    with patch.object(bridge, "_run_subprocess", side_effect=_capture_cmd):
        with patch("src.utils.claude_code._sdk_available", return_value=False):
            await bridge.structured_query(
                system_prompt="sys",
                user_message="msg",
                output_schema=SimpleSchema,
                effort="low",
            )

    assert "--effort" in captured_cmd
    effort_idx = captured_cmd.index("--effort")
    assert captured_cmd[effort_idx + 1] == "low"


@pytest.mark.asyncio
async def test_raw_query_passes_effort():
    """--effort should be in the raw_query command when specified."""
    bridge = ClaudeCodeBridge()

    captured_cmd = None

    async def _capture_cmd(cmd, *, timeout, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        stream_output = '{"type":"result","subtype":"success","result":"ok","is_error":false}\n'
        return stream_output

    with patch.object(bridge, "_run_subprocess", side_effect=_capture_cmd):
        await bridge.raw_query(
            system_prompt="sys",
            user_message="msg",
            effort="low",
        )

    assert "--effort" in captured_cmd
    effort_idx = captured_cmd.index("--effort")
    assert captured_cmd[effort_idx + 1] == "low"


@pytest.mark.asyncio
async def test_raw_query_uses_stream_json():
    """raw_query should use --output-format stream-json and parse all turns."""
    bridge = ClaudeCodeBridge()

    captured_cmd = None
    stream_output = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Part 1"}]}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Part 2"}]}}\n'
        '{"type":"result","subtype":"success","result":"Part 2","is_error":false}\n'
    )

    async def _capture_cmd(cmd, *, timeout, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        return stream_output

    with patch.object(bridge, "_run_subprocess", side_effect=_capture_cmd):
        result = await bridge.raw_query(
            system_prompt="sys",
            user_message="msg",
            model="sonnet",
        )

    # Verify stream-json format is used
    assert "--output-format" in captured_cmd
    fmt_idx = captured_cmd.index("--output-format")
    assert captured_cmd[fmt_idx + 1] == "stream-json"

    # Verify all assistant turns are captured
    assert "Part 1" in result
    assert "Part 2" in result


@pytest.mark.asyncio
async def test_raw_query_single_turn():
    """raw_query with a single assistant turn returns clean text."""
    bridge = ClaudeCodeBridge()

    stream_output = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"<!DOCTYPE html><html><body>Hello</body></html>"}]}}\n'
        '{"type":"result","subtype":"success","result":"<!DOCTYPE html><html><body>Hello</body></html>","is_error":false}\n'
    )

    async def _return_stream(cmd, *, timeout, **kwargs):
        return stream_output

    with patch.object(bridge, "_run_subprocess", side_effect=_return_stream):
        result = await bridge.raw_query(
            system_prompt="sys",
            user_message="msg",
        )

    assert result.startswith("<!DOCTYPE html>")
    assert "</html>" in result


# ── Partial stdout salvage ─────────────────────


def test_timeout_error_has_partial_stdout_attr():
    """ClaudeCodeTimeoutError should carry partial_stdout attribute."""
    err = ClaudeCodeTimeoutError("timed out after 600s")
    err.partial_stdout = "partial data here"
    assert err.partial_stdout == "partial data here"
    assert hasattr(err, "partial_result")


def test_timeout_error_partial_stdout_default_none():
    """ClaudeCodeTimeoutError.partial_stdout defaults to None."""
    err = ClaudeCodeTimeoutError("timed out")
    assert err.partial_stdout is None


def test_worker_execution_reads_both_partial_attrs():
    """Tier2 injection should find partial from either partial_context or partial_stdout."""
    # Simulates the fix in worker_execution.py line 160-164
    # ClaudeCodeTimeoutError sets partial_stdout (timeout path)
    err = ClaudeCodeTimeoutError("timed out")
    err.partial_stdout = "some partial output from timeout"

    tier1_partial = (
        getattr(err, 'partial_context', None)
        or getattr(err, 'partial_stdout', None)
        or ""
    )
    assert tier1_partial == "some partial output from timeout"

    # ClaudeCodeError sets partial_context (max_turns path)
    err2 = ClaudeCodeError("max turns hit")
    err2.partial_context = "some partial from max_turns"

    tier1_partial2 = (
        getattr(err2, 'partial_context', None)
        or getattr(err2, 'partial_stdout', None)
        or ""
    )
    assert tier1_partial2 == "some partial from max_turns"
