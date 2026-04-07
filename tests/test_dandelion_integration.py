"""Integration tests — verify module imports and graph assembly."""
import pytest


def test_all_dandelion_imports():
    """Verify all dandelion modules import without error."""
    from src.dandelion.schemas import Theme, ThemeAssignment, Imagination, Seed, ThemeResult, DandelionTree
    from src.dandelion.engine import DandelionEngine
    from src.dandelion.supervisor import ThemeSupervisor
    from src.dandelion.imaginer import Imaginer
    from src.dandelion.session import DandelionSession
    from src.dandelion.tools import SUPERVISOR_TOOLS
    from src.dandelion.prompts.ceo import THEME_DECISION_SYSTEM
    from src.dandelion.prompts.supervisor import RESEARCH_SYSTEM
    from src.dandelion.prompts.imaginer import IMAGINER_SYSTEM


def test_server_has_dandelion_endpoint():
    """Verify /ws/dandelion endpoint is registered."""
    from src.ui.server import app
    paths = [r.path for r in app.routes if hasattr(r, 'path')]
    assert '/ws/dandelion' in paths


def test_supervisor_tools_are_server_type():
    """Verify supervisor tools are server-side (no local executor needed)."""
    from src.dandelion.tools import SUPERVISOR_TOOLS
    for tool in SUPERVISOR_TOOLS:
        assert 'type' in tool
        assert tool['type'].startswith('web_')
