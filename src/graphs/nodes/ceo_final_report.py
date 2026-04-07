"""Node: ceo_final_report - CEO compiles final report directly from worker results.

CEO synthesizes all worker results into the final report in a single step.
No separate consolidation or Reporter agent — CEO handles cross-domain synthesis
and report formatting in one LLM call for maximum efficiency.
"""

from langchain_core.messages import AIMessage

from src.config.personas import CEO_PERSONA, format_persona_block
from src.config.settings import get_settings
from src.models.messages import CEOFinalReport
from src.prompts.reporter_prompts import FINAL_REPORT_SYSTEM
from src.utils.guards import node_error_handler
from src.utils.logging import get_logger
from src.utils.parallel import run_async
from src.utils.progress import WorkerStatus, get_tracker
from src.utils.report_helpers import (
    wrap_ceo_html as _wrap_ceo_html,
    generate_report_html as _generate_report_html,
    get_bridge_impl as _get_bridge_impl,
    parse_result as _parse_result,
    strip_agent_refs as _strip_agent_refs,
    count_completed_workers as _count_completed_workers,
    assess_result_quality as _assess_result_quality,
)

logger = get_logger(agent_id="ceo_final_report")


def _build_fallback_report(state: dict) -> dict:
    """Build a CEOFinalReport dict from raw worker data (flat structure).

    Called when the LLM call fails (timeout, parse error, etc.).
    """
    workers = state.get("workers", [])
    user_task = state.get("user_task", "N/A")

    all_summaries = []
    all_deliverables = []
    gaps = []
    file_paths = []

    for w in workers:
        result_data = _parse_result(w.get("execution_result", ""))
        summary = result_data.get("result_summary", result_data.get("summary", ""))
        if summary:
            all_summaries.append(_strip_agent_refs(str(summary)))
        deliverables = result_data.get("deliverables", result_data.get("key_deliverables", []))
        if isinstance(deliverables, list):
            all_deliverables.extend(str(d) for d in deliverables)
        issues = result_data.get("issues", result_data.get("gaps", []))
        if isinstance(issues, list):
            gaps.extend(str(i) for i in issues)
        fps = result_data.get("deliverable_files", [])
        if isinstance(fps, list):
            file_paths.extend(str(f) for f in fps)

    if all_summaries:
        exec_summary = f"Task: {user_task}\n\n" + "\n\n".join(all_summaries[:3])
        if len(all_summaries) > 3:
            exec_summary += f"\n\n(+{len(all_summaries) - 3} additional findings)"
    else:
        exec_summary = f"Task: {user_task}\n\nNo worker results available."

    return {
        "executive_summary": exec_summary,
        "domain_results": [{
            "domain": "combined",
            "summary": "\n\n".join(all_summaries) if all_summaries else "No results.",
            "quality_score": 7,
            "key_deliverables": all_deliverables[:10],
            "gaps": gaps[:5],
            "file_paths": file_paths,
        }],
        "overall_gap_analysis": "",
        "recommendations": [],
    }


_MAX_FILE_CHARS_FOR_CEO = 50000


def _summarize_worker_results(state: dict) -> str:
    """Build concise worker results context for CEO final report LLM call.

    블랙보드가 존재하면 (P-E-R 루프 실행 시) 전체 누적 컨텍스트를 우선 사용.
    블랙보드가 없으면 기존 방식으로 workers[]에서 직접 추출.
    """
    # ── 블랙보드 우선: P-E-R 루프 결과가 있으면 전체 이력 사용 ──
    blackboard_data = state.get("blackboard", {})
    if blackboard_data and blackboard_data.get("loops"):
        from src.utils.blackboard import PipelineBlackboard
        bb = PipelineBlackboard.from_dict(blackboard_data)
        bb_context = bb.get_accumulated_context()
        if bb_context:
            logger.info(
                "using_blackboard_context",
                iterations=bb.get_iteration_count(),
                context_length=len(bb_context),
            )
            # 블랙보드 컨텍스트 + 워커 파일 기반 상세 데이터 병합
            worker_detail = _summarize_worker_results_from_workers(state)
            return f"{bb_context}\n\n## 워커 상세 결과\n{worker_detail}"

    return _summarize_worker_results_from_workers(state)


