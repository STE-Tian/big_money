"""Decision frames and forward labels on the 4h clock.

A label at bar i with horizon H is the simple close-to-close return from i to
i+H. It is undefined unless those two opens are exactly H bars apart, so a
missing bar is not bridged. Features come from the P1 frame at the same open
time, plus the latest 1h feature whose available_time is at or before the 4h
close and not more than one hour earlier. Nothing in this module fills a
missing bar with a future value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from pquant.domain.errors import DataQualityError
from pquant.domain.models import Candle
from pquant.ingestion.quality import closed_only
from pquant.utils.time import interval_ms
from pquant.v2.p2_config import P2Settings


@dataclass
class DecisionFrame:
    symbol: str
    interval: str
    candles: list[Candle]
    feature_names: list[str]
    features: np.ndarray
    labels: dict[str, np.ndarray]
    label_end_index: dict[str, np.ndarray]
    dropped_noncanonical: int = 0
    dropped_hourly_noncanonical: int = 0

    def __post_init__(self) -> None:
        n = len(self.candles)
        if self.features.shape != (n, len(self.feature_names)):
            raise DataQualityError(f"{self.symbol} feature matrix does not match its candles")
        for name, values in self.labels.items():
            if len(values) != n or len(self.label_end_index[name]) != n:
                raise DataQualityError(f"{self.symbol} label {name} does not match its candles")

    def column(self, name: str) -> np.ndarray:
        try:
            index = self.feature_names.index(name)
        except ValueError as exc:
            raise DataQualityError(f"{self.symbol} decision frame has no column {name}") from exc
        return self.features[:, index]


def assemble_decision_frame(
    candles: list[Candle],
    features: pd.DataFrame,
    hourly_features: pd.DataFrame | None,
    settings: P2Settings,
    *,
    symbol: str,
) -> DecisionFrame:
    interval = settings.decision_interval
    closed = [candle for candle in closed_only(candles) if candle.symbol == symbol and candle.interval == interval]
    closed.sort(key=lambda candle: candle.open_time)
    closed, dropped_noncanonical = _canonical_bars(closed, interval)
    if len(closed) < 2:
        raise DataQualityError(f"{symbol} {interval} has no closed series to label")
    feature_block, names = _align_features(closed, features, settings.feature_columns, symbol, interval)
    hourly_block, hourly_names, dropped_hourly = _align_hourly(
        closed, hourly_features, settings.hourly_asof_columns, symbol
    )
    matrix = np.concatenate([feature_block, hourly_block], axis=1)
    labels: dict[str, np.ndarray] = {}
    ends: dict[str, np.ndarray] = {}
    for horizon, bars in settings.horizons_bars.items():
        values, end_index = forward_simple_returns(closed, bars=bars, interval=interval)
        labels[horizon] = values
        ends[horizon] = end_index
    return DecisionFrame(
        symbol=symbol,
        interval=interval,
        candles=closed,
        feature_names=names + hourly_names,
        features=matrix,
        labels=labels,
        label_end_index=ends,
        dropped_noncanonical=dropped_noncanonical,
        dropped_hourly_noncanonical=dropped_hourly,
    )


def forward_simple_returns(
    candles: list[Candle],
    *,
    bars: int,
    interval: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Simple return close[i+bars] / close[i] - 1, or NaN when the span is gapped."""
    if bars < 1:
        raise ValueError("horizon bars must be positive")
    n = len(candles)
    returns = np.full(n, np.nan, dtype=np.float64)
    end_index = np.full(n, -1, dtype=np.int64)
    step = timedelta(milliseconds=interval_ms(interval))
    expected = step * bars
    for index in range(n - bars):
        future = index + bars
        if candles[future].open_time - candles[index].open_time != expected:
            continue
        start_close = candles[index].close
        future_close = candles[future].close
        if start_close <= 0 or future_close <= 0:
            continue
        returns[index] = float(future_close / start_close - Decimal("1"))
        end_index[index] = future
    return returns, end_index


def _align_features(
    candles: list[Candle],
    features: pd.DataFrame,
    columns: list[str],
    symbol: str,
    interval: str,
) -> tuple[np.ndarray, list[str]]:
    if features.empty:
        raise DataQualityError(f"{symbol} {interval} feature frame is empty")
    frame = features.copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    if frame["open_time"].duplicated().any():
        raise DataQualityError(f"{symbol} {interval} feature frame has duplicate open times")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise DataQualityError(f"{symbol} {interval} feature frame missing {missing}")
    indexed = frame.set_index("open_time")
    rows: list[np.ndarray] = []
    for candle in candles:
        key = pd.Timestamp(candle.open_time)
        if key not in indexed.index:
            rows.append(np.full(len(columns), np.nan))
            continue
        record = indexed.loc[key]
        rows.append(np.array([_finite_float(record[column]) for column in columns], dtype=np.float64))
    names = [f"h4_{column}" for column in columns]
    return np.vstack(rows), names


def _canonical_bars(candles: list[Candle], interval: str) -> tuple[list[Candle], int]:
    """Keep bars whose close_time is open + interval - 1ms.

    A few official archive rows, and some REST revisions of those rows, carry a
    close time that is not the bar boundary. Those rows are not treated as
    closed research bars and they are not repaired.
    """
    step = timedelta(milliseconds=interval_ms(interval)) - timedelta(milliseconds=1)
    kept: list[Candle] = []
    dropped = 0
    for candle in candles:
        if candle.close_time - candle.open_time == step and candle.close_time > candle.open_time:
            kept.append(candle)
        else:
            dropped += 1
    return kept, dropped


def _align_hourly(
    candles: list[Candle],
    hourly: pd.DataFrame | None,
    columns: list[str],
    symbol: str,
) -> tuple[np.ndarray, list[str], int]:
    names = [f"h1_{column}" for column in columns]
    n = len(candles)
    if hourly is None or hourly.empty or not columns:
        return np.full((n, len(columns)), np.nan), names, 0
    frame = hourly.copy()
    if "available_time" not in frame.columns or "open_time" not in frame.columns:
        raise DataQualityError(f"{symbol} 1h features have no open_time and available_time")
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise DataQualityError(f"{symbol} 1h features missing {missing}")
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame["available_time"] = pd.to_datetime(frame["available_time"], utc=True)
    step = pd.Timedelta(milliseconds=interval_ms("1h") - 1)
    canonical = frame["available_time"] - frame["open_time"] == step
    dropped = int((~canonical).sum())
    frame = frame.loc[canonical].copy()
    if frame.empty:
        return np.full((n, len(columns)), np.nan), names, dropped
    if frame["available_time"].duplicated().any():
        raise DataQualityError(f"{symbol} 1h features have duplicate available_time values")
    frame = frame.sort_values("available_time")
    right = frame.loc[:, ["available_time", *columns]].copy()
    for column in columns:
        right[column] = pd.to_numeric(right[column], errors="coerce")
    left = pd.DataFrame({"available_time": [pd.Timestamp(candle.close_time) for candle in candles]})
    left["available_time"] = pd.to_datetime(left["available_time"], utc=True)
    merged = pd.merge_asof(
        left,
        right,
        on="available_time",
        direction="backward",
        tolerance=pd.Timedelta(hours=1),
    )
    block = merged.loc[:, columns].to_numpy(dtype=np.float64)
    return block, names, dropped


def _finite_float(value: object) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    try:
        if pd.isna(value):
            return np.nan
    except TypeError:
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    if not np.isfinite(number):
        return np.nan
    return number
