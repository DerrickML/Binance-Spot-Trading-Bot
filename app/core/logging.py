"""Structured logging setup using structlog."""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any

import structlog


def _make_console_safe(value: Any, encoding: str | None) -> Any:
    """Return a log value that can be written to the target console encoding."""
    target_encoding = encoding or "utf-8"
    if isinstance(value, str):
        try:
            value.encode(target_encoding)
            return value
        except LookupError:
            return value.encode("ascii", errors="replace").decode("ascii")
        except UnicodeEncodeError:
            return value.encode(target_encoding, errors="replace").decode(
                target_encoding,
                errors="replace",
            )
    if isinstance(value, Mapping):
        return {
            _make_console_safe(key, target_encoding): _make_console_safe(
                nested,
                target_encoding,
            )
            for key, nested in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_make_console_safe(item, target_encoding) for item in value)
    if isinstance(value, list):
        return [_make_console_safe(item, target_encoding) for item in value]
    return value


def _console_safe_event(
    _logger: Any,
    _method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return _make_console_safe(event_dict, encoding)


def setup_logging(log_level: str = "INFO", json_output: bool = False) -> None:
    """Configure structured logging for the application.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: If True, output JSON logs. Otherwise, use rich console output.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]
    if not json_output:
        shared_processors.append(_console_safe_event)

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

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

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a named structured logger."""
    return structlog.get_logger(name)
