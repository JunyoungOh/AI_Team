"""Structured logging configuration using structlog."""

from __future__ import annotations

import logging

import structlog

from src.config.settings import get_settings

# structlog log levels: 0=DEBUG, 10=DEBUG, 20=INFO, 30=WARNING
_LEVEL_MAP = {"debug": 0, "info": 20, "warning": 30, "error": 40}


def setup_logging(*, json_output: bool = False) -> None:
    """Configure structlog for the application."""
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # API mode (deployed) → JSON + INFO level; CLI mode (local) → console + DEBUG
    settings = get_settings()
    if settings.use_api_direct:
        json_output = True
        min_level = _LEVEL_MAP.get("info", 20)
    else:
        min_level = _LEVEL_MAP.get("debug", 0)

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also suppress noisy stdlib loggers in deployed mode
    if settings.use_api_direct:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(agent_id: str = "", phase: str = "") -> structlog.stdlib.BoundLogger:
    """Get a logger bound with agent context."""
    logger = structlog.get_logger()
    if agent_id:
        logger = logger.bind(agent_id=agent_id)
    if phase:
        logger = logger.bind(phase=phase)
    return logger
