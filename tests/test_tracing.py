"""Tests for src/utils/tracing.py LangSmith integration."""

import os

import pytest

from src.utils.tracing import (
    configure_tracing,
    get_run_config,
    is_tracing_active,
    traceable_llm,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove LangSmith env vars and reset module state before each test."""
    for key in ("LANGCHAIN_TRACING_V2", "LANGCHAIN_API_KEY", "LANGCHAIN_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    # Reset module-level flag
    import src.utils.tracing as mod
    mod._tracing_active = False
    # Reset settings singleton so monkeypatched env vars are picked up.
    # importlib bypasses the src.config.__init__ shadow (settings = get_settings())
    import importlib
    _sm = importlib.import_module('src.config.settings')
    _sm._cached_settings = None
    yield
    _sm._cached_settings = None


# ── configure_tracing ─────────────────────────────


class TestConfigureTracing:
    def test_disabled_when_no_api_key(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "")
        result = configure_tracing()
        assert result is False
        assert is_tracing_active() is False

    def test_disabled_when_tracing_flag_false(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test_key")
        result = configure_tracing()
        assert result is False

    def test_enabled_when_both_set(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test_key")
        monkeypatch.setenv("LANGCHAIN_PROJECT", "test-project")
        result = configure_tracing()
        assert result is True
        assert is_tracing_active() is True
        assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
        assert os.environ["LANGCHAIN_API_KEY"] == "ls__test_key"
        assert os.environ["LANGCHAIN_PROJECT"] == "test-project"

    def test_exports_env_vars(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__abc123")
        monkeypatch.setenv("LANGCHAIN_PROJECT", "my-project")
        configure_tracing()
        # Env vars should be set for LangGraph to read
        assert os.environ.get("LANGCHAIN_TRACING_V2") == "true"
        assert os.environ.get("LANGCHAIN_API_KEY") == "ls__abc123"


# ── get_run_config ────────────────────────────────


class TestGetRunConfig:
    def test_basic_config_without_tracing(self):
        config = get_run_config("abc123")
        assert config["configurable"]["thread_id"] == "abc123"
        assert "run_name" not in config
        assert "tags" not in config

    def test_config_with_tracing(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test")
        configure_tracing()

        config = get_run_config("xyz789", mode="interactive")
        assert config["configurable"]["thread_id"] == "xyz789"
        assert config["run_name"] == "enterprise-agent-xyz789"
        assert "interactive" in config["tags"]
        assert config["metadata"]["session_id"] == "xyz789"
        assert config["metadata"]["mode"] == "interactive"

    def test_config_scheduled_mode(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test")
        configure_tracing()

        config = get_run_config("sched-001", mode="scheduled", tags=["job:j1"])
        assert "scheduled" in config["tags"]
        assert "job:j1" in config["tags"]

    def test_custom_tags_appended(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
        monkeypatch.setenv("LANGCHAIN_API_KEY", "ls__test")
        configure_tracing()

        config = get_run_config("t1", tags=["custom"])
        assert "custom" in config["tags"]


# ── traceable_llm decorator ──────────────────────


class TestTraceableLlm:
    def test_decorator_returns_callable(self):
        decorator = traceable_llm(name="test_call")
        assert callable(decorator)

    def test_decorated_function_still_works(self):
        @traceable_llm(name="my_func")
        async def dummy(x, y):
            return x + y

        import asyncio
        result = asyncio.run(dummy(1, 2))
        assert result == 3

    def test_sync_function_decorated(self):
        @traceable_llm(name="sync_test")
        def sync_dummy(a):
            return a * 2

        result = sync_dummy(5)
        assert result == 10
