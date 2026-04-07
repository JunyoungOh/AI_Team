"""HTML report exporter for CEO final reports.

Generates two separate self-contained HTML reports using pure Python string building:
  - results.html  — user-facing analysis findings (no agent metadata)
  - quality.html  — internal quality assessment with worker details
Includes @media print CSS for clean browser-to-PDF export (Cmd+P).
Never raises — all errors are logged and return None.
"""

from __future__ import annotations

import html
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.config.settings import get_settings
from src.utils.dedup import deduplicate_deliverables, deduplicate_findings, deduplicate_summaries

logger = logging.getLogger(__name__)

CHARTJS_CDN = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>'

import re

# Patterns to strip from LLM-generated report_html
_HTML_ARTIFACT_PATTERNS = [
    # Code fence: ```html ... ``` → extract inner content
    (re.compile(r"^\s*```html\s*\n?", re.IGNORECASE), ""),
    (re.compile(r"\n?\s*```\s*$"), ""),
    # Leading/trailing bare "html" text (not inside a tag)
    (re.compile(r"^\s*html\s*\n", re.IGNORECASE), ""),
    (re.compile(r"\n\s*html\s*$", re.IGNORECASE), ""),
    # Full document tags that shouldn't be in inline HTML
    (re.compile(r"</?(?:html|head|body|meta|!DOCTYPE)[^>]*>", re.IGNORECASE), ""),
    # <style> blocks (should be inline)
    (re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE), ""),
]


_HTML_TAG_RE = re.compile(r"<(?:div|p|table|tr|td|th|ul|ol|li|h[1-6]|span|a|img|section|article|nav|header|footer|blockquote|pre|code|br|hr)\b", re.IGNORECASE)


def _sanitize_report_html(raw: str) -> str:
    """Remove code fences, bare 'html' text, and document-level tags from report_html.

    If the sanitized text contains no recognizable HTML tags, treat it as
    markdown and convert to inline-styled HTML so the report renders correctly.
    """
    text = raw.strip()
    for pattern, repl in _HTML_ARTIFACT_PATTERNS:
        text = pattern.sub(repl, text)
    text = text.strip()

    # Detect markdown masquerading as HTML — convert if no HTML tags found
    if text and not _HTML_TAG_RE.search(text):
        text = _md_to_html(text)

    return text

# Lazy-loaded modules
_markdown: object | None | bool = None  # None=not tried, False=tried+failed


def _get_markdown():
    """Lazy import markdown. Returns module or None if unavailable."""
    global _markdown
    if _markdown is None:
        try:
            import markdown as md
            _markdown = md
        except ImportError:
            _markdown = False
            logger.info("markdown_not_available, worker results rendered as plain text")
    return _markdown if _markdown is not False else None


# ── Helper filters ───────────────────────────────────────


def _esc(value) -> str:
    """HTML-escape a value."""
    return html.escape(str(value)) if value else ""


def _md_to_html(text: str) -> str:
    """Convert markdown text to HTML."""
    if not text:
        return ""
    text = _fix_newlines(str(text))
    md = _get_markdown()
    if md:
        return md.markdown(text, extensions=["tables", "fenced_code", "nl2br"])
    return f"<pre style='white-space:pre-wrap'>{_esc(text)}</pre>"


def score_color(value: int | str) -> str:
    """Return CSS class name for a quality score."""
    try:
        score = int(value)
    except (ValueError, TypeError):
        return "score-mid"
    if score >= 7:
        return "score-high"
    if score >= 5:
        return "score-mid"
    return "score-low"


def severity_color(value: str) -> str:
    """Return CSS class name for a severity level."""
    level = str(value).lower()
    if level == "critical":
        return "severity-critical"
    if level == "high":
        return "severity-high"
    if level == "medium":
        return "severity-medium"
    return "severity-low"


def completion_fill_class(value) -> str:
    """Return CSS class name for a completion percentage."""
    try:
        pct = int(value)
    except (ValueError, TypeError):
        return "completion-low"
    if pct >= 90:
        return "completion-high"
    if pct >= 70:
        return "completion-mid"
    return "completion-low"


# ── Parsing helpers ──────────────────────────────────────


def _parse_gap_analysis(raw) -> dict:
    """Parse gap_analysis from JSON string or dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def _parse_execution_result(raw) -> dict:
    """Parse worker execution_result from JSON string or dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


# ── Newline normalization ────────────────────────────────


def _fix_newlines(text: str) -> str:
    """Restore escaped newlines/tabs from JSON serialization chain."""
    if not text:
        return text
    return text.replace("\\n", "\n").replace("\\t", "\t")


# ── Template data builders ───────────────────────────────


