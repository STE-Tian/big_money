"""SMA cross. Warm-up rows stay None so the engine does not trade them."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from pquant.domain.models import Candle
from pquant.research.factors.moving_average import simple_moving_average
from pquant.research.strategies.base import Strategy, StrategyOutput


class SmaCrossStrategy(Strategy):
    strategy_id = "sma_cross"

    def __init__(self, short_window: int = 20, long_window: int = 50, version: str = "1.0.0") -> None:
        if short_window < 1 or long_window <= short_window:
            raise ValueError("SMA requires 1 <= short_window < long_window")
        self.short_window = short_window
        self.long_window = long_window
        self.version = version

    def run(self, candles: list[Candle]) -> StrategyOutput:
        close = pd.Series([float(candle.close) for candle in candles], dtype="float64")
        short = simple_moving_average(close, self.short_window)
        long = simple_moving_average(close, self.long_window)
        targets: list[Decimal | None] = []
        values: list[float | None] = []
        reasons: list[str] = []
        for short_value, long_value in zip(short.tolist(), long.tolist()):
            if pd.isna(short_value) or pd.isna(long_value):
                targets.append(None)
                values.append(None)
                reasons.append("warmup")
                continue
            spread = float(short_value) - float(long_value)
            if short_value > long_value:
                targets.append(Decimal("1"))
                reasons.append("sma_short_above_sma_long")
            else:
                targets.append(Decimal("0"))
                reasons.append("sma_short_not_above_sma_long")
            values.append(spread)
        return StrategyOutput(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            opening_target=None,
            close_targets=targets,
            signal_values=values,
            reasons=reasons,
        )
