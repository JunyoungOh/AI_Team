"""Tests for model selection logic — All-Sonnet policy.

All workers use sonnet for consistent speed and reliability.
Opus is reserved for CEO/Leader planning only.
WORKER_MODEL_OVERRIDES can still override per-worker if needed.
"""

from unittest.mock import MagicMock, patch

from src.utils.model_selector import select_worker_model


def _mock_settings(**overrides):
    s = MagicMock()
    s.enable_adaptive_model = overrides.get("enable_adaptive_model", False)
    s.worker_model = overrides.get("worker_model", "sonnet")
    s.complexity_model_map = overrides.get("complexity_model_map", None)
    return s


class TestAllSonnetPolicy:
    """All workers return sonnet regardless of complexity or traits."""

    @patch("src.utils.model_selector.get_settings")
    def test_architect_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        assert select_worker_model("architect", "low") == "sonnet"

    @patch("src.utils.model_selector.get_settings")
    def test_fact_checker_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        assert select_worker_model("fact_checker", "high") == "sonnet"

    @patch("src.utils.model_selector.get_settings")
    def test_designer_high_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        assert select_worker_model("designer", "high") == "sonnet"

    @patch("src.utils.model_selector.get_settings")
    def test_designer_low_no_tools_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        result = select_worker_model("designer", "low", has_tools=False)
        assert result == "sonnet"

    @patch("src.utils.model_selector.get_settings")
    def test_designer_low_with_tools_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        result = select_worker_model("designer", "low", has_tools=True)
        assert result == "sonnet"

    @patch("src.utils.model_selector.get_settings")
    def test_medium_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        result = select_worker_model("designer", "medium")
        assert result == "sonnet"


class TestDependencyDoesNotUpgrade:
    """Dependencies and stages do not change model — always sonnet."""

    @patch("src.utils.model_selector.get_settings")
    def test_high_with_deps_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        result = select_worker_model(
            "designer", "high", has_dependencies=True, stage=1
        )
        assert result == "sonnet"

    @patch("src.utils.model_selector.get_settings")
    def test_low_later_stage_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        result = select_worker_model(
            "researcher", "low", has_tools=False, has_dependencies=True, stage=1
        )
        assert result == "sonnet"

    @patch("src.utils.model_selector.get_settings")
    def test_medium_later_stage_stays_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        result = select_worker_model(
            "researcher", "medium", has_dependencies=True, stage=2
        )
        assert result == "sonnet"


class TestHardcodedOverrideStillWorks:
    """WORKER_MODEL_OVERRIDES (if set) still takes priority."""

    @patch("src.utils.model_selector.WORKER_MODEL_OVERRIDES", {"special_worker": "opus"})
    @patch("src.utils.model_selector.get_settings")
    def test_override_wins(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        assert select_worker_model("special_worker", "low") == "opus"

    @patch("src.utils.model_selector.get_settings")
    def test_no_override_returns_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        assert select_worker_model("designer", "high") == "sonnet"


class TestUnknownComplexity:
    """Unknown complexity strings still return sonnet."""

    @patch("src.utils.model_selector.get_settings")
    def test_unknown_complexity_defaults_sonnet(self, mock_gs):
        mock_gs.return_value = _mock_settings()
        result = select_worker_model("designer", "unknown_level")
        assert result == "sonnet"
