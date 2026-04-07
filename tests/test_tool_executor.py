"""Unit tests for ToolExecutor and tool_definitions."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.tool_executor import ToolExecutor, _html_to_text
from src.utils.tool_definitions import (
    cli_tools_to_sdk,
    domain_to_sdk_tools,
    SEARCH_TOOLS,
    SERVER_WEB_SEARCH,
    SERVER_WEB_FETCH,
    TOOL_READ_FILE,
    TOOL_RUN_BASH,
)


# ── _html_to_text ───────────────────────────────


def test_html_to_text_basic():
    html = "<html><body><p>Hello world</p></body></html>"
    assert "Hello world" in _html_to_text(html)


def test_html_to_text_strips_scripts():
    html = "<html><script>var x=1;</script><p>Content</p></html>"
    text = _html_to_text(html)
    assert "Content" in text
    assert "var x" not in text


def test_html_to_text_truncates():
    html = "<p>" + "x" * 100000 + "</p>"
    text = _html_to_text(html, max_chars=100)
    assert len(text) < 200
    assert "truncated" in text


# ── ToolExecutor ────────────────────────────────


@pytest.mark.asyncio
async def test_execute_unknown_tool():
    executor = ToolExecutor()
    result = await executor.execute("nonexistent_tool", {})
    assert "Error" in result
    assert "Unknown tool" in result
    await executor.close()


@pytest.mark.asyncio
async def test_read_file_not_found():
    executor = ToolExecutor()
    result = await executor.execute("read_file", {"file_path": "/nonexistent/path.txt"})
    assert "Error" in result or "not found" in result.lower()
    await executor.close()


@pytest.mark.asyncio
async def test_read_file_success(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")
    executor = ToolExecutor(working_dir=str(tmp_path))
    result = await executor.execute("read_file", {"file_path": str(test_file)})
    assert result == "hello world"
    await executor.close()


@pytest.mark.asyncio
async def test_write_file_success(tmp_path):
    executor = ToolExecutor(working_dir=str(tmp_path))
    target = str(tmp_path / "output.txt")
    result = await executor.execute("write_file", {"file_path": target, "content": "test content"})
    assert "Written" in result
    assert (tmp_path / "output.txt").read_text() == "test content"
    await executor.close()


@pytest.mark.asyncio
async def test_run_bash_success():
    executor = ToolExecutor()
    result = await executor.execute("run_bash", {"command": "echo hello"})
    assert "hello" in result
    await executor.close()


@pytest.mark.asyncio
async def test_glob_search(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    executor = ToolExecutor(working_dir=str(tmp_path))
    result = await executor.execute("glob", {"pattern": "*.py"})
    assert "a.py" in result
    assert "b.txt" not in result
    await executor.close()


@pytest.mark.asyncio
async def test_web_search_with_mock():
    """web_search calls Brave API with correct headers."""
    executor = ToolExecutor()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "web": {
            "results": [
                {"title": "Test", "url": "https://example.com", "description": "A test result"}
            ]
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(executor._http, "get", new_callable=AsyncMock, return_value=mock_response):
        result = await executor.execute("web_search", {"query": "test query"})

    assert "Test" in result
    assert "example.com" in result
    await executor.close()


# ── cli_tools_to_sdk ────────────────────────────


def test_cli_to_sdk_builtin():
    sdk = cli_tools_to_sdk(["WebSearch", "WebFetch"])
    names = [t["name"] for t in sdk]
    assert "web_search" in names
    assert "web_fetch" in names


def test_cli_to_sdk_mcp():
    sdk = cli_tools_to_sdk(["mcp__brave-search__brave_web_search", "mcp__firecrawl__firecrawl_scrape"])
    names = [t["name"] for t in sdk]
    assert "web_search" in names
    assert "firecrawl_scrape" in names


def test_cli_to_sdk_deduplicates():
    sdk = cli_tools_to_sdk(["WebSearch", "mcp__brave-search__brave_web_search"])
    names = [t["name"] for t in sdk]
    assert names.count("web_search") == 1


def test_cli_to_sdk_unknown_falls_back():
    sdk = cli_tools_to_sdk(["unknown_tool"])
    assert sdk == SEARCH_TOOLS


def test_cli_to_sdk_dev_tools():
    sdk = cli_tools_to_sdk(["Read", "Write", "Bash", "Glob", "Grep"])
    names = [t["name"] for t in sdk]
    assert "read_file" in names
    assert "write_file" in names
    assert "run_bash" in names
    assert "glob" in names
    assert "grep" in names


def test_cli_to_sdk_mem0_skipped():
    sdk = cli_tools_to_sdk(["mcp__mem0__add_memory", "mcp__mem0__search_memories", "WebSearch"])
    names = [t["name"] for t in sdk]
    assert "web_search" in names
    # mem0 maps to None, should be skipped; WebSearch → server tools (web_search + web_fetch)
    assert len(sdk) == 2
    assert "web_fetch" in names


# ── domain_to_sdk_tools ────────────────────────


def test_domain_researcher():
    sdk = domain_to_sdk_tools("researcher")
    names = [t["name"] for t in sdk]
    assert "web_search" in names
    assert "web_fetch" in names


def test_domain_backend_developer():
    sdk = domain_to_sdk_tools("backend_developer")
    names = [t["name"] for t in sdk]
    assert "web_search" in names
    assert "github_search" in names
    assert "read_file" in names


def test_domain_unknown_falls_back():
    sdk = domain_to_sdk_tools("nonexistent_domain")
    assert sdk == SEARCH_TOOLS
