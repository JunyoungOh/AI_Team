"""Phase-specific system prompts for Engineering mode."""

from src.engineering.prompts.brainstorm import BRAINSTORM_PROMPT
from src.engineering.prompts.plan import PLAN_PROMPT
from src.engineering.prompts.implement import IMPLEMENT_PROMPT
from src.engineering.prompts.verify import VERIFY_PROMPT

__all__ = [
    "BRAINSTORM_PROMPT",
    "PLAN_PROMPT",
    "IMPLEMENT_PROMPT",
    "VERIFY_PROMPT",
]
