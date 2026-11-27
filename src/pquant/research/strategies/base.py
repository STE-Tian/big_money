"""Strategy output is a target weight per closed bar, not an order."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pquant.domain.models import Candle


@dataclass(frozen=True)
class StrategyOutput:
    strategy_id: str
    strategy_version: str
    opening_target: Decimal | None
    close_targets: list[Decimal | None]
    signal_values: list[float | None]
    reasons: list[str]

    def __post_init__(self) -> None:
        n = len(self.close_targets)
        if len(self.signal_values) != n or len(self.reasons) != n:
            raise ValueError("strategy output arrays must have the same length")


class Strategy:
    strategy_id: str
    version: str

    def run(self, candles: list[Candle]) -> StrategyOutput:
        raise NotImplementedError
