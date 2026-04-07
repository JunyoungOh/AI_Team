"""Registry of available leader domains and their worker specializations."""

LEADER_DOMAINS: dict[str, dict] = {
    "engineering": {
        "description": "소프트웨어 엔지니어링, 시스템 아키텍처, 프론트엔드/백엔드 개발, DevOps/인프라, 기술 전략",
        "worker_types": [
            "backend_developer",
            "frontend_developer",
            "devops_engineer",
            "architect",
            "tech_researcher",
        ],
    },
    "research": {
        "description": "심층 리서치, 데이터 수집/분석, 문헌 조사, 팩트체크, 시장/기술/학술 조사, 딥 리서치",
        "worker_types": [
            "researcher",
            "deep_researcher",
            "data_analyst",
            "fact_checker",
        ],
    },
    "marketing": {
        "description": "마케팅 전략, 콘텐츠 제작, 브랜딩, 캠페인 설계, UI/UX 디자인, 시장 조사",
        "worker_types": [
            "content_writer",
            "strategist",
            "designer",
            "market_researcher",
        ],
    },
    "operations": {
        "description": "프로젝트 관리, 프로세스 최적화, 리소스 배분, 품질 관리, 변화 관리",
        "worker_types": [
            "project_manager",
            "process_analyst",
            "ops_researcher",
        ],
    },
    "finance": {
        "description": "재무 분석, 예산 관리, 투자 전략, 회계/세무, 재무 모델링, 리스크 관리",
        "worker_types": [
            "financial_analyst",
            "accountant",
            "finance_researcher",
        ],
    },
    "hr": {
        "description": "인사 관리, 채용 전략, 조직 개발, 인재 관리, 기업 문화",
        "worker_types": [
            "recruiter",
            "training_specialist",
            "org_developer",
            "compensation_analyst",
            "hr_researcher",
        ],
    },
    "legal": {
        "description": "법률 자문, 계약 검토, 규제 컴플라이언스, 지식재산 관리, 법적 리스크 분석",
        "worker_types": [
            "legal_counsel",
            "compliance_officer",
            "ip_specialist",
            "legal_researcher",
        ],
    },
    "data": {
        "description": "데이터 엔지니어링, ML/AI 파이프라인, 데이터 과학, 통계 모델링, MLOps",
        "worker_types": [
            "data_engineer",
            "ml_engineer",
            "data_scientist",
            "data_researcher",
        ],
    },
    "product": {
        "description": "프로덕트 전략, 로드맵 수립, 사용자 조사, 요구사항 관리, 제품 지표 분석",
        "worker_types": [
            "product_manager",
            "ux_researcher",
            "product_analyst",
        ],
    },
    "security": {
        "description": "정보보안 전략, 위협/취약점 분석, 보안 아키텍처, 침투 테스트, 개인정보보호",
        "worker_types": [
            "security_analyst",
            "security_engineer",
            "privacy_specialist",
            "security_researcher",
        ],
    },
}


# ── Per-worker model overrides ────────────────────────
# All agents use sonnet for consistent speed and reliability.
# Previous config had opus workers timing out at 420s; sonnet completes in 90-170s.

WORKER_MODEL_OVERRIDES: dict[str, str] = {
    # All-Sonnet: consistent speed, no timeout risk
    # (previously opus: architect, strategist, legal_counsel, product_manager → frequent timeouts)
    # (previously haiku: fact_checker, etc. → inconsistent quality)
}

# ── Text mode workers ────────────────────────────────
# Workers in this set use raw text output (--output-format text) instead of
# JSON schema enforcement. This gives analysis-heavy workers freedom to produce
# richer narratives without JSON structure constraints.
# Their output is automatically wrapped into WorkerResult.

WORKER_TEXT_MODE: frozenset[str] = frozenset({
    "researcher",
    "deep_researcher",
    "data_analyst",
    "content_writer",
    "ux_researcher",
    "product_analyst",
    "market_researcher",
    "tech_researcher",
    "ops_researcher",
    "finance_researcher",
    "hr_researcher",
    "legal_researcher",
    "data_researcher",
    "security_researcher",
})


def get_leader_domains() -> list[str]:
    return list(LEADER_DOMAINS.keys())


def get_worker_types(domain: str) -> list[str]:
    if domain not in LEADER_DOMAINS:
        raise ValueError(f"Unknown domain: {domain}. Available: {get_leader_domains()}")
    return LEADER_DOMAINS[domain]["worker_types"]


def get_domain_description(domain: str) -> str:
    if domain not in LEADER_DOMAINS:
        raise ValueError(f"Unknown domain: {domain}. Available: {get_leader_domains()}")
    return LEADER_DOMAINS[domain]["description"]


def get_all_registered_workers() -> set[str]:
    """Return a set of all pre-defined worker type names across all domains."""
    workers: set[str] = set()
    for info in LEADER_DOMAINS.values():
        workers.update(info["worker_types"])
    return workers


def is_registered_worker(worker_type: str) -> bool:
    """Check if a worker type exists in the pre-defined registry."""
    return worker_type in get_all_registered_workers()


def get_worker_model(worker_domain: str) -> str | None:
    """Return model override for a worker type, or None for default."""
    return WORKER_MODEL_OVERRIDES.get(worker_domain)


def is_text_mode_worker(worker_domain: str) -> bool:
    """Return True if this worker type should use text output mode."""
    return worker_domain in WORKER_TEXT_MODE


# ── Dynamic registration (plugin system) ─────────────────


def register_domain(
    domain: str,
    description: str,
    worker_types: list[str],
) -> bool:
    """Register a new domain. Returns False if domain already exists."""
    if domain in LEADER_DOMAINS:
        return False
    LEADER_DOMAINS[domain] = {
        "description": description,
        "worker_types": worker_types,
    }
    return True


def register_worker_model_override(worker_name: str, model: str) -> None:
    """Register a model override for a worker type."""
    WORKER_MODEL_OVERRIDES[worker_name] = model


def register_text_mode_worker(worker_name: str) -> None:
    """Register a worker as text-mode. Requires mutable set."""
    global WORKER_TEXT_MODE
    WORKER_TEXT_MODE = WORKER_TEXT_MODE | frozenset({worker_name})


def get_parent_domain(worker_type: str) -> str:
    """Return the leader domain that owns this worker type, or '' if not found."""
    for domain, info in LEADER_DOMAINS.items():
        if worker_type in info["worker_types"]:
            return domain
    return ""
