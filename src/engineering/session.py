"""AI Engineering mode — WebSocket session handler.

Orchestrates the full engineering workflow (BRAINSTORM -> PLAN -> IMPLEMENT ->
VERIFY -> COMPLETE) over a single WebSocket connection.  Coordinates the
PhaseEngine, ToolExecutor, WorkspaceManager, and ClaudeCodeBridge (CLI).

Message protocol (browser -> server):
  eng_message        — user chat message
  eng_stop           — end session gracefully
  eng_phase_back     — rewind to an earlier phase
  eng_download       — request zip of project workspace
  eng_file_uploaded  — file was uploaded via HTTP, notify AI context

Message protocol (server -> browser):
  eng_init         — session ready, includes session_id and starting phase
  eng_stream       — streaming text token (done=True marks end of turn)
  eng_phase_change — phase transition notification
  eng_file_tree    — updated workspace file listing
  eng_terminal     — terminal command + output echo
  eng_download_ready — zip download URL is ready
  eng_plan_update  — plan step status change
  eng_error        — error message
  heartbeat        — periodic keepalive
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from src.engineering.phase_engine import PhaseEngine, PlanStep
from src.engineering.resource_guard import (
    MAX_API_CALLS_PER_SESSION,
    ResourceGuard,
)
from src.engineering.session_store import SessionStore
from src.engineering.tools.executor import Phase, ToolExecutor
from src.engineering.workspace_manager import WorkspaceManager
from src.utils.bridge_factory import (
    get_bridge,
    prepare_cached_messages,
    prune_old_tool_results,
)

logger = logging.getLogger(__name__)


# ── Lightweight response wrapper for CLI bridge output ──

@dataclass
class _ToolUseBlock:
    """Mimics an Anthropic tool_use content block."""
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


# Regex to find tool-call JSON blocks the model emits when instructed.
# Matches ```tool_call\n{...}\n``` or <tool_call>{...}</tool_call>
_TOOL_CALL_FENCE_RE = re.compile(
    r'```tool_call\s*\n(\{.*?\})\s*\n```',
    re.DOTALL,
)
_TOOL_CALL_TAG_RE = re.compile(
    r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
    re.DOTALL,
)


def _parse_tool_calls(text: str) -> tuple[str, list[_ToolUseBlock]]:
    """Extract tool call JSON blocks from model output.

    Returns the cleaned text (with tool blocks removed) and a list of
    parsed _ToolUseBlock objects.
    """
    tool_uses: list[_ToolUseBlock] = []
    cleaned = text

    for pattern in (_TOOL_CALL_FENCE_RE, _TOOL_CALL_TAG_RE):
        for match in pattern.finditer(text):
            try:
                data = json.loads(match.group(1))
                tool_uses.append(_ToolUseBlock(
                    id=data.get("id", f"tool_{uuid.uuid4().hex[:8]}"),
                    name=data.get("name", ""),
                    input=data.get("input", {}),
                ))
                cleaned = cleaned.replace(match.group(0), "")
            except (json.JSONDecodeError, KeyError):
                continue

    return cleaned.strip(), tool_uses


def _build_tool_instructions(tools: list[dict]) -> str:
    """Convert Anthropic-style tool definitions into text instructions.

    When using the CLI bridge, we embed tool schemas as text in the system
    prompt so the model knows which tools are available and how to call them.
    """
    if not tools:
        return ""

    lines = [
        "\n\n## Available Tools",
        "You may call tools by outputting a fenced JSON block like this:",
        "```tool_call",
        '{"name": "tool_name", "input": {"param": "value"}}',
        "```",
        "",
        "Available tools:",
    ]
    for t in tools:
        schema = t.get("input_schema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])
        param_desc = ", ".join(
            f"{k} ({'required' if k in required else 'optional'}): {v.get('description', '')}"
            for k, v in props.items()
        )
        lines.append(f"- **{t['name']}**: {t.get('description', '')}")
        if param_desc:
            lines.append(f"  Parameters: {param_desc}")

    lines.append("")
    lines.append(
        "After each tool call, I will provide the tool result. "
        "You may call multiple tools in sequence. "
        "When you are done with tools, provide your final response as plain text."
    )
    return "\n".join(lines)


def _serialize_messages(messages: list[dict]) -> str:
    """Serialize conversation history into a text block for the CLI prompt.

    Converts the Anthropic-style messages list into a readable format
    that preserves the conversation context.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            parts.append(f"[{role}]\n{content}")
        elif isinstance(content, list):
            # Handle structured content (tool_result blocks, etc.)
            sub_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "tool_result":
                        tool_id = item.get("tool_use_id", "?")
                        result_content = item.get("content", "")
                        sub_parts.append(f"[tool_result for {tool_id}]\n{result_content}")
                    elif item.get("type") == "text":
                        sub_parts.append(item.get("text", ""))
                    else:
                        sub_parts.append(json.dumps(item, ensure_ascii=False))
                elif hasattr(item, "type"):
                    # Anthropic SDK content block objects stored from previous turns
                    if getattr(item, "type", "") == "text":
                        sub_parts.append(getattr(item, "text", ""))
                    elif getattr(item, "type", "") == "tool_use":
                        name = getattr(item, "name", "?")
                        inp = getattr(item, "input", {})
                        sub_parts.append(f"[tool_call: {name}({json.dumps(inp, ensure_ascii=False)})]")
                    else:
                        sub_parts.append(str(item))
                else:
                    sub_parts.append(str(item))
            parts.append(f"[{role}]\n" + "\n".join(sub_parts))
        else:
            parts.append(f"[{role}]\n{content}")

    return "\n\n".join(parts)


