"""Event-driven long-only / cash backtest.

A target decided at the close of bar t is filled at the open of bar t+1.
The opening target, used by buy-and-hold, is known before the first open.
The same bar's close is never used as that bar's fill price.
Unclosed bars must be removed by the caller. This engine rejects them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from pquant.domain.errors import AccountingError, DataQualityError
from pquant.domain.models import Candle
from pquant.ingestion.quality import ohlc_is_valid
from pquant.research.backtest.accounting import BookState, rebalance
from pquant.utils.decimals import QTY_QUANTUM, weight_changed


@dataclass
class FillRecord:
    symbol: str
    strategy_id: str
    side: str
    signal_time: datetime | None
    fill_time: datetime
    raw_open: Decimal
    fill_price: Decimal
    quantity: Decimal
    fee: Decimal
    slippage_cost: Decimal
    target_weight: Decimal
    cash_after: Decimal
    quantity_after: Decimal
    average_entry_price: Decimal


@dataclass
class ClosedTrade:
    entry_time: datetime
    exit_time: datetime
    quantity: Decimal
    pnl: Decimal
    won: bool


@dataclass
class EquityPoint:
    time: datetime
    equity: Decimal
    cash: Decimal
    quantity: Decimal
    mark_price: Decimal
    drawdown: Decimal


@dataclass
class BacktestResult:
    strategy_id: str
    strategy_version: str
    symbol: str
    interval: str
    segment: str
    fills: list[FillRecord]
    closed_trades: list[ClosedTrade]
    equity_curve: list[EquityPoint]
    initial_cash: Decimal
    fee_rate: Decimal
    slippage_rate: Decimal
    fee_paid: Decimal
    slippage_cost: Decimal
    final_cash: Decimal
    final_quantity: Decimal
    final_equity: Decimal
    warnings: list[str] = field(default_factory=list)


def run_backtest(
    candles: list[Candle],
    close_targets: list[Decimal | None],
    *,
    opening_target: Decimal | None,
    initial_cash: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    interval: str,
    segment: str = "full",
    qty_quantum: Decimal = QTY_QUANTUM,
) -> BacktestResult:
    if not candles:
        raise DataQualityError("backtest received no candles")
    if len(close_targets) != len(candles):
        raise DataQualityError("close targets must align 1:1 with candles")
    _validate_bars(candles)
    if any(not candle.is_closed for candle in candles):
        raise DataQualityError("unclosed candles cannot enter a historical backtest")

    state = BookState(initial_cash, Decimal("0"), Decimal("0"), Decimal("0"))
    last_target = Decimal("0")
    fills: list[FillRecord] = []
    closed: list[ClosedTrade] = []
    points: list[EquityPoint] = []
    fee_paid = Decimal("0")
    slippage_paid = Decimal("0")
    flat_cash = state.cash
    entry_time: datetime | None = None
    warnings: list[str] = []

    points.append(
        EquityPoint(
            time=candles[0].open_time,
            equity=initial_cash,
            cash=initial_cash,
            quantity=Decimal("0"),
            mark_price=candles[0].open,
            drawdown=Decimal("0"),
        )
    )

    for index, candle in enumerate(candles):
        exec_target = opening_target if index == 0 else close_targets[index - 1]
        signal_time = candles[0].open_time if index == 0 else candles[index - 1].close_time
        if exec_target is not None and weight_changed(exec_target, last_target):
            result = rebalance(
                state=state,
                target_weight=exec_target,
                open_price=candle.open,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                qty_quantum=qty_quantum,
            )
            if result.side is not None:
                if result.side == "BUY" and result.fill_price != candle.open * (Decimal("1") + slippage_rate):
                    raise AccountingError("buy fill drifted from next-open slippage formula")
                if result.side == "SELL" and result.fill_price != candle.open * (Decimal("1") - slippage_rate):
                    raise AccountingError("sell fill drifted from next-open slippage formula")
                # Same-bar close is not a fill. Guard the accidental identity.
                if result.fill_price == candle.close and candle.open != candle.close and slippage_rate == 0 and fee_rate == 0:
                    warnings.append("fill price equals close on a bar whose open differs; inspect the bar")
                fills.append(
                    FillRecord(
                        symbol=symbol,
                        strategy_id=strategy_id,
                        side=result.side,
                        signal_time=None if index == 0 and opening_target is not None else signal_time,
                        fill_time=candle.open_time,
                        raw_open=result.raw_open,
                        fill_price=result.fill_price,
                        quantity=result.quantity,
                        fee=result.fee,
                        slippage_cost=result.slippage_cost,
                        target_weight=exec_target,
                        cash_after=result.after.cash,
                        quantity_after=result.after.quantity,
                        average_entry_price=result.after.average_entry_price,
                    )
                )
                fee_paid += result.fee
                slippage_paid += result.slippage_cost
                if result.before.quantity == 0 and result.after.quantity > 0:
                    flat_cash = result.before.cash
                    entry_time = candle.open_time
                if result.before.quantity > 0 and result.after.quantity == 0:
                    pnl = result.after.cash - flat_cash
                    closed.append(
                        ClosedTrade(
                            entry_time=entry_time or candle.open_time,
                            exit_time=candle.open_time,
                            quantity=result.quantity,
                            pnl=pnl,
                            won=pnl > 0,
                        )
                    )
                    entry_time = None
            state = result.after
            last_target = exec_target
        elif exec_target is not None:
            last_target = exec_target

        equity = state.cash + state.quantity * candle.close
        if equity <= 0:
            raise AccountingError("equity is not positive")
        if state.cash < 0 or state.quantity < 0:
            raise AccountingError("negative cash or quantity after mark")
        if equity != state.cash + state.quantity * candle.close:
            raise AccountingError("equity identity failed")
        points.append(
            EquityPoint(
                time=candle.close_time,
                equity=equity,
                cash=state.cash,
                quantity=state.quantity,
                mark_price=candle.close,
                drawdown=Decimal("0"),
            )
        )

    final_signal = close_targets[-1]
    if final_signal is not None and weight_changed(final_signal, last_target):
        warnings.append("the final bar formed a signal that was not filled because the sample has no following open")

    peak = points[0].equity
    for point in points:
        if point.equity > peak:
            peak = point.equity
        point.drawdown = point.equity / peak - Decimal("1")

    return BacktestResult(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        symbol=symbol,
        interval=interval,
        segment=segment,
        fills=fills,
        closed_trades=closed,
        equity_curve=points,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        fee_paid=fee_paid,
        slippage_cost=slippage_paid,
        final_cash=state.cash,
        final_quantity=state.quantity,
        final_equity=points[-1].equity,
        warnings=warnings,
    )


def _validate_bars(candles: list[Candle]) -> None:
    previous: datetime | None = None
    for candle in candles:
        if previous is not None and candle.open_time <= previous:
            raise DataQualityError("backtest candles must be strictly increasing and unique")
        if not ohlc_is_valid(candle):
            raise DataQualityError(f"invalid OHLC at {candle.open_time.isoformat()}")
        if candle.volume < 0 or candle.quote_volume < 0:
            raise DataQualityError(f"negative volume at {candle.open_time.isoformat()}")
        previous = candle.open_time
