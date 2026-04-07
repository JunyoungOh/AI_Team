"""Sage engine — CLI bridge tool_use loop for Foresight mode.

Converted from Anthropic SDK streaming to ClaudeCodeBridge.raw_query().
Server-side tools (web_search, web_fetch) are now CLI built-ins (WebSearch, WebFetch).
Custom foresight tools are embedded as text instructions in the system prompt
and parsed from <tool_call> blocks in the response.
"""
from __future__ import annotations

import json
import logging
import random
import re
from functools import partial
from typing import Any

from src.config.settings import get_settings
from src.foresight.prompts.system import build_system_prompt
from src.foresight.tools import (
    FORESIGHT_TOOL_SCHEMAS,
    FORESIGHT_TOOL_EXECUTORS,
    make_session_context,
)
from src.foresight.calibration import platt_scale, PLATT_ALPHA
from src.tools.native import CLIENT_TOOLS, TOOL_EXECUTORS as NATIVE_EXECUTORS
from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)

# Reverse map: resolve full model IDs back to CLI aliases
_MODEL_TO_ALIAS: dict[str, str] = {
    "claude-opus-4-6": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5-20251001": "haiku",
}

# Effort setting → CLI --effort flag value
_EFFORT_TO_CLI: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}

ORACLE_NAMES = [
    "Oracle", "Pythia", "Sibyl", "Cassandra",
    "Delphi", "Augur", "Seer", "Prophet",
]

# Core native tools — always available (lightweight, high-value).
_CORE_NATIVE_TOOLS: list[str] = [
    "quickchart_render",
    "firecrawl_scrape",
]

# Extended native tools — added dynamically when Sage detects relevant data.
# Kept separate to reduce tool count (fewer tools = faster CLI response).
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


# ── Tool instruction builder (same as ensemble.py) ──────

def _build_tool_instructions(tool_schemas: dict[str, dict[str, Any]]) -> str:
    """Build text instructions for custom tools to embed in the system prompt.

    The LLM responds with <tool_call>{"name": ..., "input": ...}</tool_call>
    when it wants to use a tool.
    """
    lines = [
        "You have access to the following custom tools. To use a tool, respond with a "
        "<tool_call> block containing JSON with 'name' and 'input' keys.\n"
        "Example: <tool_call>{\"name\": \"tool_name\", \"input\": {\"key\": \"value\"}}</tool_call>\n"
        "\nYou can call MULTIPLE tools in a single response by including multiple <tool_call> blocks.\n"
        "\nAvailable custom tools:"
    ]
    for name, schema in tool_schemas.items():
        desc = schema.get("description", "")
        input_schema = json.dumps(schema.get("input_schema", {}), ensure_ascii=False)
        lines.append(f"\n## {name}\n{desc}\nInput schema: {input_schema}")
    return "\n".join(lines)


# Regex to extract tool calls from LLM text
_TOOL_CALL_RE = re.compile(
    r'<tool_call>\s*(\{.*?\})\s*</tool_call>',
    re.DOTALL,
)


