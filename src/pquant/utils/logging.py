"""Logging that never writes secret-looking values."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret|signature|password|token|private[_-]?key)\b\s*[=:]\s*\S+"
)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        if record.args:
            record.args = tuple(_redact(arg) for arg in record.args)
        return True


def _redact(value: object) -> object:
    if not isinstance(value, str):
        return value
    return _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", value)


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logger = logging.getLogger("pquant")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = _gmtime
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.addFilter(RedactSecretsFilter())
    logger.addHandler(stream)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "pquant.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RedactSecretsFilter())
        logger.addHandler(file_handler)
    return logger


def _gmtime(*args: object):  # noqa: ANN002
    import time

    return time.gmtime(*args) if args else time.gmtime()
