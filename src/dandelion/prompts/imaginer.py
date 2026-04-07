"""Imagination prompt for Haiku agents."""

IMAGINER_SYSTEM = """\
You are a creative futurist. Imagine ONE vivid, specific future scenario for the theme: "{theme_name}".

Be bold and specific — not vague trends, but a concrete scenario that could actually happen.
Ground your imagination in the research data provided.
Write in the same language as the user's context.
"""


def build_imaginer_system(theme_name: str, agent_index: int = 0) -> str:
    return IMAGINER_SYSTEM.format(theme_name=theme_name)


def build_imaginer_user(theme_description: str, context_packet: str, agent_index: int) -> str:
    truncated = context_packet[:3000] if len(context_packet) > 3000 else context_packet
    return (
        f"Theme: {theme_description}\n\n"
        f"Research Data:\n{truncated}\n\n"
        f"You are agent #{agent_index + 1} of 10. Imagine something DIFFERENT and unexpected."
    )
