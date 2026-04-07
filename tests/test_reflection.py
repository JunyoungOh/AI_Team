import json

from src.models.messages import ReflectionVerdict, WorkerResult


class TestReflectionSettings:
    def test_default_settings(self):
        from src.config.settings import Settings
        s = Settings()
        assert s.enable_worker_reflection is True
        assert s.reflection_model == "haiku"
        assert s.reflection_timeout == 15
        assert s.reflection_min_repair_budget == 60
        assert s.enable_deep_research_reflection is True


class TestReflectionVerdict:
    def test_pass_verdict(self):
        v = ReflectionVerdict(passed=True)
        assert v.passed is True
        assert v.critique == ""
        assert v.failed_criteria == []

    def test_fail_verdict(self):
        v = ReflectionVerdict(
            passed=False,
            critique="매출 데이터가 누락됨",
            failed_criteria=["경쟁사 매출 비교 데이터 제공"],
        )
        assert v.passed is False
        assert "매출" in v.critique
        assert len(v.failed_criteria) == 1


class TestWorkerResultReflectionFields:
    def test_default_values(self):
        r = WorkerResult(
            result_summary="test",
            deliverables=["d1"],
            completion_percentage=80,
        )
        assert r.reflection_passed is True
        assert r.reflection_repaired is False

    def test_explicit_values(self):
        r = WorkerResult(
            result_summary="test",
            deliverables=["d1"],
            completion_percentage=80,
            reflection_passed=False,
            reflection_repaired=True,
        )
        assert r.reflection_passed is False
        assert r.reflection_repaired is True

    def test_json_roundtrip_preserves_reflection(self):
        r = WorkerResult(
            result_summary="test",
            deliverables=["d1"],
            completion_percentage=80,
            reflection_passed=False,
            reflection_repaired=True,
        )
        data = r.model_dump()
        r2 = WorkerResult(**data)
        assert r2.reflection_passed is False
        assert r2.reflection_repaired is True


class TestExtractSuccessCriteria:
    def test_subtask_format(self):
        from src.graphs.nodes.worker_execution import _extract_success_criteria
        plan = json.dumps({
            "task_title": "Test",
            "objective": "Do X",
            "success_criteria": ["criterion A", "criterion B"],
        })
        assert _extract_success_criteria(plan) == ["criterion A", "criterion B"]

    def test_worker_plan_format_returns_empty(self):
        from src.graphs.nodes.worker_execution import _extract_success_criteria
        plan = json.dumps({
            "plan_title": "Test",
            "steps": ["step 1"],
            "expected_output": "output",
        })
        assert _extract_success_criteria(plan) == []

    def test_invalid_json_returns_empty(self):
        from src.graphs.nodes.worker_execution import _extract_success_criteria
        assert _extract_success_criteria("not json") == []


class TestNoFailureRegression:
    def test_no_regression(self):
        from src.graphs.nodes.worker_execution import _no_failure_regression
        original = WorkerResult(
            result_summary="데이터 수집 실패로 분석 불가",
            deliverables=[], completion_percentage=30,
        )
        repair = WorkerResult(
            result_summary="경쟁사 3사 매출 분석 완료",
            deliverables=["분석 결과"], completion_percentage=80,
        )
        assert _no_failure_regression(repair, original) is True

    def test_regression_detected(self):
        from src.graphs.nodes.worker_execution import _no_failure_regression
        original = WorkerResult(
            result_summary="경쟁사 분석 완료",
            deliverables=["결과"], completion_percentage=80,
        )
        repair = WorkerResult(
            result_summary="데이터 접근 실패로 사용할 수 없음",
            deliverables=[], completion_percentage=20,
        )
        assert _no_failure_regression(repair, original) is False


from src.utils.execution_tracker import ExecutionTracker


