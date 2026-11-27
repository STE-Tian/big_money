"""DuckDB market store. Strategy logic does not live here."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import duckdb

from pquant.domain.models import Candle, Instrument
from pquant.storage.schema import DUCKDB_SCHEMA
from pquant.utils.hashing import content_hash


class MarketStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def init_schema(self) -> None:
        with self.connect() as conn:
            for statement in DUCKDB_SCHEMA.split(";"):
                sql = statement.strip()
                if sql:
                    conn.execute(sql)

    @contextmanager
    def connect(self, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        conn = duckdb.connect(str(self.path), read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()

    def upsert_candles(self, candles: list[Candle]) -> int:
        if not candles:
            return 0
        rows = [_candle_row(candle) for candle in candles]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO market_candles (
                    exchange, market_type, symbol, interval, open_time, close_time,
                    open, high, low, close, volume, quote_volume, trade_count,
                    taker_buy_base_volume, taker_buy_quote_volume, is_closed, source,
                    ingested_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (exchange, market_type, symbol, interval, open_time)
                DO UPDATE SET
                    close_time = excluded.close_time,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    quote_volume = excluded.quote_volume,
                    trade_count = excluded.trade_count,
                    taker_buy_base_volume = excluded.taker_buy_base_volume,
                    taker_buy_quote_volume = excluded.taker_buy_quote_volume,
                    is_closed = excluded.is_closed,
                    source = excluded.source,
                    ingested_at = excluded.ingested_at,
                    content_hash = excluded.content_hash
                """,
                rows,
            )
        return len(rows)

    def count_candles(self, symbol: str, interval: str, *, closed_only: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM market_candles WHERE symbol = ? AND interval = ?"
        args: list[object] = [symbol, interval]
        if closed_only:
            sql += " AND is_closed = TRUE"
        with self.connect(read_only=True) as conn:
            row = conn.execute(sql, args).fetchone()
        return int(row[0]) if row else 0

    def latest_open_time(self, symbol: str, interval: str) -> datetime | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT open_time FROM market_candles
                WHERE symbol = ? AND interval = ?
                ORDER BY open_time DESC
                LIMIT 1
                """,
                [symbol, interval],
            ).fetchone()
        if row is None:
            return None
        return _as_utc(row[0])

    def hashes_for(self, symbol: str, interval: str) -> dict[datetime, str]:
        with self.connect(read_only=True) as conn:
            rows = conn.execute(
                """
                SELECT open_time, content_hash FROM market_candles
                WHERE symbol = ? AND interval = ?
                """,
                [symbol, interval],
            ).fetchall()
        return {_as_utc(open_time): content for open_time, content in rows}

    def upsert_instrument(self, instrument: Instrument) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO instruments (
                    exchange, market_type, symbol, status, base_asset, quote_asset,
                    price_tick, qty_step, min_qty, min_notional, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (exchange, market_type, symbol) DO UPDATE SET
                    status = excluded.status,
                    base_asset = excluded.base_asset,
                    quote_asset = excluded.quote_asset,
                    price_tick = excluded.price_tick,
                    qty_step = excluded.qty_step,
                    min_qty = excluded.min_qty,
                    min_notional = excluded.min_notional,
                    captured_at = excluded.captured_at
                """,
                [
                    instrument.exchange,
                    instrument.market_type,
                    instrument.symbol,
                    instrument.status,
                    instrument.base_asset,
                    instrument.quote_asset,
                    float(instrument.price_tick),
                    float(instrument.qty_step),
                    float(instrument.min_qty),
                    float(instrument.min_notional),
                    instrument.captured_at,
                ],
            )

    def get_instrument(self, symbol: str) -> Instrument | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT exchange, market_type, symbol, status, base_asset, quote_asset,
                       price_tick, qty_step, min_qty, min_notional, captured_at
                FROM instruments WHERE symbol = ?
                """,
                [symbol],
            ).fetchone()
        if row is None:
            return None
        from decimal import Decimal

        return Instrument(
            exchange=row[0],
            market_type=row[1],
            symbol=row[2],
            status=row[3],
            base_asset=row[4],
            quote_asset=row[5],
            price_tick=Decimal(str(row[6])),
            qty_step=Decimal(str(row[7])),
            min_qty=Decimal(str(row[8])),
            min_notional=Decimal(str(row[9])),
            captured_at=_as_utc(row[10]),
        )

    def record_ingestion_run(self, payload: dict[str, object]) -> None:
        columns = [
            "run_id",
            "started_at",
            "finished_at",
            "exchange",
            "symbol",
            "interval",
            "request_count",
            "rows_returned",
            "failure_count",
            "revised_count",
            "data_start",
            "data_end",
            "status",
            "error_message",
            "base_url",
        ]
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO ingestion_runs ({", ".join(columns)})
                VALUES ({", ".join(["?"] * len(columns))})
                ON CONFLICT (run_id) DO UPDATE SET
                    finished_at = excluded.finished_at,
                    request_count = excluded.request_count,
                    rows_returned = excluded.rows_returned,
                    failure_count = excluded.failure_count,
                    revised_count = excluded.revised_count,
                    data_start = excluded.data_start,
                    data_end = excluded.data_end,
                    status = excluded.status,
                    error_message = excluded.error_message
                """,
                [payload.get(column) for column in columns],
            )

    def record_request(self, payload: dict[str, object]) -> None:
        columns = [
            "request_id",
            "run_id",
            "requested_at",
            "base_url",
            "symbol",
            "interval",
            "start_time_ms",
            "end_time_ms",
            "http_status",
            "rows_returned",
            "data_open_first",
            "data_open_last",
            "used_weight_1m",
            "elapsed_ms",
            "error_message",
        ]
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO ingestion_requests ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})",
                [payload.get(column) for column in columns],
            )

    def save_quality_report(
        self,
        *,
        report_id: str,
        generated_at: datetime,
        exchange: str,
        symbol: str,
        interval: str,
        status: str,
        dataset_hash: str,
        total_rows: int,
        closed_rows: int,
        missing_count: int,
        missing_ratio: float,
        revised_candle_count: int,
        report_json: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO data_quality_reports (
                    report_id, generated_at, exchange, symbol, interval, status, dataset_hash,
                    total_rows, closed_rows, missing_count, missing_ratio, revised_candle_count, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    report_id,
                    generated_at,
                    exchange,
                    symbol,
                    interval,
                    status,
                    dataset_hash,
                    total_rows,
                    closed_rows,
                    missing_count,
                    missing_ratio,
                    revised_candle_count,
                    report_json,
                ],
            )

    def latest_quality(self, symbol: str, interval: str) -> dict[str, object] | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT report_json, generated_at, status, dataset_hash
                FROM data_quality_reports
                WHERE symbol = ? AND interval = ?
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                [symbol, interval],
            ).fetchone()
        if row is None:
            return None
        return {
            "report_json": row[0],
            "generated_at": _as_utc(row[1]),
            "status": row[2],
            "dataset_hash": row[3],
        }

    def latest_ingestion(self, symbol: str, interval: str) -> dict[str, object] | None:
        with self.connect(read_only=True) as conn:
            row = conn.execute(
                """
                SELECT run_id, finished_at, status, rows_returned, data_start, data_end, base_url, error_message
                FROM ingestion_runs
                WHERE symbol = ? AND interval = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                [symbol, interval],
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "finished_at": _as_utc(row[1]) if row[1] is not None else None,
            "status": row[2],
            "rows_returned": row[3],
            "data_start": _as_utc(row[4]) if row[4] is not None else None,
            "data_end": _as_utc(row[5]) if row[5] is not None else None,
            "base_url": row[6],
            "error_message": row[7],
        }


def _candle_row(candle: Candle) -> tuple[object, ...]:
    return (
        candle.exchange,
        candle.market_type,
        candle.symbol,
        candle.interval,
        candle.open_time,
        candle.close_time,
        float(candle.open),
        float(candle.high),
        float(candle.low),
        float(candle.close),
        float(candle.volume),
        float(candle.quote_volume),
        candle.trade_count,
        float(candle.taker_buy_base_volume),
        float(candle.taker_buy_quote_volume),
        candle.is_closed,
        candle.source,
        candle.ingested_at,
        content_hash(candle),
    )


def _as_utc(value: object) -> datetime:
    from datetime import timezone

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raise TypeError(f"expected datetime, got {type(value)!r}")
