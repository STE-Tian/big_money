"""Registered strategies. The agent may only select ids from this registry."""

from __future__ import annotations

from pquant.config.settings import Settings
from pquant.research.strategies.base import Strategy
from pquant.research.strategies.buy_hold import BuyAndHoldStrategy
from pquant.research.strategies.cash import CashStrategy
from pquant.research.strategies.momentum import MomentumStrategy
from pquant.research.strategies.sma_cross import SmaCrossStrategy

SMA_SHORT_GRID = (10, 20, 30)
SMA_LONG_GRID = (50, 100, 150)
MOMENTUM_GRID = (20, 60, 120)


def _param(model: object, key: str, default: int) -> int:
    extra = getattr(model, "model_extra", None) or {}
    if key not in extra:
        raise ValueError(f"strategy config missing {key}")
    return int(extra[key])


def build_strategies(settings: Settings) -> list[Strategy]:
    sma = settings.strategies.sma_cross
    momentum_cfg = settings.strategies.momentum
    return [
        BuyAndHoldStrategy(version=settings.strategies.buy_and_hold.version),
        SmaCrossStrategy(
            short_window=_param(sma, "short_window", 20),
            long_window=_param(sma, "long_window", 50),
            version=sma.version,
        ),
        MomentumStrategy(window=_param(momentum_cfg, "window", 60), version=momentum_cfg.version),
        CashStrategy(version=settings.strategies.cash.version),
    ]


def strategy_by_id(settings: Settings) -> dict[str, Strategy]:
    return {item.strategy_id: item for item in build_strategies(settings)}


def robustness_grid(settings: Settings) -> tuple[list[tuple[int, int]], list[int]]:
    declared = settings.strategies.robustness
    shorts = declared.get("sma_short_windows", list(SMA_SHORT_GRID))
    longs = declared.get("sma_long_windows", list(SMA_LONG_GRID))
    windows = declared.get("momentum_windows", list(MOMENTUM_GRID))
    pairs = [(int(short), int(long)) for short in shorts for long in longs if int(short) < int(long)]
    return pairs, [int(item) for item in windows]
