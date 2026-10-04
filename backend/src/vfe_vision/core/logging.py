"""Structured logging (structlog) for the API and worker processes.

Console output is human-readable (or JSON); a rotating JSON log file is always written in the
data directory so that job logs can be inspected after the fact.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

import structlog

_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging(
    *,
    level: str = "INFO",
    fmt: str = "console",
    log_dir: Path | None = None,
    process_name: str = "api",
) -> None:
    """Configure structlog and route stdlib logging (uvicorn, httpx…) through it."""
    console_renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    handlers: list[logging.Handler] = []

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_SHARED_PROCESSORS,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                console_renderer,
            ],
        )
    )
    handlers.append(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{process_name}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=_SHARED_PROCESSORS,
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(ensure_ascii=False),
                ],
            )
        )
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level)
    asyncio_log = logging.getLogger("asyncio")
    asyncio_log.filters = [f for f in asyncio_log.filters if not isinstance(f, _PeerResetFilter)]
    asyncio_log.addFilter(_PeerResetFilter())
    for noisy in ("httpx", "httpcore", "httpx2", "multipart", "PIL", "faster_whisper", "alembic"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, logging.getLevelName(level)))

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(process=process_name)


class _PeerResetFilter(logging.Filter):
    """Drop asyncio's report of a browser closing a connection first (e.g. the video player
    abandoning a Range request when seeking): on Windows the Proactor loop logs it as an error
    with a traceback, although nothing went wrong (a known CPython issue)."""

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        return not (
            isinstance(error, ConnectionResetError)
            and "_call_connection_lost" in record.getMessage()
        )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
