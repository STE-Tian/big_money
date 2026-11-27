"""Recompute data-quality reports from the local Parquet snapshot."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from pquant.ingestion.quality import build_quality_report
from pquant.jobs.common import bootstrap
from pquant.services.market import load_candles
from pquant.storage.market_store import MarketStore


def main(argv: list[str] | None = None) -> int:
    settings, logger = bootstrap(argv, "Validate stored Binance candles.")
    store = MarketStore(settings.storage.duckdb_path)
    store.init_schema()
    failed = False
    for symbol in settings.market_data.symbols:
        for interval in settings.market_data.intervals:
            candles = load_candles(settings, symbol, interval)
            if not candles:
                logger.error("No local candles for %s %s. Run scripts/collect_history.py first.", symbol, interval)
                failed = True
                continue
            report = build_quality_report(
                candles,
                symbol=symbol,
                interval=interval,
                generated_at=datetime.now(timezone.utc),
                max_missing_ratio=float(settings.validation.max_missing_ratio),
                check_freshness=True,
            )
            directory = settings.storage.reports_dir / "data_quality"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{symbol}_{interval}.json"
            path.write_text(json.dumps(report.to_json_dict(), indent=2), encoding="utf-8")
            store.save_quality_report(
                report_id=str(uuid.uuid4()),
                generated_at=report.generated_at,
                exchange="binance",
                symbol=symbol,
                interval=interval,
                status=report.status,
                dataset_hash=report.dataset_hash,
                total_rows=report.total_rows,
                closed_rows=report.closed_rows,
                missing_count=report.missing_count,
                missing_ratio=report.missing_ratio,
                revised_candle_count=report.revised_candle_count,
                report_json=json.dumps(report.to_json_dict()),
            )
            logger.info(
                "%s %s status=%s rows=%s closed=%s missing=%s hash=%s",
                symbol,
                interval,
                report.status,
                report.total_rows,
                report.closed_rows,
                report.missing_count,
                report.dataset_hash,
            )
            print(f"{symbol} {interval} {report.status} closed={report.closed_rows} missing={report.missing_count}")
            if report.status != "qualified":
                failed = True
    return 1 if failed else 0