def _build_results_data(
    final_report: dict,
    workers: list[dict],
    user_task: str,
) -> dict:
    """Build template context for the user-facing results report.

    Prioritizes rich content: CEO summary is the overview, while worker
    result_summary provides the detailed analysis body. labeled_findings
    become structured data cards for visual richness.
    """
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    task_title = user_task[:100] + "..." if len(user_task) > 100 else user_task

    domains = []
    for dr in final_report.get("domain_results", []):
        domain_name = dr.get("domain", "unknown")
        findings_parts = []
        all_deliverables = list(dr.get("key_deliverables", []))
        all_labeled_findings = []

        for w in workers:
            result_data = _parse_execution_result(w.get("execution_result", ""))
            summary = result_data.get("result_summary", result_data.get("summary", ""))
            if summary:
                findings_parts.append(str(summary))
            worker_deliverables = result_data.get(
                "deliverables", result_data.get("key_deliverables", [])
            )
            if isinstance(worker_deliverables, list):
                for d in worker_deliverables:
                    if d and str(d) not in all_deliverables:
                        all_deliverables.append(str(d))
            # Collect labeled findings for structured data rendering
            findings = result_data.get("labeled_findings", [])
            if isinstance(findings, list):
                for f in findings:
                    if isinstance(f, dict):
                        all_labeled_findings.append(f)

        # 중복 제거: findings, summaries, deliverables
        all_labeled_findings = deduplicate_findings(all_labeled_findings)
        all_deliverables = deduplicate_deliverables(all_deliverables)
        findings_parts = deduplicate_summaries(findings_parts)

        domains.append({
            "name": domain_name,
            "summary": dr.get("summary", ""),
            "quality_score": dr.get("quality_score", 0),
            "findings": "\n\n---\n\n".join(findings_parts),
            "deliverables": all_deliverables,
            "labeled_findings": all_labeled_findings,
            "gaps": dr.get("gaps", []),
        })

    return {
        "task_title": task_title,
        "user_task": user_task,
        "generated_at": generated_at,
        "executive_summary": final_report.get("executive_summary", ""),
        "domains": domains,
        "recommendations": final_report.get("recommendations", []),
        "report_html": final_report.get("report_html", ""),
    }


def _build_quality_data(
    final_report: dict,
    workers: list[dict],
    user_task: str,
    session_id: str,
) -> dict:
    """Build template context for the internal quality assessment PDF."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    domain_overview = []
    for dr in final_report.get("domain_results", []):
        domain_overview.append({
            "domain": dr.get("domain", "unknown"),
            "quality_score": dr.get("quality_score", 0),
            "gaps": dr.get("gaps", []),
            "key_deliverables": dr.get("key_deliverables", []),
        })

    # Build worker details from flat workers list
    worker_details = []
    for w in workers:
        result_data = _parse_execution_result(w.get("execution_result", ""))
        result_summary = result_data.get("result_summary", result_data.get("summary", ""))
        deliverables = result_data.get("deliverables", result_data.get("key_deliverables", []))
        if not isinstance(deliverables, list):
            deliverables = []
        completion = result_data.get("completion_percentage", "")
        issues = result_data.get("issues", result_data.get("gaps", []))
        if not isinstance(issues, list):
            issues = []
        worker_details.append({
            "domain": w.get("worker_domain", "unknown"),
            "status": w.get("status", "unknown"),
            "result_summary": result_summary,
            "deliverables": deliverables,
            "completion_percentage": completion,
            "issues": issues,
        })
    # Wrap in a single pseudo-leader for template compatibility
    leaders = [{
        "domain": "combined",
        "gap_analysis": {},
        "workers": worker_details,
    }] if worker_details else []

    return {
        "user_task": user_task,
        "session_id": session_id,
        "generated_at": generated_at,
        "executive_summary": final_report.get("executive_summary", ""),
        "domain_overview": domain_overview,
        "overall_gap_analysis": final_report.get("overall_gap_analysis", ""),
        "leaders": leaders,
        "recommendations": final_report.get("recommendations", []),
    }


# ── HTML rendering ───────────────────────────────────────

# Professional CSS for results.html — modeled after INNORED/로보택시 reference reports.
_RESULTS_CSS = """\
:root {
  --primary: #0f3460;
  --primary-dark: #1a1a2e;
  --accent: #FEE500;
  --light: #f4f6f9;
  --border: #e0e5ee;
  --text: #1a1a2e;
  --muted: #5a6a85;
  --green: #2e7d32;
  --orange: #f57f17;
  --red: #c62828;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Apple SD Gothic Neo','Noto Sans KR','Segoe UI',-apple-system,sans-serif;
  font-size: 15px;
  line-height: 1.75;
  color: var(--text);
  background: #eef1f7;
}

