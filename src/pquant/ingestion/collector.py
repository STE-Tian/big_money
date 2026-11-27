"""Paginated, resumable Binance kline collection.

The collector does not place orders. An unclosed candle is stored and flagged.
Repeated runs upsert on (exchange, market, symbol, interval, open_time).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pquant.adapters.exchanges.binance.client import BinanceMarketDataClient
from pquant.config.settings import Settings
from pquant.domain.errors import BanError, ConfigError
from pquant.domain.models import Candle
from pquant.ingestion.quality import build_quality_report
from pquant.storage.market_store import MarketStore
from pquant.storage.parquet_store import merge_candles, parquet_path, read_candles, write_candles
from pquant.utils.hashing import content_hash, dataset_hash
from pquant.utils.time import interval_ms, utc_to_ms


@dataclass
class IngestionResult:
    run_id: str
    symbol: str
    interval: str
    rows_stored: int
    request_count: int
    revised_count: int
    failure_count: int
    base_url: str
    data_start: datetime | None
    data_end: datetime | None
    status: str
    quality_status: str
    dataset_hash: str
    error_message: str | None = None
    request_logs: list[dict[str, object]] = field(default_factory=list)


class HistoryCollector:
    def __init__(self, settings: Settings, client: BinanceMarketDataClient, store: MarketStore) -> None:
        self.settings = settings
        self.client = client
        self.store = store

    def collect_symbol(
        self,
        symbol: str,
        interval: str,
        *,
        history_start: datetime | None = None,
        history_end: datetime | None = None,
    ) -> IngestionResult:
        symbol = symbol.upper()
        if symbol not in self.settings.universe_symbols:
            raise ConfigError(f"{symbol} is outside the configured universe")
        start = history_start if history_start is not None else self.settings.market_data.history_start
        if start is None:
            raise ConfigError(
                "history_start is null. Set market_data.history_start in configs/app.yaml "
                "to a finite UTC timestamp. v1 will not download an unbounded range."
            )
        if start.tzinfo is None:
            raise ConfigError("history_start must be timezone-aware UTC")
        run_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        self.store.record_ingestion_run(
            {
                "run_id": run_id,
                "started_at": started,
                "finished_at": None,
                "exchange": "binance",
                "symbol": symbol,
                "interval": interval,
                "request_count": 0,
                "rows_returned": 0,
                "failure_count": 0,
                "revised_count": 0,
                "data_start": None,
                "data_end": None,
                "status": "running",
                "error_message": None,
                "base_url": self.settings.market_data.base_url,
            }
        )
        try:
            result = self._collect(run_id, symbol, interval, start, history_end, started)
        except Exception as exc:
            self.store.record_ingestion_run(
                {
                    "run_id": run_id,
                    "started_at": started,
                    "finished_at": datetime.now(timezone.utc),
                    "exchange": "binance",
                    "symbol": symbol,
                    "interval": interval,
                    "request_count": 0,
                    "rows_returned": 0,
                    "failure_count": 1,
                    "revised_count": 0,
                    "data_start": None,
                    "data_end": None,
                    "status": "failed",
                    "error_message": str(exc),
                    "base_url": self.settings.market_data.base_url,
                }
            )
            raise
        return result

    def _collect(
        self,
        run_id: str,
        symbol: str,
        interval: str,
        history_start: datetime,
        history_end: datetime | None,
        started: datetime,
    ) -> IngestionResult:
        server_time = self.client.get_server_time()
        step = interval_ms(interval)
        limit = self.settings.market_data.klines_limit
        existing = self._load_existing(symbol, interval)
        previous_hash = dataset_hash([c for c in existing if c.is_closed]) if existing else None
        old_hashes = {candle.open_time: content_hash(candle) for candle in existing}
        resume = self.store.latest_open_time(symbol, interval)
        cursor = history_start
        if resume is not None and resume > cursor:
            # Re-read the last stored bar so a revision or a now-closed candle is updated.
            cursor = resume
        end_ms = utc_to_ms(history_end) if history_end is not None else None
        pages: list[Candle] = []
        request_logs: list[dict[str, object]] = []
        revised = 0
        failures = 0
        seen_max: datetime | None = None
        source = f"binance_rest:{self.settings.market_data.base_url}"
        while True:
            if self.client.last_used_weight_1m:
                try:
                    used = int(self.client.last_used_weight_1m)
                except ValueError:
                    used = 0
                limit_weight = self.settings.market_data.request_weight_limit_per_minute
                if used > int(limit_weight * 0.8):
                    self.client._sleeper(5)
            start_ms = utc_to_ms(cursor)
            if end_ms is not None and start_ms > end_ms:
                break
            ingested_at = datetime.now(timezone.utc)
            try:
                batch = self.client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    start_time_ms=start_ms,
                    end_time_ms=end_ms,
                    limit=limit,
                    server_time=server_time,
                    source=source,
                    ingested_at=ingested_at,
                )
            except BanError:
                failures += 1
                raise
            record = dict(self.client.last_request_record or {})
            record["request_id"] = str(uuid.uuid4())
            record["run_id"] = run_id
            record["requested_at"] = ingested_at
            record["symbol"] = symbol
            record["interval"] = interval
            if batch:
                record["data_open_first"] = batch[0].open_time
                record["data_open_last"] = batch[-1].open_time
            else:
                record["data_open_first"] = None
                record["data_open_last"] = None
            request_logs.append(record)
            self.store.record_request(
                {
                    "request_id": record["request_id"],
                    "run_id": run_id,
                    "requested_at": ingested_at,
                    "base_url": record.get("base_url", self.settings.market_data.base_url),
                    "symbol": symbol,
                    "interval": interval,
                    "start_time_ms": record.get("start_time_ms"),
                    "end_time_ms": record.get("end_time_ms"),
                    "http_status": record.get("http_status"),
                    "rows_returned": len(batch),
                    "data_open_first": record.get("data_open_first"),
                    "data_open_last": record.get("data_open_last"),
                    "used_weight_1m": record.get("used_weight_1m"),
                    "elapsed_ms": record.get("elapsed_ms"),
                    "error_message": record.get("error_message"),
                }
            )
            self._write_raw_page(symbol, interval, run_id, record, batch)
            if not batch:
                break
            new_candles = [c for c in batch if seen_max is None or c.open_time > seen_max]
            for candle in batch:
                previous = old_hashes.get(candle.open_time)
                if previous is not None and previous != content_hash(candle):
                    revised += 1
                old_hashes[candle.open_time] = content_hash(candle)
            pages.extend(batch)
            if not new_candles:
                break
            seen_max = max(c.open_time for c in batch)
            if len(batch) < limit:
                break
            cursor = _plus_ms(seen_max, step)
            self.client._sleeper(self.settings.market_data.request_pause_seconds)
        merged = merge_candles(existing, pages)
        path = parquet_path(
            self.settings.storage.parquet_dir,
            "binance",
            "spot",
            symbol,
            interval,
        )
        write_candles(path, merged)
        # DuckDB is the analytical DOUBLE copy. Parquet strings remain the backtest source.
        self.store.upsert_candles(pages if pages else merged)
        closed = [c for c in merged if c.is_closed]
        report = build_quality_report(
            merged,
            symbol=symbol,
            interval=interval,
            generated_at=datetime.now(timezone.utc),
            server_time=server_time,
            revised_candle_count=revised,
            incoming_duplicate_rows=_duplicate_open_times(pages),
            previous_dataset_hash=previous_hash,
            max_missing_ratio=float(self.settings.validation.max_missing_ratio),
            check_freshness=history_end is None,
        )
        report_id = str(uuid.uuid4())
        self.store.save_quality_report(
            report_id=report_id,
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
            revised_candle_count=revised,
            report_json=json.dumps(report.to_json_dict()),
        )
        quality_dir = self.settings.storage.reports_dir / "data_quality"
        quality_dir.mkdir(parents=True, exist_ok=True)
        (quality_dir / f"{symbol}_{interval}.json").write_text(
            json.dumps(report.to_json_dict(), indent=2),
            encoding="utf-8",
        )
        finished = datetime.now(timezone.utc)
        status = "completed"
        self.store.record_ingestion_run(
            {
                "run_id": run_id,
                "started_at": started,
                "finished_at": finished,
                "exchange": "binance",
                "symbol": symbol,
                "interval": interval,
                "request_count": len(request_logs),
                "rows_returned": len(pages),
                "failure_count": failures,
                "revised_count": revised,
                "data_start": merged[0].open_time if merged else None,
                "data_end": merged[-1].close_time if merged else None,
                "status": status,
                "error_message": None,
                "base_url": self.settings.market_data.base_url,
            }
        )
        return IngestionResult(
            run_id=run_id,
            symbol=symbol,
            interval=interval,
            rows_stored=len(merged),
            request_count=len(request_logs),
            revised_count=revised,
            failure_count=failures,
            base_url=str(request_logs[-1].get("base_url", self.settings.market_data.base_url)) if request_logs else self.settings.market_data.base_url,
            data_start=closed[0].open_time if closed else None,
            data_end=closed[-1].close_time if closed else None,
            status=status,
            quality_status=report.status,
            dataset_hash=report.dataset_hash,
            request_logs=request_logs,
        )

    def _load_existing(self, symbol: str, interval: str) -> list[Candle]:
        path = parquet_path(self.settings.storage.parquet_dir, "binance", "spot", symbol, interval)
        return read_candles(path)

    def _write_raw_page(self, symbol: str, interval: str, run_id: str, record: dict[str, object], batch: list[Candle]) -> None:
        directory = self.settings.storage.raw_dir / "binance" / f"{symbol}_{interval}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id}.jsonl"
        payload = {
            "request": {
                "base_url": record.get("base_url"),
                "symbol": symbol,
                "interval": interval,
                "start_time_ms": record.get("start_time_ms"),
                "end_time_ms": record.get("end_time_ms"),
                "http_status": record.get("http_status"),
                "used_weight_1m": record.get("used_weight_1m"),
                "elapsed_ms": record.get("elapsed_ms"),
            },
            "rows": [
                [
                    int(c.open_time.timestamp() * 1000),
                    format(c.open, "f"),
                    format(c.high, "f"),
                    format(c.low, "f"),
                    format(c.close, "f"),
                    format(c.volume, "f"),
                    int(c.close_time.timestamp() * 1000),
                    format(c.quote_volume, "f"),
                    c.trade_count,
                    format(c.taker_buy_base_volume, "f"),
                    format(c.taker_buy_quote_volume, "f"),
                ]
                for c in batch
            ],
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


def _duplicate_open_times(candles: list[Candle]) -> int:
    seen: set[datetime] = set()
    dupes = 0
    for candle in candles:
        if candle.open_time in seen:
            dupes += 1
        seen.add(candle.open_time)
    return dupes


def _plus_ms(value: datetime, milliseconds: int) -> datetime:
    from datetime import timedelta

    return value + timedelta(milliseconds=milliseconds)
