"""Worker Agent - planning, execution with Claude Code tools, revision."""

from __future__ import annotations

import json

from src.agents.base import BaseAgent
from src.config.personas import get_worker_persona, format_persona_block
from src.config.settings import get_settings
from src.models.messages import WorkerResult
from src.prompts.worker_prompts import (
    EXECUTE_PLAN_SYSTEM,
    PREDECESSOR_CONTEXT_BLOCK,
    DEV_EXECUTION_GUIDANCE,
    REVIEWER_EXECUTION_GUIDANCE,
    PLANNER_EXECUTION_GUIDANCE,
    SYNTHESIZER_EXECUTION_GUIDANCE,
)

# Tool descriptions for worker prompt injection
# Includes both CLI mode (MCP) and API mode (native) tool names
_TOOL_DESCRIPTIONS: dict[str, str] = {
    # Claude Code built-in tools (CLI mode)
    "Read": "파일 읽기 — 로컬 파일 시스템의 파일 내용 확인",
    "Write": "파일 쓰기 — 코드, 설정, 문서 등 새 파일 생성",
    "Bash": "셸 명령 실행 — 스크립트 실행, 패키지 설치, 빌드 등",
    "Glob": "파일 검색 — 패턴으로 파일 경로 검색",
    "Grep": "내용 검색 — 파일 내용에서 패턴 검색",
    "WebSearch": "웹 검색 — 키워드 기반 웹 검색 (내장 도구)",
    "WebFetch": "웹 페이지 가져오기 — URL에서 마크다운으로 변환하여 콘텐츠 추출 (내장 도구)",
    # Firecrawl MCP (CLI mode)
    "mcp__firecrawl__firecrawl_scrape": "웹 스크래핑 — 특정 URL의 구조화된 콘텐츠 추출",
    "mcp__firecrawl__firecrawl_crawl": "멀티페이지 크롤링 — 사이트 내 여러 페이지 자동 수집",
    "mcp__firecrawl__firecrawl_extract": "구조화 추출 — 웹 페이지에서 스키마 기반 데이터 추출",
    # GitHub MCP (CLI mode)
    "mcp__github__search_code": "코드 검색 — GitHub 리포지토리에서 코드 패턴 검색",
    "mcp__github__get_file_contents": "파일 조회 — GitHub 리포지토리의 파일 내용 가져오기",
    "mcp__github__create_pull_request": "PR 생성 — GitHub 풀 리퀘스트 생성",
    "mcp__github__list_issues": "이슈 목록 — GitHub 이슈 조회",
    "mcp__github__list_code_scanning_alerts": "코드 스캐닝 — GitHub 보안 취약점 알림 조회",
    "mcp__github__list_secret_scanning_alerts": "시크릿 스캐닝 — GitHub 유출 비밀키 알림 조회",
    # Fetch MCP (CLI mode)
    "mcp__fetch__fetch": "HTTP 요청 — URL에서 직접 콘텐츠 가져오기 (폴백용)",
    # ── API mode native tools ──
    "web_search": "웹 검색 — 키워드 기반 웹 검색 (Anthropic 내장)",
    "web_fetch": "웹 페이지 가져오기 — URL에서 마크다운으로 변환 (Anthropic 내장)",
    "firecrawl_scrape": "웹 스크래핑 — web_fetch 실패 시 폴백 (봇 차단/JS 렌더링 사이트)",
    "firecrawl_crawl": "멀티페이지 크롤링 — 사이트 내 여러 페이지 자동 수집",
    "github_search_code": "코드 검색 — GitHub에서 코드 패턴 검색",
    "github_get_file": "파일 조회 — GitHub 리포의 파일 내용 가져오기",
    "github_list_issues": "이슈 목록 — GitHub 이슈 조회",
    "github_create_pr": "PR 생성 — GitHub Pull Request 생성",
    # ── Domain-specific tools (Phase 1) ──
    "world_bank_data": "세계은행 데이터 — 200개국 거시경제 지표 (GDP, 인구, 인플레이션 등)",
    "exchange_rate": "환율 조회 — 160+ 통화 실시간 환율",
    "imf_data": "IMF 데이터 — 세계경제전망 주요 지표 (GDP, CPI, 실업률)",
    "dbnomics_data": "DBnomics — ECB/OECD/Eurostat 80+ 기관 경제 데이터 통합 조회",
    "pykrx_stock": "한국 주식 데이터 — KOSPI/KOSDAQ 주가, 시가총액, PER/PBR/배당수익률",
    "yfinance_data": "글로벌 주식 데이터 — 미국/유럽/아시아 주가, 재무제표, 기업 정보",
    "fear_greed_index": "암호화폐 공포탐욕지수 — 일별 시장 심리 (0=극단적 공포, 100=극단적 탐욕)",
    "quickchart_render": "차트 이미지 생성 — Chart.js 설정을 PNG URL로 변환 (리포트 시각화용)",
    "patentsview_search": "미국 특허 검색 — Google Patents 검색 URL 생성",
    "hf_datasets_search": "HuggingFace 데이터셋 검색 — 10만+ ML 데이터셋 메타데이터 탐색",
    # ── Domain-specific tools (Phase 2 — HR/Security) ──
    "bls_data": "미국 노동 통계 — 실업률, 고용, 임금, CPI 등 (API 키 불필요)",
    "nvd_cve_search": "CVE 취약점 검색 — CVSS 점수, 심각도, 설명 조회 (API 키 불필요)",
    # ── Domain-specific tools (Phase 2 — Korean data APIs) ──
    "dart_financial": "DART 재무제표 — 한국 상장사 매출액, 영업이익, 자산/부채 (API 키 필요)",
    "ecos_data": "한국은행 ECOS — 기준금리, 통화량, 물가지수, 국제수지 (API 키 필요)",
    "kosis_data": "KOSIS 통계 — 한국 고용, 임금, 인구, 경제활동 공식 통계 (API 키 필요)",
}

