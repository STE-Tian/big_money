"""CLI for the V2 P1 data layer. These commands do not place orders."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from pquant.domain.errors import BanError, ConfigError, DataQualityError, ExchangeError, LiveTradingDisabledError, NetworkError
from pquant.domain.models import ensure_utc
from pquant.utils.logging import configure_logging
from pquant.v2.config import load_v2_settings
from pquant.v2.pipeline import build_v2_features, collect_v2, validate_v2


def collect_main(argv: list[str] | None = None) -> int:
    parser = _parser("Download V2 spot 1h/4h klines and funding-rate features.")
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--interval", action="append", default=None)
    parser.add_argument("--start", default=None, help="Inclusive UTC open time, for example 2026-10-01T00:00:00Z")
    parser.add_argument("--end", default=None, help="Exclusive UTC open time, for example 2026-11-01T00:00:00Z")
    args = parser.parse_args(argv)
    try:
        settings = load_v2_settings(args.root)
        logger = configure_logging(level="INFO", log_dir=settings.root / "logs")
        logger.info("Big Money V2 P1 — public research data only. Funding is not tradable. LIVE is disabled.")
        report = collect_v2(
            settings,
            start=_parse_time(args.start),
            end=_parse_time(args.end),
            symbols=args.symbol,
            intervals=args.interval,
            explicit_end=args.end is not None,
        )
    except (LiveTradingDisabledError, ConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except BanError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    except (NetworkError, ExchangeError, DataQualityError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print(report)
    failed = any(item.get("quality_status") != "qualified" for item in report["spot"]) or any(
        item.get("status") != "qualified" for item in report["funding"]
    )
    return 1 if failed else 0


def validate_main(argv: list[str] | None = None) -> int:
    parser = _parser("Recompute V2 spot and funding quality reports from local Parquet.")
    args = parser.parse_args(argv)
    try:
        settings = load_v2_settings(args.root)
        configure_logging(level="INFO", log_dir=settings.root / "logs")
        report = validate_v2(settings)
    except (LiveTradingDisabledError, ConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    _print(report)
    return 0 if report["ok"] else 1


def build_main(argv: list[str] | None = None) -> int:
    parser = _parser("Build the point-in-time V2 feature store from stored public data.")
    args = parser.parse_args(argv)
    try:
        settings = load_v2_settings(args.root)
        configure_logging(level="INFO", log_dir=settings.root / "logs")
        report = build_v2_features(settings)
    except (LiveTradingDisabledError, ConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except DataQualityError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print(report)
    return 0


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", type=Path, default=None, help="Project root. Defaults to the directory that contains configs/.")
    return parser


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return ensure_utc(parsed)


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, default=str))
