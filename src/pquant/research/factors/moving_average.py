"""Causal moving averages. The value at index i uses closes[0:i+1] only."""

from __future__ import annotations

import pandas as pd


def simple_moving_average(close: pd.Series, window: int) -> pd.Series:
    if window < 1:
        raise ValueError("SMA window must be >= 1")
    return close.rolling(window=window, min_periods=window).mean()
