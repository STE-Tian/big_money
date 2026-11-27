"""V2 quality checks on top of the V1 candle report.

Missing spot bars are counted and never filled. Funding events are checked as
a feature series: duplicates and unsorted input are failures, a negative rate
is valid, and an empty mark price is not rewritten to zero.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from pquant.domain.models import Candle
from pquant.ingestion.quality import DataQualityReport, build_quality_report
from pquant.v2.funding import FundingEvent


def spot_quality_report(
    candles: list[Candle],
    *,
    symbol: str,
    interval: str,
    generated_at: datetime,
    server_time: datetime | None,
    max_missing_ratio: float,
    check_freshness: bool,
    revised_candle_count: int = 0,
) -> DataQualityReport:
    report = build_quality_report(
        candles,
        symbol=symbol,
        interval=interval,
        generated_at=generated_at,
        server_time=server_time,
        revised_candle_count=revised_candle_count,
        max_missing_ratio=max_missing_ratio,
        check_freshness=check_freshness,
        exchange="binance",
        market_type="spot",
    )
    reasons = list(report.reasons)
    taker_invalid = sum(
        1
        for candle in candles
        if candle.taker_buy_base_volume < 0
        or candle.taker_buy_quote_volume < 0
        or candle.taker_buy_base_volume > candle.volume
        or candle.taker_buy_quote_volume > candle.quote_volume
        or candle.trade_count < 0
    )
    if taker_invalid:
        reasons.append(f"invalid taker volume or trade count rows: {taker_invalid}")
    status = report.status
    if taker_invalid and status == "qualified":
        status = "unqualified"
    if status == "qualified" and report.open_rows and report.closed_rows == 0:
        status = "unqualified"
    return report.model_copy(update={"reasons": reasons, "status": status})


def funding_dataset_hash(events: list[FundingEvent]) -> str:
    digest = hashlib.sha256()
    for event in sorted(events, key=lambda item: item.funding_time):
        interval = "" if event.funding_interval_hours is None else format(event.funding_interval_hours, "f")
        line = (
            f"{event.exchange}|{event.market_type}|{event.symbol}|"
            f"{event.funding_time.isoformat()}|{format(event.funding_rate, 'f')}|{interval}\n"
        )
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def funding_quality(
    events: list[FundingEvent],
    *,
    symbol: str,
    window_start: datetime | None,
    window_end: datetime | None,
) -> dict[str, object]:
    """Describe funding coverage without assuming a fixed 8h schedule.

    Expected spacing is the interval published on the events themselves when
    every row carries one. A gap wider than that interval, plus one minute of
    timestamp tolerance, is disclosed. Nothing is interpolated across the gap.
    """
    ordered = sorted(events, key=lambda item: item.funding_time)
    reasons: list[str] = []
    duplicates = 0
    seen: set[datetime] = set()
    for event in ordered:
        if event.symbol != symbol:
            reasons.append(f"foreign symbol {event.symbol}")
        if event.funding_time in seen:
            duplicates += 1
        seen.add(event.funding_time)
        if event.tradable or not event.research_feature_only:
            reasons.append("funding row is marked tradable")
    if duplicates:
        reasons.append(f"duplicate funding times: {duplicates}")
    out_of_order = _out_of_order(events)
    if out_of_order:
        reasons.append(f"out-of-order funding rows: {out_of_order}")
    gaps = _gap_count(ordered)
    if gaps:
        reasons.append(f"funding gaps larger than the published interval: {gaps}")
    in_window = ordered
    if window_start is not None:
        in_window = [event for event in in_window if event.funding_time >= window_start]
    if window_end is not None:
        in_window = [event for event in in_window if event.funding_time < window_end]
    if window_start is not None and window_end is not None and not _covers(in_window, window_start, window_end):
        reasons.append("funding events do not cover the requested window")
    status = "qualified" if ordered and not reasons else ("empty" if not ordered else "unqualified")
    return {
        "symbol": symbol,
        "rows": len(ordered),
        "window_rows": len(in_window),
        "duplicate_rows": duplicates,
        "gap_count": gaps,
        "dataset_hash": funding_dataset_hash(ordered),
        "tradable": False,
        "research_feature_only": True,
        "status": status,
        "reasons": reasons,
        "first_funding_time": ordered[0].funding_time.isoformat() if ordered else None,
        "last_funding_time": ordered[-1].funding_time.isoformat() if ordered else None,
    }


def _out_of_order(events: list[FundingEvent]) -> int:
    count = 0
    previous: datetime | None = None
    for event in events:
        if previous is not None and event.funding_time < previous:
            count += 1
        previous = event.funding_time
    return count


def _gap_count(ordered: list[FundingEvent]) -> int:
    gaps = 0
    for previous, current in zip(ordered, ordered[1:]):
        interval = previous.funding_interval_hours or current.funding_interval_hours
        if interval is None:
            continue
        limit = timedelta(hours=float(interval), minutes=1)
        if current.funding_time - previous.funding_time > limit:
            gaps += 1
    return gaps


def _covers(events: list[FundingEvent], start: datetime, end: datetime) -> bool:
    if not events:
        return False
    intervals = [event.funding_interval_hours for event in events if event.funding_interval_hours is not None]
    if not intervals:
        return events[0].funding_time <= end and events[-1].funding_time >= start
    hours = max(float(item) for item in intervals)
    tolerance = timedelta(minutes=1)
    step = timedelta(hours=hours)
    if events[0].funding_time > start + step + tolerance:
        return False
    if events[-1].funding_time + step + tolerance < end:
        return False
    return True
