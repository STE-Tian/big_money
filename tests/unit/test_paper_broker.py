"""Paper broker state machine, idempotency, recovery, and cash identity."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from pquant.domain.enums import OrderStatus
from pquant.domain.errors import SafetyError
from pquant.execution.brokers.paper import PaperBroker
from pquant.storage.trading_store import TradingStore
from pquant.utils.decimals import floor_to_step


def _broker(tmp_path) -> tuple[PaperBroker, TradingStore]:
    store = TradingStore(tmp_path / "paper.sqlite")
    store.init_schema()
    broker = PaperBroker(
        store,
        allowed_symbols={"BTCUSDT", "ETHUSDT"},
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        qty_step=Decimal("0.00000001"),
        min_notional=Decimal("5"),
    )
    return broker, store


def test_fill_matches_formula_and_restart_restores_cash(tmp_path):
    broker, store = _broker(tmp_path)
    account_id = broker.create_account("paper", Decimal("10000"))
    signal_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    execution_time = datetime(2024, 1, 2, tzinfo=timezone.utc)
    result = broker.process_target(
        account_id=account_id,
        strategy_id="buy_and_hold",
        symbol="BTCUSDT",
        target_weight=Decimal("1"),
        signal_time=signal_time,
        execution_time=execution_time,
        execution_open=Decimal("100"),
    )
    assert result["duplicate"] is False
    order = result["order"]
    assert order["status"] == "FILLED"
    fill_price = Decimal("100") * Decimal("1.0005")
    unit = fill_price * Decimal("1.001")
    quantity = floor_to_step(Decimal("10000") / unit)
    cash = Decimal("10000") - quantity * fill_price * Decimal("1.001")
    view = broker.account_view(account_id)
    assert Decimal(view["account"]["current_cash"]) == cash
    assert Decimal(view["positions"][0]["quantity"]) == quantity
    assert Decimal(view["fills"][0]["fee_amount"]) == quantity * fill_price * Decimal("0.001")
    again = broker.process_target(
        account_id=account_id,
        strategy_id="buy_and_hold",
        symbol="BTCUSDT",
        target_weight=Decimal("1"),
        signal_time=signal_time,
        execution_time=execution_time,
        execution_open=Decimal("100"),
    )
    assert again["duplicate"] is True
    assert len(broker.account_view(account_id)["fills"]) == 1
    restored = PaperBroker(store, allowed_symbols={"BTCUSDT"}).account_view(account_id)
    assert restored["account"]["current_cash"] == view["account"]["current_cash"]
    assert restored["positions"][0]["quantity"] == view["positions"][0]["quantity"]


def test_illegal_weight_is_rejected_once_and_cash_is_unchanged(tmp_path):
    broker, _store = _broker(tmp_path)
    account_id = broker.create_account("paper", Decimal("10000"))
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    first = broker.process_target(
        account_id=account_id,
        strategy_id="bad",
        symbol="BTCUSDT",
        target_weight=Decimal("2"),
        signal_time=when,
        execution_time=when,
        execution_open=Decimal("100"),
    )
    assert first["order"]["status"] == "REJECTED"
    assert broker.account_view(account_id)["account"]["current_cash"] == "10000"
    second = broker.process_target(
        account_id=account_id,
        strategy_id="bad",
        symbol="BTCUSDT",
        target_weight=Decimal("2"),
        signal_time=when,
        execution_time=when,
        execution_open=Decimal("100"),
    )
    assert second["duplicate"] is True
    assert len(broker.account_view(account_id)["orders"]) == 1


def test_order_transitions_follow_the_legal_table(tmp_path):
    broker, store = _broker(tmp_path)
    account_id = broker.create_account("paper", Decimal("1000"))
    now = datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat()
    order_id = "order-1"
    store.apply_transaction(
        [
            (
                """
                INSERT INTO paper_orders (
                    order_id, client_order_id, account_id, strategy_id, symbol, side, order_type,
                    quantity, limit_price, status, created_at, updated_at, source_signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [order_id, "client-1", account_id, "sma_cross", "BTCUSDT", "BUY", "MARKET", "1", None, "CREATED", now, now, "sig"],
            )
        ]
    )
    submitted = broker.transition_for_test(order_id, OrderStatus.SUBMITTED)
    assert submitted["status"] == "SUBMITTED"
    partial = broker.transition_for_test(order_id, OrderStatus.PARTIALLY_FILLED)
    assert partial["status"] == "PARTIALLY_FILLED"
    filled = broker.transition_for_test(order_id, OrderStatus.FILLED)
    assert filled["status"] == "FILLED"
    with pytest.raises(ValueError):
        broker.cancel_order(order_id)
    _ = SafetyError
