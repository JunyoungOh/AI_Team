"""DART MCP server — exposes Open DART tools over stdio JSON-RPC.

Spawned by Claude Code CLI via ``.mcp.json`` when a DART session starts.
Reuses the existing ``src/dart/tools.py`` executors without modification —
this file is only the MCP protocol adapter (~60 lines).

Why this exists
---------------
The old ``src/dart/engine.py`` approach wrapped tool calls in `<tool_call>`
XML blocks that the engine parsed manually. Claude (Sonnet) trained on
ReAct-style traces tends to fabricate `<tool_response>` blocks right after
any `<tool_call>` it emits, leading to hallucinated filings, financial
numbers, and rcept_no values that look correct but don't exist.

Switching to MCP (native Anthropic tool-use protocol):
- Claude emits a ``tool_use`` block via the SDK's official channel.
- Claude Code CLI intercepts it, forwards to this server, gets the real
  result, and hands it back as ``tool_result`` in the next turn.
- There is no text position where Claude can fabricate a response, because
  the assistant message containing the ``tool_use`` block must terminate
  before the CLI injects the real result.

Launch (via Claude Code CLI + .mcp.json):
    python3 -m src.dart.mcp_server

Standalone test:
    DART_API_KEY=xxx python3 -m src.dart.mcp_server   # starts server,
                                                       # waits on stdin
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src.dart.tools import (
    DART_TOOL_EXECUTORS,
    DART_TOOL_SCHEMAS,
    make_session_context,
)

# All logging goes to stderr — stdout is reserved for the MCP stdio protocol.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s dart-mcp: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("dart-mcp")


# One server, one shared tool context per process. The CorpCodeIndex
# singleton lives at module level inside src/dart/corp_code.py so
# concurrent tool calls within the same process share the cache.
server: Server = Server("dart-mcp", version="1.0.0")
_ctx: dict[str, Any] = make_session_context()


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """Expose all seven DART tools to Claude via MCP."""
    tools: list[types.Tool] = []
    for name, schema in DART_TOOL_SCHEMAS.items():
        tools.append(
            types.Tool(
                name=name,
                description=schema.get("description", ""),
                inputSchema=schema.get("input_schema") or {"type": "object"},
            )
        )
    return tools


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[types.TextContent]:
    """Execute one DART tool and return its JSON result as text content.

    Errors (including DartAPIError, TypeError for bad args, unexpected
    exceptions) are converted to plain-text error messages so Claude can
    read them and recover on the next turn.
    """
    logger.info("call_tool name=%s args=%s", name, arguments)
    executor = DART_TOOL_EXECUTORS.get(name)
    if executor is None:
        return [types.TextContent(type="text", text=f"Error: unknown tool '{name}'")]
    try:
        result = await executor(_ctx, **(arguments or {}))
    except TypeError as exc:
        logger.warning("Bad arguments for %s: %s", name, exc)
        return [
            types.TextContent(type="text", text=f"Error: invalid input for {name} — {exc}")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool %s failed", name)
        return [
            types.TextContent(
                type="text",
                text=f"Error executing {name}: {type(exc).__name__}: {exc}",
            )
        ]
    return [types.TextContent(type="text", text=result)]


async def main() -> None:
    logger.info("DART MCP server starting (tools=%d)", len(DART_TOOL_SCHEMAS))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