# Fallback instruction injected into API mode worker prompts
_API_TOOL_FALLBACK = """
## 도구 사용 우선순위

### 웹 검색
- web_search를 사용하세요 (내장 도구).

### 웹 페이지 내용 가져오기
- web_fetch를 먼저 사용하세요 (내장 도구).
- web_fetch가 빈 결과를 반환하거나 오류가 발생하면,
  firecrawl_scrape로 같은 URL을 다시 시도하세요.
- 여러 페이지를 한번에 수집해야 하면 firecrawl_crawl을 사용하세요.
"""

# Development worker types that get extended timeout and dev guidance
_DEV_WORKER_TYPES = frozenset({
    "backend_developer", "frontend_developer",
    "devops_engineer", "architect",
    "data_engineer", "ml_engineer", "security_engineer",
})

# Search-heavy worker types that need extra turns for search+synthesize cycles
# Search-heavy worker types that need extra turns for search+synthesize cycles.
# max_turns hierarchy: default(25) < dev(28) < search(30)
# Criteria: multi-step search→analysis→synthesis, TEXT_MODE, or previously Opus.
_SEARCH_HEAVY_TYPES = frozenset({
    # Research domain
    "researcher", "deep_researcher",
    # Analysis workers
    "data_analyst", "fact_checker", "data_scientist",
    "market_researcher", "ux_researcher", "product_analyst",
    "financial_analyst", "compensation_analyst",
    # Strategy/product (previously Opus — complex multi-step analysis)
    "strategist", "product_manager",
    # Legal/compliance (검색→법률분석→합성 사이클)
    "legal_counsel", "compliance_officer", "ip_specialist",
    # HR analysis
    "org_developer",
    # Security analysis (MCP tools + complex threat modeling)
    "security_analyst", "privacy_specialist",
    # Process analysis
    "process_analyst",
    # All *_researcher types (TEXT_MODE 워커 — 검색 집약형)
    "tech_researcher", "ops_researcher", "finance_researcher",
    "hr_researcher", "legal_researcher", "data_researcher",
    "security_researcher",
    # Recruiter (candidate search cycles)
    "recruiter",
    # HR training (training program design + research cycles)
    "training_specialist",
})


