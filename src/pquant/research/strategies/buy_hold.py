"""Buy at the first sample open and hold. That decision is known before the first bar."""

from __future__ import annotations

from decimal import Decimal

from pquant.domain.models import Candle
from pquant.research.strategies.base import Strategy, StrategyOutput


class BuyAndHoldStrategy(Strategy):
    strategy_id = "buy_and_hold"

    def __init__(self, version: str = "1.0.0") -> None:
        self.version = version

    def run(self, candles: list[Candle]) -> StrategyOutput:
        n = len(candles)
        one = Decimal("1")
        return StrategyOutput(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            opening_target=one if n else None,
            close_targets=[one for _ in range(n)],
            signal_values=[1.0 for _ in range(n)],
            reasons=["buy_and_hold_known_before_first_open" for _ in range(n)],
        )
