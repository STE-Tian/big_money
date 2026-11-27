"""Single cash-and-quantity accounting path used by the backtest and the paper broker.

Buy fill price = open * (1 + slippage). Sell fill price = open * (1 - slippage).
Fee = fill notional * fee rate and is deducted from cash. Slippage is embedded in the
fill price and is also reported as a memo amount. It is not deducted a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pquant.domain.errors import AccountingError, SafetyError
from pquant.utils.decimals import floor_to_step


@dataclass(frozen=True)
class BookState:
    cash: Decimal
    quantity: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class RebalanceResult:
    side: str | None
    quantity: Decimal
    raw_open: Decimal
    fill_price: Decimal
    fee: Decimal
    slippage_cost: Decimal
    before: BookState
    after: BookState
    realized_pnl_delta: Decimal
    reason: str


def rebalance(
    *,
    state: BookState,
    target_weight: Decimal,
    open_price: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    qty_quantum: Decimal,
) -> RebalanceResult:
    if target_weight < 0 or target_weight > 1:
        raise SafetyError("target weight must stay inside [0, 1]; shorting and leverage are disabled")
    if open_price <= 0:
        raise AccountingError("open price must be positive")
    if fee_rate < 0 or slippage_rate < 0 or fee_rate >= 1 or slippage_rate >= 1:
        raise AccountingError("fee and slippage must be in [0, 1)")
    if state.quantity < 0 or state.cash < 0:
        raise AccountingError("book already has negative cash or quantity")

    equity = state.cash + state.quantity * open_price
    raw_target_qty = target_weight * equity / open_price
    if target_weight == 0:
        target_qty = Decimal("0")
    else:
        target_qty = floor_to_step(raw_target_qty, qty_quantum)
    delta = target_qty - state.quantity

    if delta > 0:
        return _buy(
            state=state,
            buy_qty_cap=delta,
            open_price=open_price,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            qty_quantum=qty_quantum,
        )
    if delta < 0:
        sell_qty = state.quantity if target_weight == 0 else min(state.quantity, -delta)
        return _sell(
            state=state,
            sell_qty=sell_qty,
            open_price=open_price,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            qty_quantum=qty_quantum,
            sell_all=target_weight == 0,
        )
    return _flat(state, open_price, "target quantity already matches")


def _buy(
    *,
    state: BookState,
    buy_qty_cap: Decimal,
    open_price: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    qty_quantum: Decimal,
) -> RebalanceResult:
    fill_price = open_price * (Decimal("1") + slippage_rate)
    unit_cost = fill_price * (Decimal("1") + fee_rate)
    if unit_cost <= 0:
        raise AccountingError("unit cost must be positive")
    affordable = floor_to_step(state.cash / unit_cost, qty_quantum)
    buy_qty = min(floor_to_step(buy_qty_cap, qty_quantum), affordable)
    if buy_qty <= 0:
        return _flat(state, open_price, "buy rounded to zero or cash cannot fund one step")
    gross = buy_qty * fill_price
    fee = gross * fee_rate
    cash_after = state.cash - gross - fee
    if cash_after < 0:
        raise AccountingError("buy drove cash negative")
    qty_after = state.quantity + buy_qty
    avg_after = ((state.average_entry_price * state.quantity) + (fill_price * buy_qty)) / qty_after
    after = BookState(cash_after, qty_after, avg_after, state.realized_pnl)
    _assert_book(after)
    return RebalanceResult(
        side="BUY",
        quantity=buy_qty,
        raw_open=open_price,
        fill_price=fill_price,
        fee=fee,
        slippage_cost=buy_qty * (fill_price - open_price),
        before=state,
        after=after,
        realized_pnl_delta=Decimal("0"),
        reason="buy toward target weight",
    )


def _sell(
    *,
    state: BookState,
    sell_qty: Decimal,
    open_price: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    qty_quantum: Decimal,
    sell_all: bool,
) -> RebalanceResult:
    fill_price = open_price * (Decimal("1") - slippage_rate)
    if fill_price <= 0:
        raise AccountingError("sell fill price must be positive")
    if sell_all:
        quantity = state.quantity
    else:
        quantity = min(state.quantity, floor_to_step(sell_qty, qty_quantum))
    if quantity <= 0:
        return _flat(state, open_price, "sell rounded to zero")
    if quantity > state.quantity:
        raise AccountingError("sell quantity exceeds spot position")
    gross = quantity * fill_price
    fee = gross * fee_rate
    cash_after = state.cash + gross - fee
    qty_after = state.quantity - quantity
    if qty_after < 0:
        raise AccountingError("spot quantity went negative")
    realized_delta = (fill_price - state.average_entry_price) * quantity - fee
    avg_after = Decimal("0") if qty_after == 0 else state.average_entry_price
    after = BookState(cash_after, qty_after, avg_after, state.realized_pnl + realized_delta)
    _assert_book(after)
    return RebalanceResult(
        side="SELL",
        quantity=quantity,
        raw_open=open_price,
        fill_price=fill_price,
        fee=fee,
        slippage_cost=quantity * (open_price - fill_price),
        before=state,
        after=after,
        realized_pnl_delta=realized_delta,
        reason="sell toward target weight",
    )


def _flat(state: BookState, open_price: Decimal, reason: str) -> RebalanceResult:
    return RebalanceResult(
        side=None,
        quantity=Decimal("0"),
        raw_open=open_price,
        fill_price=open_price,
        fee=Decimal("0"),
        slippage_cost=Decimal("0"),
        before=state,
        after=state,
        realized_pnl_delta=Decimal("0"),
        reason=reason,
    )


def _assert_book(state: BookState) -> None:
    if state.cash < 0:
        raise AccountingError(f"negative cash {state.cash}")
    if state.quantity < 0:
        raise AccountingError(f"negative spot quantity {state.quantity}")