def _truncate_head_tail(text: str, limit: int = 16_000) -> str:
    """Truncate preserving head and tail — errors often appear at the end of build logs."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head - 50
    return text[:head] + "\n\n...[중략]...\n\n" + text[-tail:]

# ── Phase-specific model & effort configuration ──
# model: CLI alias (resolved by bridge); effort: passed via --effort flag
_PHASE_CONFIG: dict[Phase, dict] = {
    Phase.BRAINSTORM: {"model": "sonnet", "effort": "medium", "timeout": 300},
    Phase.PLAN:       {"model": "sonnet", "effort": "high",   "timeout": 600},
    Phase.IMPLEMENT:  {"model": "sonnet", "effort": "medium", "timeout": 900},
    Phase.VERIFY:     {"model": "sonnet", "effort": "low",    "timeout": 300},
    Phase.COMPLETE:   {"model": "sonnet", "effort": "low",    "timeout": 300},
}

# ── Module-level singleton ────────────────────────
_resource_guard = ResourceGuard()


class EngineeringSession:
    """One WebSocket connection for an AI Engineering session.

    Manages the conversation loop, phase transitions, Anthropic API calls,
    tool execution, and workspace lifecycle.
    """

    def __init__(self, ws, user_id: str = "") -> None:
        self.ws = ws
        self._user_id = user_id
        self._session_id = f"eng_{uuid.uuid4().hex[:12]}"
        self._cancelled = False

        # Components
        self._phase_engine = PhaseEngine()
        self._workspace_mgr = WorkspaceManager()
        self._session_store = SessionStore()
        self._tool_executor: ToolExecutor | None = None  # lazy init at IMPLEMENT
        self._resource_guard = _resource_guard
        self._assigned_port: int | None = None

        # Bridge (ClaudeCodeBridge via CLI subprocess)
        self._bridge = get_bridge()
        self._messages: list[dict] = []  # conversation history
        self._api_call_count = 0
        self._total_tokens = 0  # approximate — CLI doesn't report exact tokens
        self._uploaded_files: list[dict] = []  # metadata of uploaded files

        # Heartbeat
        self._heartbeat_task: asyncio.Task | None = None

    # ── Main entry ────────────────────────────────

    async def run(self) -> None:
        """Main entry: check limits -> send init -> chat loop -> cleanup."""
        # Guard: server capacity
        if not self._resource_guard.can_start_session():
            await self._send({
                "type": "eng_error",
                "data": {"message": "현재 서비스가 바쁩니다. 잠시 후 다시 시도해주세요."},
            })
            return

        # Guard: per-user limit
        if self._user_id and not self._resource_guard.can_user_start(self._user_id):
            await self._send({
                "type": "eng_error",
                "data": {"message": "이미 진행 중인 세션이 있습니다."},
            })
            return

        # Guard: daily limit
        if self._user_id and not self._resource_guard.check_daily_limit(self._user_id):
            await self._send({
                "type": "eng_error",
                "data": {"message": "일일 세션 한도에 도달했습니다. 내일 다시 시도해주세요."},
            })
            return

        self._resource_guard.register_session(self._session_id, self._user_id)
        self._resource_guard._record_daily_session(self._user_id)

        await self._send({
            "type": "eng_init",
            "data": {
                "session_id": self._session_id,
                "phase": self._phase_engine.current_phase.value,
            },
        })

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            await self._chat_loop()
        finally:
            await self._cleanup()

    # ── Chat loop ─────────────────────────────────

    async def _chat_loop(self) -> None:
        """Listen for WebSocket messages and dispatch by type."""
        while not self._cancelled:
            try:
                msg = await self.ws.receive_json()
            except Exception:
                break

            msg_type = msg.get("type")

            if msg_type == "eng_stop":
                break
            elif msg_type == "eng_message":
                content = msg.get("data", {}).get("content", "").strip()
                if content:
                    await self._handle_message(content)
            elif msg_type == "eng_file_uploaded":
                file_data = msg.get("data", {})
                await self._handle_file_uploaded(file_data)
            elif msg_type == "eng_phase_back":
                target = msg.get("data", {}).get("target", "brainstorm")
                await self._handle_phase_back(target)
            elif msg_type == "eng_download":
                await self._handle_download()

    # ── Message handling ──────────────────────────

    async def _handle_message(self, content: str) -> None:
        """Process a user message through the current phase.

        1. Add user message to history
        2. Build system prompt for current phase
        3. Call CLI bridge (with tool instructions if applicable)
        4. Process response (tool_use loop or final text)
        5. Detect phase transition signals
        """
        # API call budget check
        if self._api_call_count >= MAX_API_CALLS_PER_SESSION:
            await self._send({
                "type": "eng_error",
                "data": {"message": "세션 API 호출 한도에 도달했습니다. 새 세션을 시작해주세요."},
            })
            return

        self._messages.append({"role": "user", "content": content})

        phase = self._phase_engine.current_phase
        system_prompt = self._get_system_prompt(phase)
        tools = self._get_tools(phase)

        try:
            response = await self._call_api(system_prompt, tools)
            await self._process_response(response)
        except Exception as exc:
            logger.warning("eng_handle_message_error: %s", exc)
            await self._send({
                "type": "eng_stream",
                "data": {"token": f"오류가 발생했습니다: {exc}", "done": False},
            })
            await self._send({
                "type": "eng_stream",
                "data": {"token": "", "done": True},
            })

    # ── CLI Bridge API ────────────────────────────

    async def _call_api(
        self, system_prompt: str, tools: list[dict]
    ) -> tuple[str, list[_ToolUseBlock]]:
        """Call Claude via CLI bridge with phase-specific model & effort.

        Returns (cleaned_text, tool_uses) parsed from the raw CLI output.
        """
        phase = self._phase_engine.current_phase
        config = _PHASE_CONFIG.get(phase, _PHASE_CONFIG[Phase.BRAINSTORM])

        # Build system prompt with embedded tool instructions
        full_system = system_prompt + _build_tool_instructions(tools)

        # Serialize conversation history into a text prompt
        user_message = _serialize_messages(
            prepare_cached_messages(self._messages)
        )

        raw_text = await self._bridge.raw_query(
            system_prompt=full_system,
            user_message=user_message,
            model=config["model"],
            effort=config.get("effort"),
            timeout=config.get("timeout", 900),
            max_turns=1,  # single turn — we manage the tool loop ourselves
            allowed_tools=[],  # no MCP tools; custom tools handled locally
        )
        self._api_call_count += 1

        logger.info(
            "eng_api_call: phase=%s model=%s chars=%d",
            phase.value, config["model"], len(raw_text),
        )

        # Parse tool calls from the response text
        cleaned_text, tool_uses = _parse_tool_calls(raw_text)
        return cleaned_text, tool_uses

    # ── Response processing ───────────────────────

    async def _process_response(
        self, result: tuple[str, list[_ToolUseBlock]]
    ) -> None:
        """Process CLI bridge response: stream text, execute tools, detect transitions."""
        cleaned_text, tool_uses = result

        # Stream text to the client
        if cleaned_text:
            await self._send({
                "type": "eng_stream",
                "data": {"token": cleaned_text, "done": False},
            })

        # Add assistant message to history (as plain text)
        self._messages.append({"role": "assistant", "content": cleaned_text})

        if tool_uses:
            await self._handle_tool_uses(tool_uses)
        else:
            # End of turn — signal done
            await self._send({
                "type": "eng_stream",
                "data": {"token": "", "done": True},
            })
            # Check for phase transition in the text
            await self._check_phase_transition(cleaned_text)

    async def _handle_tool_uses(self, tool_uses: list[_ToolUseBlock]) -> None:
        """Execute tool calls and return results for the next turn."""
        if self._tool_executor is None:
            # Should only happen if tools are used outside IMPLEMENT/VERIFY
            await self._send({
                "type": "eng_error",
                "data": {"message": "도구를 사용할 수 없는 단계입니다."},
            })
            return

        phase = self._phase_engine.current_phase
        tool_results: list[dict] = []

        for tool_use in tool_uses:
            result = self._tool_executor.execute(
                tool_use.name, tool_use.input, phase
            )
            raw_content = json.dumps(result, ensure_ascii=False)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": _truncate_head_tail(raw_content, 16_000),
            })

            # Stream terminal output for run_command
            if tool_use.name == "run_command":
                await self._send({
                    "type": "eng_terminal",
                    "data": {
                        "command": tool_use.input.get("command", ""),
                        "output": result,
                    },
                })

            # Update file tree after file mutations
            if tool_use.name in ("write_file", "edit_file", "delete_file"):
                files = self._workspace_mgr.list_files(self._session_id)
                await self._send({
                    "type": "eng_file_tree",
                    "data": {"files": files},
                })

        # Add tool results to messages
        self._messages.append({"role": "user", "content": tool_results})

        # Prune old tool results to reduce context (keep last 2 batches)
        prune_old_tool_results(self._messages, keep_recent=2)

        # Budget check before recursing
        if self._api_call_count >= MAX_API_CALLS_PER_SESSION:
            await self._send({
                "type": "eng_stream",
                "data": {
                    "token": "\n\n[세션 API 호출 한도에 도달했습니다]",
                    "done": False,
                },
            })
            await self._send({
                "type": "eng_stream",
                "data": {"token": "", "done": True},
            })
            return

        # Continue conversation with tool results
        system_prompt = self._get_system_prompt(phase)
        tools = self._get_tools(phase)
        result = await self._call_api(system_prompt, tools)
        await self._process_response(result)  # recursive until no more tool_use

    # ── Phase transitions ─────────────────────────

    async def _check_phase_transition(self, text: str) -> None:
        """Detect phase transition signals in the AI response text.

        The AI is instructed to include specific markers:
        - [PHASE:PLAN]      -> advance to PLAN
        - [PHASE:IMPLEMENT] -> advance to IMPLEMENT
        - [PHASE:VERIFY]    -> advance to VERIFY
        - [PHASE:COMPLETE]  -> advance to COMPLETE

        Also detects plan steps in PLAN phase responses.
        """
        phase = self._phase_engine.current_phase

        # Parse plan steps from PLAN phase responses
        if phase == Phase.PLAN and "[PLAN_STEPS]" in text:
            self._parse_and_set_plan_steps(text)

        # Detect explicit transition markers
        transition_map = {
            "[PHASE:PLAN]": Phase.PLAN,
            "[PHASE:IMPLEMENT]": Phase.IMPLEMENT,
            "[PHASE:VERIFY]": Phase.VERIFY,
            "[PHASE:COMPLETE]": Phase.COMPLETE,
        }

        for marker, target_phase in transition_map.items():
            if marker in text:
                await asyncio.sleep(2)
                await self._transition_to(target_phase)
                return

        # Fallback: detect natural language transition signals
        # (for models that don't output explicit markers)
        text_lower = text.lower()
        fallback_map: dict[Phase, list[tuple[str, Phase]]] = {
            Phase.BRAINSTORM: [
                ("계획을 세울", Phase.PLAN),
                ("계획 단계로", Phase.PLAN),
                ("다음 단계로", Phase.PLAN),
                ("plan 단계", Phase.PLAN),
            ],
            Phase.PLAN: [
                ("구현을 시작", Phase.IMPLEMENT),
                ("만들어볼게", Phase.IMPLEMENT),
                ("구현 단계로", Phase.IMPLEMENT),
                ("본격적으로 만들", Phase.IMPLEMENT),
            ],
            Phase.IMPLEMENT: [
                ("검증", Phase.VERIFY),
                ("확인을 해볼", Phase.VERIFY),
                ("테스트", Phase.VERIFY),
                ("최종 확인", Phase.VERIFY),
            ],
            Phase.VERIFY: [
                ("완성", Phase.COMPLETE),
                ("축하", Phase.COMPLETE),
                ("다운로드", Phase.COMPLETE),
            ],
        }
        for keyword, target in fallback_map.get(phase, []):
            if keyword in text_lower:
                logger.info("fallback phase transition: %s -> %s (keyword: %s)", phase.value, target.value, keyword)
                await asyncio.sleep(2)
                await self._transition_to(target)
                return

    async def _transition_to(self, target: Phase) -> None:
        """Execute a phase transition with all necessary side-effects."""
        current = self._phase_engine.current_phase

        # Advance until we reach the target (or can't advance further)
        while self._phase_engine.current_phase != target:
            if not self._phase_engine.can_advance():
                # Use force_advance if the AI signals transition explicitly
                self._phase_engine.force_advance()
            else:
                self._phase_engine.advance()

            if self._phase_engine.current_phase == current:
                # Stuck at same phase — bail
                break
            current = self._phase_engine.current_phase

        new_phase = self._phase_engine.current_phase

        # Side-effects for entering IMPLEMENT
        if new_phase == Phase.IMPLEMENT and self._tool_executor is None:
            await self._enter_implement_phase()

        await self._send({
            "type": "eng_phase_change",
            "data": {"phase": new_phase.value},
        })

        # Auto-trigger AI for the new phase (no user input needed)
        await self._auto_start_phase(new_phase)

    async def _auto_start_phase(self, phase: Phase) -> None:
        """Automatically kick off the AI for the new phase without user input.

        Injects a hidden system-level message so the AI starts working
        in the new phase context immediately after transition.
        """
        auto_messages = {
            Phase.PLAN: "브레인스토밍이 완료되었습니다. 지금까지 논의한 내용을 바탕으로 구체적인 개발 계획을 세워주세요.",
            Phase.IMPLEMENT: "개발 계획이 승인되었습니다. 이제 계획에 따라 구현을 시작해주세요.",
            Phase.VERIFY: "구현이 완료되었습니다. 프로젝트를 검증해주세요.",
            Phase.COMPLETE: "검증이 완료되었습니다. 프로젝트를 정리하고 다운로드를 준비해주세요.",
        }
        prompt = auto_messages.get(phase)
        if not prompt:
            return

        # Add as a user message (invisible to the real user but drives the AI)
        self._messages.append({"role": "user", "content": prompt})

        try:
            system_prompt = self._get_system_prompt(phase)
            tools = self._get_tools(phase)
            response = await self._call_api(system_prompt, tools)
            await self._process_response(response)
        except Exception as exc:
            logger.warning("auto_start_phase error (%s): %s", phase.value, exc)
            await self._send({
                "type": "eng_stream",
                "data": {"token": f"다음 단계를 시작하는 중 오류가 발생했습니다. 메시지를 입력해주세요.", "done": True},
            })

    async def _enter_implement_phase(self) -> None:
        """Acquire resources and initialize tool executor for IMPLEMENT phase."""
        slot = self._resource_guard.acquire_implement_slot(self._session_id)
        if slot is None:
            await self._send({
                "type": "eng_error",
                "data": {
                    "message": "현재 구현 슬롯이 부족합니다. 잠시 후 다시 시도해주세요.",
                },
            })
            return

        self._assigned_port = slot.port

        # Create workspace (idempotent — skips if already exists from file upload)
        workspace_path = self._workspace_mgr.create(self._session_id)

        # Initialize tool executor with workspace
        self._tool_executor = ToolExecutor(workspace_path)

        logger.info(
            "eng_implement_started: session=%s port=%d workspace=%s",
            self._session_id, slot.port, workspace_path,
        )

    def _parse_and_set_plan_steps(self, text: str) -> None:
        """Parse plan steps from AI response and register them with PhaseEngine.

        Expected format within the response text (after [PLAN_STEPS] marker)::

            [PLAN_STEPS]
            1. Set up project structure
            2. Implement core logic
            3. Add styling
            4. Write tests
        """
        try:
            marker_idx = text.index("[PLAN_STEPS]")
            steps_text = text[marker_idx + len("[PLAN_STEPS]"):].strip()

            # Also try JSON format: [{"id": 0, "description": "..."}]
            try:
                steps_json = json.loads(steps_text.split("\n\n")[0].strip())
                if isinstance(steps_json, list):
                    plan_steps = [
                        PlanStep(id=s.get("id", i), description=s.get("description", ""))
                        for i, s in enumerate(steps_json)
                    ]
                    self._phase_engine.set_plan_steps(plan_steps)
                    return
            except (json.JSONDecodeError, ValueError):
                pass

            # Fallback: numbered list format
            plan_steps: list[PlanStep] = []
            for line in steps_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Match patterns like "1. Do something" or "- Do something"
                cleaned = line.lstrip("0123456789.-) ").strip()
                if cleaned:
                    plan_steps.append(
                        PlanStep(id=len(plan_steps), description=cleaned)
                    )
                # Stop at next section or empty line run
                if len(plan_steps) > 20:
                    break

            if plan_steps:
                self._phase_engine.set_plan_steps(plan_steps)
        except (ValueError, IndexError):
            pass

    # ── Phase back (rewind) ───────────────────────

    async def _handle_phase_back(self, target: str) -> None:
        """Rewind to a previous phase."""
        try:
            target_phase = Phase(target)
            self._phase_engine.rewind(target_phase)
            await self._send({
                "type": "eng_phase_change",
                "data": {"phase": self._phase_engine.current_phase.value},
            })
        except (ValueError, KeyError) as exc:
            await self._send({
                "type": "eng_error",
                "data": {"message": f"페이즈 이동 실패: {exc}"},
            })

    # ── Download ──────────────────────────────────

    async def _handle_download(self) -> None:
        """Create a zip of the workspace and send the download URL."""
        if not self._workspace_mgr.get_workspace(self._session_id):
            await self._send({
                "type": "eng_error",
                "data": {"message": "다운로드할 프로젝트가 없습니다."},
            })
            return

        try:
            self._workspace_mgr.create_zip(self._session_id)
            await self._send({
                "type": "eng_download_ready",
                "data": {"url": f"/api/eng/download/{self._session_id}"},
            })
        except FileNotFoundError:
            await self._send({
                "type": "eng_error",
                "data": {"message": "워크스페이스를 찾을 수 없습니다."},
            })

    # ── File upload handling ─────────────────────

    # Text-readable extensions (auto-read content for AI context)
    _TEXT_EXTENSIONS = {
        ".csv", ".json", ".txt", ".py", ".js", ".ts", ".html", ".css",
        ".md", ".xml", ".yaml", ".yml", ".env", ".sql", ".tsx", ".jsx",
        ".toml", ".cfg", ".ini", ".sh", ".bat",
    }

    async def _handle_file_uploaded(self, file_data: dict) -> None:
        """Process a file upload notification from the frontend.

        Reads text-based files under 100 KB and injects their content into
        the conversation so the AI is aware of them.  Binary files (images,
        PDFs, etc.) are reported by name and size only.
        """
        filename = file_data.get("filename", "")
        size = file_data.get("size", 0)
        rel_path = file_data.get("path", "")

        self._uploaded_files.append({
            "filename": filename,
            "size": size,
            "path": rel_path,
        })

        # Try to read text content for AI context
        file_content: str | None = None
        ext = ("." + filename.rsplit(".", 1)[-1]).lower() if "." in filename else ""

        if ext in self._TEXT_EXTENSIONS and size <= 100 * 1024:
            ws = self._workspace_mgr.get_workspace(self._session_id)
            if ws:
                file_path = ws / rel_path
                if file_path.exists():
                    try:
                        file_content = file_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                    except Exception:
                        pass

        # Build context message for AI
        if file_content:
            context_msg = (
                f"[사용자가 파일을 첨부했습니다: {filename} ({size:,} bytes)]\n"
                f"--- 파일 내용 ---\n{file_content}\n--- 파일 끝 ---"
            )
        else:
            kind = "이미지" if ext in {".png", ".jpg", ".jpeg", ".gif", ".svg"} else "바이너리"
            context_msg = (
                f"[사용자가 {kind} 파일을 첨부했습니다: {filename} ({size:,} bytes)] "
                f"이 파일은 workspace/uploads/{filename} 경로에 저장되어 있습니다."
            )

        # Add to conversation history so the AI knows about the file
        self._messages.append({"role": "user", "content": context_msg})

        # Confirm back to the frontend
        await self._send({
            "type": "eng_stream",
            "data": {
                "token": f"파일을 확인했습니다: **{filename}** ({self._format_size(size)})\n\n",
                "done": False,
            },
        })
        await self._send({
            "type": "eng_stream",
            "data": {"token": "", "done": True},
        })

        logger.info(
            "eng_file_uploaded: session=%s file=%s size=%d text=%s",
            self._session_id, filename, size, file_content is not None,
        )

    def _get_uploaded_files(self) -> list[dict]:
        """List files in workspace/uploads/ with metadata."""
        ws = self._workspace_mgr.get_workspace(self._session_id)
        if not ws:
            return []
        uploads_dir = ws / "uploads"
        if not uploads_dir.exists():
            return []
        return [
            {
                "filename": f.name,
                "size": f.stat().st_size,
                "path": f"uploads/{f.name}",
            }
            for f in uploads_dir.iterdir()
            if f.is_file()
        ]

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Human-readable file size."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes / (1024 * 1024):.1f} MB"

    # ── System prompts ────────────────────────────

    def _get_system_prompt(self, phase: Phase) -> str:
        """Return the system prompt appropriate for the current phase."""
        from src.engineering.prompts import (
            BRAINSTORM_PROMPT,
            IMPLEMENT_PROMPT,
            PLAN_PROMPT,
            VERIFY_PROMPT,
        )

        prompts = {
            Phase.BRAINSTORM: BRAINSTORM_PROMPT,
            Phase.PLAN: PLAN_PROMPT,
            Phase.IMPLEMENT: IMPLEMENT_PROMPT,
            Phase.VERIFY: VERIFY_PROMPT,
            Phase.COMPLETE: VERIFY_PROMPT,
        }
        base = prompts.get(phase, BRAINSTORM_PROMPT)

        # Inject phase-specific context
        if phase == Phase.IMPLEMENT and self._assigned_port:
            base += f"\n\nAssigned dev server port: {self._assigned_port}"

        if phase == Phase.IMPLEMENT:
            summary = self._phase_engine.get_plan_summary()
            if summary["total"] > 0:
                steps_desc = "\n".join(
                    f"  [{s['status']}] {s['id']}. {s['description']}"
                    for s in summary["steps"]
                )
                base += f"\n\nCurrent plan ({summary['done']}/{summary['total']} done):\n{steps_desc}"

        # Inject uploaded file context
        if self._uploaded_files:
            file_list = "\n".join(
                f"  - {f['filename']} ({f['size']:,} bytes) → workspace/{f['path']}"
                for f in self._uploaded_files
            )
            base += (
                f"\n\nUser-uploaded files (available in workspace/uploads/):\n{file_list}"
                "\nYou may read or reference these files as needed."
            )

        return base

    def _get_tools(self, phase: Phase) -> list[dict]:
        """Return Anthropic tool definitions for the current phase."""
        if self._tool_executor:
            return self._tool_executor.get_tool_definitions(phase)
        return []

    # ── Cleanup ───────────────────────────────────

    async def _cleanup(self) -> None:
        """Release all resources on session end."""
        # Cancel heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Stop dev server if running
        if self._tool_executor and self._tool_executor._process_manager.is_running():
            self._tool_executor._process_manager.stop()

        # Release implement slot
        self._resource_guard.release_slot(self._session_id)
        self._resource_guard.unregister_session(self._session_id)

        logger.info(
            "eng_session_ended: session=%s calls=%d tokens=%d",
            self._session_id, self._api_call_count, self._total_tokens,
        )

    # ── Disconnect handler ────────────────────────

    async def on_disconnect(self) -> None:
        """Handle unexpected disconnect: save context, then cleanup."""
        self._cancelled = True
        await self._save_context()
        await self._cleanup()

    def cancel(self) -> None:
        """Synchronous cancel flag (called from WebSocket exception handlers)."""
        self._cancelled = True

    # ── Context persistence ───────────────────────

    async def _save_context(self) -> None:
        """Save lightweight technical context to SessionStore for later resume."""
        context = {
            "session_id": self._session_id,
            "last_phase": self._phase_engine.current_phase.value,
            "plan_summary": self._phase_engine.get_plan_summary(),
            "api_calls": self._api_call_count,
            "total_tokens": self._total_tokens,
        }
        try:
            self._session_store.save(context, user_id=self._user_id)
        except Exception as exc:
            logger.warning("eng_save_context_error: %s", exc)

    # ── WebSocket helpers ─────────────────────────

    async def _send(self, msg: dict) -> None:
        """Send a JSON message to the WebSocket client."""
        try:
            await self.ws.send_json(msg)
        except Exception:
            self._cancelled = True

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to keep the connection alive."""
        try:
            while not self._cancelled:
                await asyncio.sleep(15)
                await self._send({"type": "heartbeat", "ts": time.time()})
        except asyncio.CancelledError:
            pass
