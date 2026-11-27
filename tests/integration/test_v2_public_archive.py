"""One public archive file. Network failures skip rather than fail the unit suite."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pquant.domain.errors import ExchangeError, NetworkError
from pquant.v2.archive import VisionArchiveClient, parse_funding_zip, parse_spot_kline_zip


@pytest.mark.integration
def test_public_october_funding_archive_and_one_spot_day():
    client = VisionArchiveClient(timeout_seconds=30, max_retries=2)
    try:
        funding = client.fetch("/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-10.zip")
        spot = client.fetch("/data/spot/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-11-01.zip")
    except (NetworkError, ExchangeError, OSError) as exc:
        pytest.skip(f"Binance public archive unreachable: {exc}")
    if funding is None or spot is None:
        pytest.skip("expected public archive files are not published")
    assert funding.checksum_verified is True
    assert spot.checksum_verified is True
    events = parse_funding_zip(
        funding.payload,
        symbol="BTCUSDT",
        source="binance_vision:test",
        ingested_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    candles = parse_spot_kline_zip(
        spot.payload,
        symbol="BTCUSDT",
        interval="1h",
        server_time=datetime(2026, 11, 27, tzinfo=timezone.utc),
        source="binance_vision:test",
        ingested_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    assert events
    assert all(not event.tradable for event in events)
    assert events[0].funding_time >= datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert candles
    assert all(candle.is_closed for candle in candles)
    assert all(candle.taker_buy_base_volume <= candle.volume for candle in candles)
    assert candles[0].open_time == datetime(2026, 11, 1, tzinfo=timezone.utc)
