"""JARVIS engine — ClaudeCodeBridge tool_use loop for DataLab.

Uses ClaudeCodeBridge (CLI subprocess) instead of Anthropic SDK direct calls.
Custom tools (run_python, read_uploaded_data, export_file, etc.) are described
in the system prompt as text instructions with <tool_call> structured format.
The engine parses tool calls from model output and executes them locally.
"""
from __future__ import annotations

import json
import logging
import re
from functools import partial
from pathlib import Path
from typing import Any

from src.config.settings import get_settings
from src.datalab.prompts.system import build_system_prompt
from src.datalab.tools import (
    DATALAB_TOOL_SCHEMAS,
    DATALAB_TOOL_EXECUTORS,
    make_session_context,
)
from src.tools.native import CLIENT_TOOLS, TOOL_EXECUTORS as NATIVE_EXECUTORS
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)

# Model aliases — CLI accepts these directly
MODEL_MAP = {
    "opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
}

# Effort mapping — CLI --effort flag values
EFFORT_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}

# Core native tools — always available (lightweight, high-value).
_CORE_NATIVE_TOOLS: list[str] = [
    "quickchart_render",
]

# Extended native tools — added dynamically when JARVIS detects relevant data.
# Kept separate to reduce tool count (fewer tools = faster API response).
_EXTENDED_NATIVE_TOOLS: list[str] = [
    "exchange_rate",
    "dart_financial",
    "ecos_data",
    "kosis_data",
    "pykrx_stock",
    "yfinance_data",
    "world_bank_data",
    "imf_data",
    "bls_data",
    "hf_datasets_search",
    "firecrawl_scrape",
]


# ── Tool instruction helpers ────────────────────────────


def _build_tool_instructions(tools: list[dict]) -> str:
    """Convert tool schemas to text instructions for the model.

    Since ClaudeCodeBridge can't natively handle custom tools like run_python,
    we embed tool descriptions in the system prompt and ask the model to output
    tool calls in a structured <tool_call> format that we parse locally.
    """
    lines = [
        "You have the following tools available. Call them by outputting a <tool_call> block:",
        "",
    ]
    for tool in tools:
        lines.append(f"Tool: {tool['name']}")
        if tool.get("description"):
            lines.append(f"Description: {tool['description']}")
        schema = tool.get("input_schema", {})
        if schema:
            lines.append(f"Parameters: {json.dumps(schema, ensure_ascii=False)}")
        lines.append("")
    lines.append("To call a tool, output exactly this format (you may call multiple tools):")
    lines.append("<tool_call>")
    lines.append('{"name": "tool_name", "input": {...}}')
    lines.append("</tool_call>")
    lines.append("")
    lines.append("After each tool call, I will provide the result. Continue until the task is complete.")
    lines.append("When you are done and have no more tool calls to make, just provide your final response text.")
    return "\n".join(lines)


