"""Cash benchmark. Equity stays at the initial cash. No yield is assumed."""

from __future__ import annotations

from decimal import Decimal

from pquant.domain.models import Candle
from pquant.research.strategies.base import Strategy, StrategyOutput


class CashStrategy(Strategy):
    strategy_id = "cash"

    def __init__(self, version: str = "1.0.0") -> None:
        self.version = version

    def run(self, candles: list[Candle]) -> StrategyOutput:
        n = len(candles)
        zero = Decimal("0")
        return StrategyOutput(
            strategy_id=self.strategy_id,
            strategy_version=self.version,
            opening_target=zero if n else None,
            close_targets=[zero for _ in range(n)],
            signal_values=[0.0 for _ in range(n)],
            reasons=["cash_benchmark" for _ in range(n)],
        )
