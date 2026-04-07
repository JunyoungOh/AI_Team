"""HTML extraction and markdown-to-HTML wrapping utilities.

Used by deep_research_node to extract HTML reports from Claude CLI text output.
"""

from __future__ import annotations

import re

# Patterns to strip from Claude output (plugin contamination, e.g. bkit footer)
_CONTAMINATION_PATTERNS = [
    re.compile(
        r"─+\s*\n\s*📊\s*bkit\s+Feature\s+Usage.*?─+",
        re.DOTALL,
    ),
    re.compile(r"📊\s*bkit\s+Feature\s+Usage.*$", re.MULTILINE),
]


def sanitize_output(text: str) -> str:
    """Remove plugin contamination (bkit footer, etc.) from Claude output."""
    for pattern in _CONTAMINATION_PATTERNS:
        text = pattern.sub("", text)
    return text.strip()


def extract_html(raw_text: str) -> str | None:
    """3-level fallback HTML extraction from Claude text output.

    Level 1: Full text starts with <!DOCTYPE html> or <html → return as-is
    Level 2: Extract from ```html ... ``` code fence
    Level 3: Search for <html...>...</html> block via regex

    Returns None if no HTML found.
    """
    stripped = raw_text.strip()

    # Level 1: entire output is an HTML document
    if stripped.lower().startswith("<!doctype html") or stripped.lower().startswith("<html"):
        return stripped

    # Level 2: code fence ```html ... ```
    fence_match = re.search(
        r"```html\s*\n(.*?)```",
        raw_text,
        re.DOTALL,
    )
    if fence_match:
        html = fence_match.group(1).strip()
        if html:
            return html

    # Level 3: inline <html>...</html> block
    block_match = re.search(
        r"(<html[\s>].*?</html>)",
        raw_text,
        re.DOTALL | re.IGNORECASE,
    )
    if block_match:
        return block_match.group(1).strip()

    return None


def wrap_markdown_as_html(text: str, title: str = "Research Report") -> str:
    """Wrap plain text/markdown in a basic HTML template.

    Uses the ``markdown`` library if available, otherwise wraps in <pre>.
    Includes print-friendly CSS for Cmd+P PDF export.
    """
    try:
        import markdown as md_lib

        body_html = md_lib.markdown(
            text,
            extensions=["tables", "fenced_code", "toc"],
        )
    except ImportError:
        # Fallback: preserve whitespace with <pre>
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        body_html = f"<pre>{escaped}</pre>"

    return f"""\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ font-family: 'Pretendard', -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.7; color: #1a1a1a; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.5rem; }}
  h2 {{ color: #2c3e50; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }}
  th {{ background: #f5f5f5; font-weight: 600; }}
  pre {{ background: #f8f9fa; padding: 1rem; border-radius: 4px; overflow-x: auto; }}
  @media print {{
    body {{ max-width: 100%; padding: 1rem; }}
    h1 {{ page-break-after: avoid; }}
    table {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
<h1>{title}</h1>
{body_html}
</body>
</html>"""
