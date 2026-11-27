"""Decimal helpers for cash and quantity. Statistics may convert to float later."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

QTY_QUANTUM = Decimal("0.00000001")
WEIGHT_EPS = Decimal("0.0000001")


def to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def floor_to_step(value: Decimal, quantum: Decimal = QTY_QUANTUM) -> Decimal:
    if quantum <= 0:
        raise ValueError("quantity step must be positive")
    units = (value / quantum).to_integral_value(rounding=ROUND_DOWN)
    return units * quantum


def weight_changed(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) > WEIGHT_EPS
