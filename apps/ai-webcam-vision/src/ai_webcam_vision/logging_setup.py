"""Logging with mandatory redaction.

The filter is attached to handlers (not loggers) so that records propagated
from child loggers are scrubbed too.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .secretstore import scrub

LOGGER_NAME = "ai_webcam_vision"


class RedactingFilter(logging.Filter):
    """Scrub the formatted message and every argument of each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            rendered = str(record.msg)
        record.msg = scrub(rendered)
        record.args = ()
        if record.exc_info:
            # Keep the type, drop the payload: the value may carry raw text
            # from a third-party library that never heard of our scrubber.
            exc_type, exc_value, _tb = record.exc_info
            record.exc_info = None
            detail = scrub(exc_value) if exc_value is not None else ""
            name = getattr(exc_type, "__name__", "Exception")
            record.msg = f"{record.msg} | {name}: {detail}"
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    """Configure the application logger. Idempotent."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(RedactingFilter())
    logger.addHandler(stream)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.addFilter(RedactingFilter())
        logger.addHandler(file_handler)

    return logger
