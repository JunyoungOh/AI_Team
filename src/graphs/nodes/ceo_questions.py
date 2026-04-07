"""Node 3: ceo_questions - CEO generates all domain questions in a single call.

Replaces the previous leader_questions + ceo_optimize_questions two-node flow.
CEO directly produces per-domain questions based on routing rationale and user task.
"""

from langchain_core.messages import AIMessage

from src.agents.factory import create_ceo
from src.utils.guards import node_error_handler


@node_error_handler("ceo_questions")
def ceo_questions_node(state: dict) -> dict:
    """CEO generates clarifying questions for all selected domains at once."""
    # Team mode: questions are still asked (user needs to clarify intent)
    ceo = create_ceo()
    selected_domains = state.get("selected_domains", [])

    if not selected_domains:
        # Fallback: extract from workers if available
        selected_domains = list({w.get("worker_domain", "") for w in state.get("workers", [])})

    result = ceo.generate_all_questions(state, selected_domains)

    # Flatten all questions into a numbered list for state["clarifying_questions"]
    # Global numbering ensures answer-to-question matching across domains.
    all_questions: list[str] = []
    messages = []
    global_idx = 1
    for entry in result.questions_by_domain:
        domain_qs = entry.questions
        formatted_lines = []
        for q in domain_qs:
            all_questions.append(f"Q{global_idx}. {q}")
            formatted_lines.append(f"  Q{global_idx}. {q}")
            global_idx += 1

        messages.append(AIMessage(
            content=f"[CEO -> {entry.domain}] Questions:\n" + "\n".join(formatted_lines)
        ))

    messages.insert(0, AIMessage(
        content=f"[CEO] Generated {result.total_questions} questions for {len(selected_domains)} domains.\n{result.generation_rationale}"
    ))

    return {
        "clarifying_questions": all_questions,
        "messages": messages,
        "phase": "awaiting_user_answers",
    }
