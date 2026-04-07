"""Metrics report exporter for external tool consumption.

Generates data/metrics-report.md from MetricsStore data.
Designed to be read by Claude Code CLI, Cursor, or other AI coding tools
when the user asks them to optimize the system.

Auto-called at session end; the report is fully replaced each time (not appended).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.utils.metrics_store import MetricsStore, DomainStats


def _format_elapsed(seconds: float) -> str:
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.0f}s"
    mins = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{mins}m {secs:02d}s"


def _pct(value: float) -> str:
    return f"{int(value * 100)}%"


def _model_usage_str(usage: dict[str, int]) -> str:
    parts = [f"{model}:{count}" for model, count in sorted(usage.items())]
    return ", ".join(parts) if parts else "-"


class MetricsExporter:
    """Generates Markdown analysis reports from MetricsStore."""

    def __init__(self, store: MetricsStore):
        self._store = store

    def export_report(
        self,
        path: str = "data/metrics-report.md",
        days: int = 30,
    ) -> str:
        """Generate the full metrics report, replacing the file entirely.

        Returns the file path written.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        overall = self._store.get_overall_stats(days)
        domain_stats = self._store.get_domain_stats(days)
        failures = self._store.get_failure_patterns(days)
        improvements = self._analyze_improvements(domain_stats, overall, failures)

        lines: list[str] = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Header
        lines.append("# Execution Metrics Report")
        lines.append(
            f"Generated: {now} | "
            f"Sessions analyzed: {overall['session_count']} (last {days} days)"
        )
        lines.append("")

        # Performance Summary
        lines.append("## Performance Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total sessions | {overall['session_count']} |")
        lines.append(
            f"| Avg session duration | {_format_elapsed(overall['avg_duration_s'])} |"
        )
        lines.append(f"| Avg workers per session | {overall['avg_workers']} |")
        lines.append(f"| Worker success rate | {_pct(overall['success_rate'])} |")
        lines.append(f"| Cache hit rate | {_pct(overall['cache_hit_rate'])} |")
        lines.append("")

        # Domain Performance
        if domain_stats:
            lines.append("## Domain Performance")
            lines.append("")
            lines.append(
                "| Domain | Executions | Avg Duration | P95 Duration | "
                "Timeout Rate | Success Rate | Models Used |"
            )
            lines.append(
                "|--------|-----------|-------------|-------------|"
                "-------------|-------------|-------------|"
            )
            for domain in sorted(domain_stats.keys()):
                s = domain_stats[domain]
                lines.append(
                    f"| {s.domain} | {s.session_count} | "
                    f"{_format_elapsed(s.avg_duration_s)} | "
                    f"{_format_elapsed(s.p95_duration_s)} | "
                    f"{_pct(s.timeout_rate)} | "
                    f"{_pct(s.success_rate)} | "
                    f"{_model_usage_str(s.model_usage)} |"
                )
            lines.append("")

        # Failure Patterns
        if failures:
            lines.append("## Failure Patterns")
            lines.append("")
            lines.append(
                "| Worker Domain | Failure Rate | Failures / Total | Top Errors |"
            )
            lines.append(
                "|--------------|-------------|-----------------|------------|"
            )
            for fp in failures:
                errors = ", ".join(fp.top_errors[:2]) if fp.top_errors else "-"
                lines.append(
                    f"| {fp.worker_domain} | {_pct(fp.failure_rate)} | "
                    f"{fp.failure_count}/{fp.total_count} | {errors} |"
                )
            lines.append("")

        # Improvement Opportunities
        lines.append("## Improvement Opportunities")
        lines.append("")
        if improvements:
            lines.append(
                "These are data-driven suggestions. To apply, read this file in "
                "your AI coding tool (Claude Code, Cursor, etc.) and ask it to "
                "implement the relevant changes."
            )
            lines.append("")
            for i, imp in enumerate(improvements, 1):
                lines.append(f"{i}. **[{imp['type']}]** {imp['description']}")
                lines.append(f"   - Evidence: {imp['evidence']}")
                lines.append(f"   - Suggested action: {imp['action']}")
                lines.append("")
        else:
            lines.append(
                "No actionable improvements detected. System is performing well."
            )
            lines.append("")

        # Context for AI tools
        lines.append("## Reference: Key Files for Improvement")
        lines.append("")
        lines.append("When implementing improvements, these are the relevant files:")
        lines.append("")
        lines.append("- `src/config/settings.py` — Timeout values, model configs")
        lines.append(
            "- `src/config/agent_registry.py` — Worker model overrides, domain definitions"
        )
        lines.append("- `src/tools/__init__.py` — Worker-to-tool mappings")
        lines.append("- `src/prompts/` — System prompt templates per domain")
        lines.append("- `domains/*.yaml` — Plugin domain definitions")
        lines.append("")

        content = "\n".join(lines)
        Path(path).write_text(content, encoding="utf-8")
        return path

    def _analyze_improvements(
        self,
        domain_stats: dict[str, DomainStats],
        overall: dict,
        failures: list,
    ) -> list[dict]:
        """Detect improvement patterns from aggregated data."""
        improvements: list[dict] = []

        if overall["session_count"] < 3:
            # Not enough data for meaningful analysis
            return improvements

        for domain, stats in domain_stats.items():
            if stats.session_count < 2:
                continue

            # 1. Timeout calibration: p95 > 80% of current timeout
            from src.config.settings import get_settings
            settings = get_settings()
            timeout = settings.execution_timeout
            if stats.p95_duration_s > timeout * 0.8 and stats.timeout_rate > 0.1:
                suggested = int(stats.p95_duration_s * 1.4)
                improvements.append({
                    "type": "TIMEOUT",
                    "description": (
                        f"`{domain}` avg {_format_elapsed(stats.avg_duration_s)}, "
                        f"p95 {_format_elapsed(stats.p95_duration_s)} "
                        f"(current timeout: {timeout}s)"
                    ),
                    "evidence": (
                        f"timeout_rate={_pct(stats.timeout_rate)}, "
                        f"p95={stats.p95_duration_s:.0f}s vs limit={timeout}s"
                    ),
                    "action": (
                        f"In `src/config/settings.py`, increase `execution_timeout` "
                        f"to {suggested}s or add domain-specific timeout"
                    ),
                })

            # 2. Model downgrade: using opus but could use sonnet
            opus_count = stats.model_usage.get("opus", 0)
            sonnet_count = stats.model_usage.get("sonnet", 0)
            if opus_count > 3 and stats.success_rate > 0.85:
                improvements.append({
                    "type": "MODEL",
                    "description": (
                        f"`{domain}` uses opus {opus_count} times "
                        f"with {_pct(stats.success_rate)} success rate"
                    ),
                    "evidence": (
                        f"opus={opus_count}, sonnet={sonnet_count}, "
                        f"success_rate={_pct(stats.success_rate)}"
                    ),
                    "action": (
                        f"In `src/config/agent_registry.py`, add "
                        f"`WORKER_MODEL_OVERRIDES['{domain}'] = 'sonnet'` "
                        f"to reduce cost without quality loss"
                    ),
                })

            # 3. High timeout rate (tier 2+ is high)
            if stats.timeout_rate > 0.25 and stats.session_count >= 5:
                improvements.append({
                    "type": "RELIABILITY",
                    "description": (
                        f"`{domain}` has {_pct(stats.timeout_rate)} degraded/timeout rate"
                    ),
                    "evidence": (
                        f"session_count={stats.session_count}, "
                        f"timeout_rate={_pct(stats.timeout_rate)}"
                    ),
                    "action": (
                        f"Review tool mappings in `src/tools/__init__.py` for `{domain}` "
                        f"— consider replacing unreliable tools or simplifying the task scope"
                    ),
                })

        # 4. Global failure patterns
        for fp in failures:
            if fp.failure_rate > 0.3 and fp.total_count >= 3:
                improvements.append({
                    "type": "FAILURE",
                    "description": (
                        f"`{fp.worker_domain}` has {_pct(fp.failure_rate)} failure rate "
                        f"({fp.failure_count}/{fp.total_count})"
                    ),
                    "evidence": f"top_errors: {', '.join(fp.top_errors[:2])}",
                    "action": (
                        f"Investigate `{fp.worker_domain}` worker — check tool availability, "
                        f"prompt clarity, and timeout settings"
                    ),
                })

        return improvements
