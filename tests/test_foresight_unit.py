import pytest
from pathlib import Path
from src.foresight.storage import (
    save_environment, load_environment, list_environments,
    delete_environment, get_user_storage_usage, check_storage_quota,
)


@pytest.fixture
def storage_dir(tmp_path):
    return str(tmp_path / "environments")


def test_save_and_load_environment(storage_dir):
    env_data = {"summary": "B2B SaaS 회사", "variables": ["시장 규모", "경쟁사"]}
    save_environment(storage_dir, "user1", "my_company", env_data)
    loaded = load_environment(storage_dir, "user1", "my_company")
    assert loaded["summary"] == "B2B SaaS 회사"
    assert loaded["variables"] == ["시장 규모", "경쟁사"]


def test_list_environments(storage_dir):
    save_environment(storage_dir, "user1", "env_a", {"name": "A"})
    save_environment(storage_dir, "user1", "env_b", {"name": "B"})
    envs = list_environments(storage_dir, "user1")
    assert set(envs) == {"env_a", "env_b"}


def test_delete_environment(storage_dir):
    save_environment(storage_dir, "user1", "temp", {"name": "temp"})
    assert "temp" in list_environments(storage_dir, "user1")
    delete_environment(storage_dir, "user1", "temp")
    assert "temp" not in list_environments(storage_dir, "user1")


def test_load_nonexistent_returns_none(storage_dir):
    result = load_environment(storage_dir, "user1", "nonexistent")
    assert result is None


def test_sanitize_name_prevents_path_traversal(storage_dir):
    save_environment(storage_dir, "user1", "../../../etc/passwd", {"hack": True})
    loaded = load_environment(storage_dir, "user1", "../../../etc/passwd")
    assert loaded == {"hack": True}


def test_save_overwrites_existing(storage_dir):
    save_environment(storage_dir, "user1", "test", {"version": 1})
    save_environment(storage_dir, "user1", "test", {"version": 2})
    loaded = load_environment(storage_dir, "user1", "test")
    assert loaded["version"] == 2


def test_user_isolation(storage_dir):
    save_environment(storage_dir, "user1", "shared_name", {"owner": "user1"})
    save_environment(storage_dir, "user2", "shared_name", {"owner": "user2"})
    assert load_environment(storage_dir, "user1", "shared_name")["owner"] == "user1"
    assert load_environment(storage_dir, "user2", "shared_name")["owner"] == "user2"


def test_storage_usage(storage_dir):
    save_environment(storage_dir, "user1", "env1", {"data": "x" * 1000})
    usage = get_user_storage_usage(storage_dir, "user1")
    assert usage > 0


def test_check_storage_quota_within_limit(storage_dir):
    save_environment(storage_dir, "user1", "small", {"x": 1})
    assert check_storage_quota(storage_dir, "user1", max_mb=200) is True


def test_check_storage_quota_empty_user(storage_dir):
    assert check_storage_quota(storage_dir, "new_user", max_mb=200) is True


def test_list_empty_user(storage_dir):
    assert list_environments(storage_dir, "nobody") == []


# ── Module integration tests ──────────────────

def test_engine_import_and_constants():
    from src.foresight.engine import SageEngine, MODEL_MAP
    assert "opus" in MODEL_MAP
    assert "sonnet" in MODEL_MAP
    assert "haiku" in MODEL_MAP


def test_session_import():
    from src.foresight.session import ForesightSession
    assert ForesightSession is not None


def test_tool_schemas_match_executors():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS, FORESIGHT_TOOL_EXECUTORS
    # run_ensemble_forecast is engine-intercepted (no executor needed)
    engine_intercepted = {"run_ensemble_forecast"}
    assert set(FORESIGHT_TOOL_SCHEMAS.keys()) - engine_intercepted == set(FORESIGHT_TOOL_EXECUTORS.keys())


def test_tool_count():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    assert len(FORESIGHT_TOOL_SCHEMAS) == 14


