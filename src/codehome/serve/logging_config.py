"""Structured logging configuration using structlog.

Provides JSON-formatted log output with timestamp, log level, caller info,
and request ID correlation.  Use ``get_logger()`` to obtain a bound logger
in any module.

Usage::

    from codehome.serve.logging_config import get_logger

    logger = get_logger()
    logger.info("service started", branch="bag:navchat", port=8080)
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger


def configure_logging(*, json_output: bool = True) -> None:
    """Configure structlog processors and stdlib integration.

    Call once at server startup (in the lifespan handler).

    Args:
        json_output: If True (default), emit JSON lines.  Set False for
                     human-readable console output during development.

    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.CallsiteParameterAdder(
            [
                structlog.processors.CallsiteParameter.MODULE,
                structlog.processors.CallsiteParameter.FUNC_NAME,
                structlog.processors.CallsiteParameter.LINENO,
            ],
        ),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_binds: object) -> FilteringBoundLogger:
    """Return a bound structlog logger, optionally with initial key-value bindings.

    Example::

        logger = get_logger(component="auth")
        logger.info("login attempt", username="alice")
    """
    return structlog.get_logger(**initial_binds)  # type: ignore[no-any-return]
