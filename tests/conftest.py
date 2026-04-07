"""Shared pytest fixtures for the enterprise agent test suite."""

import shutil
import subprocess

import pytest

from src.models.state import create_initial_state


# ── Claude CLI detection ─────────────────────────────

def _claude_cli_available() -> bool:
    """Check if 'claude' CLI is installed and responds."""
    if shutil.which("claude") is None:
        return False
    try:
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


_HAS_CLAUDE = _claude_cli_available()


@pytest.fixture
def require_claude_cli():
    """Skip the test if Claude CLI is not available."""
    if not _HAS_CLAUDE:
        pytest.skip("Claude CLI not available")


@pytest.fixture
def tmp_db_path(tmp_path):
    """Return a temporary SQLite database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def sample_state():
    """Return a factory for sample EnterpriseAgentState dicts."""

    def _make(**overrides):
        state = create_initial_state("Test task")
        state.update(overrides)
        return state

    return _make
