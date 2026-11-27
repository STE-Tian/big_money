"""V1 fill and accounting semantics. V2 must not change these results."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pquant.config.loader import load_settings
from pquant.domain.errors import DataQualityError
from pquant.research.backtest.accounting import BookState, rebalance
from pquant.research.backtest.engine import run_backtest

from tests.conftest import ROOT, make_candles


def test_v1_defaults_remain_daily_next_open_and_research(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    settings = load_settings(ROOT)
    assert settings.market_data.intervals == ["1d"]
    assert settings.market_data.exclude_open_candle is True
    assert settings.backtest.signal_execution == "next_bar_open"
    assert settings.app.trading_mode.value == "research"
    assert settings.app.live_trading_enabled is False
    assert settings.safety.allow_live_orders is False
    assert settings.safety.allow_short is False
    assert settings.safety.allow_leverage is False


def test_signal_at_bar_t_close_fills_at_bar_t_plus_one_open():
    candles = make_candles(["110", "130", "150"], opens=["100", "120", "140"])
    result = run_backtest(
        candles,
        [Decimal("1"), None, None],
        opening_target=None,
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="sma_cross",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    fill = result.fills[0]
    assert fill.signal_time == candles[0].close_time
    assert fill.fill_time == candles[1].open_time
    assert fill.raw_open == candles[1].open
    assert fill.fill_price == candles[1].open * Decimal("1.0005")
    assert fill.fill_price != candles[0].close
    assert fill.fill_price != candles[1].close
    assert result.final_equity == result.final_cash + result.final_quantity * candles[-1].close


def test_sell_fill_uses_open_minus_slippage_and_deducts_fee_once():
    state = BookState(Decimal("0"), Decimal("2"), Decimal("100"), Decimal("0"))
    result = rebalance(
        state=state,
        target_weight=Decimal("0"),
        open_price=Decimal("120"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        qty_quantum=Decimal("0.00000001"),
    )
    assert result.side == "SELL"
    assert result.fill_price == Decimal("120") * (Decimal("1") - Decimal("0.0005"))
    gross = Decimal("2") * result.fill_price
    assert result.fee == gross * Decimal("0.001")
    assert result.after.cash == gross - result.fee
    assert result.after.quantity == Decimal("0")
    assert result.slippage_cost == Decimal("2") * (Decimal("120") - result.fill_price)


def test_last_bar_signal_and_unclosed_bar_do_not_fill():
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
    unclosed = [candle.model_copy(update={"is_closed": False}) for candle in candles]
    with pytest.raises(DataQualityError):
        run_backtest(
            unclosed,
            [Decimal("1"), None, None],
            opening_target=None,
            initial_cash=Decimal("10000"),
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.0005"),
            strategy_id="sma_cross",
            strategy_version="1.0.0",
            symbol="BTCUSDT",
            interval="1d",
        )
