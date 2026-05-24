import logging
import logging.handlers
from pathlib import Path
from typing import Optional

import structlog

_LOG_DIR = Path("logs")


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_file: Optional[Path] = None,
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
    ]

    renderer = structlog.processors.JSONRenderer() if json_format else structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    root.handlers.clear()

    # always log to stdout
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    # when json_format=True (production), also write to rotating file
    if json_format:
        target = log_file or (_LOG_DIR / "btcbot.log")
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target,
            maxBytes=50 * 1024 * 1024,   # 50 MB per file
            backupCount=10,               # keep 10 rotated files (~500 MB total)
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root.setLevel(log_level)

    # suppress noisy third-party loggers
    for name in ("websockets", "asyncio", "aiohttp"):
        logging.getLogger(name).setLevel(logging.WARNING)
