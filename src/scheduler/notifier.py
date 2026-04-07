"""Notification service for scheduler events.

Channels:
- macOS Notification Center (osascript)
- Console log (structlog/logging)
- JSONL file log (persistent history)

Extensible for future Telegram bot API integration.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class NotificationEvent(StrEnum):
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_TIMEOUT = "execution_timeout"
    JOB_AUTO_PAUSED = "job_auto_paused"
    TOKEN_BUDGET_WARNING = "token_budget_warning"


class Severity(StrEnum):
    LOW = "low"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


# Event → Severity mapping
_EVENT_SEVERITY = {
    NotificationEvent.EXECUTION_COMPLETED: Severity.LOW,
    NotificationEvent.EXECUTION_FAILED: Severity.HIGH,
    NotificationEvent.EXECUTION_TIMEOUT: Severity.HIGH,
    NotificationEvent.JOB_AUTO_PAUSED: Severity.CRITICAL,
    NotificationEvent.TOKEN_BUDGET_WARNING: Severity.WARNING,
}


class Notifier:
    """Sends notifications via local channels.

    Always logs to console and JSONL file.
    Sends macOS notification for HIGH/CRITICAL events (and optionally LOW).
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._log_path = Path(self._settings.scheduler_notification_log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def notify(
        self,
        event: NotificationEvent,
        title: str,
        message: str,
        job_id: str = "",
        execution_id: str = "",
    ) -> None:
        """Send a notification through all applicable channels."""
        severity = _EVENT_SEVERITY.get(event, Severity.LOW)

        # Always: console log
        self._log_to_console(event, severity, title, message)

        # Always: JSONL file
        self._log_to_file(event, severity, title, message, job_id, execution_id)

        # macOS notification for high-severity events
        should_notify_local = (
            severity in (Severity.HIGH, Severity.CRITICAL, Severity.WARNING)
            or (severity == Severity.LOW and self._settings.scheduler_notify_on_success)
        )
        if should_notify_local and sys.platform == "darwin":
            self._send_macos_notification(title, message, severity)

    def _log_to_console(
        self, event: NotificationEvent, severity: Severity, title: str, message: str,
    ) -> None:
        """Log to console with appropriate level."""
        log_fn = {
            Severity.LOW: logger.info,
            Severity.WARNING: logger.warning,
            Severity.HIGH: logger.error,
            Severity.CRITICAL: logger.critical,
        }.get(severity, logger.info)
        log_fn("[%s] %s: %s", event.value, title, message)

    def _log_to_file(
        self,
        event: NotificationEvent,
        severity: Severity,
        title: str,
        message: str,
        job_id: str,
        execution_id: str,
    ) -> None:
        """Append notification to JSONL log file."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event.value,
            "severity": severity.value,
            "title": title,
            "message": message,
            "job_id": job_id,
            "execution_id": execution_id,
        }
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error("Failed to write notification log: %s", e)

    @staticmethod
    def _send_macos_notification(title: str, message: str, severity: Severity) -> None:
        """Send macOS Notification Center alert via osascript.

        Uses ``on run argv`` to pass data as script arguments instead of
        interpolating into AppleScript source code, preventing injection.
        """
        sound = "Basso" if severity in (Severity.HIGH, Severity.CRITICAL) else "default"

        # Fixed AppleScript template — data is passed as argv, never interpolated
        script = (
            "on run argv\n"
            "  display notification (item 1 of argv) "
            "with title \"Enterprise Agent\" "
            "subtitle (item 2 of argv) "
            "sound name (item 3 of argv)\n"
            "end run"
        )
        try:
            subprocess.run(
                ["osascript", "-e", script, message[:500], title[:200], sound],
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("macOS notification failed: %s", e)
