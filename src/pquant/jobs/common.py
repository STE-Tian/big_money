"""Shared CLI helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pquant.config.loader import load_settings
from pquant.utils.logging import configure_logging


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", type=Path, default=None, help="Project root. Defaults to the directory that contains configs/.")
    return parser


def bootstrap(argv: list[str] | None, description: str):
    parser = build_parser(description)
    args = parser.parse_args(argv)
    settings = load_settings(args.root)
    logger = configure_logging(settings.app.log_level, settings.root / "logs")
    logger.info("Personal Quant AI v1 — research and paper only. Live trading is disabled.")
    if settings.ignored_env_names:
        logger.info(
            "Ignored %s secret-like environment name(s) without reading their values: %s",
            len(settings.ignored_env_names),
            ", ".join(settings.ignored_env_names),
        )
    return settings, logger


def exit_with(logger, message: str, code: int) -> int:
    logger.error(message)
    print(message, file=sys.stderr)
    return code
