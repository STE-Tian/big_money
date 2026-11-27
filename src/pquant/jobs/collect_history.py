"""Download public Binance spot klines. This command does not place orders."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pquant.adapters.exchanges.binance.client import BinanceMarketDataClient
from pquant.config.loader import load_settings
from pquant.domain.errors import BanError, ExchangeError, NetworkError
from pquant.ingestion.collector import HistoryCollector
from pquant.jobs.common import exit_with
from pquant.storage.market_store import MarketStore
from pquant.utils.logging import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download Binance public spot klines.")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--interval", action="append", default=None)
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.root)
    except Exception as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    logger = configure_logging(settings.app.log_level, settings.root / "logs")
    logger.info("Personal Quant AI v1 — public market data only. Live trading is disabled.")
    symbols = [item.upper() for item in (args.symbol or settings.market_data.symbols)]
    intervals = args.interval or settings.market_data.intervals
    store = MarketStore(settings.storage.duckdb_path)
    store.init_schema()
    client = BinanceMarketDataClient(
        base_url=settings.market_data.base_url,
        fallback_base_url=settings.market_data.fallback_base_url,
        timeout_seconds=settings.market_data.request_timeout_seconds,
        max_retries=settings.market_data.max_retries,
    )
    try:
        instruments = client.get_exchange_info(symbols)
        for instrument in instruments:
            store.upsert_instrument(instrument)
            logger.info(
                "Instrument %s status=%s tick=%s step=%s min_notional=%s",
                instrument.symbol,
                instrument.status,
                instrument.price_tick,
                instrument.qty_step,
                instrument.min_notional,
            )
    except (NetworkError, ExchangeError, BanError) as exc:
        logger.error("exchangeInfo failed: %s", exc)
        _write_blocker(settings.storage.reports_dir, f"exchangeInfo failed: {exc}")
        return 4
    collector = HistoryCollector(settings, client, store)
    failures = 0
    for symbol in symbols:
        for interval in intervals:
            logger.info("Collecting %s %s", symbol, interval)
            try:
                result = collector.collect_symbol(symbol, interval)
            except BanError as exc:
                _write_blocker(settings.storage.reports_dir, str(exc))
                return exit_with(logger, str(exc), 4)
            except (NetworkError, ExchangeError) as exc:
                failures += 1
                logger.error("Collection failed for %s %s: %s", symbol, interval, exc)
                _write_blocker(settings.storage.reports_dir, f"{symbol} {interval}: {exc}")
                continue
            logger.info(
                "Stored %s %s rows=%s requests=%s quality=%s hash=%s range=%s -> %s",
                symbol,
                interval,
                result.rows_stored,
                result.request_count,
                result.quality_status,
                result.dataset_hash,
                result.data_start,
                result.data_end,
            )
            print(
                json.dumps(
                    {
                        "symbol": result.symbol,
                        "interval": result.interval,
                        "rows_stored": result.rows_stored,
                        "request_count": result.request_count,
                        "quality_status": result.quality_status,
                        "dataset_hash": result.dataset_hash,
                        "data_start": result.data_start.isoformat() if result.data_start else None,
                        "data_end": result.data_end.isoformat() if result.data_end else None,
                        "base_url": result.base_url,
                    }
                )
            )
    return 1 if failures else 0


def _write_blocker(reports: Path, message: str) -> None:
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / "ingest_error.json"
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    if not isinstance(existing, list):
        existing = [existing]
    existing.append({"error": message})
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
