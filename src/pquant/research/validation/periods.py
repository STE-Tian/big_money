"""Calendar splits and a pre-specified trailing-return regime label.

Regime rule, fixed before looking at strategy results:
trailing 90-calendar-day close-to-close return > +20% is bull,
< -20% is bear, otherwise range. The label at bar t uses only closes at or before t.
"""

from __future__ import annotations

from datetime import datetime

from pquant.domain.models import Candle
from pquant.research.backtest.engine import EquityPoint


def calendar_returns(points: list[EquityPoint], candles: list[Candle]) -> dict[str, list[dict[str, object]]]:
    closes = points[1:]
    if len(closes) != len(candles):
        raise ValueError("equity points after the initial mark must match candles")
    initial = points[0].equity
    yearly = _bucket(closes, lambda ts: str(ts.year), initial)
    quarterly = _bucket(closes, lambda ts: f"{ts.year}Q{(ts.month - 1) // 3 + 1}", initial)
    return {"yearly": yearly, "quarterly": quarterly}


def negative_buckets(rows: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for row in rows:
        value = row.get("total_return")
        if isinstance(value, float) and value < 0:
            names.append(str(row["bucket"]))
    return names


def drawdown_episode(points: list[EquityPoint]) -> dict[str, object]:
    if not points:
        return {"peak_time": None, "trough_time": None, "recovery_time": None, "max_drawdown": None}
    trough_index = min(range(len(points)), key=lambda index: points[index].drawdown)
    peak_equity = max(point.equity for point in points[: trough_index + 1])
    peak_index = 0
    for index, point in enumerate(points[: trough_index + 1]):
        if point.equity == peak_equity:
            peak_index = index
    recovery = None
    for point in points[trough_index + 1 :]:
        if point.equity >= peak_equity:
            recovery = point.time
            break
    return {
        "peak_time": points[peak_index].time.isoformat(),
        "trough_time": points[trough_index].time.isoformat(),
        "recovery_time": recovery.isoformat() if isinstance(recovery, datetime) else None,
        "max_drawdown": float(points[trough_index].drawdown),
        "recovered": recovery is not None,
    }


def regime_labels(candles: list[Candle]) -> list[str]:
    lookback = _lookback_bars(candles[0].interval) if candles else 90
    labels: list[str] = []
    closes = [candle.close for candle in candles]
    for index, close in enumerate(closes):
        if index < lookback:
            labels.append("unclassified")
            continue
        past = closes[index - lookback]
        if past <= 0:
            labels.append("unclassified")
            continue
        change = close / past - 1
        if change > 0.20:
            labels.append("bull")
        elif change < -0.20:
            labels.append("bear")
        else:
            labels.append("range")
    return labels


def regime_mean_bar_return(points: list[EquityPoint], labels: list[str]) -> dict[str, float | None]:
    closes = points[1:]
    if len(closes) != len(labels):
        raise ValueError("regime labels must align with candles")
    grouped: dict[str, list[float]] = {}
    previous = points[0].equity
    for point, label in zip(closes, labels):
        change = float(point.equity / previous - 1)
        grouped.setdefault(label, []).append(change)
        previous = point.equity
    return {key: (sum(values) / len(values) if values else None) for key, values in grouped.items()}


def _bucket(points: list[EquityPoint], key_fn, initial_equity) -> list[dict[str, object]]:
    buckets: dict[str, list[EquityPoint]] = {}
    for point in points:
        buckets.setdefault(key_fn(point.time), []).append(point)
    rows: list[dict[str, object]] = []
    previous_equity = initial_equity
    first = True
    for name in sorted(buckets):
        series = buckets[name]
        end_equity = series[-1].equity
        total = float(end_equity / previous_equity - 1) if previous_equity > 0 else None
        rows.append(
            {
                "bucket": name,
                "start_equity": float(previous_equity),
                "end_equity": float(end_equity),
                "total_return": total,
                "bars": len(series),
                "partial_start": first,
            }
        )
        previous_equity = end_equity
        first = False
    return rows


def _lookback_bars(interval: str) -> int:
    if interval == "1d":
        return 90
    if interval == "4h":
        return 90 * 6
    if interval == "1h":
        return 90 * 24
    raise ValueError(interval)
