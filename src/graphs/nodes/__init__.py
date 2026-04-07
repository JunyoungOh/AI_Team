"""Graph nodes - each node is an independent, testable function.

Every node follows the signature:
    def node_name(state: EnterpriseAgentState) -> dict
    Returns a partial state update dict.

Flow: intake → ceo_route → ceo_questions → await_user_answers
      → ceo_task_decomposition → worker_execution
      → ceo_final_report → report_review → user_review_results → END/revision
"""

from src.graphs.nodes.intake import intake_node
from src.graphs.nodes.ceo_route import ceo_route_node
from src.graphs.nodes.ceo_questions import ceo_questions_node
from src.graphs.nodes.await_user_answers import await_user_answers_node
from src.graphs.nodes.worker_execution import worker_execution_node
from src.graphs.nodes.ceo_final_report import ceo_final_report_node
from src.graphs.nodes.report_review import report_review_node
from src.graphs.nodes.ceo_report_revise import ceo_report_revise_node
from src.graphs.nodes.user_review_results import user_review_results_node
from src.graphs.nodes.worker_result_revision import worker_result_revision_node
from src.graphs.nodes.deep_research import deep_research_node

__all__ = [
    "intake_node",
    "ceo_route_node",
    "ceo_questions_node",
    "await_user_answers_node",
    "worker_execution_node",
    "ceo_final_report_node",
    "report_review_node",
    "ceo_report_revise_node",
    "user_review_results_node",
    "worker_result_revision_node",
    "deep_research_node",
]
