"""Stable hashes for datasets and candle revisions. No secrets are hashed here."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pquant.domain.models import Candle


def content_hash(candle: Candle) -> str:
    """Hash OHLCV fields only. An is_closed flip is an update, not a price revision."""
    payload = candle.ohlcv_identity().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def dataset_hash(candles: list[Candle]) -> str:
    """Canonical hash of closed candles in open_time order."""
    digest = hashlib.sha256()
    ordered = sorted(candles, key=lambda c: c.open_time)
    for candle in ordered:
        if not candle.is_closed:
            continue
        line = (
            f"{candle.exchange}|{candle.market_type}|{candle.symbol}|{candle.interval}|"
            f"{_iso(candle.open_time)}|{_iso(candle.close_time)}|{candle.ohlcv_identity()}\n"
        )
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def _iso(value: datetime) -> str:
    return value.isoformat()


def client_order_id(
    account_id: str,
    strategy_id: str,
    symbol: str,
    signal_time: datetime,
    target_weight: str,
) -> str:
    raw = "|".join(
        [
            account_id,
            strategy_id,
            symbol,
            signal_time.isoformat(),
            target_weight,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
