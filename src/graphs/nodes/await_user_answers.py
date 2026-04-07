"""Node 4: await_user_answers - interrupt for human input."""

from src.engine import request_interrupt

from src.utils.guards import node_error_handler
from src.utils.logging import get_logger

_logger = get_logger(agent_id="await_user_answers")


@node_error_handler("await_user_answers")
def await_user_answers_node(state: dict) -> dict:
    """Pause execution and wait for user answers to clarifying questions.

    In interactive/instant mode: auto-supplies default answers (no interrupt).
    In scheduled mode: auto-supplies answers from pre_context.
    """
    # Team mode: questions are still asked for precise task execution
    questions = state.get("clarifying_questions", [])
    default_answer = "배경 정보를 기반으로 최선의 판단으로 진행하세요."

    if state.get("execution_mode") == "scheduled":
        pre_context = state.get("pre_context", {})
        domain_answers = pre_context.get("domain_answers", {})
        default_answer = pre_context.get("default_answer", default_answer)

        if domain_answers:
            flat_answers = []
            for domain_list in domain_answers.values():
                if isinstance(domain_list, list):
                    flat_answers.extend(domain_list)
            user_answers = flat_answers if flat_answers else [default_answer] * len(questions)
        else:
            user_answers = [default_answer] * len(questions)

        _logger.info(
            "scheduled_auto_answers",
            question_count=len(questions),
            answer_count=len(user_answers),
        )
    else:
        # ── Interactive mode: interrupt for user clarification ──
        raw_response = request_interrupt(state, {
            "type": "clarifying_questions",
            "questions": questions,
            "instruction": (
                "각 질문 번호(Q1, Q2, …)에 맞춰 답변해 주세요. "
                "예: A1. 글로벌 / A2. 최근 3년"
            ),
        })

        # Normalize response: browser may send string, dict, or list
        if isinstance(raw_response, dict):
            user_answers = [raw_response.get("response", str(raw_response))]
        elif isinstance(raw_response, str):
            user_answers = [raw_response]
        elif isinstance(raw_response, list):
            user_answers = raw_response
        else:
            user_answers = [str(raw_response)]

        _logger.info(
            "interactive_user_answers",
            question_count=len(questions),
            answer_count=len(user_answers),
        )

    return {
        "user_answers": user_answers,
        "phase": "worker_planning",
    }
