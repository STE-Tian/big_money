"""Data-quality checks. Missing OHLC is never forward-filled."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pquant.domain.models import Candle
from pquant.utils.hashing import dataset_hash
from pquant.utils.time import interval_ms


class DataQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: str
    market_type: str
    symbol: str
    interval: str
    generated_at: datetime
    server_time: datetime | None = None
    start: datetime | None = None
    end: datetime | None = None
    total_rows: int
    closed_rows: int
    open_rows: int
    duplicate_rows: int
    out_of_order_rows: int
    missing_count: int
    missing_ratio: float
    missing_timestamps_sample: list[str] = Field(default_factory=list)
    ohlc_invalid_count: int
    negative_volume_count: int
    future_timestamp_count: int
    latest_closed_open_time: datetime | None = None
    latest_closed_lag_seconds: float | None = None
    revised_candle_count: int = 0
    incoming_duplicate_rows: int = 0
    previous_dataset_hash: str | None = None
    dataset_hash: str
    status: str
    reasons: list[str]
    forward_filled: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def ohlc_is_valid(candle: Candle) -> bool:
    if min(candle.open, candle.high, candle.low, candle.close) < 0:
        return False
    if candle.high < max(candle.open, candle.close, candle.low):
        return False
    if candle.low > min(candle.open, candle.close, candle.high):
        return False
    return True


def build_quality_report(
    candles: list[Candle],
    *,
    symbol: str,
    interval: str,
    generated_at: datetime,
    server_time: datetime | None = None,
    revised_candle_count: int = 0,
    incoming_duplicate_rows: int = 0,
    previous_dataset_hash: str | None = None,
    max_missing_ratio: float = 0.01,
    check_freshness: bool = True,
    exchange: str = "binance",
    market_type: str = "spot",
) -> DataQualityReport:
    ordered = sorted(candles, key=lambda c: (c.open_time, c.ingested_at))
    duplicate_rows = _duplicate_count(ordered)
    out_of_order = _out_of_order_count(candles)
    closed = [c for c in ordered if c.is_closed]
    open_rows = [c for c in ordered if not c.is_closed]
    ohlc_invalid = sum(1 for c in ordered if not ohlc_is_valid(c))
    negative_volume = sum(1 for c in ordered if c.volume < 0 or c.quote_volume < 0)
    reference = server_time or generated_at
    future_count = sum(
        1
        for c in ordered
        if c.open_time > reference + timedelta(minutes=5)
    )
    missing, missing_ratio = _missing(closed, interval)
    latest = closed[-1] if closed else None
    lag = None
    if latest is not None:
        lag = (reference - latest.close_time).total_seconds()
    digest = dataset_hash(closed)
    reasons: list[str] = []
    if not ordered:
        reasons.append("no rows")
    if duplicate_rows:
        reasons.append(f"duplicate open times: {duplicate_rows}")
    if incoming_duplicate_rows:
        reasons.append(f"duplicate rows inside fetched pages: {incoming_duplicate_rows}")
    if out_of_order:
        reasons.append(f"out-of-order rows in the incoming sequence: {out_of_order}")
    if ohlc_invalid:
        reasons.append(f"invalid OHLC rows: {ohlc_invalid}")
    if negative_volume:
        reasons.append(f"negative volume rows: {negative_volume}")
    if future_count:
        reasons.append(f"future timestamps: {future_count}")
    if missing_ratio > max_missing_ratio:
        reasons.append(f"missing ratio {missing_ratio:.6f} exceeds {max_missing_ratio}")
    if check_freshness and lag is not None and lag > 2 * (interval_ms(interval) / 1000):
        reasons.append(f"latest closed candle lag {lag:.0f}s is more than two intervals")
    hard_fail = bool(duplicate_rows or ohlc_invalid or negative_volume or future_count or not closed)
    if not ordered:
        status = "empty"
    elif hard_fail or missing_ratio > max_missing_ratio:
        status = "unqualified"
    else:
        status = "qualified"
        if out_of_order:
            # Stored data is sorted by the writer. Incoming disorder is disclosed, not fatal
            # once the unique key has collapsed the rows.
            reasons.append("incoming sequence was not sorted; storage sorts by open_time")
    if status == "qualified" and reasons:
        # Lag and sort notes can coexist with a qualified dataset. Keep them visible.
        pass
    if status == "qualified" and any(r.startswith("latest closed") for r in reasons):
        status = "unqualified"
    return DataQualityReport(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        interval=interval,
        generated_at=generated_at,
        server_time=server_time,
        start=ordered[0].open_time if ordered else None,
        end=ordered[-1].close_time if ordered else None,
        total_rows=len(ordered),
        closed_rows=len(closed),
        open_rows=len(open_rows),
        duplicate_rows=duplicate_rows,
        out_of_order_rows=out_of_order,
        missing_count=len(missing),
        missing_ratio=missing_ratio,
        missing_timestamps_sample=[item.isoformat() for item in missing[:50]],
        ohlc_invalid_count=ohlc_invalid,
        negative_volume_count=negative_volume,
        future_timestamp_count=future_count,
        latest_closed_open_time=latest.open_time if latest else None,
        latest_closed_lag_seconds=lag,
        revised_candle_count=revised_candle_count,
        incoming_duplicate_rows=incoming_duplicate_rows,
        previous_dataset_hash=previous_dataset_hash,
        dataset_hash=digest,
        status=status,
        reasons=reasons,
        forward_filled=False,
    )


def closed_only(candles: list[Candle]) -> list[Candle]:
    """Historical backtests use closed bars only. This does not invent replacement bars."""
    return [c for c in sorted(candles, key=lambda item: item.open_time) if c.is_closed]


def _duplicate_count(ordered: list[Candle]) -> int:
    seen: set[datetime] = set()
    dupes = 0
    for candle in ordered:
        if candle.open_time in seen:
            dupes += 1
        seen.add(candle.open_time)
    return dupes


def _out_of_order_count(candles: list[Candle]) -> int:
    count = 0
    previous: datetime | None = None
    for candle in candles:
        if previous is not None and candle.open_time < previous:
            count += 1
        previous = candle.open_time
    return count


def _missing(closed: list[Candle], interval: str) -> tuple[list[datetime], float]:
    if len(closed) < 2:
        return [], 0.0
    step = timedelta(milliseconds=interval_ms(interval))
    present = {c.open_time for c in closed}
    start = closed[0].open_time
    end = closed[-1].open_time
    missing: list[datetime] = []
    cursor = start
    # Guard against a pathological step.
    if step.total_seconds() <= 0:
        return [], 1.0
    expected = 0
    while cursor <= end:
        expected += 1
        if cursor not in present:
            missing.append(cursor)
        cursor += step
    ratio = len(missing) / expected if expected else 0.0
    return missing, ratio