class TestReflectionMetrics:
    def test_reflection_result_in_summary(self):
        tracker = ExecutionTracker()
        tracker.worker_start("researcher", "r1", stage=0, model="sonnet")
        tracker.worker_end("researcher", tier=1, success=True)
        tracker.set_reflection_result("researcher", "pass")
        summary = tracker.summary()
        worker = summary["workers"][0]
        assert worker["reflection"] == "pass"

    def test_reflection_result_default(self):
        tracker = ExecutionTracker()
        tracker.worker_start("researcher", "r1")
        tracker.worker_end("researcher", tier=1, success=True)
        summary = tracker.summary()
        worker = summary["workers"][0]
        assert worker["reflection"] == ""


class TestReflectionIntegration:
    """Reflection 흐름의 주요 경로를 단위 테스트로 검증."""

    def test_skipped_for_dev_worker(self):
        """Dev 워커는 reflection 건너뜀."""
        from src.graphs.nodes.worker_execution import _reflect_on_result
        from src.models.messages import WorkerResult
        from src.utils.parallel import run_async

        worker = {"worker_domain": "backend_developer", "plan": "{}"}
        result = WorkerResult(
            result_summary="코드 작성 완료",
            deliverables=["main.py"],
            completion_percentage=100,
        )
        final, status = run_async(_reflect_on_result(worker, result, 600.0, 100.0))
        assert status == "skipped"
        assert final is result

    def test_pass_for_good_result_without_criteria(self):
        """criteria 없는 양호한 결과 → pass."""
        from src.graphs.nodes.worker_execution import _reflect_on_result
        from src.models.messages import WorkerResult
        from src.utils.parallel import run_async
        import json

        worker = {
            "worker_domain": "researcher",
            "plan": json.dumps({"plan_title": "Research", "steps": ["s1"]}),
        }
        result = WorkerResult(
            result_summary="A" * 200,
            deliverables=["finding 1"],
            completion_percentage=80,
        )
        final, status = run_async(_reflect_on_result(worker, result, 600.0, 100.0))
        assert status == "pass"

    def test_sanity_fail_with_no_budget_keeps_original(self):
        """sanity 실패 + time_budget 부족 → repair 건너뛰고 원본 유지."""
        from src.graphs.nodes.worker_execution import _reflect_on_result
        from src.models.messages import WorkerResult
        from src.utils.parallel import run_async
        import json

        worker = {
            "worker_domain": "researcher",
            "plan": json.dumps({"objective": "X", "success_criteria": ["c1"]}),
        }
        result = WorkerResult(
            result_summary="short",  # < 100 chars → sanity fail
            deliverables=[],
            completion_percentage=10,
        )
        # time_budget=70, tier1_elapsed=50 → repair_budget=20 < 60 → skip repair
        final, status = run_async(_reflect_on_result(worker, result, 70.0, 50.0))
        assert status == "fail_kept_original"
        assert final is result
        assert final.reflection_passed is False

    def test_skipped_for_content_writer(self):
        """content_writer는 _SEARCH_HEAVY_TYPES에 없으므로 reflection 건너뜀."""
        from src.graphs.nodes.worker_execution import _reflect_on_result
        from src.models.messages import WorkerResult
        from src.utils.parallel import run_async

        worker = {"worker_domain": "content_writer", "plan": "{}"}
        result = WorkerResult(
            result_summary="콘텐츠 작성",
            deliverables=["article.md"],
            completion_percentage=90,
        )
        final, status = run_async(_reflect_on_result(worker, result, 600.0, 100.0))
        assert status == "skipped"

    def test_skipped_when_disabled(self):
        """enable_worker_reflection=False일 때 전체 건너뜀."""
        from unittest.mock import patch, MagicMock
        from src.graphs.nodes.worker_execution import _reflect_on_result
        from src.models.messages import WorkerResult
        from src.utils.parallel import run_async

        worker = {"worker_domain": "researcher", "plan": "{}"}
        result = WorkerResult(
            result_summary="A" * 200,
            deliverables=["d1"],
            completion_percentage=80,
        )

        mock_settings = MagicMock()
        mock_settings.enable_worker_reflection = False

        with patch("src.graphs.nodes.worker_execution.get_settings", return_value=mock_settings):
            final, status = run_async(_reflect_on_result(worker, result, 600.0, 100.0))
        assert status == "skipped"
