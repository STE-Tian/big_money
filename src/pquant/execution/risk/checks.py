"""Deterministic paper-trading checks. These functions do not call an exchange."""

from __future__ import annotations

from decimal import Decimal

from pquant.domain.errors import SafetyError


def assert_spot_constraints(*, target_weight: Decimal, symbol: str, allowed_symbols: set[str]) -> None:
    if symbol not in allowed_symbols:
        raise SafetyError(f"{symbol} is outside the configured universe")
    if target_weight < 0 or target_weight > 1:
        raise SafetyError("target weight outside [0, 1]; shorting and leverage are disabled")


def drawdown_breached(equity: Decimal, peak: Decimal, limit: Decimal) -> bool:
    if peak <= 0:
        return False
    return equity / peak - Decimal("1") <= -abs(limit)
