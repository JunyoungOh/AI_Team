"""Supervisor prompts for collection and consolidation stages (Sonnet)."""

RESEARCH_SYSTEM = """\
You are a research analyst preparing data for a foresight imagination exercise.

The user has asked a question about the future, and it has been decomposed into these themes:
{themes_block}

Your job:
1. Use the available tools (web_search, web_fetch) to collect relevant, recent data that covers ALL themes above.
2. Synthesize findings into a comprehensive "research packet" for 40 imagination agents.

The research packet should:
- Include key facts, trends, statistics, and recent developments
- Cover data relevant to each of the 4 themes
- Be 800-1500 words
- Be written in the same language as the user's query

User's question and context:
{common_context}

After researching, respond with your synthesized findings as plain text.
Do NOT wrap in JSON or code fences — just write the research packet directly.
"""


def build_research_system(themes: list[dict], common_context: str) -> str:
    themes_block = "\n".join(
        f"- {t['name']}: {t['description']}" for t in themes
    )
    return RESEARCH_SYSTEM.format(
        themes_block=themes_block,
        common_context=common_context,
    )


CONSOLIDATION_SYSTEM = """\
You are a foresight consolidation analyst for the theme: "{theme_name}".

You will receive 10 imagination results from different AI agents. Your job:
1. Identify duplicate or highly similar imaginations and merge them.
2. For merged items, combine their details and reasoning into a unified narrative.
3. Assign a "weight" to each final seed: 1 for unique imaginations, N for merged (N = count of originals merged).
4. Convert free-form time_point strings to time_months (integer, months from now). Clamp to 1-60.
5. Return at least 1 seed, even if all 10 are duplicates (merge into 1 with weight=10).

Respond with a JSON object:
{{"seeds": [{{"title": "concise title", "summary": "2-3 line summary for tooltip display", "detail": "detailed markdown description (combine if merged)", "reasoning": "why this future was imagined (combine if merged)", "time_months": 6, "weight": 3, "source_count": 3}}]}}

Guidelines:
- Two imaginations are "duplicate" if they describe the same future outcome, even with different wording.
- Keep titles concise (under 20 characters if possible).
- Write in the same language as the original imaginations.
- source_count always equals weight.
"""


def build_consolidation_system(theme_name: str) -> str:
    return CONSOLIDATION_SYSTEM.format(theme_name=theme_name)


def build_consolidation_user(imaginations: list[dict]) -> str:
    parts = []
    for i, img in enumerate(imaginations):
        parts.append(
            f"--- Imagination {i+1} ---\n"
            f"Title: {img['title']}\n"
            f"Summary: {img['summary']}\n"
            f"Detail: {img['detail']}\n"
            f"Reasoning: {img['reasoning']}\n"
            f"Time: {img['time_point']} ({img['time_months']} months)\n"
        )
    return "\n".join(parts)
