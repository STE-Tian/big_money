"""Backtest accounting: next-open fills, costs, and inventory invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pquant.domain.errors import DataQualityError
from pquant.research.backtest.accounting import BookState, rebalance
from pquant.research.backtest.engine import run_backtest
from pquant.research.strategies.buy_hold import BuyAndHoldStrategy
from pquant.research.strategies.cash import CashStrategy
from pquant.utils.decimals import floor_to_step

from tests.conftest import make_candles


def test_signal_on_close_fills_at_next_open_not_same_close():
    candles = make_candles(["110", "130", "150"], opens=["100", "120", "140"])
    targets = [Decimal("1"), None, None]
    result = run_backtest(
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
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.fill_time == candles[1].open_time
    assert fill.fill_price == candles[1].open * Decimal("1.0005")
    assert fill.fill_price != candles[0].close


def test_final_bar_signal_is_not_filled_inside_the_sample():
    candles = make_candles(["10", "11", "12"], opens=["9", "10", "11"])
    result = run_backtest(
        candles,
        [None, None, Decimal("1")],
        opening_target=None,
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="sma_cross",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    assert result.fills == []
    assert result.warnings


def test_unchanged_target_does_not_trade_again():
    candles = make_candles(["10", "11", "12", "13", "14", "15", "16"], opens=["9", "10", "11", "12", "13", "14", "15"])
    targets = [None, None, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"), Decimal("0")]
    result = run_backtest(
        candles,
        targets,
        opening_target=None,
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0"),
        strategy_id="sma_cross",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert result.fills[0].fill_time == candles[3].open_time
    assert result.fills[1].fill_time == candles[6].open_time


def test_buy_cash_identity_matches_independent_formula():
    candles = make_candles(["110"], opens=["100"])
    fee = Decimal("0.001")
    slip = Decimal("0.0005")
    result = run_backtest(
        candles,
        [Decimal("1")],
        opening_target=Decimal("1"),
        initial_cash=Decimal("10000"),
        fee_rate=fee,
        slippage_rate=slip,
        strategy_id="buy_and_hold",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    fill_price = Decimal("100") * (Decimal("1") + slip)
    unit = fill_price * (Decimal("1") + fee)
    quantity = floor_to_step(Decimal("10000") / unit)
    gross = quantity * fill_price
    fee_amount = gross * fee
    cash = Decimal("10000") - gross - fee_amount
    assert result.fills[0].quantity == quantity
    assert result.fills[0].fee == fee_amount
    assert result.final_cash == cash
    assert result.final_quantity == quantity
    assert result.final_equity == cash + quantity * Decimal("110")
    assert result.final_cash >= 0
    assert result.final_quantity >= 0


def test_same_sell_quantity_with_higher_fee_or_slippage_returns_less_cash():
    state = BookState(Decimal("0"), Decimal("2"), Decimal("100"), Decimal("0"))
    low_fee = rebalance(
        state=state,
        target_weight=Decimal("0"),
        open_price=Decimal("120"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0"),
        qty_quantum=Decimal("0.00000001"),
    )
    high_fee = rebalance(
        state=state,
        target_weight=Decimal("0"),
        open_price=Decimal("120"),
        fee_rate=Decimal("0.01"),
        slippage_rate=Decimal("0"),
        qty_quantum=Decimal("0.00000001"),
    )
    assert low_fee.quantity == high_fee.quantity
    assert high_fee.after.cash < low_fee.after.cash
    low_slip = rebalance(
        state=state,
        target_weight=Decimal("0"),
        open_price=Decimal("120"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0"),
        qty_quantum=Decimal("0.00000001"),
    )
    high_slip = rebalance(
        state=state,
        target_weight=Decimal("0"),
        open_price=Decimal("120"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.01"),
        qty_quantum=Decimal("0.00000001"),
    )
    assert high_slip.fill_price < low_slip.fill_price
    assert high_slip.after.cash < low_slip.after.cash


def test_rising_market_higher_fee_does_not_increase_equity():
    candles = make_candles(["105", "115", "125", "135"], opens=["100", "110", "120", "130"])
    kwargs = dict(
        close_targets=[Decimal("1")] * 4,
        opening_target=Decimal("1"),
        initial_cash=Decimal("10000"),
        slippage_rate=Decimal("0"),
        strategy_id="buy_and_hold",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    low = run_backtest(candles, fee_rate=Decimal("0.001"), **kwargs)
    high = run_backtest(candles, fee_rate=Decimal("0.01"), **kwargs)
    assert high.final_equity < low.final_equity


def test_buy_and_hold_trades_once_and_cash_benchmark_does_not_trade():
    candles = make_candles(["11", "12", "13"], opens=["10", "11", "12"])
    held = run_backtest(
        candles,
        BuyAndHoldStrategy().run(candles).close_targets,
        opening_target=Decimal("1"),
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="buy_and_hold",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    cash = run_backtest(
        candles,
        CashStrategy().run(candles).close_targets,
        opening_target=Decimal("0"),
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="cash",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    assert len(held.fills) == 1
    assert held.fee_paid > 0
    assert held.closed_trades == []
    assert cash.fills == []
    assert cash.final_equity == Decimal("10000")
    assert held.initial_cash == cash.initial_cash


def test_no_negative_inventory_on_a_deterministic_path():
    closes = [str(100 + (index % 7) - 3) for index in range(40)]
    opens = [str(Decimal(close) - Decimal("1")) for close in closes]
    candles = make_candles(closes, opens=opens)
    targets = []
    for index in range(40):
        if index < 5:
            targets.append(None)
        else:
            targets.append(Decimal("1") if index % 10 < 5 else Decimal("0"))
    result = run_backtest(
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
    assert all(point.cash >= 0 and point.quantity >= 0 for point in result.equity_curve)


def test_unclosed_bar_is_rejected():
    candles = make_candles(["10", "11"], closed_flags=[True, False])
    with pytest.raises(DataQualityError):
        run_backtest(
            candles,
            [None, None],
            opening_target=None,
            initial_cash=Decimal("10000"),
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.0005"),
            strategy_id="cash",
            strategy_version="1.0.0",
            symbol="BTCUSDT",
            interval="1d",
        )