# Re-export from shared module for backward compatibility
from src.utils.plan_utils import format_plan_for_execution  # noqa: F401


class WorkerAgent(BaseAgent):
    """Worker that creates plans, executes tasks (via Claude Code tools), and revises."""

    def __init__(
        self,
        agent_id: str,
        model: str = "sonnet",
        worker_domain: str = "",
        allowed_tools: list[str] | None = None,
        text_mode: bool = False,
        tool_category: str | None = None,
        worker_name: str = "",
        role_type: str = "executor",
    ) -> None:
        super().__init__(agent_id, model=model)
        self.worker_domain = worker_domain
        self.worker_name = worker_name or worker_domain
        self.role_type = role_type
        self.allowed_tools = allowed_tools or []
        self.text_mode = text_mode
        self.tool_category = tool_category
        self._persona = get_worker_persona(worker_domain)
        self._persona_block = format_persona_block(self._persona) if self._persona else ""
        self._settings = get_settings()

    def invoke(self, state: dict) -> dict:
        raise NotImplementedError("Use specific methods")

    def _is_development_worker(self) -> bool:
        """Check if this worker is a development-type worker (gets extended timeout + dev guide)."""
        return self.worker_domain in _DEV_WORKER_TYPES

    # ── Execution (Claude Code handles tools internally) ──

    def _build_tools_description(self) -> str:
        """Build human-readable description of available tools for prompt injection."""
        if not self.allowed_tools:
            return "도구 없음 — 지식 기반으로만 작업을 수행하세요."

        settings = get_settings()
        # CLI mode: show original tool names
        lines = []
        for tool_name in self.allowed_tools:
            desc = _TOOL_DESCRIPTIONS.get(tool_name, tool_name)
            lines.append(f"- {tool_name}: {desc}")
        return "\n".join(lines)

    def _build_tool_examples(self) -> str:
        """Build dynamic tool usage examples based only on allowed_tools.

        Returns an empty string if no tools are available, or a focused
        examples section showing only relevant tool categories.
        """
        if not self.allowed_tools:
            return ""

        tools = set(self.allowed_tools)
        sections: list[str] = ["## 도구 활용 전략 및 호출 예시"]

        # Search section
        has_websearch = "WebSearch" in tools
        if has_websearch:
            sections.append(
                "### 검색\n```\n"
                'WebSearch(query="카카오 2025 연간 매출 영업이익")\n'
                'WebSearch(query="site:dart.fss.or.kr 카카오 사업보고서 2024")\n'
                "```"
            )

        # Scraping section
        has_firecrawl = any("firecrawl" in t for t in tools)
        has_fetch = any("fetch" in t for t in tools)
        has_webfetch = "WebFetch" in tools
        if has_firecrawl or has_fetch or has_webfetch:
            lines = ["### 웹 스크래핑\n```"]
            if has_webfetch:
                lines.append('WebFetch(url="https://ir.kakao.com/...", prompt="실적 데이터 추출")')
            if has_firecrawl:
                lines.append('firecrawl_scrape(url="https://ir.kakao.com/...")   # 풍부한 마크다운 변환')
                lines.append('firecrawl_crawl(url="https://...", maxPages=5)     # 멀티페이지 수집')
                lines.append('firecrawl_extract(url="https://...", schema={{"매출": "string"}})  # 구조화 추출')
            if has_fetch:
                lines.append('fetch(url="https://...")                           # 단순 HTTP 폴백')
            lines.append("```")
            sections.append("\n".join(lines))

        # Filesystem section
        has_fs = any(t in tools for t in ("Read", "Write", "Bash", "Glob", "Grep", "Edit"))
        if has_fs:
            lines = ["### 파일 시스템\n```"]
            if "Glob" in tools or "Grep" in tools or "Read" in tools:
                lines.append('Glob(pattern="src/**/*.py")')
                lines.append('Grep(pattern="class BaseAgent", path="src/")')
                lines.append('Read(file_path="/abs/path/to/file.py")')
            if "Write" in tools or "Edit" in tools:
                lines.append('Write(file_path="/abs/path/to/new_file.py", content="...")')
                lines.append('Edit(file_path="/abs/path/to/file.py", old_string="...", new_string="...")')
            if "Bash" in tools:
                lines.append('Bash(command="python3 -m pytest tests/ -q")')
                lines.append("Bash(command=\"python3 -c 'import src.module; print(\\\"OK\\\")'\")")
            lines.append("```")
            sections.append("\n".join(lines))

        # GitHub section
        has_github = any("github" in t for t in tools)
        if has_github:
            sections.append(
                "### GitHub\n"
                "```\n"
                'search_code(query="ClaudeCodeBridge repo:user/repo language:python")\n'
                'get_file_contents(owner="user", repo="repo", path="src/utils/file.py")\n'
                'list_issues(owner="user", repo="repo", state="open")\n'
                "```"
            )

        # Finance domain tools
        has_finance = any(t in tools for t in (
            "world_bank_data", "exchange_rate", "imf_data",
            "dbnomics_data", "pykrx_stock", "yfinance_data", "fear_greed_index",
        ))
        if has_finance:
            lines = ["### 금융 데이터 (도구별 용도에 맞게 선택)\n```"]
            if "world_bank_data" in tools:
                lines.append('world_bank_data(country="KR", indicator="NY.GDP.MKTP.CD")  # GDP')
            if "exchange_rate" in tools:
                lines.append('exchange_rate(base="USD", target="KRW")  # 환율')
            if "imf_data" in tools:
                lines.append('imf_data(country="KR", indicator="NGDPD")  # IMF GDP 전망')
            if "dbnomics_data" in tools:
                lines.append('dbnomics_data(provider="OECD", dataset="MEI", series="...")  # OECD 경제지표')
            if "pykrx_stock" in tools:
                lines.append('pykrx_stock(ticker="005930", data_type="ohlcv")  # 삼성전자 주가')
            if "yfinance_data" in tools:
                lines.append('yfinance_data(ticker="AAPL", data_type="financials")  # 애플 재무제표')
            if "fear_greed_index" in tools:
                lines.append('fear_greed_index(limit=7)  # 암호화폐 공포탐욕지수')
            if "dart_financial" in tools:
                lines.append('dart_financial(corp_name="삼성전자", bsns_year="2024")  # 재무제표')
            if "ecos_data" in tools:
                lines.append('ecos_data(stat_code="722Y001")  # 한국 기준금리')
            lines.append("```")
            sections.append("\n".join(lines))

        # Chart tool
        if "quickchart_render" in tools:
            sections.append(
                "### 차트 시각화\n"
                "```\n"
                'quickchart_render(chart_config=\'{"type":"bar","data":{"labels":["Q1","Q2"],'
                '"datasets":[{"label":"매출","data":[100,150]}]}}\')  # PNG URL 반환\n'
                "```\n"
                "반환된 URL을 리포트 HTML에 <img src=\"...\">로 삽입하세요."
            )

        # Patent tool
        if "patentsview_search" in tools:
            sections.append(
                "### 특허 검색\n"
                "```\n"
                'patentsview_search(query="artificial intelligence neural network")  # Google Patents URL\n'
                "```\n"
                "반환된 URL을 web_fetch로 가져오거나 web_search로 site:patents.google.com 검색하세요."
            )

        # HuggingFace datasets
        if "hf_datasets_search" in tools:
            sections.append(
                "### 데이터셋 검색\n"
                "```\n"
                'hf_datasets_search(query="Korean NER", sort="downloads")  # HuggingFace 데이터셋\n'
                "```"
            )

        # HR: BLS labor statistics
        if "bls_data" in tools:
            sections.append(
                "### 미국 노동 통계 (BLS)\n"
                "```\n"
                'bls_data(series_id="LNS14000000")  # 실업률\n'
                'bls_data(series_id="CES0000000001", start_year=2022, end_year=2025)  # 비농업 고용\n'
                'bls_data(series_id="CES0500000003")  # 평균 시급\n'
                'bls_data(series_id="CUUR0000SA0")  # 소비자물가지수 (CPI-U)\n'
                "```"
            )

        # Korean statistics (KOSIS)
        if "kosis_data" in tools:
            sections.append(
                "### 한국 공식 통계 (KOSIS)\n"
                "```\n"
                'kosis_data(stat_id="DT_1DA7002S")  # 경제활동인구\n'
                'kosis_data(stat_id="DT_1ES4F01", start_period="2022")  # 임금\n'
                'kosis_data(stat_id="DT_1IN1502")  # 인구추계\n'
                "```"
            )

        # Security: NVD CVE search
        if "nvd_cve_search" in tools:
            sections.append(
                "### CVE 취약점 검색 (NVD)\n"
                "```\n"
                'nvd_cve_search(keyword="log4j")  # 키워드 검색\n'
                'nvd_cve_search(cve_id="CVE-2021-44228")  # 특정 CVE 조회\n'
                'nvd_cve_search(keyword="apache", results_per_page=10)  # 결과 수 지정\n'
                "```"
            )

        return "\n\n".join(sections) if len(sections) > 1 else ""

    def _compute_time_budget(self, override_seconds: float | None = None) -> int:
        """Return time budget in minutes for prompt display.

        Args:
            override_seconds: Actual remaining time from outer timeout.
                              None = use default settings.
        """
        if override_seconds is not None:
            return max(1, int(override_seconds / 60))
        if self._is_development_worker():
            return self._settings.dev_execution_timeout // 60
        return self._settings.execution_timeout // 60

    def _compute_max_turns(self) -> int:
        """Return max turns for this worker type.

        Search-heavy detection: registered types in _SEARCH_HEAVY_TYPES,
        OR custom workers with tool_category 'research' or 'analysis'.
        """
        if self._is_development_worker():
            return self._settings.worker_max_turns_dev
        if self.worker_domain in _SEARCH_HEAVY_TYPES:
            return self._settings.worker_max_turns_search
        # Custom workers: check tool_category for search-heavy classification
        if getattr(self, 'tool_category', None) in ('research', 'analysis'):
            return self._settings.worker_max_turns_search
        return self._settings.worker_max_turns

    def _build_prompt_blocks(self, predecessor_context: str = "") -> tuple[str, str]:
        """Build conditional prompt blocks for predecessor context and dev guidance.

        Returns:
            (predecessor_block, dev_guidance_block) — each may be empty string.
        """
        # Predecessor context block
        predecessor_block = ""
        if predecessor_context:
            predecessor_block = PREDECESSOR_CONTEXT_BLOCK.format(
                predecessor_context=predecessor_context,
            )

        # Role-specific guidance block
        dev_guidance_block = ""
        if self.role_type == "reviewer":
            dev_guidance_block = REVIEWER_EXECUTION_GUIDANCE
        elif self.role_type == "planner":
            dev_guidance_block = PLANNER_EXECUTION_GUIDANCE
        elif self.role_type == "synthesizer":
            dev_guidance_block = SYNTHESIZER_EXECUTION_GUIDANCE
        elif self._is_development_worker():
            dev_guidance_block = DEV_EXECUTION_GUIDANCE.format(
                time_budget=self._compute_time_budget(),
            )

        return predecessor_block, dev_guidance_block

    def _build_execution_prompt(
        self, approved_plan: str, predecessor_context: str = "",
        time_budget: float | None = None,
        upstream_context: str = "",
    ) -> str:
        """Build the system prompt for plan execution."""
        readable_plan = format_plan_for_execution(approved_plan)
        predecessor_block, dev_guidance_block = self._build_prompt_blocks(predecessor_context)

        # Upstream context block (user_task, answers, work_order fields)
        upstream_context_block = ""
        if upstream_context:
            upstream_context_block = (
                "\n## 상위 작업 맥락\n"
                "이 작업은 아래 요청에서 비롯되었습니다. "
                "전체 맥락을 이해한 위에서 작업을 수행하세요.\n\n"
                f"{upstream_context}\n"
            )

        return self._format_prompt(
            EXECUTE_PLAN_SYSTEM,
            worker_domain=self.worker_domain,
            worker_name=self.worker_name,
            approved_plan=readable_plan,
            persona_block=self._persona_block,
            available_tools_description=self._build_tools_description(),
            tool_examples_block=self._build_tool_examples(),
            upstream_context_block=upstream_context_block,
            predecessor_block=predecessor_block,
            dev_guidance_block=dev_guidance_block,
            time_budget=self._compute_time_budget(override_seconds=time_budget),
            worker_output_dir=self._settings.worker_output_dir,
        )

    @staticmethod
    def _wrap_text_result(raw_text: str) -> WorkerResult:
        """Wrap raw text output into a WorkerResult.

        Text mode workers produce narrative output instead of structured JSON.
        Extract a reasonable summary and wrap into the standard schema.
        """
        text = raw_text.strip()
        if not text:
            return WorkerResult(
                result_summary="No output produced.",
                deliverables=[],
                completion_percentage=0,
            )
        # Use first 800 chars as summary, full text as single deliverable
        summary = text[:800]
        if len(text) > 800:
            summary += "…"
        return WorkerResult(
            result_summary=summary,
            deliverables=[text],
            completion_percentage=80,
        )

    def execute_plan(
        self, approved_plan: str, predecessor_context: str = "",
        time_budget: float | None = None,
        upstream_context: str = "",
    ) -> WorkerResult:
        """Execute the approved plan — Claude Code handles tool calls internally."""
        system = self._build_execution_prompt(approved_plan, predecessor_context, time_budget=time_budget, upstream_context=upstream_context)
        timeout = self._settings.dev_execution_timeout if self._is_development_worker() else None
        # A1: time_budget이 주어지면 execution_timeout과 min
        effective_timeout = timeout or self._settings.execution_timeout
        if time_budget is not None:
            effective_timeout = max(60, min(effective_timeout, int(time_budget)))

        if self.text_mode:
            from src.utils.parallel import run_async as _run
            raw = _run(self._bridge.raw_query(
                system_prompt=system,
                user_message="승인된 계획에 따라 작업을 실행하고 결과를 보고해주세요.",
                model=self.model,
                allowed_tools=self.allowed_tools,
                max_turns=self._compute_max_turns(),
                timeout=effective_timeout,
                effort=self._settings.worker_effort,
            ))
            return self._wrap_text_result(raw)

        return self._query(
            system_prompt=system,
            user_content="승인된 계획에 따라 작업을 실행하고 결과를 보고해주세요.",
            output_schema=WorkerResult,
            allowed_tools=self.allowed_tools,
            timeout=effective_timeout,
            max_turns=self._compute_max_turns(),
            time_budget=time_budget,
        )

    async def aexecute_plan(
        self, approved_plan: str, predecessor_context: str = "",
        time_budget: float | None = None,
        progress_callback=None,
        upstream_context: str = "",
    ) -> WorkerResult:
        """Async version — Claude Code handles tool calls internally."""
        system = self._build_execution_prompt(approved_plan, predecessor_context, time_budget=time_budget, upstream_context=upstream_context)
        timeout = self._settings.dev_execution_timeout if self._is_development_worker() else None
        # A1: time_budget이 주어지면 execution_timeout과 min
        effective_timeout = timeout or self._settings.execution_timeout
        if time_budget is not None:
            effective_timeout = max(60, min(effective_timeout, int(time_budget)))

        if self.text_mode:
            raw = await self._bridge.raw_query(
                system_prompt=system,
                user_message="승인된 계획에 따라 작업을 실행하고 결과를 보고해주세요.",
                model=self.model,
                allowed_tools=self.allowed_tools,
                max_turns=self._compute_max_turns(),
                timeout=effective_timeout,
                effort=self._settings.worker_effort,
            )
            return self._wrap_text_result(raw)

        return await self._aquery(
            system_prompt=system,
            user_content="승인된 계획에 따라 작업을 실행하고 결과를 보고해주세요.",
            output_schema=WorkerResult,
            allowed_tools=self.allowed_tools,
            timeout=effective_timeout,
            max_turns=self._compute_max_turns(),
            time_budget=time_budget,
            progress_callback=progress_callback,
        )