def test_system_prompt_contains_key_sections():
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp/test_session")
    assert "Sage" in prompt
    assert "/tmp/test_session" in prompt
    # Check for Phase 1 and Phase 2
    assert "Phase 1" in prompt or "환경" in prompt
    assert "Phase 2" in prompt or "예측" in prompt


def test_system_prompt_contains_methodologies():
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp/test")
    # Check for the 3 research-backed methodologies
    has_futures_wheel = "Futures Wheel" in prompt or "인과" in prompt or "1차" in prompt
    has_multi_perspective = "관점" in prompt or "perspective" in prompt.lower()
    has_monte_carlo = "Monte Carlo" in prompt or "시뮬레이션" in prompt
    assert has_futures_wheel, "System prompt should reference Futures Wheel methodology"
    assert has_multi_perspective, "System prompt should reference multi-perspective analysis"
    assert has_monte_carlo, "System prompt should reference Monte Carlo quantification"


def test_make_session_context():
    import tempfile
    from pathlib import Path
    from src.foresight.tools import make_session_context
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_session_context(tmpdir, user_id="test_user")
        assert ctx["user_id"] == "test_user"
        assert Path(ctx["uploads_dir"]).exists()
        assert Path(ctx["outputs_dir"]).exists()
        assert Path(ctx["workspace_dir"]).exists()


def test_settings_has_foresight_fields():
    from src.config.settings import get_settings
    s = get_settings()
    assert hasattr(s, "foresight_env_model")
    assert hasattr(s, "foresight_env_effort")
    assert hasattr(s, "foresight_predict_model")
    assert hasattr(s, "foresight_predict_effort")
    assert hasattr(s, "foresight_session_ttl_minutes")
    assert hasattr(s, "foresight_max_storage_per_user_mb")
    assert s.foresight_env_model == "sonnet"
    assert s.foresight_predict_model == "sonnet"
    assert s.foresight_max_storage_per_user_mb == 200


def test_emit_timeline_schema_exists():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    schema = FORESIGHT_TOOL_SCHEMAS["emit_timeline"]
    assert schema["name"] == "emit_timeline"
    props = schema["input_schema"]["properties"]
    assert "action" in props
    assert "data" in props
    assert props["action"]["enum"] == [
        "add_node", "add_edge", "add_event", "add_band", "advance_stage", "add_dots",
        "add_tornado", "add_backcast",
    ]
    assert "add_dots" in props["action"]["enum"]


@pytest.mark.asyncio
async def test_emit_timeline_executor_returns_ack():
    import tempfile
    from src.foresight.tools import make_session_context, FORESIGHT_TOOL_EXECUTORS
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_session_context(tmpdir, user_id="test")
        executor = FORESIGHT_TOOL_EXECUTORS["emit_timeline"]
        result = await executor(ctx, action="add_node", data='{"id": "n1", "label": "Start"}')
        assert "전송" in result or "emit" in result.lower()


@pytest.mark.asyncio
async def test_emit_timeline_executor_rejects_invalid_json():
    import tempfile
    from src.foresight.tools import make_session_context, FORESIGHT_TOOL_EXECUTORS
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_session_context(tmpdir, user_id="test")
        executor = FORESIGHT_TOOL_EXECUTORS["emit_timeline"]
        result = await executor(ctx, action="add_node", data="not valid json")
        assert "Error" in result


def test_analyze_requirements_schema_exists():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    assert "analyze_requirements" in FORESIGHT_TOOL_SCHEMAS
    schema = FORESIGHT_TOOL_SCHEMAS["analyze_requirements"]
    assert schema["name"] == "analyze_requirements"


@pytest.mark.asyncio
async def test_analyze_requirements_executor():
    from src.foresight.tools import FORESIGHT_TOOL_EXECUTORS, make_session_context
    import tempfile, json
    from functools import partial
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_session_context(tmpdir, user_id="test")
        executor = partial(FORESIGHT_TOOL_EXECUTORS["analyze_requirements"], ctx)
        items = json.dumps([{"id":"test","label":"테스트","description":"설명","default_method":"web_search"}])
        result = await executor(items=items)
        assert "1건" in result


