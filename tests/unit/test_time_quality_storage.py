"""Data-layer unit tests: time, OHLC, dedupe, gaps, and storage round trips."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pquant.domain.models import Candle
from pquant.ingestion.quality import build_quality_report, closed_only, ohlc_is_valid
from pquant.storage.market_store import MarketStore
from pquant.storage.parquet_store import read_candles, write_candles
from pquant.utils.hashing import content_hash
from pquant.utils.time import annualization_periods, ms_to_utc, utc_to_ms

from tests.conftest import make_candles


def test_milliseconds_convert_to_utc():
    moment = datetime(2017, 8, 17, tzinfo=timezone.utc)
    assert ms_to_utc(utc_to_ms(moment)) == moment
    assert ms_to_utc(0) == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_annualization_uses_365_not_252():
    assert annualization_periods("1d") == 365
    assert annualization_periods("4h") == 365 * 6
    assert annualization_periods("1h") == 365 * 24


def test_ohlc_rules():
    good = make_candles(["10"], opens=["9"])[0]
    assert ohlc_is_valid(good)
    bad = good.model_copy(update={"high": Decimal("8")})
    assert not ohlc_is_valid(bad)
    negative = good.model_copy(update={"volume": Decimal("-1")})
    report = build_quality_report(
        [negative],
        symbol="BTCUSDT",
        interval="1d",
        generated_at=datetime(2020, 1, 2, tzinfo=timezone.utc),
        server_time=datetime(2020, 1, 2, tzinfo=timezone.utc),
        check_freshness=False,
    )
    assert report.negative_volume_count == 1
    assert report.status == "unqualified"


def test_duplicates_and_missing_days_are_counted_without_filling():
    candles = make_candles(["1", "2", "3", "4"])
    missing = [candles[0], candles[1], candles[3]]
    duplicate = missing + [candles[1]]
    report = build_quality_report(
        duplicate,
        symbol="BTCUSDT",
        interval="1d",
        generated_at=datetime(2020, 1, 10, tzinfo=timezone.utc),
        server_time=datetime(2020, 1, 10, tzinfo=timezone.utc),
        check_freshness=False,
    )
    assert report.duplicate_rows == 1
    assert report.missing_count == 1
    assert report.forward_filled is False
    assert report.missing_ratio > 0.01
    assert report.status == "unqualified"


def test_four_hour_gap_detector():
    candles = make_candles(["1", "2", "3"], interval="4h")
    gapped = [candles[0], candles[2]]
    report = build_quality_report(
        gapped,
        symbol="BTCUSDT",
        interval="4h",
        generated_at=candles[2].close_time,
        server_time=candles[2].close_time + timedelta(minutes=1),
        check_freshness=False,
    )
    assert report.missing_count == 1


def test_open_candle_is_not_a_backtest_input():
    candles = make_candles(["1", "2"], closed_flags=[True, False])
    assert len(closed_only(candles)) == 1
    assert closed_only(candles)[0].is_closed


def test_parquet_preserves_decimal_strings(tmp_path):
    candles = make_candles(["60458.01000000"], opens=["60457.99000000"])
    path = tmp_path / "candles.parquet"
    write_candles(path, candles)
    loaded = read_candles(path)
    assert loaded[0].open == Decimal("60457.99000000")
    assert loaded[0].close == Decimal("60458.01000000")


def test_duckdb_dedupes_on_open_time(tmp_path):
    store = MarketStore(tmp_path / "market.duckdb")
    store.init_schema()
    candles = make_candles(["10", "11"])
    store.upsert_candles(candles)
    revised = [candles[0].model_copy(update={"close": Decimal("12")}), candles[1]]
    store.upsert_candles(revised)
    assert store.count_candles("BTCUSDT", "1d") == 2
    hashes = store.hashes_for("BTCUSDT", "1d")
    assert hashes[candles[0].open_time] == content_hash(revised[0])


def test_future_timestamp_fails_quality():
    candle = make_candles(["10"])[0]
    report = build_quality_report(
        [candle],
        symbol="BTCUSDT",
        interval="1d",
        generated_at=candle.open_time - timedelta(days=2),
        server_time=candle.open_time - timedelta(days=2),
        check_freshness=False,
    )
    assert report.future_timestamp_count == 1
    assert report.status == "unqualified"
    _ = Candle
