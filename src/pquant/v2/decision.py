"""Pre-registered long-or-cash map from a forecast to a target weight.

Weight is 1 only when the expected simple return is strictly greater than one
round trip of the base cost (fee plus slippage, both sides). Every other
forecast, including a missing one, is cash. The function does not read a
realized label, and it does not return a short, a borrow, or a weight above 1.

This is the P2 research rule. It is not the P3 mean-variance sizer.
"""

from __future__ import annotations

import math
from decimal import Decimal

LONG = Decimal("1")
CASH = Decimal("0")
ROUND_TRIP_SIDES = 2


def round_trip_cost(fee_rate_per_side: Decimal, slippage_rate_per_side: Decimal) -> Decimal:
    if fee_rate_per_side < 0 or slippage_rate_per_side < 0:
        raise ValueError("cost rates must be non-negative")
    return (fee_rate_per_side + slippage_rate_per_side) * ROUND_TRIP_SIDES


def position_from_expected(expected_return: float | None, round_trip: Decimal) -> Decimal:
    if round_trip < 0:
        raise ValueError("round-trip cost must be non-negative")
    if expected_return is None or isinstance(expected_return, bool) or not math.isfinite(float(expected_return)):
        return CASH
    if float(expected_return) > float(round_trip):
        return LONG
    return CASH
