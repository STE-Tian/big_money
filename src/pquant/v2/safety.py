"""V2 path guards.

Spot order paths stay on the V1 allowlist. Funding history is a separate
read-only research call. Archive downloads are limited to checksummed public
files for spot klines and USD-M funding rates. Perpetual prices are not
downloaded, and nothing here can submit an order.
"""

from __future__ import annotations

import re

from pquant.domain.errors import SafetyError

# Official public file host. This is not an order API.
VISION_HOST = "data.binance.vision"

# Official USD-M market-data host for GET /fapi/v1/fundingRate only.
FUNDING_HOST = "fapi.binance.com"

ALLOWED_FUNDING_RESEARCH_PATHS = frozenset({"/fapi/v1/fundingRate"})

_SYMBOL = r"(BTCUSDT|ETHUSDT)"
_INTERVAL = r"(1h|4h|1d)"
_MONTH = r"\d{4}-\d{2}"
_DAY = r"\d{4}-\d{2}-\d{2}"

_SPOT_MONTHLY = re.compile(
    rf"^/data/spot/monthly/klines/{_SYMBOL}/{_INTERVAL}/{_SYMBOL}-{_INTERVAL}-{_MONTH}\.zip$"
)
_SPOT_DAILY = re.compile(
    rf"^/data/spot/daily/klines/{_SYMBOL}/{_INTERVAL}/{_SYMBOL}-{_INTERVAL}-{_DAY}\.zip$"
)
_FUNDING_MONTHLY = re.compile(
    rf"^/data/futures/um/monthly/fundingRate/{_SYMBOL}/{_SYMBOL}-fundingRate-{_MONTH}\.zip$"
)

_FORBIDDEN_FUNDING_MARKERS = (
    "order",
    "leverage",
    "position",
    "account",
    "withdraw",
    "margin",
    "transfer",
    "listenkey",
)


def assert_funding_research_path(path: str) -> None:
    """Allow only the public funding-rate history query.

    The response is a research feature. It is not a position, an order, or a
    permission to trade USD-M perpetuals.
    """
    clean = path.split("?", 1)[0]
    lowered = clean.lower()
    for marker in _FORBIDDEN_FUNDING_MARKERS:
        if marker in lowered:
            raise SafetyError(f"refusing non-research funding path: {clean}")
    if clean not in ALLOWED_FUNDING_RESEARCH_PATHS:
        raise SafetyError(f"funding research allowlist rejects path: {clean}")


def assert_vision_archive_path(path: str) -> None:
    """Allow spot kline zips and USD-M fundingRate zips, plus their checksums.

    USD-M kline archives are rejected. P1 does not ingest perpetual prices.
    """
    clean = path.split("?", 1)[0]
    base = clean[: -len(".CHECKSUM")] if clean.endswith(".CHECKSUM") else clean
    spot = _SPOT_MONTHLY.fullmatch(base) or _SPOT_DAILY.fullmatch(base)
    funding = _FUNDING_MONTHLY.fullmatch(base)
    if spot is not None:
        symbol_dir, interval_dir, symbol_file, interval_file = spot.groups()
        if symbol_dir != symbol_file or interval_dir != interval_file:
            raise SafetyError(f"archive path does not describe one symbol and interval: {clean}")
        return
    if funding is not None:
        symbol_dir, symbol_file = funding.groups()
        if symbol_dir != symbol_file:
            raise SafetyError(f"funding archive symbol mismatch in path: {clean}")
        return
    raise SafetyError(f"archive path is not an allowed public research file: {clean}")


def assert_https_host(url: str, host: str) -> None:
    prefix = f"https://{host}"
    if url.rstrip("/") != prefix:
        raise SafetyError(f"refusing unexpected host {url!r}; expected {prefix}")