_TOOL_CALL_RE = re.compile(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', re.DOTALL)


def _parse_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Extract tool calls from model output."""
    calls = []
    for match in _TOOL_CALL_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
            calls.append((data["name"], data.get("input", {})))
        except (json.JSONDecodeError, KeyError):
            continue
    return calls


def _strip_tool_calls(text: str) -> str:
    """Remove <tool_call> blocks from text, returning only the prose."""
    return _TOOL_CALL_RE.sub("", text).strip()


class JarvisEngine:
    """ClaudeCodeBridge-based tool_use engine that powers JARVIS in DataLab mode.

    Connects Claude to DataLab tools (run_python, read_uploaded_data,
    export_file) and selected native tools (finance APIs, charts, web
    scraping).  Streams text tokens and agent state updates to the
    frontend via WebSocket.

    Unlike the SDK version, tools are described as text instructions in the
    system prompt. The model outputs <tool_call> blocks which are parsed
    and executed locally.
    """

    def __init__(
        self,
        session_dir: str,
        ws: Any,
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        self._bridge = get_bridge()
        resolved_model = model or settings.datalab_model
        self._model = MODEL_MAP.get(resolved_model, resolved_model)
        self._effort = EFFORT_MAP.get(settings.datalab_effort, "medium")
        self._ws = ws
        self._session_dir = session_dir
        self._session_id = Path(session_dir).name
        self._messages: list[dict[str, Any]] = []
        self._base_system_prompt = build_system_prompt(session_dir)
        self._tools = self._build_tool_list()
        self._cancelled = False

        # Create per-engine session context and bind executors —
        # each JarvisEngine gets its own ctx so concurrent sessions
        # don't contaminate each other's file paths.
        ctx = make_session_context(session_dir)
        self._datalab_executors = {
            name: partial(fn, ctx)
            for name, fn in DATALAB_TOOL_EXECUTORS.items()
        }

    # ── Tool list assembly ───────────────────────────────

    def _build_tool_list(self) -> list[dict[str, Any]]:
        """Build tool list — core tools only (4 total). Keep it small for faster API."""
        tools: list[dict[str, Any]] = list(DATALAB_TOOL_SCHEMAS.values())

        for name in _CORE_NATIVE_TOOLS:
            if name in CLIENT_TOOLS:
                tools.append(CLIENT_TOOLS[name])

        return tools

    def add_extended_tools(self, tool_names: list[str]) -> None:
        """Dynamically add native tools when data context requires them."""
        for name in tool_names:
            if name in _EXTENDED_NATIVE_TOOLS and name in CLIENT_TOOLS:
                if not any(t.get("name") == name for t in self._tools):
                    self._tools.append(CLIENT_TOOLS[name])

    def _build_full_system_prompt(self) -> str:
        """Combine base system prompt with tool instructions."""
        tool_instructions = _build_tool_instructions(self._tools)
        return f"{self._base_system_prompt}\n\n---\n\n{tool_instructions}"

    # ── Public API ───────────────────────────────────────

    async def send_message(self, content: str) -> None:
        """Append a user message and run the JARVIS loop."""
        self._messages.append({"role": "user", "content": content})
        await self._jarvis_loop()

    def cancel(self) -> None:
        """Cancel the currently running loop."""
        self._cancelled = True

    # ── Core tool_use loop via ClaudeCodeBridge ──────────

    async def _jarvis_loop(self) -> None:
        """Run Claude via ClaudeCodeBridge and execute tool calls in a loop.

        Each iteration:
        1. Call bridge.raw_query() with system prompt + serialized conversation.
        2. Parse <tool_call> blocks from the response text.
        3. If tool calls found, execute each tool and feed results back.
        4. If no tool calls, the conversation turn is complete.

        Max 10 turns to prevent runaway loops and excessive API calls.
        """
        turns = 0
        max_turns = 10

        while turns < max_turns and not self._cancelled:
            turns += 1

            # Send progress update to frontend
            await self._ws_send({
                "type": "datalab_progress",
                "data": {"turn": turns, "max_turns": max_turns},
            })

            try:
                # Build the user message from conversation history
                user_message = self._serialize_conversation()
                system_prompt = self._build_full_system_prompt()

                response_text = await self._bridge.raw_query(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    model=self._model,
                    allowed_tools=[],   # No CLI tools — we manage tools ourselves
                    max_turns=1,        # Single turn per call; we control the loop
                    timeout=get_settings().worker_timeout,
                    effort=self._effort,
                )

                logger.info(
                    "JARVIS turn %d: response length=%d chars",
                    turns, len(response_text),
                )

            except Exception as exc:
                logger.exception("Error in JARVIS loop (turn %d)", turns)
                await self._ws_send({
                    "type": "datalab_stream",
                    "data": {
                        "token": f"\n\n[JARVIS] 오류가 발생했습니다: {exc}",
                        "done": True,
                    },
                })
                return

            if self._cancelled:
                return

            # Parse tool calls from response
            tool_calls = _parse_tool_calls(response_text)
            prose_text = _strip_tool_calls(response_text)

            # Stream the prose (non-tool-call) text to the frontend
            if prose_text:
                await self._ws_send({
                    "type": "datalab_stream",
                    "data": {"token": prose_text, "done": False},
                })

            # Append assistant message to conversation history
            self._messages.append({
                "role": "assistant",
                "content": response_text,
            })

            if not tool_calls:
                # No tools — conversation turn complete
                await self._ws_send({
                    "type": "datalab_stream",
                    "data": {"token": "", "done": True},
                })
                return

            # Execute each tool and collect results
            tool_results: list[str] = []
            for tool_name, tool_input in tool_calls:
                result_text = await self._execute_tool(tool_name, tool_input)

                # Dashboard detection: check after ANY tool call
                outputs_dash = Path(self._session_dir) / "outputs" / "dashboard.html"
                if outputs_dash.exists():
                    dash_mtime = outputs_dash.stat().st_mtime
                    if not hasattr(self, '_last_dash_mtime') or dash_mtime > self._last_dash_mtime:
                        self._last_dash_mtime = dash_mtime
                        await self._ws_send({
                            "type": "datalab_dashboard",
                            "data": {"url": f"/api/datalab/download/{self._session_id}/dashboard.html?t={int(dash_mtime)}"},
                        })
                    else:
                        logger.warning("No dashboard.html found after run_python in %s", self._session_dir)

                tool_results.append(
                    f"[Tool Result: {tool_name}]\n{result_text[:8_000]}"
                )

            # Feed tool results back as a user message
            combined_results = "\n\n".join(tool_results)
            self._messages.append({
                "role": "user",
                "content": f"Tool execution results:\n\n{combined_results}",
            })

            # Prune old tool results to reduce context size
            self._prune_old_tool_results()

        # Max turns reached
        if not self._cancelled:
            await self._ws_send({
                "type": "datalab_stream",
                "data": {
                    "token": "\n\n[JARVIS] 최대 실행 횟수에 도달했습니다.",
                    "done": True,
                },
            })

    # ── Conversation serialization ───────────────────────

    def _serialize_conversation(self) -> str:
        """Serialize the conversation history into a single user message for raw_query.

        Since raw_query takes a single user_message string, we serialize
        the full multi-turn conversation as a formatted text block.
        """
        if not self._messages:
            return "(no messages)"

        parts: list[str] = []
        for msg in self._messages:
            role = msg["role"].upper()
            content = msg["content"]
            if isinstance(content, str):
                parts.append(f"[{role}]\n{content}")
            elif isinstance(content, list):
                # Legacy format from old SDK-based messages (shouldn't happen in new code)
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                        elif item.get("type") == "tool_result":
                            text_parts.append(
                                f"[Tool Result: {item.get('tool_use_id', '?')}]\n"
                                f"{item.get('content', '')}"
                            )
                        elif item.get("type") == "tool_use":
                            text_parts.append(
                                f"[Tool Call: {item.get('name', '?')}]\n"
                                f"{json.dumps(item.get('input', {}), ensure_ascii=False)}"
                            )
                parts.append(f"[{role}]\n" + "\n".join(text_parts))
            else:
                parts.append(f"[{role}]\n{content}")

        return "\n\n".join(parts)

    # ── Tool execution ───────────────────────────────────

    async def _execute_tool(self, name: str, inputs: dict[str, Any]) -> str:
        """Execute a tool by name, checking DataLab executors first."""
        try:
            # DataLab tools take priority (per-engine bound executors)
            if name in self._datalab_executors:
                executor = self._datalab_executors[name]
                return await executor(**inputs)

            # Fall back to native tool executors
            if name in NATIVE_EXECUTORS:
                executor = NATIVE_EXECUTORS[name]
                return await executor(**inputs)

            return f"Error: unknown tool '{name}'"

        except Exception as exc:
            logger.exception("Tool execution error (%s)", name)
            return f"Error executing {name}: {type(exc).__name__}: {exc}"

    # ── Context pruning ─────────────────────────────────

    def _prune_old_tool_results(self) -> None:
        """Summarise old tool result messages to shrink context.

        Keeps the most recent tool result batch in full.  Older
        batches are truncated to the first 200 chars so that Claude
        retains awareness of what was done without the raw data.
        """
        tr_indices: list[int] = []
        for i, msg in enumerate(self._messages):
            if (
                msg["role"] == "user"
                and isinstance(msg["content"], str)
                and msg["content"].startswith("Tool execution results:")
            ):
                tr_indices.append(i)

        # Nothing to prune if 0 or 1 tool_result batches
        if len(tr_indices) <= 1:
            return

        for idx in tr_indices[:-1]:
            raw = self._messages[idx]["content"]
            if len(raw) > 300:
                self._messages[idx]["content"] = (
                    raw[:200] + "\n...[이전 결과 요약됨]"
                )

    # ── Helpers ──────────────────────────────────────────

    async def _ws_send(self, data: dict[str, Any]) -> None:
        """Send a JSON message through the WebSocket, ignoring errors."""
        try:
            await self._ws.send_json(data)
        except Exception:
            # WebSocket may have closed — log and continue
            logger.debug("WebSocket send failed (likely disconnected)")
