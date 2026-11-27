"""60-day momentum by default. Positive momentum targets 100% spot, otherwise cash."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from pquant.domain.models import Candle
from pquant.research.factors.momentum import momentum
from pquant.research.strategies.base import Strategy, StrategyOutput


class MomentumStrategy(Strategy):
    strategy_id = "momentum"

    def __init__(self, window: int = 60, version: str = "1.0.0") -> None:
        if window < 1:
            raise ValueError("momentum window must be >= 1")
        self.window = window
        self.version = version

    def run(self, candles: list[Candle]) -> StrategyOutput:
        close = pd.Series([float(candle.close) for candle in candles], dtype="float64")
        series = momentum(close, self.window)
        targets: list[Decimal | None] = []
        values: list[float | None] = []
        reasons: list[str] = []
        for value in series.tolist():
            if pd.isna(value):
                targets.append(None)
                values.append(None)
                reasons.append("warmup")
                continue
            number = float(value)
            if number > 0:
                targets.append(Decimal("1"))
                reasons.append("momentum_positive")
            else:
                targets.append(Decimal("0"))
                reasons.append("momentum_not_positive")
            values.append(number)
        return StrategyOutput(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            opening_target=None,
            close_targets=targets,
            signal_values=values,
            reasons=reasons,
        )
