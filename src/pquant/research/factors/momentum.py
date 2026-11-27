"""Causal momentum. close.shift(window) is past data, never future data."""

from __future__ import annotations

import pandas as pd


def momentum(close: pd.Series, window: int) -> pd.Series:
    if window < 1:
        raise ValueError("momentum window must be >= 1")
    return close / close.shift(window) - 1.0
