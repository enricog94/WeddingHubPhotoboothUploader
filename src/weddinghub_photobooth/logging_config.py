"""Structured logging and secret masking for WeddingHub Photobooth Uploader."""

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# Standard lifecycle event types
EVENT_PHOTO_DISCOVERED = "PHOTO_DISCOVERED"
EVENT_PHOTO_STABLE = "PHOTO_STABLE"
EVENT_PHOTO_QUEUED = "PHOTO_QUEUED"
EVENT_UPLOAD_STARTED = "UPLOAD_STARTED"
EVENT_UPLOAD_RETRY = "UPLOAD_RETRY"
EVENT_UPLOAD_COMPLETED = "UPLOAD_COMPLETED"
EVENT_UPLOAD_FAILED = "UPLOAD_FAILED"
EVENT_AUTH_ERROR = "AUTH_ERROR"
EVENT_SERVICE_STARTED = "SERVICE_STARTED"
EVENT_SERVICE_STOPPED = "SERVICE_STOPPED"


def mask_url_secrets(url_str: str) -> str:
    """Mask sensitive query parameters from presigned upload URLs."""
    try:
        parts = urlsplit(url_str)
        if parts.query:
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "[SIGNED_PARAMS]", parts.fragment))
        return url_str
    except (ValueError, AttributeError):
        return "[URL]"


class SecretSanitizingFilter(logging.Filter):
    """Logging filter to prevent accidental leakage of device tokens or signed URLs."""

    def __init__(self, token_to_mask: str | None = None) -> None:
        super().__init__()
        self.token_to_mask = token_to_mask.strip() if token_to_mask else None
        self._bearer_pattern = re.compile(r"Bearer\s+([a-zA-Z0-9_\-\.]{6,})", re.IGNORECASE)
        self._url_with_query_pattern = re.compile(r"(https?://[^\s\?]+\?)([^\s]+)")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self.sanitize(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self.sanitize(v) if isinstance(v, str) else v for v in record.args)
        return True

    def sanitize(self, text: str) -> str:
        if not text:
            return text

        if self.token_to_mask and self.token_to_mask in text:
            text = text.replace(self.token_to_mask, "[DEVICE_TOKEN_MASKED]")

        # Mask general Bearer tokens
        text = self._bearer_pattern.sub(r"Bearer [MASKED]", text)

        # Mask signed query parameters in URLs
        text = self._url_with_query_pattern.sub(r"\1[SIGNED_PARAMS]", text)

        return text


class StructuredFormatter(logging.Formatter):
    """Formatter that presents structured events clearly for journald and log files."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        event_tag = f" [{event}]" if event else ""
        formatted_time = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return f"{formatted_time} [{record.levelname}]{event_tag} {record.getMessage()}"


def configure_logging(
    level_name: str = "INFO",
    log_file: Path | None = None,
    token_to_mask: str | None = None,
) -> logging.Logger:
    """Configure the root weddinghub_photobooth logger."""
    logger = logging.getLogger("weddinghub_photobooth")
    logger.setLevel(getattr(logging, level_name.upper(), logging.INFO))
    logger.handlers.clear()

    formatter = StructuredFormatter()
    sanitizer = SecretSanitizingFilter(token_to_mask=token_to_mask)

    # Console / journald handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sanitizer)
    logger.addHandler(console_handler)

    # Optional file handler
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sanitizer)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, level: int, event: str, message: str, **kwargs: Any) -> None:
    """Log a structured lifecycle event with extra context."""
    logger.log(level, message, extra={"event": event, **kwargs})
