"""Multi-agent ensemble forecasting — AIA Forecaster pattern.

Spawns N independent forecasting agents, aggregates via
arithmetic mean, optional supervisor reconciliation,
then Platt scaling calibration.

CLI bridge version: uses ClaudeCodeBridge.raw_query() instead of Anthropic SDK.
Tool calls are embedded in the system prompt as text instructions and parsed
from the response via <tool_call> JSON blocks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.utils.bridge_factory import get_bridge

logger = logging.getLogger(__name__)

# Tool schema for agent's forecast submission
SUBMIT_FORECAST_TOOL = {
    "name": "submit_forecast",
    "description": "Submit your final probability forecast.",
    "input_schema": {
        "type": "object",
        "properties": {
            "probability": {"type": "number", "description": "확률 (0.01-0.99)"},
            "meta_prediction": {
                "type": "number",
                "description": "다른 AI 에이전트들이 평균적으로 어떤 확률을 제시할 것 같은지 추정 (0.01-0.99)",
            },
            "confidence": {"type": "number", "description": "확신도 (0.0-1.0)"},
            "reasoning": {"type": "string", "description": "추론 요약"},
            "key_searches": {
                "type": "array", "items": {"type": "string"},
                "description": "핵심 검색 쿼리 목록",
            },
        },
        "required": ["probability", "reasoning"],
    },
}


@dataclass
class AgentForecast:
    """Output of a single forecasting agent."""
    agent_id: str
    probability: float          # 0.0-1.0
    reasoning: str
    search_queries: list[str] = field(default_factory=list)
    confidence: float = 0.5     # self-reported confidence
    meta_prediction: float | None = None  # "what would other AIs predict?" (Surprisingly Popular)
    model: str = ""             # which model was used (for logging/analysis)


def parse_probability(text: str) -> float:
    """Extract a probability value from LLM output text.

    Tries patterns: "65%", "0.65", "probability: 0.72", etc.
    Returns 0.5 (maximum uncertainty) if nothing found.
    """
    val = None

    # Pattern: N% (integer or decimal)
    m = re.search(r'(\d{1,2}(?:\.\d+)?)\s*%', text)
    if m:
        val = float(m.group(1)) / 100.0

    # Pattern: 0.NN or probability: 0.NN
    if val is None:
        m = re.search(r'(?:probability|확률|chance|prob)[:\s]*([01]?\.\d+)', text, re.IGNORECASE)
        if m:
            val = float(m.group(1))

    # Pattern: standalone decimal 0.NN
    if val is None:
        m = re.search(r'\b(0\.\d{1,4})\b', text)
        if m:
            val = float(m.group(1))

    if val is None:
        return 0.5  # fallback: maximum uncertainty

    return max(0.01, min(0.99, val))  # clamp to valid probability range


def aggregate_forecasts(
    forecasts: list[AgentForecast],
    threshold: float = 0.2,
    trim_fraction: float = 0.15,
) -> dict[str, Any]:
    """Aggregate N agent forecasts via extremized trimmed mean.

    1. Sort probabilities
    2. Trim top/bottom 15% (removes outliers from weak agents)
    3. Take mean of remaining (trimmed mean)
    4. Also compute simple mean for comparison

    Trimmed mean outperforms simple mean for LLM ensembles
    (Halawi et al., NeurIPS 2024).
    """
    if not forecasts:
        return {"mean": 0.5, "spread": 0.0, "min": 0.5, "max": 0.5,
                "needs_supervisor": False, "forecasts": []}
    probs = [f.probability for f in forecasts]
    simple_mean = sum(probs) / len(probs)
    spread = max(probs) - min(probs)

    # Trimmed mean: remove top/bottom outliers
    sorted_probs = sorted(probs)
    n = len(sorted_probs)
    trim_n = max(1, int(n * trim_fraction)) if n >= 4 else 0
    trimmed = sorted_probs[trim_n:n - trim_n] if trim_n > 0 else sorted_probs
    trimmed_mean = sum(trimmed) / len(trimmed)

    # Confidence interval from agent distribution (non-parametric)
    q1_idx = max(0, n // 4)
    q3_idx = min(n - 1, 3 * n // 4)
    ci_low = sorted_probs[q1_idx]
    ci_high = sorted_probs[q3_idx]

    # Surprisingly Popular signal: agents with private info predict differently
    # from what they think others will predict. The gap = hidden information.
    sp_adjustments = []
    for f in forecasts:
        if f.meta_prediction is not None:
            gap = f.probability - f.meta_prediction  # positive = "I know more than others"
            sp_adjustments.append(gap)

    sp_signal = sum(sp_adjustments) / len(sp_adjustments) if sp_adjustments else 0.0

    # Apply SP signal to trimmed mean (Nature 2017, Prelec et al.)
    # SP > 0: agents have private info pointing higher than consensus
    # Weight 0.3 is conservative — avoids noise amplification with limited data
    SP_WEIGHT = 0.3
    adjusted_mean = max(0.01, min(0.99, trimmed_mean + sp_signal * SP_WEIGHT))

    return {
        "mean": adjusted_mean,
        "mean_before_sp": trimmed_mean,
        "simple_mean": simple_mean,
        "spread": spread,
        "min": min(probs),
        "max": max(probs),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "sp_signal": round(sp_signal, 4),  # Surprisingly Popular signal
        "trimmed_count": len(trimmed),
        "needs_supervisor": spread >= threshold,
        "forecasts": forecasts,
    }


# ── Tool instruction builder ────────────────────────────

def _build_tool_instructions(tools: list[dict[str, Any]]) -> str:
    """Build text instructions for tools to embed in the system prompt.

    The LLM responds with <tool_call>{"name": ..., "input": ...}</tool_call>
    when it wants to use a tool.
    """
    lines = [
        "You have access to the following tools. To use a tool, respond with a "
        "<tool_call> block containing JSON with 'name' and 'input' keys.\n"
        "Example: <tool_call>{\"name\": \"tool_name\", \"input\": {\"key\": \"value\"}}</tool_call>\n"
        "\nAvailable tools:"
    ]
    for tool in tools:
        name = tool.get("name", "")
        desc = tool.get("description", "")
        schema = json.dumps(tool.get("input_schema", {}), ensure_ascii=False)
        lines.append(f"\n## {name}\n{desc}\nInput schema: {schema}")
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


# ── Single forecasting agent ────────────────────────────

async def run_forecasting_agent(
    model: str,
    question: str,
    agent_id: str,
    tools: list[dict[str, Any]],
    max_turns: int = 6,
    temperature: float = 1.0,
    on_status: Any = None,
    system_prompt: str | None = None,
    negative_framing: bool = False,
) -> AgentForecast:
    """Run a single independent forecasting agent with a role-specific prompt.

    If negative_framing=True, the question is inverted ("Will X NOT happen?")
    and the returned probability is flipped (1-p). This cancels acquiescence
    bias where LLMs tend to agree with the proposition as stated.
    (Schoenegger et al., Science Advances 2024)

    Uses CLI bridge raw_query() per turn. Tool calls are parsed from
    <tool_call> blocks in the response text.
    """
    bridge = get_bridge()

    if system_prompt is None:
        from src.foresight.prompts.agent import FORECASTER_AGENT_PROMPT
        system_prompt = FORECASTER_AGENT_PROMPT

    # For negative framing, flip probability at the end
    def _maybe_flip(p: float) -> float:
        return max(0.01, min(0.99, 1.0 - p)) if negative_framing else p

    if negative_framing:
        user_msg = f"다음 결과가 일어나지 않을 확률을 추정하세요 (NOT 관점):\n\n{question}"
    else:
        user_msg = f"다음 질문에 대해 확률을 추정하세요:\n\n{question}"

    # Build tool instructions for the system prompt
    agent_tools = tools + [SUBMIT_FORECAST_TOOL]
    tool_instructions = _build_tool_instructions(agent_tools)
    full_system = f"{system_prompt}\n\n{tool_instructions}"

    # Accumulate conversation context across turns
    conversation_context = user_msg

    async def _safe_status(aid, st, dt):
        """Fire on_status callback without aborting the agent on failure."""
        if on_status:
            try:
                await on_status(aid, st, dt)
            except Exception:
                logger.debug("on_status callback raised for %s, ignoring", aid)

    for turn in range(max_turns):
        await _safe_status(agent_id, "searching", f"턴 {turn + 1}/{max_turns}")

        try:
            response_text = await bridge.raw_query(
                system_prompt=full_system,
                user_message=conversation_context,
                model=model,
                allowed_tools=[],  # No CLI tools — we manage tools via text
                max_turns=1,
                timeout=120,
            )
        except Exception as exc:
            logger.warning("Agent %s bridge error on turn %d: %s", agent_id, turn, exc)
            return AgentForecast(agent_id=agent_id, probability=0.5, reasoning=f"Bridge error: {exc}", model=model)

        # Parse tool calls from response
        tool_calls = _parse_tool_calls(response_text)

        if not tool_calls:
            # No tool calls — try to parse probability from text
            await _safe_status(agent_id, "done", "예측 완료 (텍스트 파싱)")
            return AgentForecast(
                agent_id=agent_id,
                probability=_maybe_flip(parse_probability(response_text)),
                reasoning=response_text[:1000],
                model=model,
            )

        # Process tool calls
        tool_results_text = ""
        for call in tool_calls:
            name = call.get("name", "")
            inp = call.get("input", {})

            if name == "submit_forecast":
                await _safe_status(agent_id, "done", "예측 완료")
                meta = inp.get("meta_prediction")
                raw_p = max(0.01, min(0.99, inp.get("probability", 0.5)))
                clamped_meta = max(0.01, min(0.99, meta)) if meta is not None else None
                return AgentForecast(
                    agent_id=agent_id,
                    probability=_maybe_flip(raw_p),
                    reasoning=inp.get("reasoning", ""),
                    search_queries=inp.get("key_searches", []),
                    confidence=inp.get("confidence", 0.5),
                    meta_prediction=_maybe_flip(clamped_meta) if clamped_meta is not None else None,
                    model=model,
                )
            else:
                # Execute native tool
                from src.tools.native import TOOL_EXECUTORS
                if name in TOOL_EXECUTORS:
                    try:
                        result = await TOOL_EXECUTORS[name](**inp)
                    except Exception as exc:
                        result = f"Error: {exc}"
                else:
                    result = f"Unknown tool: {name}"
                tool_results_text += f"\n\n[Tool result for {name}]: {str(result)[:4000]}"

        # Feed tool results back for next turn
        conversation_context = (
            f"{conversation_context}\n\n"
            f"[Assistant response]:\n{response_text}\n\n"
            f"[Tool results]:{tool_results_text}\n\n"
            f"Continue your analysis. When ready, use submit_forecast to submit your final prediction."
        )

    return AgentForecast(agent_id=agent_id, probability=_maybe_flip(0.5), reasoning="Max turns reached", model=model)


@dataclass
class SupervisorResult:
    """Output of the supervisor reconciliation agent."""
    probability: float
    confidence: str  # "high", "medium", "low"
    reasoning: str


SUBMIT_RECONCILED_TOOL = {
    "name": "submit_reconciled",
    "description": "Submit the reconciled forecast after reviewing all agents.",
    "input_schema": {
        "type": "object",
        "properties": {
            "probability": {"type": "number", "description": "조정된 확률 (0.01-0.99)"},
            "confidence": {
                "type": "string", "enum": ["high", "medium", "low"],
                "description": "high=평균 대체, medium=평균과 가중, low=평균 유지",
            },
            "reasoning": {"type": "string", "description": "조정 근거"},
        },
        "required": ["probability", "confidence", "reasoning"],
    },
}


async def supervisor_reconcile(
    model: str,
    question: str,
    forecasts: list[AgentForecast],
    tools: list[dict[str, Any]],
    on_status: Any = None,
) -> SupervisorResult:
    """Supervisor agent that reconciles disagreements among forecasters."""
    bridge = get_bridge()
    from src.foresight.prompts.supervisor import SUPERVISOR_PROMPT

    agent_summaries = "\n\n".join([
        f"## {f.agent_id}\n"
        f"확률: {f.probability:.1%}\n"
        f"확신도: {f.confidence:.1%}\n"
        f"추론: {f.reasoning[:500]}\n"
        f"검색: {', '.join(f.search_queries[:3])}"
        for f in forecasts
    ])

    probs = [f.probability for f in forecasts]
    spread = max(probs) - min(probs)

    user_msg = (
        f"질문: {question}\n\n"
        f"{len(forecasts)}개 에이전트의 독립 예측 결과:\n\n"
        f"{agent_summaries}\n\n"
        f"스프레드: {spread:.1%} — 불일치를 해소하고 최종 확률을 결정하세요."
    )

    # Build tool instructions
    sup_tools = tools + [SUBMIT_RECONCILED_TOOL]
    tool_instructions = _build_tool_instructions(sup_tools)
    full_system = f"{SUPERVISOR_PROMPT}\n\n{tool_instructions}"

    if on_status:
        await on_status("supervisor", "reconciling", "에이전트 간 불일치 조정 중...")

    conversation_context = user_msg

    for turn in range(4):  # max 4 turns for supervisor
        try:
            response_text = await bridge.raw_query(
                system_prompt=full_system,
                user_message=conversation_context,
                model=model,
                allowed_tools=[],
                max_turns=1,
                timeout=120,
            )
        except Exception as exc:
            logger.warning("Supervisor bridge error: %s", exc)
            fallback = sum(probs) / len(probs) if probs else 0.5
            return SupervisorResult(probability=fallback, confidence="low", reasoning=f"Bridge error: {exc}")

        tool_calls = _parse_tool_calls(response_text)

        if not tool_calls:
            break  # No tool calls — end turn

        tool_results_text = ""
        for call in tool_calls:
            name = call.get("name", "")
            inp = call.get("input", {})

            if name == "submit_reconciled":
                if on_status:
                    await on_status("supervisor", "done", "조정 완료")
                return SupervisorResult(
                    probability=max(0.01, min(0.99, inp.get("probability", 0.5))),
                    confidence=inp.get("confidence", "low"),
                    reasoning=inp.get("reasoning", ""),
                )
            else:
                from src.tools.native import TOOL_EXECUTORS
                if name in TOOL_EXECUTORS:
                    try:
                        result = await TOOL_EXECUTORS[name](**inp)
                    except Exception as exc:
                        result = f"Error: {exc}"
                else:
                    result = f"Unknown tool: {name}"
                tool_results_text += f"\n\n[Tool result for {name}]: {str(result)[:4000]}"

        conversation_context = (
            f"{conversation_context}\n\n"
            f"[Assistant response]:\n{response_text}\n\n"
            f"[Tool results]:{tool_results_text}\n\n"
            f"Continue and submit your reconciled forecast using submit_reconciled."
        )

    # Fallback: no tool submission
    fallback = sum(probs) / len(probs) if probs else 0.5
    return SupervisorResult(
        probability=fallback,
        confidence="low",
        reasoning="Supervisor did not submit reconciled forecast",
    )


async def multi_agent_forecast(
    model: str,
    question: str,
    tools: list[dict[str, Any]],
    n_sonnet: int = 4,
    n_haiku: int = 4,
    max_turns: int = 6,
    haiku_max_turns: int = 4,
    haiku_model: str = "haiku",
    supervisor_threshold: float = 0.2,
    on_status: Any = None,
) -> dict[str, Any]:
    """Spawn role-differentiated Sonnet + Haiku agents in parallel.

    Each agent gets a unique analytical role to maximize information diversity
    and reduce inter-agent correlation (lower rho -> better ensemble floor).

    Sonnet roles: Base Rate Analyst, Devil's Advocate, Causal Reasoner, Contrarian
    Haiku roles:  News Scout x2, Pattern Matcher x2

    Framing variation: Haiku agents at odd indices use negative framing
    to cancel acquiescence bias (Science Advances, Schoenegger et al. 2024).
    """
    from src.foresight.prompts.agent import SONNET_ROLES, HAIKU_ROLES

    tasks = []

    # Sonnet agents — deep reasoning with differentiated roles
    for i in range(n_sonnet):
        role_prompt = SONNET_ROLES[i % len(SONNET_ROLES)]
        tasks.append(run_forecasting_agent(
            model=model, question=question,
            agent_id=f"sonnet_{i}", tools=tools,
            max_turns=max_turns,
            temperature=0.7 + 0.1 * i,
            on_status=on_status,
            system_prompt=role_prompt,
        ))

    # Haiku agents — broad exploration with framing variation
    # Odd-indexed Haiku agents use negative framing (1-p inversion)
    for i in range(n_haiku):
        role_prompt = HAIKU_ROLES[i % len(HAIKU_ROLES)]
        use_negative = (i % 2 == 1)  # haiku_1, haiku_3 get negative framing
        tasks.append(run_forecasting_agent(
            model=haiku_model, question=question,
            agent_id=f"haiku_{i}", tools=tools,
            max_turns=haiku_max_turns,
            temperature=min(1.0, 0.6 + 0.1 * i),  # capped at 1.0 (API limit)
            on_status=on_status,
            system_prompt=role_prompt,
            negative_framing=use_negative,
        ))

    forecasts = await asyncio.gather(*tasks, return_exceptions=True)
    valid = [f for f in forecasts if isinstance(f, AgentForecast)]

    if not valid:
        return {"mean": 0.5, "spread": 0.0, "needs_supervisor": False,
                "forecasts": [], "error": "All agents failed"}

    result = aggregate_forecasts(valid, threshold=supervisor_threshold)

    # Add model breakdown to result
    sonnet_forecasts = [f for f in valid if f.model and "sonnet" in f.model]
    haiku_forecasts = [f for f in valid if f.model and "haiku" in f.model]
    result["sonnet_mean"] = (sum(f.probability for f in sonnet_forecasts) / len(sonnet_forecasts)) if sonnet_forecasts else None
    result["haiku_mean"] = (sum(f.probability for f in haiku_forecasts) / len(haiku_forecasts)) if haiku_forecasts else None

    if result["needs_supervisor"]:
        try:
            sup = await supervisor_reconcile(model, question, valid, tools, on_status)
            sp_adj = result.get("sp_signal", 0.0) * 0.3  # preserve SP adjustment
            if sup.confidence == "high":
                # Blend supervisor probability with SP signal
                result["mean"] = max(0.01, min(0.99, sup.probability + sp_adj))
            elif sup.confidence == "medium":
                result["mean"] = max(0.01, min(0.99, (result["mean"] + sup.probability) / 2))
            result["supervisor"] = sup
        except Exception as exc:
            logger.warning("Supervisor failed: %s", exc)
            result["supervisor_error"] = str(exc)

    return result
