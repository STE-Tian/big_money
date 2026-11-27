"""Runtime guards. Importing a module cannot turn live trading on."""

from __future__ import annotations

from decimal import Decimal

from pquant.domain.enums import TradingMode
from pquant.domain.errors import LiveTradingDisabledError, SafetyError

# Paths the market-data adapter is allowed to call. Anything else is refused.
ALLOWED_PUBLIC_PATHS = frozenset(
    {
        "/api/v3/ping",
        "/api/v3/time",
        "/api/v3/exchangeInfo",
        "/api/v3/klines",
        "/api/v3/ticker/price",
        "/api/v3/ticker/24hr",
    }
)

FORBIDDEN_PATH_MARKERS = (
    "/api/v3/order",
    "/api/v3/openOrders",
    "/api/v3/allOrders",
    "/api/v3/account",
    "/api/v3/myTrades",
    "/sapi/",
    "withdraw",
    "capital",
    "margin",
    "futures",
    "leverage",
)


def assert_mode_allowed(mode: TradingMode) -> None:
    if mode is TradingMode.LIVE:
        raise LiveTradingDisabledError(
            "LIVE trading is disabled in v1 and cannot be enabled from configuration or the UI."
        )


def assert_public_path(path: str) -> None:
    clean = path.split("?", 1)[0]
    lowered = clean.lower()
    for marker in FORBIDDEN_PATH_MARKERS:
        if marker.lower() in lowered:
            raise SafetyError(f"refusing non-market-data path: {clean}")
    if clean not in ALLOWED_PUBLIC_PATHS:
        raise SafetyError(f"path is not on the public market-data allowlist: {clean}")


def assert_weight_in_unit_interval(weight: Decimal) -> None:
    if weight < Decimal("0") or weight > Decimal("1"):
        raise SafetyError(
            f"target weight {weight} is outside [0, 1]. Shorting and leverage are disabled."
        )


def assert_no_live_order_api(module_exports: set[str]) -> None:
    banned = {"place_order", "place_live_order", "cancel_live_order", "withdraw_asset", "transfer_asset"}
    found = banned & module_exports
    if found:
        raise SafetyError(f"live trading exports are forbidden: {sorted(found)}")
