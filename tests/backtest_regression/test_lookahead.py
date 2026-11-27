"""Future prices must not change signals or fills that were already knowable."""

from __future__ import annotations

from decimal import Decimal

from pquant.research.backtest.engine import run_backtest
from pquant.research.strategies.momentum import MomentumStrategy
from pquant.research.strategies.sma_cross import SmaCrossStrategy

from tests.conftest import make_candles


def test_future_price_changes_do_not_change_past_signals():
    closes = [str(100 + (index % 11) + index / 10) for index in range(80)]
    original = make_candles(closes)
    before = SmaCrossStrategy(10, 20).run(original)
    momentum_before = MomentumStrategy(15).run(original)
    tampered_closes = closes.copy()
    for index in range(50, 80):
        tampered_closes[index] = str(Decimal(tampered_closes[index]) * Decimal("3"))
    tampered = make_candles(tampered_closes)
    after = SmaCrossStrategy(10, 20).run(tampered)
    momentum_after = MomentumStrategy(15).run(tampered)
    assert before.close_targets[:50] == after.close_targets[:50]
    assert before.signal_values[:50] == after.signal_values[:50]
    assert momentum_before.close_targets[:50] == momentum_after.close_targets[:50]


def test_future_opens_do_not_change_earlier_fills():
    candles = make_candles(
        [str(100 + index) for index in range(12)],
        opens=[str(90 + index) for index in range(12)],
    )
    targets = [Decimal("1") if index >= 2 else None for index in range(12)]
    first = run_backtest(
        candles,
        targets,
        opening_target=None,
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="sma_cross",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    changed = []
    for index, candle in enumerate(candles):
        if index > 6:
            changed.append(candle.model_copy(update={"open": candle.open * Decimal("5"), "close": candle.close * Decimal("5"), "high": candle.high * Decimal("5")}))
        else:
            changed.append(candle)
    second = run_backtest(
        changed,
        targets,
        opening_target=None,
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="sma_cross",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    early_first = [fill for fill in first.fills if fill.fill_time <= candles[6].open_time]
    early_second = [fill for fill in second.fills if fill.fill_time <= candles[6].open_time]
    assert [(fill.fill_time, fill.fill_price, fill.quantity) for fill in early_first] == [
        (fill.fill_time, fill.fill_price, fill.quantity) for fill in early_second
    ]
