"""UTC conversions. Binance timestamps are milliseconds."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

INTERVAL_MS: dict[str, int] = {
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

SUPPORTED_INTERVALS = frozenset(INTERVAL_MS)


def ms_to_utc(ms: int) -> datetime:
    """Convert a Unix millisecond timestamp to timezone-aware UTC."""
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def utc_to_ms(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.timestamp() * 1000)


def interval_ms(interval: str) -> int:
    try:
        return INTERVAL_MS[interval]
    except KeyError as exc:
        raise ValueError(
            f"unsupported interval {interval!r}; v1 data layer accepts {sorted(SUPPORTED_INTERVALS)}"
        ) from exc


def annualization_periods(interval: str) -> int:
    """Bar-return annualization. Daily uses sqrt(365), not sqrt(252). 4h uses sqrt(365*6)."""
    if interval == "1d":
        return 365
    if interval == "4h":
        return 365 * 6
    if interval == "1h":
        return 365 * 24
    raise ValueError(f"no annualization factor for interval {interval!r}")


def decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