def _summarize_worker_results_from_workers(state: dict) -> str:
    """기존 방식: workers[]에서 직접 결과 추출."""
    settings = get_settings()
    max_chars = settings.max_result_chars_in_context
    parts = []

    for w in state.get("workers", []):
        result_data = _parse_result(w.get("execution_result", ""))
        domain = w.get("worker_domain", "unknown")
        task_title = w.get("task_title", "")

        if task_title:
            parts.append(f"### [{domain}] {task_title}")

        # Try reading full result from file (lossless)
        result_file = w.get("result_file_path", "")
        file_content_used = False
        if result_file:
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    file_content = f.read(_MAX_FILE_CHARS_FOR_CEO)
                parts.append(f"\n#### 상세 데이터 (파일 원본)\n{file_content}")
                file_content_used = True
            except (OSError, IOError) as e:
                logger.warning("result_file_read_failed", worker=domain, error=str(e)[:100])

        # Fall back to in-memory summary if no file
        if not file_content_used:
            summary = str(result_data.get("result_summary", result_data.get("summary", "")))

            if len(summary) > max_chars:
                summary = summary[:max_chars] + "..."
            if summary:
                summary = _strip_agent_refs(summary)

            if summary:
                parts.append(f"결과: {summary}")

            # Labeled findings — grouped by importance for CEO quick scanning
            findings = result_data.get("labeled_findings", [])
            if findings and isinstance(findings, list):
                sorted_findings = sorted(
                    findings,
                    key=lambda f: f.get("importance", 1) if isinstance(f, dict) else 1,
                    reverse=True,
                )
                parts.append("\n#### 라벨링된 정보 (중요도순)")
                for f in sorted_findings:
                    if not isinstance(f, dict):
                        continue
                    imp = f.get("importance", 1)
                    cat = f.get("category", "fact")
                    content = str(f.get("content", ""))[:500]
                    source = f.get("source", "")
                    marker = "★" * min(imp, 5)
                    source_tag = f" [{source}]" if source else ""
                    parts.append(f"- {marker} [{cat}] {content}{source_tag}")

            deliverables = result_data.get("deliverables", result_data.get("key_deliverables", []))
            if isinstance(deliverables, list) and deliverables:
                parts.append(f"산출물: {', '.join(str(d) for d in deliverables[:15])}")

        file_paths = result_data.get("deliverable_files", [])
        if isinstance(file_paths, list) and file_paths:
            parts.append(f"Files: {', '.join(str(f) for f in file_paths[:5])}")

        parts.append("")  # blank line separator

    return "\n".join(parts)



async def _ceo_compile_report(state: dict) -> CEOFinalReport:
    """CEO compiles the final report from Domain Analyst outputs.

    Domain Analyst가 도메인별 최종물을 이미 생성했으므로,
    CEO는 다중 도메인 병합 + 갭 체크 + 최종 포맷팅만 수행.
    """
    settings = get_settings()
    bridge = _get_bridge_impl()

    system = FINAL_REPORT_SYSTEM.format(
        persona_block=format_persona_block(CEO_PERSONA),
        user_task=state.get("user_task", ""),
    )

    # 워커 결과를 CEO에게 직접 전달 (domain_analyst 바이패스 — 데이터 손실 최소화)
    worker_context = _summarize_worker_results(state)
    logger.info("ceo_report_source", source="worker_direct")

    result = await bridge.structured_query(
        system_prompt=system,
        user_message=worker_context,
        output_schema=CEOFinalReport,
        model=settings.ceo_model,
        allowed_tools=[],
        timeout=settings.reporter_timeout,
        max_turns=settings.reporter_max_turns,
        effort=settings.reporter_effort,
        max_tokens=65536,
    )
    return result


