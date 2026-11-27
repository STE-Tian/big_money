"""CLI for the V2 P2 study. This command does not place orders."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pquant.domain.errors import ConfigError, DataQualityError, LiveTradingDisabledError
from pquant.utils.logging import configure_logging
from pquant.v2.p2_config import load_p2_settings
from pquant.v2.study import run_study


def train_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train and evaluate Big Money V2 P2 on the local feature store. No orders are sent."
    )
    parser.add_argument("--root", type=Path, default=None, help="Project root. Defaults to the directory that contains configs/.")
    args = parser.parse_args(argv)
    try:
        settings = load_p2_settings(args.root)
        configure_logging(level="INFO", log_dir=settings.root / "logs")
        summary = run_study(settings.root)
    except (LiveTradingDisabledError, ConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except DataQualityError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    judgment = summary["judgment"]
    print(judgment["conclusion"])
    print(f"enough_evidence={judgment['enough_evidence']}")
    print(f"report={settings.root / 'docs' / 'V2_P2_REPORT.md'}")
    return 0