def _parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse <tool_call> blocks from LLM response text."""
    calls = []
    for m in _TOOL_CALL_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            if "name" in obj:
                calls.append(obj)
        except json.JSONDecodeError:
            logger.debug("Failed to parse tool_call JSON: %s", m.group(1)[:200])
    return calls


class SageEngine:
    """CLI bridge tool_use engine that powers Sage in Foresight mode.

    Connects Claude to Foresight tools (run_python, read_uploaded_file,
    save/load_environment, export_report) and selected native tools
    (finance APIs, charts, web scraping).  Sends text and agent
    state updates to the frontend via WebSocket.

    Uses ClaudeCodeBridge.raw_query() with:
    - WebSearch / WebFetch as CLI built-in tools (allowed_tools)
    - Custom foresight + native tools as text instructions parsed via <tool_call>
    """

    # Phase-specific model configuration
    PHASE_CONFIG = {
        "environment": {  # Phase 1: 환경 구축 — 검색, 정리, 구조화
            "model_key": "foresight_env_model",
            "effort_key": "foresight_env_effort",
            "max_tokens": 4096,
        },
        "prediction": {   # Phase 2: 미래 예측 — 인과 추론, 다중 관점, 시나리오
            "model_key": "foresight_predict_model",
            "effort_key": "foresight_predict_effort",
            "max_tokens": 8192,
        },
    }

    def __init__(
        self,
        session_dir: str,
        ws: Any,
        user_id: str = "",
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        self._bridge = get_bridge()
        self._phase = "environment"  # Start in Phase 1
        self._compressed_profile: str | None = None  # 압축된 환경 프로필

        # Load Phase 1 model config — resolve to CLI alias
        phase_cfg = self.PHASE_CONFIG[self._phase]
        resolved_model = model or getattr(settings, phase_cfg["model_key"])
        self._model = _MODEL_TO_ALIAS.get(resolved_model, resolved_model)
        self._effort = _EFFORT_TO_CLI.get(
            getattr(settings, phase_cfg["effort_key"], "medium"), "medium"
        )

        self._ws = ws
        self._session_dir = session_dir
        self._messages: list[dict[str, Any]] = []
        self._system_prompt = build_system_prompt(session_dir)
        self._custom_tool_schemas = self._build_custom_tool_schemas()
        self._tool_instructions = _build_tool_instructions(self._custom_tool_schemas)
        self._used_oracles: list[str] = []
        self._cancelled = False
        self._last_report_filename: str | None = None

        # Create per-engine session context and bind executors —
        # each SageEngine gets its own ctx so concurrent sessions
        # don't contaminate each other's file paths.
        ctx = make_session_context(session_dir, user_id=user_id)
        self._foresight_executors = {
            name: partial(fn, ctx)
            for name, fn in FORESIGHT_TOOL_EXECUTORS.items()
        }

    def _switch_to_prediction(self, profile: str) -> None:
        """Switch from Phase 1 (environment) to Phase 2 (prediction).

        Triggered when Sage calls the compress_environment tool.
        Uses settings-configured model for prediction phase.
        """
        settings = get_settings()
        self._phase = "prediction"
        self._compressed_profile = profile

        phase_cfg = self.PHASE_CONFIG[self._phase]
        resolved_model = getattr(settings, phase_cfg["model_key"])
        self._model = _MODEL_TO_ALIAS.get(resolved_model, resolved_model)
        self._effort = _EFFORT_TO_CLI.get(
            getattr(settings, phase_cfg["effort_key"], "medium"), "medium"
        )

        logger.info(
            "Phase transition: environment -> prediction (model=%s, effort=%s)",
            self._model, self._effort,
        )

    # -- Custom tool schema assembly ------------------------------

    def _build_custom_tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Build dict of custom tool schemas (foresight + core native).

        These tools are executed locally (not by CLI). They are described
        in the system prompt as text instructions.
        """
        schemas: dict[str, dict[str, Any]] = {}
        # Foresight tools
        schemas.update(FORESIGHT_TOOL_SCHEMAS)
        # Core native tools
        for name in _CORE_NATIVE_TOOLS:
            if name in CLIENT_TOOLS:
                schemas[name] = CLIENT_TOOLS[name]
        return schemas

    def add_extended_tools(self, tool_names: list[str]) -> None:
        """Dynamically add native tools when data context requires them."""
        changed = False
        for name in tool_names:
            if name in _EXTENDED_NATIVE_TOOLS and name in CLIENT_TOOLS:
                if name not in self._custom_tool_schemas:
                    self._custom_tool_schemas[name] = CLIENT_TOOLS[name]
                    changed = True
        if changed:
            self._tool_instructions = _build_tool_instructions(self._custom_tool_schemas)

    # -- Oracle assignment ----------------------------------------

    def _assign_oracle(self) -> str:
        """Assign a random unused oracle name for visual agent identity."""
        available = [o for o in ORACLE_NAMES if o not in self._used_oracles]
        if not available:
            # All oracles used — reset and start over
            self._used_oracles.clear()
            available = list(ORACLE_NAMES)

        oracle = random.choice(available)
        self._used_oracles.append(oracle)
        return oracle

    # -- Public API -----------------------------------------------

    async def send_message(self, content: str) -> None:
        """Append a user message and run the Sage loop."""
        self._messages.append({"role": "user", "content": content})
        await self._sage_loop()

    def cancel(self) -> None:
        """Cancel the currently running loop."""
        self._cancelled = True

    # -- Core tool_use loop (CLI bridge version) ------------------

    async def _sage_loop(self) -> None:
        """Run Claude via CLI bridge and execute tool calls in a loop.

        Each iteration:
        1. Call bridge.raw_query() with WebSearch/WebFetch as CLI tools
           and custom tools as text instructions in the system prompt.
        2. Parse the response for <tool_call> blocks.
        3. If tool calls found, execute each locally and feed results back.
        4. If no tool calls, the conversation turn is complete.
        5. Stream text to frontend via WebSocket.

        Max 10 turns to prevent runaway loops.
        """
        turns = 0
        max_turns = 10

        while turns < max_turns and not self._cancelled:
            turns += 1

            # Send progress update to frontend
            await self._ws_send({
                "type": "foresight_progress",
                "data": {"turn": turns, "max_turns": max_turns},
            })

            # Build the full system prompt with custom tool instructions
            full_system = f"{self._system_prompt}\n\n{self._tool_instructions}"

            # Build conversation text from message history
            user_message = self._build_conversation_text()

            try:
                response_text = await self._bridge.raw_query(
                    system_prompt=full_system,
                    user_message=user_message,
                    model=self._model,
                    allowed_tools=["WebSearch", "WebFetch"],
                    max_turns=3,  # Allow CLI to handle a few web search/fetch cycles
                    timeout=300,
                    effort=self._effort,
                )
            except Exception as exc:
                logger.error("Bridge error in Sage loop: %s", exc)
                await self._ws_send({
                    "type": "foresight_stream",
                    "data": {
                        "token": f"\n\n[Sage] API 오류가 발생했습니다: {exc}",
                        "done": True,
                    },
                })
                return

            if self._cancelled:
                return

            # Stream the full response text to frontend
            # (CLI bridge doesn't support token-by-token streaming,
            #  so we send the complete response at once)
            # Strip tool_call blocks from the display text
            display_text = _TOOL_CALL_RE.sub('', response_text).strip()
            if display_text:
                await self._ws_send({
                    "type": "foresight_stream",
                    "data": {"token": display_text, "done": False},
                })

            # Append assistant message to conversation history
            self._messages.append({
                "role": "assistant",
                "content": response_text,
            })

            # Parse custom tool calls from response
            tool_calls = _parse_tool_calls(response_text)

            if not tool_calls:
                # No custom tools — conversation turn complete
                await self._ws_send({
                    "type": "foresight_stream",
                    "data": {"token": "", "done": True},
                })
                return

            # Execute each tool and collect results
            tool_results_parts: list[str] = []
            for tool_idx, call in enumerate(tool_calls):
                tool_name = call.get("name", "")
                tool_input = call.get("input", {})
                oracle = self._assign_oracle()

                # Build descriptive log message
                tool_desc = self._describe_tool_call(tool_name, tool_input)

                # Notify frontend: agent activated
                await self._ws_send({
                    "type": "foresight_agent_state",
                    "data": {
                        "agent": oracle,
                        "state": "active",
                        "tool": tool_name,
                    },
                })
                await self._ws_send({
                    "type": "foresight_log",
                    "data": {
                        "agent": oracle,
                        "message": tool_desc,
                    },
                })

                result_text = await self._execute_tool(tool_name, tool_input)

                # Ensemble forecast: run_ensemble_forecast → spawn parallel agents
                if tool_name == "run_ensemble_forecast":
                    result_text = await self._handle_ensemble_forecast(
                        tool_input, oracle, result_text
                    )

                # Update sub-turn tool progress
                await self._ws_send({
                    "type": "foresight_tool_progress",
                    "data": {
                        "turn": turns,
                        "tool_index": tool_idx + 1,
                        "tool_total": len(tool_calls),
                    },
                })

                # Phase transition: compress_environment triggers model switch
                if tool_name == "compress_environment":
                    profile = tool_input.get("profile", "")
                    self._switch_to_prediction(profile)
                    await self._ws_send({
                        "type": "foresight_phase",
                        "data": {"phase": "prediction", "model": self._model},
                    })

                # Timeline visualization: emit_timeline → forward to frontend
                if tool_name == "emit_timeline":
                    try:
                        action = tool_input.get("action", "")
                        payload = json.loads(tool_input.get("data", "{}"))
                        # Platt-calibrate probability values before display
                        if action == "add_node" and payload.get("probability") is not None:
                            raw = payload["probability"]
                            if isinstance(raw, (int, float)) and 0 < raw < 100:
                                calibrated = platt_scale(raw / 100.0) * 100
                                payload["probability"] = round(calibrated, 1)
                                payload["raw_probability"] = raw
                        await self._ws_send({
                            "type": "foresight_timeline",
                            "data": {"action": action, "payload": payload},
                        })
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.warning("emit_timeline parse error: %s", exc)

                # Clarification questions: generate_clarifications -> forward to frontend
                if tool_name == "generate_clarifications":
                    try:
                        questions = json.loads(tool_input.get("questions", "[]"))
                        await self._ws_send({
                            "type": "foresight_clarification",
                            "data": {"questions": questions},
                        })
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.warning("generate_clarifications parse error: %s", exc)

                # Requirements analysis: analyze_requirements → forward to frontend
                if tool_name == "analyze_requirements":
                    try:
                        items = json.loads(tool_input.get("items", "[]"))
                        await self._ws_send({
                            "type": "foresight_requirements",
                            "data": {"items": items},
                        })
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.warning("analyze_requirements parse error: %s", exc)

                # Requirement status: emit_requirement_status → forward to frontend
                if tool_name == "emit_requirement_status":
                    await self._ws_send({
                        "type": "foresight_requirement_status",
                        "data": {
                            "id": tool_input.get("item_id", ""),
                            "status": tool_input.get("status", "done"),
                        },
                    })

                # Delphi panel: emit_delphi → forward to frontend
                if tool_name == "emit_delphi":
                    try:
                        experts = json.loads(tool_input.get("experts", "[]"))
                        await self._ws_send({
                            "type": "foresight_delphi",
                            "data": {
                                "round": tool_input.get("round", 1),
                                "experts": experts,
                            },
                        })
                    except (json.JSONDecodeError, Exception) as exc:
                        logger.warning("emit_delphi parse error: %s", exc)

                # Report generation: export_interactive_report → notify frontend
                if tool_name == "export_interactive_report":
                    if not result_text.startswith("Error"):
                        report_filename = ""
                        if ": " in result_text and " (" in result_text:
                            report_filename = result_text.split(": ", 1)[-1].split(" (")[0]
                        await self._ws_send({
                            "type": "foresight_report_ready",
                            "data": {
                                "filename": report_filename,
                                "title": tool_input.get("title", ""),
                            },
                        })
                        self._last_report_filename = report_filename

                # Build completion summary
                result_summary = result_text[:80].replace("\n", " ") if result_text else ""
                done_msg = (
                    f"완료 — {result_summary}..."
                    if len(result_text) > 80
                    else f"완료 — {result_summary}"
                )

                # Notify frontend: agent completed
                await self._ws_send({
                    "type": "foresight_log",
                    "data": {
                        "agent": oracle,
                        "message": done_msg,
                    },
                })
                await self._ws_send({
                    "type": "foresight_agent_state",
                    "data": {
                        "agent": oracle,
                        "state": "done",
                        "tool": tool_name,
                    },
                })

                tool_results_parts.append(
                    f"[Tool result for {tool_name}]: {result_text[:8_000]}"
                )

            # Feed tool results back as a user message for the next turn
            tool_results_msg = "\n\n".join(tool_results_parts)
            self._messages.append({
                "role": "user",
                "content": f"Tool execution results:\n\n{tool_results_msg}\n\nContinue your analysis.",
            })

            # Prune old messages to reduce context size
            self._prune_old_messages()

        # Max turns reached
        if not self._cancelled:
            await self._ws_send({
                "type": "foresight_stream",
                "data": {
                    "token": "\n\n[Sage] 최대 실행 횟수에 도달했습니다.",
                    "done": True,
                },
            })

    # -- Ensemble forecast handler --------------------------------

    async def _handle_ensemble_forecast(
        self,
        tool_input: dict[str, Any],
        oracle: str,
        default_result: str,
    ) -> str:
        """Handle the run_ensemble_forecast tool call.

        Performs question assessment via CLI bridge, then routes to either
        scenario mode (complex/chaotic) or probability ensemble (complicated/clear).
        """
        from src.foresight.ensemble import multi_agent_forecast
        from src.foresight.calibration import platt_scale

        question = tool_input.get("question", "")
        context = tool_input.get("context", "")
        full_question = f"{question}\n\n배경:\n{context}" if context else question

        # Question assessment via CLI bridge
        cynefin_domain = "complicated"  # default
        try:
            assess_text = await self._bridge.raw_query(
                system_prompt=(
                    "You assess forecasting questions on two dimensions.\n\n"
                    "1. CLARITY: Is the question clear enough for analysis?\n"
                    "   - Time horizon specified?\n"
                    "   - Measurable outcome defined?\n"
                    "   - Key terms unambiguous?\n\n"
                    "2. CYNEFIN DOMAIN: What type of problem is this?\n"
                    "   - CLEAR: Obvious cause-effect. Best practice applies.\n"
                    "   - COMPLICATED: Cause-effect discoverable by analysis. Expert analysis needed.\n"
                    "   - COMPLEX: Cause-effect only visible in retrospect. Emergent, unpredictable.\n"
                    "   - CHAOTIC: No perceivable cause-effect. Novel situation.\n\n"
                    "Respond in EXACTLY this format (one line each):\n"
                    "CLARITY: CLEAR or UNCLEAR: [reason]\n"
                    "DOMAIN: CLEAR or COMPLICATED or COMPLEX or CHAOTIC\n"
                    "REASON: [one sentence explaining domain choice]"
                ),
                user_message=full_question,
                model="sonnet",  # Always Sonnet for classification
                allowed_tools=[],
                max_turns=1,
                timeout=60,
            )

            if not assess_text.strip():
                assess_text = "CLARITY: CLEAR\nDOMAIN: COMPLICATED"

            # Parse line-by-line (robust to format variations)
            lines = assess_text.splitlines()
            clarity_line = next((l for l in lines if l.upper().startswith("CLARITY")), "CLARITY: CLEAR")
            domain_line = next((l for l in lines if l.upper().startswith("DOMAIN")), "DOMAIN: COMPLICATED")

            if "UNCLEAR" in clarity_line.upper():
                clarity_reason = clarity_line.split("UNCLEAR")[-1].strip(": ").strip() or "질문이 명확하지 않습니다"
                result_text = (
                    f"질문 명확화 필요:\n{clarity_reason}\n\n"
                    "질문을 더 구체적으로 수정한 후 다시 run_ensemble_forecast를 호출하세요."
                )
                await self._ws_send({
                    "type": "foresight_ensemble_progress",
                    "data": {"agent_id": "assessor", "status": "unclear",
                             "detail": clarity_reason},
                })
                await self._ws_send({
                    "type": "foresight_agent_state",
                    "data": {"agent": oracle, "state": "done", "tool": "run_ensemble_forecast"},
                })
                return result_text

            # Parse Cynefin domain
            for domain in ("CHAOTIC", "COMPLEX", "COMPLICATED", "CLEAR"):
                if domain in domain_line.upper():
                    cynefin_domain = domain.lower()
                    break

            await self._ws_send({
                "type": "foresight_ensemble_progress",
                "data": {"agent_id": "assessor", "status": "classified",
                         "detail": f"도메인: {cynefin_domain}"},
            })

        except Exception as exc:
            logger.debug("Question assessment failed, using default (complicated): %s", exc)
            await self._ws_send({
                "type": "foresight_ensemble_progress",
                "data": {"agent_id": "assessor", "status": "fallback",
                         "detail": "분류 실패 — 기본 모드(확률 앙상블)로 진행"},
            })

        settings = get_settings()

        # Route by Cynefin domain
        if cynefin_domain in ("complex", "chaotic"):
            # Scenario exploration mode — skip probability ensemble
            result_text = (
                f"[도메인 분류: {cynefin_domain}] 이 질문은 확률 추정보다 시나리오 탐색이 적합합니다.\n\n"
                "다음 Dator 4원형 시나리오를 생성하세요:\n"
                "1. 🚀 성장(Growth): 현재 추세가 가속된다면 어떤 미래?\n"
                "2. 💥 붕괴(Collapse): 어떤 시스템 실패가 이 결과를 뒤집을 수 있는가?\n"
                "3. ⚖️ 규율(Discipline): 규제·제약·자발적 제한이 강화된다면?\n"
                "4. 🔄 변혁(Transformation): 근본적 게임 체인저가 등장한다면?\n\n"
                "각 시나리오에 대해:\n"
                "- 핵심 동인과 인과 메커니즘\n"
                "- 조기 경보 신호 2-3개\n"
                "- 전략적 시사점\n"
                "을 포함하여 emit_timeline으로 시각화하세요.\n\n"
                "확률을 억지로 배정하지 마세요 — 이 도메인에서는 확률보다 시나리오 다양성이 중요합니다."
            )
            await self._ws_send({
                "type": "foresight_ensemble_progress",
                "data": {"agent_id": "router", "status": "scenario_mode",
                         "detail": f"Cynefin: {cynefin_domain} -> 시나리오 탐색 모드"},
            })
            await self._ws_send({
                "type": "foresight_agent_state",
                "data": {"agent": oracle, "state": "done", "tool": "run_ensemble_forecast"},
            })
            return result_text

        # Status callback for WebSocket progress
        async def _ensemble_status(agent_id, status, detail):
            await self._ws_send({
                "type": "foresight_ensemble_progress",
                "data": {"agent_id": agent_id, "status": status, "detail": detail},
            })

        try:
            # Build search-only tool list for ensemble agents (text instructions)
            agent_tools = [
                schema for name, schema in CLIENT_TOOLS.items()
                if name in ("web_search", "web_fetch", "firecrawl_scrape")
            ]

            ensemble_result = await multi_agent_forecast(
                model=self._model,
                question=full_question,
                tools=agent_tools,
                n_sonnet=settings.foresight_sonnet_agents,
                n_haiku=settings.foresight_haiku_agents,
                max_turns=settings.foresight_ensemble_max_turns,
                haiku_max_turns=settings.foresight_haiku_max_turns,
                supervisor_threshold=settings.foresight_supervisor_threshold,
                on_status=_ensemble_status,
            )

            # Apply Platt scaling to final mean
            raw_mean = ensemble_result["mean"]
            calibrated = platt_scale(raw_mean)
            ensemble_result["calibrated_mean"] = round(calibrated, 4)
            ensemble_result["raw_mean"] = round(raw_mean, 4)

            # Send progress complete
            await self._ws_send({
                "type": "foresight_ensemble_progress",
                "data": {"agent_id": "ensemble", "status": "complete",
                         "detail": f"보정 확률: {calibrated:.1%} (원본: {raw_mean:.1%})"},
            })

            # Format result for LLM consumption
            sonnet_mean = ensemble_result.get("sonnet_mean")
            haiku_mean = ensemble_result.get("haiku_mean")
            ci_low = ensemble_result.get("ci_low", raw_mean)
            ci_high = ensemble_result.get("ci_high", raw_mean)
            sp_signal = ensemble_result.get("sp_signal", 0.0)
            result_text = (
                f"앙상블 예측 완료:\n"
                f"- 보정 확률: {calibrated:.1%}\n"
                f"- 신뢰구간: [{platt_scale(ci_low):.1%} ~ {platt_scale(ci_high):.1%}]\n"
                f"- 원본 평균: {raw_mean:.1%}\n"
                f"- 에이전트 수: {len(ensemble_result.get('forecasts', []))}\n"
                f"- 스프레드: {ensemble_result.get('spread', 0):.1%}\n"
            )
            if sp_signal != 0.0:
                direction = "상향" if sp_signal > 0 else "하향"
                result_text += f"- SP 시그널: {direction} {abs(sp_signal):.1%} (에이전트 고유 정보 방향)\n"
            if sonnet_mean is not None:
                result_text += f"- Sonnet 평균: {sonnet_mean:.1%}\n"
            if haiku_mean is not None:
                result_text += f"- Haiku 평균: {haiku_mean:.1%}\n"
            if ensemble_result.get("supervisor"):
                sup = ensemble_result["supervisor"]
                result_text += f"- Supervisor 조정: {sup.confidence} ({sup.probability:.1%})\n"

            # Add individual agent summaries
            for f in ensemble_result.get("forecasts", []):
                model_tag = f"[{f.model.split('-')[1] if '-' in f.model else f.model}]" if f.model else ""
                result_text += f"\n[{f.agent_id}] {model_tag} {f.probability:.1%} — {f.reasoning[:200]}"

            return result_text

        except Exception as exc:
            logger.exception("Ensemble forecast failed")
            return f"앙상블 예측 실패: {exc}"

    # -- Tool execution -------------------------------------------

    async def _execute_tool(self, name: str, inputs: dict[str, Any]) -> str:
        """Execute a tool by name, checking Foresight executors first."""
        try:
            # Foresight tools take priority (per-engine bound executors)
            if name in self._foresight_executors:
                executor = self._foresight_executors[name]
                return await executor(**inputs)

            # Fall back to native tool executors
            if name in NATIVE_EXECUTORS:
                executor = NATIVE_EXECUTORS[name]
                return await executor(**inputs)

            return f"Error: unknown tool '{name}'"

        except Exception as exc:
            logger.exception("Tool execution error (%s)", name)
            return f"Error executing {name}: {type(exc).__name__}: {exc}"

    # -- Conversation builder -------------------------------------

    def _build_conversation_text(self) -> str:
        """Build a single user message from the conversation history.

        Since CLI bridge takes a single user_message string (not a message list),
        we flatten the conversation history into a readable format.
        """
        if not self._messages:
            return ""

        parts = []
        for msg in self._messages:
            role = msg["role"]
            content = msg["content"]

            if role == "user":
                if isinstance(content, str):
                    parts.append(f"[User]: {content}")
                elif isinstance(content, list):
                    # Tool results from old format
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            parts.append(f"[Tool Result]: {item.get('content', '')}")
                        elif isinstance(item, dict) and item.get("type") == "text":
                            parts.append(f"[User]: {item.get('text', '')}")
                        elif isinstance(item, str):
                            parts.append(f"[User]: {item}")
            elif role == "assistant":
                if isinstance(content, str):
                    parts.append(f"[Assistant]: {content}")
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                parts.append(f"[Assistant]: {block.get('text', '')}")
                            elif block.get("type") == "tool_use":
                                parts.append(
                                    f"[Assistant used tool {block.get('name', '')}]: "
                                    f"{json.dumps(block.get('input', {}), ensure_ascii=False)[:500]}"
                                )

        return "\n\n".join(parts)

    # -- Context pruning ------------------------------------------

    def _prune_old_messages(self) -> None:
        """Keep conversation manageable by summarizing old tool results.

        For the CLI bridge version, we prune long tool result strings
        in older user messages to keep context size reasonable.
        """
        # Find user messages containing tool results
        tr_indices: list[int] = []
        for i, msg in enumerate(self._messages):
            if (
                msg["role"] == "user"
                and isinstance(msg["content"], str)
                and "Tool execution results:" in msg["content"]
            ):
                tr_indices.append(i)

        # Nothing to prune if 0 or 1 tool result batches
        if len(tr_indices) <= 1:
            return

        for idx in tr_indices[:-1]:
            content = self._messages[idx]["content"]
            if isinstance(content, str) and len(content) > 500:
                self._messages[idx]["content"] = (
                    content[:300] + "\n...[이전 결과 요약됨]"
                )

    # -- Helpers --------------------------------------------------

    @staticmethod
    def _describe_tool_call(name: str, inputs: dict[str, Any]) -> str:
        """Create a human-readable description of a tool call for the mission log."""
        if name == "run_python":
            code = inputs.get("code", "")
            for line in code.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("import"):
                    desc = stripped[:60] + "..." if len(stripped) > 60 else stripped
                    return f"Python 실행: {desc}"
            return "Python 코드 실행 중..."
        elif name == "read_uploaded_file":
            return f"파일 구조 분석: {inputs.get('filename', '?')}"
        elif name == "export_report":
            return f"보고서 생성: {inputs.get('filename', '?')}"
        elif name == "save_environment":
            return f"환경 저장: {inputs.get('name', '?')}"
        elif name == "load_environment":
            return f"환경 불러오기: {inputs.get('name', '?')}"
        elif name == "list_environments":
            return "저장된 환경 목록 조회..."
        elif name == "quickchart_render":
            return "차트 이미지 생성 중..."
        elif name == "exchange_rate":
            return f"환율 조회: {inputs.get('from_currency', '?')} -> {inputs.get('to_currency', '?')}"
        elif name == "dart_financial":
            return f"DART 재무 데이터 조회: {inputs.get('corp_name', '?')}"
        elif name == "ecos_data":
            return "한국은행 경제지표 조회..."
        elif name == "kosis_data":
            return "통계청 KOSIS 데이터 조회..."
        elif name == "emit_timeline":
            action = inputs.get("action", "?")
            return f"타임라인 시각화 데이터 전송: {action}"
        elif name == "analyze_requirements":
            return "예측에 필요한 정보 항목 분석 중..."
        elif name == "emit_requirement_status":
            return f"정보 수집 상태 업데이트: {inputs.get('item_id', '?')}"
        elif name == "export_interactive_report":
            return f"인터랙티브 보고서 생성: {inputs.get('title', '?')}"
        elif name == "emit_delphi":
            return f"Delphi 패널 라운드 {inputs.get('round', '?')} 데이터 전송"
        elif name == "run_ensemble_forecast":
            return f"앙상블 예측 실행 중 ({inputs.get('question', '?')[:50]}...)"
        else:
            return f"{name} 실행 중..."

    async def _ws_send(self, data: dict[str, Any]) -> None:
        """Send a JSON message through the WebSocket, ignoring errors."""
        try:
            await self._ws.send_json(data)
        except Exception:
            # WebSocket may have closed — log and continue
            logger.debug("WebSocket send failed (likely disconnected)")