/* ─── Cover / Header ─── */
.report-wrapper { max-width: 1100px; margin: 0 auto; background: #fff; box-shadow: 0 4px 40px rgba(0,0,0,0.10); }
.report-header {
  background: linear-gradient(135deg, var(--primary) 0%, #16213e 60%, var(--primary-dark) 100%);
  color: #fff;
  padding: 56px 60px 44px;
  position: relative;
  overflow: hidden;
}
.report-header::before {
  content: '';
  position: absolute;
  top: -60px; right: -60px;
  width: 320px; height: 320px;
  border-radius: 50%;
  background: rgba(254,229,0,0.10);
}
.report-header::after {
  content: '';
  position: absolute;
  bottom: -80px; left: 40px;
  width: 200px; height: 200px;
  border-radius: 50%;
  background: rgba(254,229,0,0.06);
}
.report-header .badge {
  display: inline-block;
  background: var(--accent);
  color: var(--primary-dark);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 2px;
  text-transform: uppercase;
  padding: 4px 14px;
  border-radius: 20px;
  margin-bottom: 18px;
  position: relative;
}
.report-header h1 {
  font-size: 32px;
  font-weight: 800;
  line-height: 1.25;
  margin-bottom: 10px;
  letter-spacing: -0.5px;
  position: relative;
}
.report-header .subtitle { font-size: 15px; opacity: 0.65; margin-bottom: 32px; position: relative; }
.header-meta {
  display: flex;
  gap: 30px;
  flex-wrap: wrap;
  font-size: 12px;
  opacity: 0.6;
  border-top: 1px solid rgba(255,255,255,0.15);
  padding-top: 18px;
  margin-top: 18px;
  position: relative;
}

/* ─── TOC ─── */
.toc {
  background: #f8f9fc;
  border-bottom: 2px solid var(--border);
  padding: 20px 60px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 24px;
}
.toc-item {
  font-size: 12px;
  color: var(--primary);
  font-weight: 600;
  text-decoration: none;
  padding: 4px 0;
  border-bottom: 2px solid transparent;
}
.toc-item:hover { border-color: var(--accent); }

/* ─── Content ─── */
.report-body { padding: 0 60px 48px; }
.report-body-rich { padding: 40px 60px; }

/* ─── Executive Summary ─── */
.exec-summary {
  background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
  color: #fff;
  border-radius: 12px;
  padding: 36px 40px;
  margin: 40px 0 32px;
}
.exec-summary .section-label { color: var(--accent); }
.exec-summary h2 { color: #fff; border-color: rgba(254,229,0,0.3); font-size: 20px; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid rgba(254,229,0,0.3); }
.exec-summary p { color: rgba(255,255,255,0.88); margin-bottom: 10px; }
.exec-key-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-top: 20px;
}
.exec-key-card {
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 10px;
  padding: 18px;
}
.exec-key-card .ek-label { font-size: 11px; color: rgba(255,255,255,0.5); text-transform: uppercase; letter-spacing: 1px; }
.exec-key-card .ek-value { font-size: 22px; font-weight: 800; color: #fff; margin: 4px 0; }
.exec-key-card .ek-desc { font-size: 12px; color: rgba(255,255,255,0.6); }

/* ─── Section ─── */
.section {
  margin-top: 40px;
  padding-top: 20px;
  border-top: 3px solid var(--primary);
}
.section-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 2.5px;
  text-transform: uppercase;
  color: #B8860B;
  margin-bottom: 6px;
}
.section h2 {
  font-size: 22px;
  font-weight: 800;
  color: var(--primary);
  margin-bottom: 18px;
}
.section h3 {
  font-size: 16px;
  font-weight: 700;
  color: #16213e;
  margin: 24px 0 10px;
  padding-left: 12px;
  border-left: 3px solid var(--primary);
}
.section h4 { font-size: 14px; font-weight: 700; color: var(--primary); margin: 16px 0 6px; }

/* ─── Summary Box ─── */
.summary-box {
  background: #f8f9fc;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 22px 26px;
  margin: 12px 0 20px;
}
.summary-box h1,.summary-box h2,.summary-box h3,.summary-box h4,.summary-box h5 { font-size: 14px; color: var(--primary); margin: 0.6em 0 0.3em; border: none; padding: 0; }
.summary-box table { font-size: 13px; margin: 0.5em 0; }
.summary-box p { margin: 0.4em 0; }
.summary-box ul,.summary-box ol { margin: 0.4em 0 0.4em 1.2em; }
.summary-box strong { color: var(--primary); }

/* ─── Quality Badge ─── */
.quality-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 20px;
  margin-left: 12px;
}
.quality-badge.high { background: #e8f5e9; color: var(--green); }
.quality-badge.mid { background: #fff8e1; color: var(--orange); }
.quality-badge.low { background: #ffebee; color: var(--red); }

/* ─── Data Table ─── */
p { margin-bottom: 10px; color: #444; }
table { width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 13px; }
th {
  background: var(--primary);
  color: #fff;
  text-align: left;
  padding: 10px 16px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
td { padding: 10px 16px; font-size: 13px; border-bottom: 1px solid #eef0f5; color: #555; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #f8f9ff; }

/* ─── Deliverables ─── */
.deliverables-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 10px;
  margin: 12px 0;
}
.deliverable-card {
  background: var(--light);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  font-size: 13px;
  color: var(--text);
}
.deliverable-card::before { content: '\\2713  '; color: var(--green); font-weight: 700; }

/* ─── Recommendations ─── */
.recs-section { margin-top: 48px; border-top: 3px solid var(--primary); padding-top: 24px; }
.recs-section h2 { font-size: 20px; font-weight: 800; color: var(--primary); margin-bottom: 20px; }
.rec-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.rec-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
  position: relative;
  padding-left: 56px;
}
.rec-card .rec-num {
  position: absolute;
  left: 16px;
  top: 18px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.rec-card p { color: var(--text); font-size: 14px; margin: 0; }

/* ─── Score Classes ─── */
.score-high { color: var(--green); font-weight: 600; }
.score-mid { color: var(--orange); font-weight: 600; }
.score-low { color: var(--red); font-weight: 600; }

/* ─── Labeled Findings (data cards) ─── */
.findings-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin: 16px 0 24px;
}
.finding-card {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px 18px;
  transition: border-color 0.15s;
}
.finding-card:hover { border-color: var(--primary); }
.finding-card.imp-5 { border-left: 4px solid var(--accent); }
.finding-card.imp-4 { border-left: 4px solid var(--primary); }
.finding-card.imp-3 { border-left: 4px solid #6c757d; }
.finding-stars {
  flex-shrink: 0;
  color: #f5a623;
  font-size: 12px;
  min-width: 60px;
  padding-top: 2px;
}
.finding-body { flex: 1; }
.finding-category {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  padding: 2px 8px;
  border-radius: 4px;
  background: #e8eaf6;
  color: var(--primary);
  margin-bottom: 4px;
}
.finding-content { font-size: 14px; color: var(--text); line-height: 1.6; }
.finding-source { font-size: 11px; color: var(--muted); margin-top: 4px; }

/* ─── Gap Alert Box ─── */
.gap-alert {
  background: #fff8e1;
  border-left: 4px solid #ff8f00;
  border-radius: 0 8px 8px 0;
  padding: 14px 20px;
  margin: 16px 0;
  font-size: 13px;
  color: #5d4037;
}
.gap-alert strong { color: #e65100; }

/* ─── Findings List ─── */
ul { margin: 8px 0 8px 20px; }
li { margin-bottom: 4px; font-size: 14px; color: #444; }

/* ─── References ─── */
.references-section {
  margin-top: 48px;
  padding: 28px 0;
  border-top: 2px solid var(--border);
}
.references-section h2 { font-size: 18px; font-weight: 800; color: var(--primary); margin-bottom: 14px; }
.references-list { list-style: none; margin: 0; padding: 0; }
.references-list li {
  font-size: 13px;
  color: var(--muted);
  padding: 6px 0;
  border-bottom: 1px solid #f0f0f5;
}
.references-list li::before { content: '\\1F4CE  '; }

/* ─── Footer ─── */
.report-footer {
  background: var(--primary-dark);
  color: rgba(255,255,255,0.6);
  padding: 20px 60px;
  font-size: 11px;
  display: flex;
  justify-content: space-between;
}
.report-footer strong { color: var(--primary); }

/* ─── Print ─── */
@media print {
  body { background: white; font-size: 12px; }
  .report-wrapper { box-shadow: none; max-width: 100%; }
  .report-header,.exec-summary,.report-footer { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  @page { size: A4; margin: 1.5cm; }
}
@media (max-width: 768px) {
  .report-header,.report-footer { padding-left: 24px; padding-right: 24px; }
  .report-body,.report-body-rich { padding: 24px; }
  .toc { padding: 16px 24px; }
  .exec-key-grid { grid-template-columns: 1fr; }
  .rec-grid { grid-template-columns: 1fr; }
}"""


def _quality_badge_class(score) -> str:
    """Return quality badge CSS class."""
    try:
        s = float(score)
    except (ValueError, TypeError):
        return "mid"
    if s >= 7:
        return "high"
    if s >= 5:
        return "mid"
    return "low"


def _render_results_html(data: dict) -> str:
    """Render the user-facing results report as a self-contained HTML string.

    If CEO provided report_html (inline-styled), insert it in the rich body.
    Otherwise, render professional-quality HTML from structured data
    (Cover → TOC → Executive Summary → Domain Sections → Recommendations → Footer).
    """
    task_title = _esc(data.get("task_title", ""))
    generated_at = _esc(data.get("generated_at", ""))
    report_html = _sanitize_report_html(data.get("report_html", ""))
    domains = data.get("domains", [])

    head_scripts = CHARTJS_CDN if data.get("interactive") else ""

    # ── HTML head + Cover header ──
    parts = [
        f"<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n"
        f"<meta charset=\"UTF-8\">\n"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"<title>{task_title}</title>\n"
        f"<style>\n{_RESULTS_CSS}\n</style>\n"
        f"{head_scripts}\n"
        f"</head>\n<body>\n"
        f"<div class=\"report-wrapper\">\n"
        f"<header class=\"report-header\">\n"
        f"  <div class=\"badge\">ANALYSIS REPORT</div>\n"
        f"  <h1>{task_title}</h1>\n"
        f"  <div class=\"subtitle\">Enterprise Agent System — Automated Analysis</div>\n"
        f"  <div class=\"header-meta\">\n"
        f"    <span>Generated {generated_at}</span>\n"
        f"    <span>&nbsp;&nbsp;|&nbsp;&nbsp;Domains: {len(domains)}</span>\n"
        f"  </div>\n"
        f"</header>\n"
    ]

    if report_html:
        # ── Rich HTML path: CEO-generated inline-styled HTML ──
        # Still add TOC + footer around it
        if domains:
            toc_items = "".join(
                f"<a class=\"toc-item\" href=\"#domain-{i}\">{_esc(d.get('name', '').title())}</a>"
                for i, d in enumerate(domains)
            )
            parts.append(f"<nav class=\"toc\">{toc_items}</nav>\n")
        parts.append(f"<div class=\"report-body-rich\">\n{report_html}\n</div>\n")
    else:
        # ── Professional fallback: server-side rendered from structured data ──

        # TOC
        if domains:
            toc_items = "".join(
                f"<a class=\"toc-item\" href=\"#domain-{i}\">{_esc(d.get('name', '').title())}</a>"
                for i, d in enumerate(domains)
            )
            parts.append(f"<nav class=\"toc\">"
                         f"<a class=\"toc-item\" href=\"#exec-summary\">Executive Summary</a>"
                         f"{toc_items}"
                         f"<a class=\"toc-item\" href=\"#recommendations\">Recommendations</a>"
                         f"</nav>\n")

        parts.append("<div class=\"report-body\">\n")

        # Executive Summary (dark card)
        exec_summary = data.get("executive_summary", "")
        if exec_summary:
            parts.append(
                f"<div class=\"exec-summary\" id=\"exec-summary\">\n"
                f"  <div class=\"section-label\">EXECUTIVE SUMMARY</div>\n"
                f"  <h2>핵심 요약</h2>\n"
                f"  {_md_to_html(exec_summary)}\n"
            )

            parts.append("</div>\n")  # .exec-summary

        # Domain sections — rich analysis layout
        for i, domain in enumerate(domains):
            name = _esc(domain.get("name", "").title())
            qs = domain.get("quality_score", 0)
            badge_cls = _quality_badge_class(qs)

            parts.append(
                f"<div class=\"section\" id=\"domain-{i}\">\n"
                f"  <div class=\"section-label\">SECTION {i + 1}</div>\n"
                f"  <h2>{name}</h2>\n"
            )

            # CEO's domain summary as overview
            if domain.get("summary"):
                parts.append(f"  <div class=\"summary-box\">{_md_to_html(domain['summary'])}</div>\n")

            # Detailed worker findings — the analysis body (rich markdown → HTML)
            if domain.get("findings"):
                parts.append(
                    f"  <h3>상세 분석</h3>\n"
                    f"  <div class=\"summary-box\">{_md_to_html(domain['findings'])}</div>\n"
                )

            # Labeled findings — structured data cards
            labeled = domain.get("labeled_findings", [])
            if labeled:
                parts.append("  <h3>핵심 데이터</h3>\n  <div class=\"findings-grid\">\n")
                for f in labeled:
                    imp = f.get("importance", 1)
                    cat = _esc(f.get("category", "fact"))
                    content = _esc(str(f.get("content", ""))[:500])
                    source = _esc(f.get("source", ""))
                    stars = "★" * min(imp, 5) + "☆" * max(0, 5 - imp)
                    imp_cls = f"imp-{min(imp, 5)}"
                    source_html = f"<div class=\"finding-source\">{source}</div>" if source else ""
                    parts.append(
                        f"    <div class=\"finding-card {imp_cls}\">\n"
                        f"      <div class=\"finding-stars\">{stars}</div>\n"
                        f"      <div class=\"finding-body\">\n"
                        f"        <span class=\"finding-category\">{cat}</span>\n"
                        f"        <div class=\"finding-content\">{content}</div>\n"
                        f"        {source_html}\n"
                        f"      </div>\n"
                        f"    </div>\n"
                    )
                parts.append("  </div>\n")

            # Deliverables as card grid
            if domain.get("deliverables"):
                parts.append("  <h3>주요 산출물</h3>\n  <div class=\"deliverables-grid\">\n")
                for d in domain["deliverables"]:
                    parts.append(f"    <div class=\"deliverable-card\">{_esc(d)}</div>\n")
                parts.append("  </div>\n")

            # Gaps as alert box
            gaps = domain.get("gaps", [])
            if gaps:
                gap_items = ", ".join(_esc(g) for g in gaps)
                parts.append(
                    f"  <div class=\"gap-alert\">\n"
                    f"    <strong>보완 필요:</strong> {gap_items}\n"
                    f"  </div>\n"
                )

            parts.append("</div>\n")  # .section

        # Recommendations
        recs = data.get("recommendations", [])
        if recs:
            parts.append(
                f"<div class=\"recs-section\" id=\"recommendations\">\n"
                f"  <div class=\"section-label\">NEXT STEPS</div>\n"
                f"  <h2>권고사항</h2>\n"
                f"  <div class=\"rec-grid\">\n"
            )
            for j, r in enumerate(recs, 1):
                parts.append(
                    f"    <div class=\"rec-card\">\n"
                    f"      <div class=\"rec-num\">{j}</div>\n"
                    f"      <p>{_esc(r)}</p>\n"
                    f"    </div>\n"
                )
            parts.append("  </div>\n</div>\n")

        # References / Sources section
        all_sources = []
        for domain in domains:
            for f in domain.get("labeled_findings", []):
                src = f.get("source", "")
                if src and src not in all_sources:
                    all_sources.append(src)
        if all_sources:
            parts.append(
                f"<div class=\"references-section\">\n"
                f"  <div class=\"section-label\">REFERENCES</div>\n"
                f"  <h2>참고 자료</h2>\n"
                f"  <ul class=\"references-list\">\n"
            )
            for j, src in enumerate(all_sources, 1):
                parts.append(f"    <li>{_esc(src)}</li>\n")
            parts.append("  </ul>\n</div>\n")

        parts.append("</div>\n")  # .report-body

    # Footer
    parts.append(
        f"<footer class=\"report-footer\">\n"
        f"  <span>Generated by <strong>Enterprise Agent System</strong></span>\n"
        f"  <span>{generated_at}</span>\n"
        f"</footer>\n"
        f"</div>\n</body>\n</html>"
    )

    return "".join(parts)


# ── Quality report (internal — kept as-is) ───────────────

_QUALITY_CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Noto Sans KR","Helvetica Neue",Arial,sans-serif; font-size: 11pt; line-height: 1.7; color: #333; background: #f8f9fa; padding: 2em; }
.page-container { max-width: 900px; margin: 0 auto; background: #fff; box-shadow: 0 1px 8px rgba(0,0,0,0.08); border-radius: 8px; padding: 3em; }
.cover { display: flex; flex-direction: column; justify-content: center; align-items: center; min-height: 60vh; text-align: center; border-bottom: 2px solid #e8eaf6; margin-bottom: 2em; padding-bottom: 2em; }
.cover h1 { font-size: 28pt; color: #1a237e; margin-bottom: 0.3em; }
.cover .subtitle { font-size: 12pt; color: #666; margin-bottom: 2em; max-width: 80%; }
.cover .meta { font-size: 9pt; color: #999; }
.cover .meta span { margin: 0 0.8em; }
h2 { font-size: 16pt; color: #1a237e; border-bottom: 2px solid #c5cae9; padding-bottom: 0.3em; margin: 1.5em 0 0.8em; }
h3 { font-size: 13pt; color: #303f9f; margin: 1.2em 0 0.5em; }
h4 { font-size: 11pt; color: #455a64; margin: 0.8em 0 0.4em; }
table { width: 100%; border-collapse: collapse; margin: 0.8em 0; font-size: 10pt; }
th { background: #e8eaf6; color: #1a237e; text-align: left; padding: 0.6em 0.8em; border-bottom: 2px solid #c5cae9; }
td { padding: 0.5em 0.8em; border-bottom: 1px solid #e0e0e0; vertical-align: top; }
tr:nth-child(even) td { background: #fafafa; }
.score-high { color: #2e7d32; font-weight: bold; } .score-mid { color: #f57f17; font-weight: bold; } .score-low { color: #c62828; font-weight: bold; }
.severity-critical { background: #ffcdd2; padding: 0.2em 0.5em; border-radius: 3px; }
.severity-high { background: #ffe0b2; padding: 0.2em 0.5em; border-radius: 3px; }
.severity-medium { background: #fff9c4; padding: 0.2em 0.5em; border-radius: 3px; }
.severity-low { background: #c8e6c9; padding: 0.2em 0.5em; border-radius: 3px; }
.completion-high { color: #2e7d32; font-weight: bold; } .completion-mid { color: #f57f17; font-weight: bold; } .completion-low { color: #c62828; font-weight: bold; }
.summary-box { background: #f5f7ff; border-left: 4px solid #3f51b5; padding: 1em 1.5em; margin: 0.8em 0; border-radius: 0 6px 6px 0; }
.summary-box h1,.summary-box h2,.summary-box h3,.summary-box h4,.summary-box h5 { font-size: 11pt; color: #303f9f; margin: 0.6em 0 0.3em; border: none; padding: 0; }
.summary-box table { font-size: 9.5pt; margin: 0.5em 0; }
.summary-box p { margin: 0.3em 0; }
.summary-box ul,.summary-box ol { margin: 0.3em 0 0.3em 1.2em; }
.summary-box code { background: #e8eaf6; padding: 0.1em 0.3em; border-radius: 3px; font-size: 9.5pt; }
.summary-box pre { background: #f5f5f5; padding: 0.8em; border-radius: 4px; overflow-x: auto; font-size: 9pt; white-space: pre-wrap; }
.summary-box strong { color: #1a237e; }
.gap-box { background: #fff8e1; border-left: 4px solid #ff8f00; padding: 1em 1.5em; margin: 0.8em 0; border-radius: 0 6px 6px 0; }
.issues-note { color: #c62828; font-size: 9.5pt; margin-top: 0.3em; }
ul { margin: 0.5em 0 0.5em 1.5em; } li { margin-bottom: 0.3em; }
.worker-tag { background: #e8eaf6; color: #303f9f; padding: 0.2em 0.6em; border-radius: 12px; font-size: 9pt; }
.page-break { margin-top: 2em; border-top: 2px solid #e8eaf6; padding-top: 1em; }
@media print {
  body { background: none; padding: 0; }
  .page-container { max-width: none; box-shadow: none; border-radius: 0; padding: 0; }
  .cover { min-height: 90vh; page-break-after: always; }
  h2,h3,h4 { break-after: avoid; }
  table,.summary-box,.gap-box { break-inside: avoid; }
  @page { size: A4; margin: 2cm; }
}"""


def _render_quality_html(data: dict) -> str:
    """Render the internal quality assessment report as a self-contained HTML string."""
    user_task = _esc(data.get("user_task", ""))
    session_id = _esc(data.get("session_id", ""))
    generated_at = _esc(data.get("generated_at", ""))

    parts = [
        f"<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n"
        f"<meta charset=\"UTF-8\">\n"
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"<title>Quality Assessment Report</title>\n"
        f"<style>\n{_QUALITY_CSS}\n</style>\n</head>\n<body>\n"
        f"<div class=\"page-container\">\n"
        f"<div class=\"cover\">\n"
        f"  <h1>Quality Assessment Report</h1>\n"
        f"  <div class=\"subtitle\">{user_task}</div>\n"
        f"  <div class=\"meta\">\n"
        f"    <span>Session: {session_id}</span>\n"
        f"    <span>|</span>\n"
        f"    <span>Generated: {generated_at}</span>\n"
        f"  </div>\n"
        f"</div>\n"
    ]

    # Quality Overview table
    parts.append("<h2>Quality Overview</h2>\n<table>\n<thead><tr><th>Domain</th><th>Quality</th><th>Gaps</th><th>Key Deliverables</th></tr></thead>\n<tbody>\n")
    for dr in data.get("domain_overview", []):
        domain_name = _esc(str(dr.get("domain", "unknown")).title())
        qs = dr.get("quality_score", 0)
        sc = score_color(qs)
        gaps = dr.get("gaps", [])
        gaps_html = "<ul>" + "".join(f"<li>{_esc(g)}</li>" for g in gaps) + "</ul>" if gaps else "-"
        delivs = dr.get("key_deliverables", [])
        delivs_html = "<ul>" + "".join(f"<li>{_esc(d)}</li>" for d in delivs) + "</ul>" if delivs else "-"
        parts.append(
            f"<tr><td><strong>{domain_name}</strong></td>"
            f"<td><span class=\"{sc}\">{_esc(qs)}/10</span></td>"
            f"<td>{gaps_html}</td>"
            f"<td>{delivs_html}</td></tr>\n"
        )
    parts.append("</tbody>\n</table>\n")

    # Overall Gap Analysis
    gap_text = data.get("overall_gap_analysis", "No overall gap analysis available.")
    parts.append(f"<h2>Overall Gap Analysis</h2>\n<div class=\"gap-box\">\n  {_md_to_html(gap_text)}\n</div>\n")

    # Detailed Worker Results
    leaders = data.get("leaders", [])
    if leaders:
        parts.append("<div class=\"page-break\">\n  <h2>Detailed Worker Results</h2>\n")
        for leader in leaders:
            leader_domain = _esc(str(leader.get("domain", "unknown")).title())
            parts.append(f"  <h3>{leader_domain}</h3>\n")

            gap = leader.get("gap_analysis", {})
            if gap:
                parts.append(
                    "  <table>\n  <thead><tr>"
                    "<th>Quality Criteria</th><th>Score</th><th>Severity</th>"
                    "<th>Gaps</th><th>Approved</th>"
                    "</tr></thead>\n  <tbody><tr>\n"
                )
                quality_scores = gap.get("quality_scores", {})
                if quality_scores:
                    score_parts = []
                    for criterion, s in quality_scores.items():
                        score_parts.append(f"<span class=\"{score_color(s)}\">{_esc(criterion)}: {_esc(s)}</span>")
                    parts.append(f"    <td>{', '.join(score_parts)}</td>\n")
                    vals = [v for v in quality_scores.values() if isinstance(v, (int, float))]
                    avg = round(sum(vals) / len(vals), 1) if vals else "-"
                    parts.append(f"    <td>{avg}</td>\n")
                else:
                    parts.append("    <td>-</td>\n    <td>-</td>\n")
                sev = gap.get("severity", "")
                if sev:
                    parts.append(f"    <td><span class=\"{severity_color(sev)}\">{_esc(sev).upper()}</span></td>\n")
                else:
                    parts.append("    <td>-</td>\n")
                gap_list = gap.get("gaps", [])
                parts.append(f"    <td>{len(gap_list)}</td>\n")
                approved = gap.get("approved")
                parts.append(f"    <td>{_esc(approved) if approved is not None else '-'}</td>\n")
                parts.append("  </tr></tbody>\n  </table>\n")

            for w in leader.get("workers", []):
                w_domain = _esc(w.get("domain", "unknown"))
                w_status = _esc(w.get("status", "unknown"))
                parts.append(f"  <h4>{w_domain} <span class=\"worker-tag\">{w_status}</span></h4>\n")
                if w.get("result_summary"):
                    parts.append(f"  <div class=\"summary-box\">{_md_to_html(w['result_summary'])}</div>\n")
                if w.get("deliverables"):
                    items = "".join(f"<li>{_esc(d)}</li>" for d in w["deliverables"])
                    parts.append(f"  <ul>{items}</ul>\n")
                if w.get("completion_percentage"):
                    cc = completion_fill_class(w["completion_percentage"])
                    parts.append(f"  <p>Completion: <span class=\"{cc}\">{_esc(w['completion_percentage'])}%</span></p>\n")
                if w.get("issues"):
                    issues_str = ", ".join(_esc(i) for i in w["issues"])
                    parts.append(f"  <p class=\"issues-note\">Issues: {issues_str}</p>\n")

        parts.append("</div>\n")

    recs = data.get("recommendations", [])
    if recs:
        items = "".join(f"    <li>{_esc(r)}</li>\n" for r in recs)
        parts.append(f"<div class=\"page-break\">\n  <h2>Recommendations</h2>\n  <ul>\n{items}  </ul>\n</div>\n")

    parts.append("</div>\n</body>\n</html>")
    return "".join(parts)


def _render_html(template_data: dict, template_name: str = "report_results.html") -> str:
    """Render HTML from template data using pure Python string building."""
    if template_name == "report_quality.html":
        return _render_quality_html(template_data)
    return _render_results_html(template_data)


# ── HTML writer ───────────────────────────────────────────


def _write_html(html_content: str, output_path: Path) -> Path:
    """Write self-contained HTML file. Returns actual path."""
    html_path = output_path.with_suffix(".html")
    html_path.write_text(html_content, encoding="utf-8")
    return html_path


# ── Main export function ─────────────────────────────────


def _cleanup_old_reports(base_dir: Path, max_keep: int) -> None:
    """Remove oldest report folders when count exceeds max_keep."""
    try:
        for f in base_dir.iterdir():
            if f.is_file() and f.suffix == ".pdf":
                f.unlink(missing_ok=True)
                logger.info("orphan_pdf_cleaned", path=str(f))

        folders = sorted(
            [f for f in base_dir.iterdir() if f.is_dir()],
            key=lambda f: f.stat().st_mtime,
        )
        to_delete = folders[:-max_keep] if len(folders) > max_keep else []
        for folder in to_delete:
            shutil.rmtree(folder, ignore_errors=True)
            logger.info("old_report_cleaned", path=str(folder))
    except Exception:
        pass


def _safe_report(final_report: dict | None) -> dict:
    """Normalize final_report dict — ensure all required keys exist with safe defaults."""
    if not isinstance(final_report, dict):
        final_report = {}
    safe = {
        "executive_summary": str(final_report.get("executive_summary", "") or ""),
        "domain_results": [],
        "overall_gap_analysis": str(final_report.get("overall_gap_analysis", "") or ""),
        "recommendations": list(final_report.get("recommendations", []) or []),
        "report_html": str(final_report.get("report_html", "") or ""),
    }
    for dr in final_report.get("domain_results", []) or []:
        if not isinstance(dr, dict):
            continue
        safe["domain_results"].append({
            "domain": str(dr.get("domain", "unknown") or "unknown"),
            "summary": str(dr.get("summary", "") or ""),
            "quality_score": float(dr.get("quality_score", 0) or 0),
            "key_deliverables": list(dr.get("key_deliverables", []) or []),
            "gaps": list(dr.get("gaps", []) or []),
            "file_paths": list(dr.get("file_paths", []) or []),
        })
    return safe


def export_report(
    final_report: dict,
    user_task: str,
    session_id: str,
    interactive: bool = False,
    workers: list[dict] | None = None,
    active_leaders: list[dict] | None = None,  # Legacy compat — ignored
) -> str | None:
    """Export two self-contained HTML reports: results + quality.

    Returns the output folder path, or None on failure.
    Defensively normalizes all inputs — should never fail on valid filesystem.
    """
    # ── Step 1: Settings check (only legit reason to return None) ──
    try:
        settings = get_settings()
    except Exception:
        logger.warning("export_report_settings_failed", exc_info=True)
        return None
    if not settings.report_export_enabled:
        logger.info("report_export_disabled")
        return None

    # ── Step 2: Normalize inputs (prevent crashes from malformed data) ──
    final_report = _safe_report(final_report)
    if workers is None:
        workers = []
    if not isinstance(workers, list):
        workers = []
    user_task = str(user_task or "Report")
    session_id = str(session_id or "")

    # ── Step 3: Create output directory ──
    try:
        base_dir = Path(settings.report_output_dir)
        valid_id = session_id and session_id not in ("", "unknown")
        folder_name = session_id if valid_id else datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = base_dir / folder_name
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.warning("export_report_mkdir_failed", exc_info=True)
        return None

    # ── Step 4: Render results.html (main report — must succeed) ──
    try:
        results_data = _build_results_data(final_report, workers, user_task)
        results_data["interactive"] = interactive
        results_html = _render_html(results_data, "report_results.html")
        _write_html(results_html, output_dir / "results")
    except Exception:
        logger.error("export_report_results_render_failed", exc_info=True)
        # Emergency fallback: write minimal HTML so file always exists
        try:
            minimal = (
                f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
                f"<title>{_esc(user_task)}</title></head><body>"
                f"<h1>{_esc(user_task)}</h1>"
                f"<p>{_esc(final_report.get('executive_summary', ''))}</p>"
                f"</body></html>"
            )
            (output_dir / "results.html").write_text(minimal, encoding="utf-8")
        except Exception:
            return None

    # quality.html 생성 제거 — results.html 단일 리포트로 통합

    logger.info("reports_exported", path=str(output_dir))
    return str(output_dir)


# ── Advisory comments injection ──────────────────────

_ADVISORY_CSS = """\
.advisory-section { margin-top: 32px; padding: 28px 60px; border-top: 3px solid #9333ea; background: linear-gradient(180deg, rgba(147,51,234,0.04) 0%, transparent 100%); }
.advisory-section h2 { font-size: 15px; color: #9333ea; margin-bottom: 16px; border-bottom: none; padding: 0; }
.advisory-card { margin-bottom: 14px; padding: 16px 20px; border: 1px solid rgba(147,51,234,0.2); border-radius: 10px; background: white; }
.advisory-card .adv-name { font-size: 13px; font-weight: 700; color: #9333ea; margin-bottom: 6px; }
.advisory-card .adv-comment { font-size: 12.5px; line-height: 1.7; color: #444; white-space: pre-line; }
@media (max-width: 768px) { .advisory-section { padding: 20px 24px; } }
@media print { .advisory-section { break-before: page; } }"""


def inject_advisory_comments(report_folder: str, comments: list[dict]) -> bool:
    """Append advisory comments section to existing results.html.

    Inserts before </footer> so it appears after the report body.
    Returns True on success, False on failure. Never raises.
    """
    if not report_folder or not comments:
        return False
    try:
        folder = Path(report_folder)
        # Find main report file
        for name in ("results.html", "result.html", "result_whole.html"):
            target = folder / name
            if target.exists():
                break
        else:
            return False

        content = target.read_text(encoding="utf-8")

        # Skip if already injected
        if "advisory-section" in content:
            return True

        # Build advisory HTML
        cards = []
        for c in comments:
            cname = _esc(c.get("name", ""))
            ctext = _esc(c.get("comment", ""))
            cards.append(
                f'<div class="advisory-card">\n'
                f'  <div class="adv-name">\U0001f9ec {cname}</div>\n'
                f'  <div class="adv-comment">{ctext}</div>\n'
                f'</div>'
            )
        section_html = (
            f'<div class="advisory-section">\n'
            f'  <h2>\U0001f9ec 자문단 코멘트</h2>\n'
            f'  {"".join(cards)}\n'
            f'</div>\n'
        )

        # Inject CSS into <style> block and section before </footer>
        if "</style>" in content:
            content = content.replace("</style>", f"{_ADVISORY_CSS}\n</style>", 1)
        if "<footer" in content:
            content = content.replace("<footer", f"{section_html}<footer", 1)
        else:
            # No footer — insert before </body>
            content = content.replace("</body>", f"{section_html}</body>", 1)

        target.write_text(content, encoding="utf-8")
        logger.info("advisory_comments_injected", file=str(target), count=len(comments))
        return True
    except Exception:
        logger.warning("advisory_inject_failed", exc_info=True)
        return False
