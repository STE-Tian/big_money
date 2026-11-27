"""Local paper broker.

Orders never leave the process. There is no exchange client in this module.
A repeated client_order_id returns the original order and does not create another fill.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pquant.domain.enums import OrderSide, OrderStatus, OrderType, assert_legal_transition
from pquant.domain.errors import AccountingError, SafetyError
from pquant.execution.portfolio.accounting import dec
from pquant.execution.risk.checks import assert_spot_constraints, drawdown_breached
from pquant.research.backtest.accounting import BookState, rebalance
from pquant.storage.trading_store import TradingStore
from pquant.utils.decimals import QTY_QUANTUM
from pquant.utils.hashing import client_order_id as make_client_order_id


class PaperBroker:
    def __init__(
        self,
        store: TradingStore,
        *,
        allowed_symbols: set[str],
        quote_asset: str = "USDT",
        qty_step: Decimal = QTY_QUANTUM,
        min_notional: Decimal = Decimal("5"),
        max_drawdown_event: Decimal = Decimal("0.20"),
        fee_rate: Decimal = Decimal("0.001"),
        slippage_rate: Decimal = Decimal("0.0005"),
    ) -> None:
        self.store = store
        self.allowed_symbols = set(allowed_symbols)
        self.quote_asset = quote_asset
        self.qty_step = qty_step
        self.min_notional = min_notional
        self.max_drawdown_event = max_drawdown_event
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    def create_account(self, name: str, initial_cash: Decimal) -> str:
        if initial_cash <= 0:
            raise AccountingError("initial cash must be positive")
        account_id = str(uuid.uuid4())
        created = _now()
        cash = format(initial_cash, "f")
        self.store.apply_transaction(
            [
                (
                    """
                    INSERT INTO paper_accounts (
                        account_id, name, quote_asset, initial_cash, current_cash, created_at, peak_equity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [account_id, name, self.quote_asset, cash, cash, created, cash],
                )
            ]
        )
        return account_id

    def process_target(
        self,
        *,
        account_id: str,
        strategy_id: str,
        symbol: str,
        target_weight: Decimal,
        signal_time: datetime,
        execution_time: datetime,
        execution_open: Decimal,
        mark_price: Decimal | None = None,
    ) -> dict[str, object]:
        try:
            assert_spot_constraints(target_weight=target_weight, symbol=symbol, allowed_symbols=self.allowed_symbols)
        except SafetyError as exc:
            return self._reject_without_state_change(
                account_id=account_id,
                strategy_id=strategy_id,
                symbol=symbol,
                target_weight=target_weight,
                signal_time=signal_time,
                reason=str(exc),
                event_type="LEVERAGE_SHORT_OR_UNIVERSE_BLOCKED",
            )
        client_id = make_client_order_id(
            account_id,
            strategy_id,
            symbol,
            signal_time.astimezone(timezone.utc),
            format(target_weight, "f"),
        )

        def _tx(conn) -> dict[str, object]:
            existing = conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id = ?",
                [client_id],
            ).fetchone()
            if existing is not None:
                return {"order": dict(existing), "duplicate": True}
            account = conn.execute("SELECT * FROM paper_accounts WHERE account_id = ?", [account_id]).fetchone()
            if account is None:
                raise AccountingError(f"unknown paper account {account_id}")
            position = conn.execute(
                "SELECT * FROM paper_positions WHERE account_id = ? AND symbol = ?",
                [account_id, symbol],
            ).fetchone()
            cash = dec(account["current_cash"])
            quantity = dec(position["quantity"]) if position else Decimal("0")
            average = dec(position["average_entry_price"]) if position else Decimal("0")
            realized = Decimal("0")
            state = BookState(cash, quantity, average, realized)
            result = rebalance(
                state=state,
                target_weight=target_weight,
                open_price=execution_open,
                fee_rate=self.fee_rate,
                slippage_rate=self.slippage_rate,
                qty_quantum=self.qty_step,
            )
            now = _now()
            if result.side is None:
                return {"order": None, "duplicate": False, "reason": result.reason}
            notional = result.quantity * result.fill_price
            if notional < self.min_notional:
                return self._insert_rejected(
                    conn,
                    account_id=account_id,
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side=result.side,
                    client_id=client_id,
                    signal_time=signal_time,
                    reason=f"notional {notional} is below min_notional {self.min_notional}",
                    event_type="MIN_NOTIONAL",
                )
            order_id = str(uuid.uuid4())
            signal_id = f"{strategy_id}:{symbol}:{signal_time.isoformat()}"
            self._insert_order(
                conn,
                order_id=order_id,
                client_id=client_id,
                account_id=account_id,
                strategy_id=strategy_id,
                symbol=symbol,
                side=result.side,
                quantity=result.quantity,
                status=OrderStatus.CREATED,
                signal_id=signal_id,
                now=now,
            )
            self._transition(conn, order_id, OrderStatus.CREATED, OrderStatus.SUBMITTED, now, {"stage": "submitted"})
            self._transition(
                conn,
                order_id,
                OrderStatus.SUBMITTED,
                OrderStatus.FILLED,
                now,
                {"fill_price": format(result.fill_price, "f")},
            )
            conn.execute(
                """
                INSERT INTO paper_fills (
                    fill_id, order_id, fill_time, fill_price, fill_quantity, fee_amount, fee_asset, slippage_amount
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(uuid.uuid4()),
                    order_id,
                    execution_time.astimezone(timezone.utc).isoformat(),
                    format(result.fill_price, "f"),
                    format(result.quantity, "f"),
                    format(result.fee, "f"),
                    self.quote_asset,
                    format(result.slippage_cost, "f"),
                ],
            )
            conn.execute(
                "UPDATE paper_accounts SET current_cash = ? WHERE account_id = ?",
                [format(result.after.cash, "f"), account_id],
            )
            conn.execute(
                """
                INSERT INTO paper_positions (account_id, symbol, quantity, average_entry_price, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_entry_price = excluded.average_entry_price,
                    updated_at = excluded.updated_at
                """,
                [
                    account_id,
                    symbol,
                    format(result.after.quantity, "f"),
                    format(result.after.average_entry_price, "f"),
                    now,
                ],
            )
            mark = mark_price if mark_price is not None else execution_open
            equity = result.after.cash + result.after.quantity * mark
            unrealized = (
                result.after.quantity * (mark - result.after.average_entry_price)
                if result.after.quantity > 0
                else Decimal("0")
            )
            conn.execute(
                """
                INSERT INTO paper_equity_snapshots (
                    snapshot_id, account_id, timestamp, cash, market_value, total_equity, unrealized_pnl, realized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(uuid.uuid4()),
                    account_id,
                    execution_time.astimezone(timezone.utc).isoformat(),
                    format(result.after.cash, "f"),
                    format(result.after.quantity * mark, "f"),
                    format(equity, "f"),
                    format(unrealized, "f"),
                    format(result.after.realized_pnl, "f"),
                ],
            )
            peak = dec(account["peak_equity"])
            if equity > peak:
                peak = equity
                conn.execute(
                    "UPDATE paper_accounts SET peak_equity = ? WHERE account_id = ?",
                    [format(peak, "f"), account_id],
                )
            if drawdown_breached(equity, peak, self.max_drawdown_event):
                conn.execute(
                    """
                    INSERT INTO risk_events (
                        event_id, account_id, event_time, severity, event_type, details_json, action_taken
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(uuid.uuid4()),
                        account_id,
                        now,
                        "warning",
                        "DRAWDOWN_THRESHOLD",
                        json.dumps({"equity": format(equity, "f"), "peak": format(peak, "f")}),
                        "recorded_only",
                    ],
                )
            order = conn.execute("SELECT * FROM paper_orders WHERE order_id = ?", [order_id]).fetchone()
            return {"order": dict(order), "duplicate": False, "fill_price": format(result.fill_price, "f")}

        return self.store.transaction(_tx)

    def cancel_order(self, order_id: str) -> dict[str, object]:
        return self._manual_transition(order_id, OrderStatus.CANCELED)

    def expire_order(self, order_id: str) -> dict[str, object]:
        return self._manual_transition(order_id, OrderStatus.EXPIRED)

    def transition_for_test(self, order_id: str, new_status: OrderStatus) -> dict[str, object]:
        """Used by tests to exercise the legal transition table, including partial fills."""
        return self._manual_transition(order_id, new_status)

    def account_view(self, account_id: str) -> dict[str, object]:
        account = self.store.get_account(account_id)
        if account is None:
            raise AccountingError(f"unknown paper account {account_id}")
        return {
            "account": account,
            "orders": self.store.list_orders(account_id),
            "fills": self.store.list_fills(account_id),
            "positions": self.store.list_positions(account_id),
            "equity": self.store.list_equity(account_id),
            "risk_events": self.store.list_risk_events(account_id),
        }

    def _manual_transition(self, order_id: str, new_status: OrderStatus) -> dict[str, object]:
        def _tx(conn):
            row = conn.execute("SELECT * FROM paper_orders WHERE order_id = ?", [order_id]).fetchone()
            if row is None:
                raise AccountingError(f"unknown order {order_id}")
            current = OrderStatus(row["status"])
            assert_legal_transition(current, new_status)
            now = _now()
            self._transition(conn, order_id, current, new_status, now, {"manual": True})
            updated = conn.execute("SELECT * FROM paper_orders WHERE order_id = ?", [order_id]).fetchone()
            return dict(updated)

        return self.store.transaction(_tx)

    def _reject_without_state_change(self, **kwargs) -> dict[str, object]:
        signal_time = kwargs["signal_time"]
        client_id = make_client_order_id(
            kwargs["account_id"],
            kwargs["strategy_id"],
            kwargs["symbol"],
            signal_time.astimezone(timezone.utc),
            format(kwargs["target_weight"], "f"),
        )

        def _tx(conn):
            existing = conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id = ?",
                [client_id],
            ).fetchone()
            if existing is not None:
                return {"order": dict(existing), "duplicate": True}
            return self._insert_rejected(
                conn,
                account_id=kwargs["account_id"],
                strategy_id=kwargs["strategy_id"],
                symbol=kwargs["symbol"],
                side="BUY",
                client_id=client_id,
                signal_time=signal_time,
                reason=kwargs["reason"],
                event_type=kwargs["event_type"],
            )

        return self.store.transaction(_tx)

    def _insert_rejected(self, conn, **kwargs) -> dict[str, object]:
        now = _now()
        order_id = str(uuid.uuid4())
        self._insert_order(
            conn,
            order_id=order_id,
            client_id=kwargs["client_id"],
            account_id=kwargs["account_id"],
            strategy_id=kwargs["strategy_id"],
            symbol=kwargs["symbol"],
            side=kwargs["side"],
            quantity=Decimal("0"),
            status=OrderStatus.CREATED,
            signal_id=f"{kwargs['strategy_id']}:{kwargs['symbol']}:{kwargs['signal_time'].isoformat()}",
            now=now,
        )
        self._transition(conn, order_id, OrderStatus.CREATED, OrderStatus.SUBMITTED, now, {"stage": "submitted"})
        self._transition(
            conn,
            order_id,
            OrderStatus.SUBMITTED,
            OrderStatus.REJECTED,
            now,
            {"reason": kwargs["reason"]},
        )
        conn.execute(
            """
            INSERT INTO risk_events (
                event_id, account_id, event_time, severity, event_type, details_json, action_taken
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid.uuid4()),
                kwargs["account_id"],
                now,
                "high",
                kwargs["event_type"],
                json.dumps({"reason": kwargs["reason"]}),
                "order_rejected",
            ],
        )
        order = conn.execute("SELECT * FROM paper_orders WHERE order_id = ?", [order_id]).fetchone()
        return {"order": dict(order), "duplicate": False}

    def _insert_order(self, conn, **kwargs) -> None:
        side = kwargs["side"]
        if side not in {OrderSide.BUY.value, OrderSide.SELL.value}:
            raise SafetyError(f"unsupported side {side}")
        conn.execute(
            """
            INSERT INTO paper_orders (
                order_id, client_order_id, account_id, strategy_id, symbol, side, order_type,
                quantity, limit_price, status, created_at, updated_at, source_signal_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                kwargs["order_id"],
                kwargs["client_id"],
                kwargs["account_id"],
                kwargs["strategy_id"],
                kwargs["symbol"],
                side,
                OrderType.MARKET.value,
                format(kwargs["quantity"], "f"),
                None,
                kwargs["status"].value,
                kwargs["now"],
                kwargs["now"],
                kwargs["signal_id"],
            ],
        )
        conn.execute(
            """
            INSERT INTO paper_order_events (event_id, order_id, event_time, status, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [str(uuid.uuid4()), kwargs["order_id"], kwargs["now"], kwargs["status"].value, json.dumps({"stage": "created"})],
        )

    def _transition(self, conn, order_id: str, current: OrderStatus, new: OrderStatus, now: str, details: dict) -> None:
        assert_legal_transition(current, new)
        conn.execute(
            "UPDATE paper_orders SET status = ?, updated_at = ? WHERE order_id = ?",
            [new.value, now, order_id],
        )
        conn.execute(
            """
            INSERT INTO paper_order_events (event_id, order_id, event_time, status, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [str(uuid.uuid4()), order_id, now, new.value, json.dumps(details)],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