async def _ceo_deep_research_synthesis(state: dict) -> "CEOFinalReport":
    """CEO가 다중 Deep Research 결과를 통합 리포트로 합성.

    각 도메인의 executive_summary + importance 4+ findings만 받아서
    교차 도메인 통합 리포트를 생성.
    """
    from src.prompts.deep_research_prompts import DEEP_RESEARCH_CEO_SYNTHESIS_SYSTEM
    from src.models.messages import DeepResearchResult, LabeledFinding

    settings = get_settings()
    bridge = _get_bridge_impl()

    # Deep Research 결과를 DeepResearchResult로 복원
    raw_results = state.get("deep_research_results", [])
    results = []
    for raw in raw_results:
        if isinstance(raw, dict):
            # key_findings를 LabeledFinding으로 복원
            findings = []
            for f in raw.get("key_findings", []):
                if isinstance(f, dict):
                    findings.append(LabeledFinding(**f))
            results.append(DeepResearchResult(
                domain=raw.get("domain", "unknown"),
                executive_summary=raw.get("executive_summary", ""),
                report_html=raw.get("report_html", ""),
                key_findings=findings,
                sources=raw.get("sources", []),
                confidence_score=raw.get("confidence_score", 5),
                gaps=raw.get("gaps", []),
            ))

    # CEO에게 핵심 요약만 전달
    from src.graphs.nodes.deep_research import _build_summary_for_ceo
    summary_context = _build_summary_for_ceo(results)

    system = DEEP_RESEARCH_CEO_SYNTHESIS_SYSTEM.format(
        user_task=state.get("user_task", ""),
    )

    result = await bridge.structured_query(
        system_prompt=system,
        user_message=summary_context,
        output_schema=CEOFinalReport,
        model=settings.ceo_model,
        allowed_tools=[],
        timeout=settings.reporter_timeout,
        max_turns=settings.reporter_max_turns,
        effort=settings.reporter_effort,
        max_tokens=65536,
    )
    return result


