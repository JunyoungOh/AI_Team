"""Unit tests for AnthropicClient — SDK direct API calls."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from src.utils.anthropic_client import (
    AnthropicClient,
    ApiMetrics,
    CircuitBreaker,
    MaxTurnsExceeded,
    SdkQueryError,
)


class SimpleSchema(BaseModel):
    name: str
    value: int


class NeedsResearch(BaseModel):
    needs_research: bool = Field(description="research needed")
    reason: str = Field(description="reason")


# ── ApiMetrics ──────────────────────────────────


def test_api_metrics_record():
    m = ApiMetrics()
    m.record(100, 50, 1.5)
    m.record(200, 80, 2.0)
    snap = m.snapshot()
    assert snap["total_calls"] == 2
    assert snap["input_tokens"] == 300
    assert snap["output_tokens"] == 130
    assert snap["errors"] == 0
    assert abs(snap["avg_elapsed"] - 1.75) < 0.01


def test_api_metrics_record_error():
    m = ApiMetrics()
    m.record_error()
    m.record_error()
    assert m.snapshot()["errors"] == 2


def test_api_metrics_thread_safety():
    m = ApiMetrics()
    def worker():
        for _ in range(100):
            m.record(10, 5, 0.1)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert m.snapshot()["total_calls"] == 400


# ── CircuitBreaker ──────────────────────────────


def test_circuit_breaker_allows_initially():
    cb = CircuitBreaker(threshold=3, cooldown=60.0)
    assert cb.can_proceed() is True


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(threshold=2, cooldown=60.0)
    cb.record_failure()
    assert cb.can_proceed() is True  # 1 < 2
    cb.record_failure()
    assert cb.can_proceed() is False  # 2 >= 2


def test_circuit_breaker_resets_on_success():
    cb = CircuitBreaker(threshold=2, cooldown=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.can_proceed() is False
    # Simulate cooldown by resetting
    cb.record_success()
    assert cb.can_proceed() is True


# ── AnthropicClient singleton ───────────────────


def test_singleton_pattern():
    # Reset singleton for test
    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic"):
        a = AnthropicClient()
        b = AnthropicClient()
        assert a is b
    AnthropicClient._instance = None  # Clean up


# ── structured_query ────────────────────────────


@pytest.mark.asyncio
async def test_structured_query_success():
    """SDK structured_query returns parsed Pydantic model."""
    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client

        mock_response = MagicMock()
        mock_response.parsed_output = SimpleSchema(name="test", value=42)
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_client.messages.parse = AsyncMock(return_value=mock_response)

        sdk = AnthropicClient()
        result = await sdk.structured_query(
            system_prompt="test system",
            user_message="test message",
            output_schema=SimpleSchema,
            model="sonnet",
            effort="low",
            timeout=10,
        )

        assert result.name == "test"
        assert result.value == 42
        assert sdk.metrics.snapshot()["total_calls"] == 1

        # Verify correct API call
        mock_client.messages.parse.assert_called_once()
        call_kwargs = mock_client.messages.parse.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6-20250514"
        assert call_kwargs["system"] == "test system"
        assert call_kwargs["thinking"] == {"type": "adaptive"}
        assert call_kwargs["output_config"] == {"effort": "low"}
        assert call_kwargs["output_format"] == SimpleSchema

    AnthropicClient._instance = None


@pytest.mark.asyncio
async def test_structured_query_circuit_breaker_open():
    """Raises SdkQueryError when circuit breaker is open."""
    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic"):
        sdk = AnthropicClient()
        # Force circuit open
        for _ in range(10):
            sdk._circuit.record_failure()

        with pytest.raises(SdkQueryError, match="Circuit breaker open"):
            await sdk.structured_query(
                system_prompt="test",
                user_message="test",
                output_schema=SimpleSchema,
            )

    AnthropicClient._instance = None


@pytest.mark.asyncio
async def test_structured_query_timeout_error():
    """Timeout wraps into SdkQueryError."""
    import anthropic

    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.messages.parse = AsyncMock(
            side_effect=anthropic.APITimeoutError(request=MagicMock())
        )

        sdk = AnthropicClient()
        with pytest.raises(SdkQueryError, match="Timeout"):
            await sdk.structured_query(
                system_prompt="test",
                user_message="test",
                output_schema=SimpleSchema,
                timeout=5,
            )

        assert sdk.metrics.snapshot()["errors"] == 1

    AnthropicClient._instance = None


@pytest.mark.asyncio
async def test_structured_query_rate_limit_error():
    """Rate limit wraps into SdkQueryError."""
    import anthropic

    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        mock_client.messages.parse = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="rate limited",
                response=mock_response,
                body=None,
            )
        )

        sdk = AnthropicClient()
        with pytest.raises(SdkQueryError, match="Rate limit"):
            await sdk.structured_query(
                system_prompt="test",
                user_message="test",
                output_schema=SimpleSchema,
            )

    AnthropicClient._instance = None


# ── text_query ──────────────────────────────────


@pytest.mark.asyncio
async def test_text_query_success():
    """SDK text_query returns concatenated text blocks."""
    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client

        text_block = MagicMock()
        text_block.text = "Hello world"
        mock_response = MagicMock()
        mock_response.content = [text_block]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 20
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        sdk = AnthropicClient()
        result = await sdk.text_query(
            system_prompt="test",
            user_message="test",
            model="sonnet",
        )

        assert result == "Hello world"
        assert sdk.metrics.snapshot()["total_calls"] == 1

    AnthropicClient._instance = None


# ── Model resolution ────────────────────────────


def test_model_resolution():
    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic"):
        sdk = AnthropicClient()
        assert sdk._resolve_model("opus") == "claude-opus-4-6-20250116"
        assert sdk._resolve_model("sonnet") == "claude-sonnet-4-6-20250514"
        assert sdk._resolve_model("haiku") == "claude-haiku-4-5-20251001"
        assert sdk._resolve_model("claude-sonnet-4-6-20250514") == "claude-sonnet-4-6-20250514"  # passthrough
    AnthropicClient._instance = None


# ── MaxTurnsExceeded ────────────────────────────


def test_max_turns_exceeded_is_sdk_error():
    """MaxTurnsExceeded inherits from SdkQueryError."""
    exc = MaxTurnsExceeded("exceeded 10 turns")
    assert isinstance(exc, SdkQueryError)
    assert "10 turns" in str(exc)


# ── BaseAgent SDK→CLI fallback ──────────────────


@pytest.mark.asyncio
async def test_base_agent_sdk_fallback():
    """When SDK fails, SdkQueryError is raised (no CLI fallback)."""
    from src.agents.base import BaseAgent

    # Create a concrete subclass for testing
    class TestAgent(BaseAgent):
        def invoke(self, state: dict) -> dict:
            return {}

    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic") as MockSdk:
        mock_sdk_client = AsyncMock()
        MockSdk.return_value = mock_sdk_client
        # SDK fails → SdkQueryError raised (no CLI fallback in Phase 4)
        mock_sdk_client.messages.parse = AsyncMock(side_effect=Exception("SDK error"))

        agent = TestAgent(agent_id="test", model="sonnet")
        with pytest.raises(SdkQueryError, match="SDK error"):
            await agent._aquery(
                system_prompt="test",
                user_content="test",
                output_schema=SimpleSchema,
                allowed_tools=[],
            )

    AnthropicClient._instance = None


@pytest.mark.asyncio
async def test_base_agent_sdk_success_no_cli():
    """When SDK succeeds, CLI is not called."""
    from src.agents.base import BaseAgent

    class TestAgent(BaseAgent):
        def invoke(self, state: dict) -> dict:
            return {}

    AnthropicClient._instance = None
    with patch("src.utils.anthropic_client.anthropic.AsyncAnthropic") as MockSdk:
        mock_sdk_client = AsyncMock()
        MockSdk.return_value = mock_sdk_client

        mock_response = MagicMock()
        mock_response.parsed_output = SimpleSchema(name="sdk", value=42)
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50
        mock_sdk_client.messages.parse = AsyncMock(return_value=mock_response)

        agent = TestAgent(agent_id="test", model="sonnet")
        result = await agent._aquery(
            system_prompt="test",
            user_content="test",
            output_schema=SimpleSchema,
            allowed_tools=[],
        )

        assert result.name == "sdk"
        assert result.value == 42

    AnthropicClient._instance = None
