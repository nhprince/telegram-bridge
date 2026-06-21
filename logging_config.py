"""
Telegram Bridge — Structured Logging & Error Tracking
Professional logging with JSON format for production monitoring.
"""

import logging
import json
import time
import traceback
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging (compatible with ELK/Loki/Grafana)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": record.process,
            "thread": record.thread,
        }

        # Add extra fields
        if hasattr(record, "app_id"):
            log_entry["app_id"] = record.app_id
        if hasattr(record, "file_unique_id"):
            log_entry["file_unique_id"] = record.file_unique_id
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms

        # Add exception info
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_entry, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure structured JSON logging for the application."""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Console handler with JSON format
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JSONFormatter())
    logger.addHandler(console_handler)

    # File handler for persistent logs
    try:
        file_handler = logging.FileHandler("/var/log/telegram-bridge/app.log")
        file_handler.setFormatter(JSONFormatter())
        logger.addHandler(file_handler)
    except (PermissionError, FileNotFoundError):
        pass  # Fallback to console only

    return logger


class LogContext:
    """Context manager for adding request-level context to logs."""

    def __init__(self, **kwargs: Any):
        self.extra = kwargs
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        # Add context to logger adapter
        self.logger = logging.getLogger("telegram-bridge.request")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.time() - self.start_time) * 1000
        if exc_type:
            logging.error(
                f"Request failed: {exc_type.__name__}: {exc_val}",
                extra={**self.extra, "duration_ms": duration_ms},
                exc_info=(exc_type, exc_val, exc_tb),
            )
        else:
            logging.info(
                "Request completed",
                extra={**self.extra, "duration_ms": duration_ms},
            )
        return False

    def info(self, message: str, **kwargs: Any):
        logging.info(message, extra={**self.extra, **kwargs})

    def error(self, message: str, **kwargs: Any):
        logging.error(message, extra={**self.extra, **kwargs})

    def warning(self, message: str, **kwargs: Any):
        logging.warning(message, extra={**self.extra, **kwargs})
