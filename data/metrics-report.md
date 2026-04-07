# Execution Metrics Report
Generated: 2026-04-06 04:38 UTC | Sessions analyzed: 3 (last 30 days)

## Performance Summary

| Metric | Value |
|--------|-------|
| Total sessions | 3 |
| Avg session duration | 8m 51s |
| Avg workers per session | 2.0 |
| Worker success rate | 100% |
| Cache hit rate | 0% |

## Domain Performance

| Domain | Executions | Avg Duration | P95 Duration | Timeout Rate | Success Rate | Models Used |
|--------|-----------|-------------|-------------|-------------|-------------|-------------|
| research | 6 | 1m 28s | 1m 51s | 0% | 100% | sonnet:6 |

## Improvement Opportunities

No actionable improvements detected. System is performing well.

## Reference: Key Files for Improvement

When implementing improvements, these are the relevant files:

- `src/config/settings.py` — Timeout values, model configs
- `src/config/agent_registry.py` — Worker model overrides, domain definitions
- `src/tools/__init__.py` — Worker-to-tool mappings
- `src/prompts/` — System prompt templates per domain
- `domains/*.yaml` — Plugin domain definitions
