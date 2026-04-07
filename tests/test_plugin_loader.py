"""Tests for domain plugin loader system."""

import os
import tempfile

import pytest

from src.config.plugin_schema import DomainConfig, WorkerConfig


# ── Schema validation tests ──────────────────────────────────


class TestPluginSchema:
    def test_valid_domain_config(self):
        config = DomainConfig(
            domain="sustainability",
            description="ESG analysis",
            workers=[
                WorkerConfig(name="esg_analyst", description="ESG metrics"),
            ],
        )
        assert config.domain == "sustainability"
        assert len(config.workers) == 1

    def test_invalid_model(self):
        with pytest.raises(ValueError, match="Invalid model"):
            WorkerConfig(name="test", model="gpt4")

    def test_invalid_domain_name(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            DomainConfig(
                domain="bad domain!",
                workers=[WorkerConfig(name="w1")],
            )

    def test_empty_workers_rejected(self):
        with pytest.raises(ValueError, match="at least one worker"):
            DomainConfig(domain="test", workers=[])

    def test_worker_with_tool_category(self):
        w = WorkerConfig(name="researcher", tool_category="research")
        assert w.tool_category == "research"
        assert w.tools == []

    def test_worker_with_explicit_tools(self):
        tools = ["mcp__brave-search__brave_web_search", "Read"]
        w = WorkerConfig(name="dev", tools=tools)
        assert w.tools == tools

    def test_default_values(self):
        w = WorkerConfig(name="test")
        assert w.model == "sonnet"
        assert w.text_mode is False
        assert w.persona is None


# ── Plugin loader tests ──────────────────────────────────────


class TestLoadPlugins:
    def test_load_from_empty_dir(self):
        from src.config.plugin_loader import load_plugins
        with tempfile.TemporaryDirectory() as tmpdir:
            configs = load_plugins(tmpdir)
            assert configs == []

    def test_load_skips_template_files(self):
        from src.config.plugin_loader import load_plugins
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Template file (starts with _)
            template = os.path.join(tmpdir, "_example.yaml")
            with open(template, "w") as f:
                yaml.dump({
                    "domain": "test",
                    "workers": [{"name": "w1"}],
                }, f)

            configs = load_plugins(tmpdir)
            assert configs == []

    def test_load_valid_plugin(self):
        from src.config.plugin_loader import load_plugins
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_file = os.path.join(tmpdir, "sustainability.yaml")
            with open(plugin_file, "w") as f:
                yaml.dump({
                    "domain": "sustainability",
                    "description": "ESG",
                    "workers": [
                        {"name": "esg_analyst", "tool_category": "research"},
                    ],
                }, f)

            configs = load_plugins(tmpdir)
            assert len(configs) == 1
            assert configs[0].domain == "sustainability"

    def test_load_invalid_plugin_skipped(self):
        from src.config.plugin_loader import load_plugins
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Invalid: no workers
            plugin_file = os.path.join(tmpdir, "bad.yaml")
            with open(plugin_file, "w") as f:
                yaml.dump({"domain": "bad", "workers": []}, f)

            configs = load_plugins(tmpdir)
            assert configs == []

    def test_nonexistent_dir_returns_empty(self):
        from src.config.plugin_loader import load_plugins
        configs = load_plugins("/nonexistent/path/plugins/")
        assert configs == []


# ── Merge tests ──────────────────────────────────────────────


class TestMergePlugins:
    def test_merge_new_domain(self):
        from src.config.agent_registry import LEADER_DOMAINS
        from src.config.plugin_loader import merge_plugins

        # Ensure domain doesn't exist
        test_domain = "test_merge_plugin_domain"
        LEADER_DOMAINS.pop(test_domain, None)

        try:
            config = DomainConfig(
                domain=test_domain,
                description="Test domain",
                workers=[
                    WorkerConfig(name="test_worker_abc", tool_category="general"),
                ],
            )
            results = merge_plugins([config])
            assert results[test_domain] == "registered"
            assert test_domain in LEADER_DOMAINS
            assert "test_worker_abc" in LEADER_DOMAINS[test_domain]["worker_types"]
        finally:
            # Cleanup
            LEADER_DOMAINS.pop(test_domain, None)

    def test_merge_existing_domain_skipped(self):
        from src.config.plugin_loader import merge_plugins

        # "engineering" already exists
        config = DomainConfig(
            domain="engineering",
            description="Should not overwrite",
            workers=[WorkerConfig(name="custom_eng")],
        )
        results = merge_plugins([config])
        assert results["engineering"] == "skipped_exists"

    def test_merge_model_override(self):
        from src.config.agent_registry import (
            LEADER_DOMAINS,
            WORKER_MODEL_OVERRIDES,
        )
        from src.config.plugin_loader import merge_plugins

        test_domain = "test_model_override_domain"
        LEADER_DOMAINS.pop(test_domain, None)

        try:
            config = DomainConfig(
                domain=test_domain,
                description="Test",
                workers=[
                    WorkerConfig(name="test_opus_worker", model="opus"),
                ],
            )
            merge_plugins([config])
            assert WORKER_MODEL_OVERRIDES.get("test_opus_worker") == "opus"
        finally:
            LEADER_DOMAINS.pop(test_domain, None)
            WORKER_MODEL_OVERRIDES.pop("test_opus_worker", None)
