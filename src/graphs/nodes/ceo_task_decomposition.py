"""Node: ceo_task_decomposition — CEO directly decomposes task into subtasks.

Replaces the Leader layer entirely. CEO creates subtasks with tool_category
and detailed objectives, which are converted into WorkerReport dicts for
worker_execution.
"""

from langchain_core.messages import AIMessage

from src.agents.factory import create_ceo
from src.config.settings import get_settings
from src.models.messages import CEOTaskDecomposition
from src.models.state import create_worker_report
from src.prompts.ceo_decomposition_prompts import CEO_TASK_DECOMPOSITION_SYSTEM
from src.tools import get_tools_for_category
from src.utils.guards import node_error_handler
from src.utils.logging import get_logger

_logger = get_logger(agent_id="ceo_task_decomposition")


def _build_workers_from_decomposition(decomposition: CEOTaskDecomposition) -> list[dict]:
    """Convert CEO subtasks into WorkerReport dicts."""
    workers = []
    for subtask in decomposition.subtasks:
        worker = create_worker_report(
            worker_id="",  # Assigned in worker_execution
            worker_domain=subtask.tool_category,  # tool_category as domain
        )
        worker["plan"] = subtask.model_dump_json()
        worker["status"] = "plan_submitted"
        worker["tool_category"] = subtask.tool_category
        worker["task_title"] = subtask.task_title
        worker["worker_name"] = subtask.worker_name or subtask.task_title
        worker["role_type"] = subtask.role_type
        if subtask.dependencies:
            worker["dependencies"] = subtask.dependencies
        workers.append(worker)
    return workers


@node_error_handler("ceo_task_decomposition")
def ceo_task_decomposition_node(state: dict) -> dict:
    """CEO directly decomposes the task into subtasks with tool categories."""
    import json as _json

    # ── Team mode: use saved team structure directly ──
    pre_context = state.get("pre_context") or {}
    team_agents = pre_context.get("team_agents")
    if team_agents:
        workers = []
        team_edges = pre_context.get("team_edges", [])
        for agent in team_agents:
            worker = create_worker_report(
                worker_id="",
                worker_domain=agent.get("tool_category", "research"),
            )
            worker["status"] = "plan_submitted"
            worker["tool_category"] = agent.get("tool_category", "research")
            worker["task_title"] = agent.get("name", "worker")
            worker["worker_name"] = agent.get("name", "worker")
            worker["role_type"] = agent.get("role_type", "executor")
            worker["plan"] = _json.dumps({
                "task_title": agent.get("name"),
                "objective": agent.get("role", state["user_task"]),
                "tool_category": agent.get("tool_category", "research"),
                "success_criteria": ["작업 완료"],
            }, ensure_ascii=False)
            deps = [e["from"] for e in team_edges if e.get("to") == agent.get("id")]
            if deps:
                worker["dependencies"] = deps
            workers.append(worker)

        team_name = pre_context.get("team_name", "")
        _logger.info("team_mode_direct_execution", team=team_name, worker_count=len(workers))
        role_summary = ", ".join(f"{a.get('name')}({a.get('role_type', 'executor')})" for a in team_agents)
        messages = [AIMessage(content=f"[CEO] '{team_name}' 팀 즉시 실행 — {len(workers)}명\n팀원: {role_summary}")]
        return {
            "workers": workers,
            "messages": messages,
            "phase": "worker_execution",
        }

    ceo = create_ceo()
    settings = get_settings()

    # Build Q&A pairs — numbered questions matched to their answers
    questions = state.get("clarifying_questions", [])
    user_answers = state.get("user_answers", [])
    if questions and user_answers:
        qa_lines = []
        for i, q in enumerate(questions):
            a = user_answers[i] if i < len(user_answers) else "(미답변)"
            qa_lines.append(f"- {q}\n  → {a}")
        answers_block = "\n".join(qa_lines)
    elif user_answers:
        answers_block = "\n".join(f"- {a}" for a in user_answers)
    else:
        answers_block = "(답변 없음)"

    system = CEO_TASK_DECOMPOSITION_SYSTEM.format(
        persona_block=ceo._persona_block,
        user_task=state["user_task"],
        routing_rationale=state.get("ceo_routing_rationale", ""),
        estimated_complexity=state.get("estimated_complexity", "medium"),
        user_answers_block=answers_block,
    )

    # Retry up to 2 times if decomposition returns empty subtasks
    decomposition = None
    for attempt in range(3):
        decomposition = ceo._query(
            system_prompt=system,
            user_content=f"작업: {state['user_task']}\n\n위 정보를 바탕으로 작업을 분해하고 각 하위 작업의 objective를 구체적으로 작성하세요.",
            output_schema=CEOTaskDecomposition,
            allowed_tools=[],
            timeout=settings.planning_timeout,
            max_turns=settings.ceo_max_turns,
            model=settings.ceo_decomposition_model,
            effort=settings.ceo_decomposition_effort,
        )
        if decomposition and decomposition.subtasks:
            break
        _logger.warning("empty_decomposition_retry", attempt=attempt + 1)

    # Guarantee at least one worker even if decomposition failed
    if not decomposition or not decomposition.subtasks:
        _logger.warning("decomposition_empty_fallback")
        from src.models.messages import Subtask
        fallback_domain = (state.get("selected_domains") or ["research"])[0]
        decomposition = CEOTaskDecomposition(
            subtasks=[Subtask(
                task_title=state["user_task"][:100],
                worker_type=fallback_domain,
                tool_category=fallback_domain,
                objective=state["user_task"],
                success_criteria=["작업 완료"],
            )],
            decomposition_rationale="작업 분해 실패 — 단일 워커 폴백",
        )

    workers = _build_workers_from_decomposition(decomposition)

    _logger.info(
        "ceo_task_decomposition_complete",
        subtask_count=len(decomposition.subtasks),
        categories=[s.tool_category for s in decomposition.subtasks],
        roles=[s.role_type for s in decomposition.subtasks],
        names=[s.worker_name for s in decomposition.subtasks],
    )

    # Build summary messages
    role_summary = ", ".join(
        f"{s.worker_name or s.task_title}({s.role_type})"
        for s in decomposition.subtasks
    )
    messages = [AIMessage(
        content=(
            f"[CEO] 팀 구성 완료 — {len(decomposition.subtasks)}명\n"
            f"팀원: {role_summary}\n"
            f"근거: {decomposition.decomposition_rationale}"
        )
    )]
    for s in decomposition.subtasks:
        messages.append(AIMessage(
            content=f"  [{s.role_type}:{s.tool_category}] {s.worker_name or s.task_title}: {s.objective[:100]}..."
        ))

    return {
        "workers": workers,
        "messages": messages,
        "phase": "worker_execution",
    }
