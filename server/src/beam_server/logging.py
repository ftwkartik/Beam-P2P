"""Structured logging setup.

Replaces [redacted]'s bare `console.log` (see docs/reference-analysis.md) with structlog,
rendering human-readable colored output in development and single-line JSON in
production so logs are queryable. A processor redacts keys that commonly carry
secrets, matching the policy in docs/security.md ("Secrets and error handling").
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

_REDACT_KEY_PATTERN = re.compile(
    r"(password|token|secret|authorization|api_key|apikey|cookie)", re.IGNORECASE
)
_REDACTED = "***redacted***"


def _redact_sensitive_fields(
    logger: object, method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    for key in list(event_dict):
        if _REDACT_KEY_PATTERN.search(key):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(*, level: str = "INFO", json_format: bool = False) -> None:
    """Configure structlog and route stdlib logging through it.

    Call once, at process startup (see main.py's `create_app`).
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_sensitive_fields,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_format
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level.upper())