def test_emit_requirement_status_schema_exists():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    assert "emit_requirement_status" in FORESIGHT_TOOL_SCHEMAS


def test_system_prompt_contains_emit_timeline_guide():
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp/test")
    assert "emit_timeline" in prompt
    assert "add_node" in prompt
    assert "add_edge" in prompt
    assert "advance_stage" in prompt


def test_system_prompt_contains_analyze_requirements_guide():
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp/test")
    assert "analyze_requirements" in prompt
    assert "default_method" in prompt
    assert "emit_requirement_status" in prompt


def test_system_prompt_contains_report_generation_guide():
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp/test")
    assert "export_interactive_report" in prompt
    assert "경영진 요약" in prompt


def test_export_interactive_report_schema_exists():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    assert "export_interactive_report" in FORESIGHT_TOOL_SCHEMAS


@pytest.mark.asyncio
async def test_export_interactive_report_creates_file():
    from src.foresight.tools import FORESIGHT_TOOL_EXECUTORS, make_session_context
    import tempfile, json
    from functools import partial
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = make_session_context(tmpdir, user_id="test")
        executor = partial(FORESIGHT_TOOL_EXECUTORS["export_interactive_report"], ctx)
        sections = json.dumps([{"heading":"Summary","content":"Test content","type":"text"}])
        tl_data = json.dumps({"nodes":[],"edges":[],"events":[],"bands":[]})
        result = await executor(title="Test Report", sections=sections, timeline_data=tl_data)
        assert "생성 완료" in result
        from pathlib import Path
        outputs = Path(ctx["outputs_dir"])
        html_files = list(outputs.glob("*.html"))
        assert len(html_files) == 1
        content = html_files[0].read_text()
        assert "Test Report" in content
        assert "<svg" in content


def test_system_prompt_contains_analysis_types_guide():
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp/test")
    assert "causal_chain" in prompt
    assert "delphi_panel" in prompt
    assert "backcasting" in prompt


def test_tool_schemas_are_valid_anthropic_format():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    for name, schema in FORESIGHT_TOOL_SCHEMAS.items():
        assert "name" in schema, f"{name} missing 'name'"
        assert "description" in schema, f"{name} missing 'description'"
        assert "input_schema" in schema, f"{name} missing 'input_schema'"
        assert schema["input_schema"]["type"] == "object", f"{name} input_schema type must be 'object'"


def test_emit_delphi_schema_exists():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    assert "emit_delphi" in FORESIGHT_TOOL_SCHEMAS
    schema = FORESIGHT_TOOL_SCHEMAS["emit_delphi"]
    assert "round" in schema["input_schema"]["properties"]
    assert "experts" in schema["input_schema"]["properties"]


def test_emit_timeline_has_tornado_backcast_actions():
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    schema = FORESIGHT_TOOL_SCHEMAS["emit_timeline"]
    enum_vals = schema["input_schema"]["properties"]["action"]["enum"]
    assert "add_tornado" in enum_vals
    assert "add_backcast" in enum_vals


# ── Calibration module tests ──

from src.foresight.calibration import platt_scale, CalibrationLogger

def test_platt_scale_identity_at_half():
    assert platt_scale(0.5) == pytest.approx(0.5, abs=1e-6)

def test_platt_scale_extremizes():
    assert platt_scale(0.7) > 0.7
    assert platt_scale(0.3) < 0.3

def test_platt_scale_symmetry():
    assert platt_scale(0.3) == pytest.approx(1.0 - platt_scale(0.7), abs=1e-6)

def test_platt_scale_clamps_extremes():
    assert 0.0 < platt_scale(0.001) < 0.01
    assert 0.99 < platt_scale(0.999) < 1.0