@node_error_handler("ceo_final_report")
def ceo_final_report_node(state: dict) -> dict:
    """CEO compiles all worker results into a final report."""

    report_failed = False

    # Build worker results summary for review loop (before any LLM calls)
    worker_results_summary = _summarize_worker_results(state)

    # CEO compiles final report (simplified schema: executive_summary + report_html + recommendations)
    report = _build_fallback_report(state)
    for attempt in range(2):
        try:
            result = run_async(_ceo_compile_report(state))
            report = result.model_dump()
            break
        except Exception as exc:
            logger.warning("ceo_report_llm_failed", attempt=attempt, error=str(exc))
            report_failed = True

    # Format Activity Log message
    domain_summaries = []
    for dr in report.get("domain_results", []):
        gaps_text = f" (gaps: {', '.join(dr['gaps'])})" if dr.get("gaps") else ""
        domain_summaries.append(
            f"  [{dr['domain']}] {dr['summary'][:200]} - quality: {dr['quality_score']}/10{gaps_text}"
        )
    recommendations = "\n".join(f"  - {r}" for r in report.get("recommendations", []))

    messages = [
        AIMessage(
            content=(
                f"[CEO Final Report]\n\n"
                f"## Summary\n{report.get('executive_summary', '')}\n\n"
                f"## Domain Results\n" + "\n".join(domain_summaries) + "\n\n"
                f"## Recommendations\n{recommendations}"
            )
        )
    ]

    if report_failed:
        messages.append(AIMessage(
            content="[Note] CEO report synthesis failed. Report generated from raw worker results."
        ))

    # Export reports to HTML — guaranteed to produce a file
    import os
    report_folder = None
    has_quality_report = False
    session_id = state.get("session_id", "unknown")

    # 1순위: Opus raw_query로 적응형 HTML 생성
    try:
        opus_html = run_async(_generate_report_html(
            user_task=state.get("user_task", ""),
            executive_summary=report.get("executive_summary", ""),
            worker_results_summary=worker_results_summary,
        ))
        if opus_html and len(opus_html) > 200:
            report_folder = os.path.join("data/reports", session_id)
            os.makedirs(report_folder, exist_ok=True)
            report_path = os.path.join(report_folder, "results.html")
            full_html = _wrap_ceo_html(opus_html, state.get("user_task", ""))
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(full_html)
            logger.info("opus_html_saved", path=report_path, html_len=len(opus_html))
        else:
            raise ValueError("Opus HTML too short or empty")
    except Exception as opus_exc:
        logger.warning("opus_html_failed_fallback_to_exporter", error=str(opus_exc)[:200])
        # 2순위: Python exporter fallback
        try:
            from src.utils.report_exporter import export_report
            report_folder = export_report(
                final_report=report,
                workers=state.get("workers", []),
                user_task=state.get("user_task", ""),
                session_id=session_id,
            )
            has_quality_report = True
        except Exception:
            logger.warning("export_report_fallback_failed", exc_info=True)

    # 3순위: Emergency fallback — 최소 HTML 보장 (모든 시도가 실패한 경우)
    if not report_folder:
        try:
            report_folder = os.path.join("data/reports", session_id)
            os.makedirs(report_folder, exist_ok=True)
            emergency_html = _wrap_ceo_html(
                f"<h2>{state.get('user_task', 'Report')}</h2>"
                f"<p>{report.get('executive_summary', 'Report generation encountered issues.')}</p>"
                f"<hr><p style='color:#888'>이 보고서는 긴급 폴백으로 생성되었습니다.</p>",
                state.get("user_task", "Report"),
            )
            with open(os.path.join(report_folder, "results.html"), "w", encoding="utf-8") as f:
                f.write(emergency_html)
            logger.info("emergency_html_saved", path=report_folder)
        except Exception:
            logger.error("emergency_html_failed", exc_info=True)
            report_folder = None

    # Collect worker-generated file paths directly from workers
    all_file_paths = []
    for w in state.get("workers", []):
        result_file = w.get("result_file_path", "")
        if result_file:
            all_file_paths.append(result_file)
    if all_file_paths:
        files_list = "\n".join(f"  - {f}" for f in all_file_paths)
        messages.append(AIMessage(content=f"[Generated Files]\n{files_list}"))

    if report_folder:
        report_msg = f"[Reports Saved]\n  Results: {report_folder}/results.html"
        if has_quality_report:
            report_msg += f"\n  Quality: {report_folder}/quality.html"
        messages.append(AIMessage(content=report_msg))

    # Persist execution metrics and generate report
    try:
        settings = get_settings()
        if settings.enable_metrics:
            from src.utils.execution_tracker import get_exec_tracker
            from src.utils.metrics_store import MetricsStore
            from src.utils.metrics_exporter import MetricsExporter

            store = MetricsStore(settings.metrics_db_path)
            store.save_session(
                get_exec_tracker().summary(),
                user_task=state.get("user_task", ""),
                session_id=state.get("session_id", ""),
            )
            MetricsExporter(store).export_report()
    except Exception:
        logger.warning("metrics_persist_failed", exc_info=True)

    # Stop tracker (no worker-progress row for CEO — step_progress handles it)
    sid = state.get("session_id", "")
    tracker = get_tracker(sid)
    tracker.stop()

    # P-E-S-R 아키텍처: Reviewer가 루프 내에서 이미 검증 완료
    # report_review 노드 제거 → 바로 user_review_results로
    next_phase = "user_review_results"

    return {
        "final_report": report,
        "messages": messages,
        "phase": next_phase,
        "report_file_path": report_folder or "",
        "worker_results_summary": worker_results_summary,
    }
