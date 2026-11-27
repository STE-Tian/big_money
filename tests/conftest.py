"""Shared synthetic candles. These are not market history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from pquant.config.loader import load_settings
from pquant.domain.models import Candle
from pquant.utils.time import interval_ms

ROOT = Path(__file__).resolve().parents[1]


def make_candles(
    closes: list[str],
    *,
    opens: list[str] | None = None,
    start: datetime | None = None,
    interval: str = "1d",
    symbol: str = "BTCUSDT",
    source: str = "synthetic",
    closed_flags: list[bool] | None = None,
) -> list[Candle]:
    start = start or datetime(2020, 1, 1, tzinfo=timezone.utc)
    step = timedelta(milliseconds=interval_ms(interval))
    candles: list[Candle] = []
    for index, close_text in enumerate(closes):
        close = Decimal(close_text)
        open_ = Decimal(opens[index]) if opens else close
        high = max(open_, close)
        low = min(open_, close)
        open_time = start + index * step
        candles.append(
            Candle(
                exchange="binance",
                market_type="spot",
                symbol=symbol,
                interval=interval,
                open_time=open_time,
                close_time=open_time + step - timedelta(milliseconds=1),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=Decimal("10"),
                quote_volume=Decimal("1000"),
                trade_count=5,
                taker_buy_base_volume=Decimal("4"),
                taker_buy_quote_volume=Decimal("400"),
                is_closed=True if closed_flags is None else closed_flags[index],
                source=source,
                ingested_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        )
    return candles


@pytest.fixture
def settings():
    return load_settings(ROOT)