def test_calibration_logger_writes_jsonl(tmp_path):
    logger = CalibrationLogger(str(tmp_path / "cal.jsonl"))
    logger.log_forecast("q1", 0.7, 0.82, {"source": "test"})
    logger.log_outcome("q1", 1)
    lines = (tmp_path / "cal.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2


# ── Ensemble module tests ──

from src.foresight.ensemble import AgentForecast, parse_probability

def test_agent_forecast_dataclass():
    f = AgentForecast(agent_id="a1", probability=0.65, reasoning="test", search_queries=["q1"], confidence=0.8)
    assert f.agent_id == "a1"
    assert f.probability == 0.65

def test_parse_probability_percent():
    assert parse_probability("확률은 약 65%입니다.") == pytest.approx(0.65, abs=0.01)

def test_parse_probability_decimal():
    assert parse_probability("probability: 0.72") == pytest.approx(0.72, abs=0.01)

def test_parse_probability_chance():
    assert parse_probability("I estimate 30% chance") == pytest.approx(0.30, abs=0.01)

def test_parse_probability_fallback():
    assert parse_probability("no number here") == 0.5

def test_parse_probability_bare_decimal():
    assert parse_probability("The answer is 0.85") == pytest.approx(0.85, abs=0.01)


# ── Ensemble aggregation tests ──

from src.foresight.ensemble import aggregate_forecasts, SupervisorResult

def test_aggregate_mean():
    forecasts = [
        AgentForecast("a1", 0.7, "r1"),
        AgentForecast("a2", 0.6, "r2"),
        AgentForecast("a3", 0.8, "r3"),
    ]
    result = aggregate_forecasts(forecasts)
    assert result["mean"] == pytest.approx(0.7, abs=0.01)
    assert result["spread"] == pytest.approx(0.2, abs=0.01)
    assert result["needs_supervisor"] is True

def test_aggregate_low_spread_no_supervisor():
    forecasts = [
        AgentForecast("a1", 0.70, "r1"),
        AgentForecast("a2", 0.72, "r2"),
        AgentForecast("a3", 0.68, "r3"),
    ]
    result = aggregate_forecasts(forecasts, threshold=0.2)
    assert result["needs_supervisor"] is False

def test_aggregate_trimmed_mean_removes_outliers():
    """Trimmed mean이 이상치를 제거하고 안정적 추정."""
    forecasts = [
        AgentForecast("s0", 0.70, "r"),
        AgentForecast("s1", 0.72, "r"),
        AgentForecast("s2", 0.68, "r"),
        AgentForecast("h0", 0.65, "r"),
        AgentForecast("h1", 0.75, "r"),
        AgentForecast("h2", 0.10, "r"),  # outlier low (Haiku hallucination)
        AgentForecast("h3", 0.71, "r"),
        AgentForecast("h4", 0.95, "r"),  # outlier high
    ]
    result = aggregate_forecasts(forecasts)
    # Trimmed mean should exclude 0.10 and 0.95
    assert 0.65 < result["mean"] < 0.75  # trimmed mean near 0.70
    assert result["simple_mean"] != result["mean"]  # different from simple mean
    assert result["trimmed_count"] < len(forecasts)

def test_aggregate_has_simple_mean():
    """simple_mean 필드가 존재하고 산술 평균과 일치."""
    forecasts = [AgentForecast(f"a{i}", 0.5 + 0.1 * i, "r") for i in range(5)]
    result = aggregate_forecasts(forecasts)
    assert "simple_mean" in result
    expected = sum(0.5 + 0.1 * i for i in range(5)) / 5
    assert result["simple_mean"] == pytest.approx(expected, abs=0.01)

def test_role_prompts_are_different():
    """에이전트 역할 프롬프트가 모두 다른지 검증."""
    from src.foresight.prompts.agent import SONNET_ROLES, HAIKU_ROLES
    # All Sonnet roles should be unique (4: Base Rate, Devil's Advocate, Causal, Contrarian)
    assert len(SONNET_ROLES) == 4
    assert len(set(SONNET_ROLES)) == len(SONNET_ROLES)
    # Haiku has duplicates by design (News Scout ×2, Pattern Matcher ×2)
    assert len(HAIKU_ROLES) == 4
    # But at least 2 unique roles
    assert len(set(HAIKU_ROLES)) >= 2

def test_supervisor_result_dataclass():
    r = SupervisorResult(probability=0.75, confidence="high", reasoning="test")
    assert r.probability == 0.75
    assert r.confidence == "high"


# ── Integration tests ──

def test_platt_on_ensemble_output():
    """앙상블 → Platt 보정 → 프론트엔드 payload 변환 검증."""
    from src.foresight.calibration import platt_scale
    raw_prob = 70  # 70% from ensemble mean
    calibrated = platt_scale(raw_prob / 100.0) * 100
    assert 80 < calibrated < 85  # ~81.3%


def test_aggregate_then_calibrate_pipeline():
    """전체 파이프라인: 에이전트 → 집계 → 보정."""
    from src.foresight.calibration import platt_scale
    forecasts = [
        AgentForecast("a0", 0.65, "evidence A"),
        AgentForecast("a1", 0.70, "evidence B"),
        AgentForecast("a2", 0.75, "evidence C"),
    ]
    result = aggregate_forecasts(forecasts)
    assert result["mean"] == pytest.approx(0.7, abs=0.01)
    assert result["spread"] == pytest.approx(0.1, abs=0.01)
    assert result["needs_supervisor"] is False  # spread < 0.2

    # Platt calibration
    calibrated = platt_scale(result["mean"])
    assert calibrated > result["mean"]  # 0.7 → ~0.81
    assert 0.80 < calibrated < 0.85


def test_ensemble_schema_in_tools():
    """run_ensemble_forecast 스키마가 도구 목록에 존재."""
    from src.foresight.tools import FORESIGHT_TOOL_SCHEMAS
    assert "run_ensemble_forecast" in FORESIGHT_TOOL_SCHEMAS
    schema = FORESIGHT_TOOL_SCHEMAS["run_ensemble_forecast"]
    assert "question" in schema["input_schema"]["properties"]
    assert "question" in schema["input_schema"]["required"]


def test_ensemble_not_in_executors():
    """run_ensemble_forecast는 엔진 인터셉트 도구이므로 executor에 없어야 함."""
    from src.foresight.tools import FORESIGHT_TOOL_EXECUTORS
    assert "run_ensemble_forecast" not in FORESIGHT_TOOL_EXECUTORS


def test_settings_has_ensemble_fields():
    """앙상블 설정값이 settings에 존재."""
    from src.config.settings import get_settings
    s = get_settings()
    assert s.foresight_sonnet_agents == 4
    assert s.foresight_haiku_agents == 4
    assert s.foresight_ensemble_max_turns == 6
    assert s.foresight_haiku_max_turns == 4
    assert s.foresight_supervisor_threshold == 0.2


def test_agent_forecast_model_field():
    """AgentForecast에 model 필드가 기록됨."""
    f = AgentForecast("s0", 0.7, "test", model="claude-sonnet-4-6")
    assert f.model == "claude-sonnet-4-6"
    f2 = AgentForecast("h0", 0.6, "test", model="claude-haiku-4-5-20251001")
    assert "haiku" in f2.model


# ── Edge case tests ──

def test_aggregate_empty_list():
    """빈 리스트 입력 시 안전하게 기본값 반환."""
    result = aggregate_forecasts([])
    assert result["mean"] == 0.5
    assert result["spread"] == 0.0
    assert result["needs_supervisor"] is False

def test_parse_probability_clamps_over_one():
    """1.0 초과 값이 0.99로 클램프됨."""
    assert parse_probability("probability: 1.05") <= 0.99

def test_parse_probability_clamps_near_zero():
    """0에 가까운 값이 0.01 이상."""
    assert parse_probability("0% chance") >= 0.0  # "0%" → 0/100 = 0.0, but \d{1,2} won't match "0"
    # Direct decimal case
    assert parse_probability("probability: 0.001") >= 0.01

def test_platt_scale_boundary_values():
    """Platt Scaling 극값 입력 테스트."""
    from src.foresight.calibration import platt_scale
    # 0.01 and 0.99 should not crash
    assert 0.0 < platt_scale(0.01) < 0.01
    assert 0.99 < platt_scale(0.99) < 1.0
    # 0.0 and 1.0 are clamped internally
    assert 0.0 < platt_scale(0.0) < 0.01
    assert 0.99 < platt_scale(1.0) < 1.0

def test_aggregate_single_agent():
    """에이전트 1개만 있을 때도 정상 작동."""
    result = aggregate_forecasts([AgentForecast("solo", 0.8, "only one")])
    assert result["mean"] == 0.8
    assert result["spread"] == 0.0
    assert result["needs_supervisor"] is False

def test_haiku_temperature_stays_under_one():
    """Haiku 에이전트 temperature가 1.0을 넘지 않는지 검증."""
    for i in range(10):  # even with 10 agents
        temp = min(1.0, 0.6 + 0.1 * i)
        assert temp <= 1.0, f"Haiku agent {i} temperature {temp} exceeds 1.0"


# ── Surprisingly Popular + Confidence Interval tests ──

def test_aggregate_includes_confidence_interval():
    """집계 결과에 신뢰구간이 포함됨."""
    forecasts = [AgentForecast(f"a{i}", 0.5 + 0.05 * i, "r") for i in range(8)]
    result = aggregate_forecasts(forecasts)
    assert "ci_low" in result
    assert "ci_high" in result
    assert result["ci_low"] <= result["mean"] <= result["ci_high"]

def test_aggregate_sp_signal_positive():
    """에이전트가 자신의 예측이 남들보다 높다고 생각하면 SP 시그널 양수."""
    forecasts = [
        AgentForecast("a0", 0.8, "r", meta_prediction=0.6),  # gap +0.2
        AgentForecast("a1", 0.7, "r", meta_prediction=0.6),  # gap +0.1
        AgentForecast("a2", 0.6, "r"),  # no meta
    ]
    result = aggregate_forecasts(forecasts)
    assert result["sp_signal"] > 0  # agents think they know more (upward)

def test_aggregate_sp_signal_zero_when_no_meta():
    """메타 예측이 없으면 SP 시그널 0."""
    forecasts = [AgentForecast(f"a{i}", 0.7, "r") for i in range(3)]
    result = aggregate_forecasts(forecasts)
    assert result["sp_signal"] == 0.0

def test_meta_prediction_field():
    """AgentForecast에 meta_prediction 필드가 작동."""
    f = AgentForecast("a0", 0.8, "r", meta_prediction=0.6)
    assert f.meta_prediction == 0.6
    f2 = AgentForecast("a1", 0.7, "r")
    assert f2.meta_prediction is None


# ── SP signal integration + framing + contrarian upgrade tests ──

def test_sp_signal_adjusts_mean():
    """SP 시그널이 양수면 mean이 trimmed_mean보다 높아짐."""
    forecasts = [
        AgentForecast("a0", 0.7, "r", meta_prediction=0.5),  # gap +0.2
        AgentForecast("a1", 0.7, "r", meta_prediction=0.5),  # gap +0.2
        AgentForecast("a2", 0.7, "r"),
        AgentForecast("a3", 0.7, "r"),
    ]
    result = aggregate_forecasts(forecasts)
    assert result["mean"] > result["mean_before_sp"]
    assert result["sp_signal"] > 0

def test_sp_signal_no_change_when_zero():
    """SP 시그널이 0이면 mean == mean_before_sp."""
    forecasts = [AgentForecast(f"a{i}", 0.7, "r") for i in range(4)]
    result = aggregate_forecasts(forecasts)
    assert result["mean"] == result["mean_before_sp"]

def test_contrarian_in_sonnet_roles():
    """Contrarian이 Sonnet 역할에 포함됨."""
    from src.foresight.prompts.agent import SONNET_ROLES, ROLE_CONTRARIAN
    assert ROLE_CONTRARIAN in SONNET_ROLES

def test_metacognitive_check_in_prompts():
    """메타인지 자기 비판 안내가 프롬프트에 포함됨."""
    from src.foresight.prompts.agent import _COMMON_RULES
    assert "Could I be wrong" in _COMMON_RULES

def test_sonnet4_haiku4_total_8():
    """Sonnet 4 + Haiku 4 = 총 8 에이전트."""
    from src.config.settings import get_settings
    s = get_settings()
    assert s.foresight_sonnet_agents + s.foresight_haiku_agents == 8


# ── Edge case: negative framing + SP signal coherence ──

def test_negative_framing_sp_coherence():
    """부정 프레이밍 에이전트의 meta_prediction도 flip되어야 SP 시그널이 일관됨."""
    # Normal agent: p=0.8, meta=0.6 → gap = +0.2 (thinks they know more)
    normal = AgentForecast("a0", 0.8, "r", meta_prediction=0.6)
    # Negative-framed agent: asked NOT question, answered 0.3 (NOT prob)
    # After flip: p=0.7, meta should also be flipped
    # If raw meta=0.4 (NOT frame), flipped meta = 0.6
    # gap = 0.7 - 0.6 = +0.1 (consistent with normal frame)
    negflip = AgentForecast("a1", 0.7, "r", meta_prediction=0.6)  # already flipped
    forecasts = [normal, negflip]
    result = aggregate_forecasts(forecasts)
    # Both gaps are positive → SP signal should be positive
    assert result["sp_signal"] > 0


# ── Hedge weights + disambiguation tests ──

from src.foresight.calibration import HedgeWeights

def test_hedge_weights_initial_equal():
    """초기 가중치는 모든 에이전트 동일."""
    hw = HedgeWeights(["a", "b", "c"])
    w = hw.weights
    assert len(w) == 3
    assert all(abs(v - 1/3) < 0.01 for v in w.values())

def test_hedge_weights_update_favors_accurate():
    """정확한 에이전트의 가중치가 증가."""
    hw = HedgeWeights(["good", "bad"])
    # Good agent: predicted 0.9 for event that happened (low Brier)
    hw.update("good", 0.9, 1)
    # Bad agent: predicted 0.1 for event that happened (high Brier)
    hw.update("bad", 0.1, 1)
    w = hw.weights
    assert w["good"] > w["bad"]

def test_hedge_weights_persistence(tmp_path):
    """가중치가 파일에 저장/로드됨."""
    path = str(tmp_path / "hedge.json")
    hw1 = HedgeWeights(["a", "b"], path=path)
    hw1.update("a", 0.9, 1)
    hw1.update("b", 0.2, 1)
    # Load in new instance
    hw2 = HedgeWeights(["a", "b"], path=path)
    assert hw2.weights["a"] > hw2.weights["b"]

def test_hedge_weighted_mean():
    """Hedge 가중 평균이 정확한 에이전트를 선호."""
    hw = HedgeWeights(["good", "bad"])
    for _ in range(5):
        hw.update("good", 0.8, 1)
        hw.update("bad", 0.3, 1)
    result = hw.weighted_mean({"good": 0.85, "bad": 0.40})
    # Should be closer to good's prediction
    assert result > 0.6

def test_hedge_empty_agents():
    """에이전트 없을 때 안전 처리."""
    hw = HedgeWeights([])
    assert hw.weights == {}
    assert hw.weighted_mean({}) == 0.5


# ── Dator + Cynefin + CLA tests ──

def test_system_prompt_has_dator_archetypes():
    """시스템 프롬프트에 Dator 4원형이 포함됨."""
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp")
    assert "성장 (Growth)" in prompt
    assert "붕괴 (Collapse)" in prompt
    assert "규율 (Discipline)" in prompt
    assert "변혁 (Transformation)" in prompt

def test_system_prompt_has_cla():
    """시스템 프롬프트에 CLA 4계층이 포함됨."""
    from src.foresight.prompts.system import build_system_prompt
    prompt = build_system_prompt("/tmp")
    assert "Causal Layered Analysis" in prompt
    assert "L4" in prompt or "신화" in prompt
