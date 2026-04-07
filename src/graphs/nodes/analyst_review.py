"""Node: analyst_review — Blackboard 데이터 충분성 판단.

blackboard_sync 후 실행.
Analyst가 Blackboard 전체를 읽고 사용자 질문 대비 데이터 커버리지를 평가.
verdict = "sufficient" → CEO 보고서로 진행
verdict = "insufficient" + analyst_loop_count < max → 추가 워커 생성 후 재실행
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from src.config.settings import get_settings
from src.models.messages import AnalystVerdict
from src.models.state import EnterpriseAgentState
from src.utils.collection_blackboard import CollectionBlackboard
from src.utils.logging import get_logger
from src.utils.parallel import run_async

logger = get_logger(agent_id="analyst_review")


def _get_bridge():
    from src.utils.bridge_factory import get_bridge
    return get_bridge()


ANALYST_SYSTEM_PROMPT = """\
당신은 데이터 충분성 판단 전문가(Analyst)입니다.
Blackboard에 수집된 데이터를 검토하고, 사용자의 원래 질문에 대해 충분한 답변을 작성할 수 있을 만큼 데이터가 모였는지 판단합니다.

## 판단 기준
1. **커버리지**: 사용자 질문의 모든 측면(who, what, when, where, why, how)이 데이터로 커버되는가?
2. **깊이**: 핵심 토픽에 대해 표면적 정보가 아닌 구체적 수치/사실이 있는가?
3. **다양성**: 단일 출처가 아닌 다양한 관점의 데이터가 있는가?
4. **중요도 분포**: importance 4-5 (핵심) 데이터가 충분한가?

## 판단 결과
- **sufficient**: 핵심 질문에 답할 수 있는 데이터가 충분. confidence_score 8 이상.
- **insufficient**: 핵심 데이터 누락. gaps에 부족한 영역을 구체적으로 명시하고, additional_research_directives에 "어떤 정보를, 어디서 찾을지" 지시.

## 중요
- 완벽주의를 지양하세요. 80% 커버리지면 sufficient입니다.
- 이미 수집된 데이터를 반복 수집하라고 지시하지 마세요.
- gaps는 구체적으로: "OpenAI의 2025년 매출 수치 누락" (O), "더 많은 데이터 필요" (X)
"""


def analyst_review(state: EnterpriseAgentState) -> dict:
    """Blackboard 데이터 충분성 판단."""
    settings = get_settings()
    loop_count = state.get("analyst_loop_count", 0)

    bb = CollectionBlackboard.deserialize(state.get("collection_blackboard"))
    bb_text = bb.read_for_analyst()
    user_task = state.get("user_task", "")

    logger.info(f"[Analyst] 데이터 충분성 검토 중 (loop {loop_count + 1})")

    # Blackboard이 비어있으면 LLM 호출 없이 바로 sufficient 반환
    # (labeled_findings 미지원 워커 출력 → Blackboard 비어있어도 worker 결과는 존재)
    if bb.total_entries == 0:
        logger.info("[Analyst] Blackboard 비어있음 — sufficient로 기본 진행 (워커 결과는 직접 활용)")
        return {
            "analyst_verdict": "sufficient",
            "analyst_gaps": [],
            "analyst_loop_count": loop_count + 1,
            "messages": [AIMessage(content="[Analyst] Blackboard 비어있음 — 워커 결과 직접 활용으로 진행")],
        }

    user_content = (
        f"## 사용자 원래 요청\n{user_task}\n\n"
        f"## Blackboard 수집 데이터\n{bb_text}\n\n"
        f"위 데이터가 사용자의 요청에 충분히 답할 수 있는지 판단하세요."
    )

    bridge = _get_bridge()
    try:
        verdict: AnalystVerdict = run_async(
            bridge.structured_query(
                system_prompt=ANALYST_SYSTEM_PROMPT,
                user_message=user_content,
                output_schema=AnalystVerdict,
                model=settings.analyst_model,
                allowed_tools=[],
                timeout=settings.analyst_timeout,
                max_turns=settings.analyst_max_turns,
                effort=settings.analyst_effort,
            )
        )
    except Exception as e:
        logger.error(f"[Analyst] 판단 실패: {e} — sufficient로 기본 진행")
        return {
            "analyst_verdict": "sufficient",
            "analyst_gaps": [],
            "analyst_loop_count": loop_count + 1,
            "messages": [AIMessage(content="[Analyst] 판단 실패 — 기본 진행")],
        }

    logger.info(
        f"[Analyst] verdict={verdict.verdict}, confidence={verdict.confidence_score}, "
        f"gaps={len(verdict.gaps)}, loop={loop_count + 1}"
    )

    logger.info(
        f"[Analyst] 판단: {verdict.verdict} (확신도 {verdict.confidence_score}/10, "
        f"갭 {len(verdict.gaps)}개)"
    )

    return {
        "analyst_verdict": verdict.verdict,
        "analyst_gaps": verdict.gaps,
        "analyst_loop_count": loop_count + 1,
        "messages": [AIMessage(
            content=(
                f"[Analyst Review] {verdict.verdict} "
                f"(confidence: {verdict.confidence_score}/10)\n"
                f"근거: {verdict.rationale}\n"
                f"커버리지: {verdict.coverage_assessment}"
                + (f"\n부족 영역: {', '.join(verdict.gaps)}" if verdict.gaps else "")
            )
        )],
    }
