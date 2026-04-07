"""Load domain plugins from YAML files and merge into existing registries.

Plugins are loaded from the configured plugin directory (default: domains/).
Each .yaml file defines one domain with its workers, tools, and personas.
Existing hardcoded domains are never overwritten — conflicts are logged and skipped.
"""

from __future__ import annotations

from pathlib import Path

from src.config.plugin_schema import DomainConfig, WorkerConfig
from src.utils.logging import get_logger

_logger = get_logger(agent_id="plugin_loader")


class PluginLoadError(Exception):
    """Raised when a plugin YAML file fails validation."""


def load_plugins(plugin_dir: str = "domains/") -> list[DomainConfig]:
    """Load and validate all .yaml/.yml files from plugin directory.

    Returns list of validated DomainConfig objects.
    Files starting with _ are treated as templates and skipped.
    Invalid files are logged and skipped (non-fatal).
    """
    try:
        import yaml
    except ImportError:
        _logger.info("pyyaml_not_installed_skipping_plugins")
        return []

    plugin_path = Path(plugin_dir)
    if not plugin_path.exists():
        _logger.debug("plugin_dir_not_found", path=str(plugin_path))
        return []

    configs: list[DomainConfig] = []

    for yaml_file in sorted(plugin_path.glob("*.y*ml")):
        # Skip template files
        if yaml_file.stem.startswith("_"):
            continue

        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            if not raw or not isinstance(raw, dict):
                _logger.warning("plugin_empty_or_invalid", file=yaml_file.name)
                continue

            config = DomainConfig(**raw)
            configs.append(config)
            _logger.info(
                "plugin_loaded",
                domain=config.domain,
                workers=len(config.workers),
                file=yaml_file.name,
            )
        except Exception as e:
            _logger.warning(
                "plugin_load_failed",
                file=yaml_file.name,
                error=str(e)[:200],
            )

    return configs


def merge_plugins(configs: list[DomainConfig]) -> dict[str, str]:
    """Merge validated plugin configs into existing registries.

    Returns dict of {domain: status} for reporting.
    Status values: "registered", "skipped_exists", "error".
    """
    from src.config.agent_registry import (
        register_domain,
        register_text_mode_worker,
        register_worker_model_override,
    )
    from src.config.personas import register_custom_persona
    from src.tools import get_tools_for_category, register_domain_tools

    results: dict[str, str] = {}

    for config in configs:
        domain = config.domain

        # Register domain
        worker_names = [w.name for w in config.workers]
        registered = register_domain(domain, config.description, worker_names)

        if not registered:
            _logger.warning("plugin_domain_exists_skipped", domain=domain)
            results[domain] = "skipped_exists"
            continue

        # Register each worker's tools, model, persona, text_mode
        for worker in config.workers:
            # Tools: explicit tools list > tool_category > default
            if worker.tools:
                register_domain_tools(worker.name, worker.tools)
            elif worker.tool_category:
                register_domain_tools(worker.name, get_tools_for_category(worker.tool_category))

            # Model override (only if not default sonnet)
            if worker.model != "sonnet":
                register_worker_model_override(worker.name, worker.model)

            # Text mode
            if worker.text_mode:
                register_text_mode_worker(worker.name)

            # Persona
            if worker.persona:
                register_custom_persona(worker.name, worker.persona)

        # Approval threshold (update settings domain_approval_thresholds)
        if config.approval_threshold != 7:
            try:
                from src.config.settings import get_settings
                settings = get_settings()
                settings.domain_approval_thresholds[domain] = config.approval_threshold
            except Exception:
                pass

        results[domain] = "registered"
        _logger.info(
            "plugin_merged",
            domain=domain,
            workers=len(config.workers),
        )

    return results


def load_and_merge_plugins(plugin_dir: str = "domains/") -> dict[str, str]:
    """Convenience: load + merge in one call. Used at app startup."""
    configs = load_plugins(plugin_dir)
    if not configs:
        return {}
    return merge_plugins(configs)
