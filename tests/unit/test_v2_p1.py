"""V2 P1: alignment, no lookahead, unclosed bars, and funding as a feature."""

from __future__ import annotations

import hashlib
import io
import math
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pandas as pd
import pytest

from pquant.domain.errors import ConfigError, DataQualityError, ExchangeError, LiveTradingDisabledError, SafetyError
from pquant.domain.models import Candle
from pquant.safety import assert_public_path
from pquant.storage.parquet_store import parquet_path
from pquant.utils.time import interval_ms
from pquant.v2.archive import (
    VisionArchiveClient,
    parse_checksum,
    parse_funding_zip,
    parse_spot_kline_zip,
    spot_kline_archive_path,
)
from pquant.v2.archive import ArchiveFile
from pquant.v2.config import enforce_v2_safety, load_v2_settings
from pquant.v2.epochs import epoch_to_ms
from pquant.v2.features import (
    SymbolFeatureInput,
    aggregate_closed_4h_to_1d,
    build_feature_frames,
    config_fingerprint,
)
from pquant.v2.funding import FundingEvent, FundingRateClient, asof_funding
from pquant.v2.pipeline import build_v2_features, collect_v2
from pquant.v2.safety import assert_funding_research_path, assert_vision_archive_path
from pquant.v2.store import read_feature_frame, read_funding

from tests.conftest import ROOT, make_candles


START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_v2_config_stays_research_and_funding_is_not_tradable(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    settings = load_v2_settings(ROOT)
    assert settings.app.trading_mode.value == "research"
    assert settings.app.live_trading_enabled is False
    assert settings.market_data.exclude_open_candle is True
    assert settings.market_data.intervals == ["1h", "4h"]
    assert settings.funding.tradable is False
    assert settings.funding.path == "/fapi/v1/fundingRate"
    assert settings.safety.trade_perpetual is False
    broken = settings.model_copy(update={"funding": settings.funding.model_copy(update={"tradable": True})})
    with pytest.raises(LiveTradingDisabledError):
        enforce_v2_safety(broken)
    opened = settings.model_copy(
        update={"market_data": settings.market_data.model_copy(update={"exclude_open_candle": False})}
    )
    with pytest.raises(ConfigError):
        enforce_v2_safety(opened)


def test_spot_client_allowlist_does_not_include_funding_or_orders():
    with pytest.raises(SafetyError):
        assert_public_path("/fapi/v1/fundingRate")
    with pytest.raises(SafetyError):
        assert_public_path("/fapi/v1/order")
    with pytest.raises(SafetyError):
        assert_public_path("/fapi/v1/leverage")


def test_archive_and_funding_paths_reject_orders_and_perpetual_klines():
    assert_vision_archive_path("/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-10.zip")
    assert_vision_archive_path(
        "/data/futures/um/monthly/fundingRate/ETHUSDT/ETHUSDT-fundingRate-2026-10.zip.CHECKSUM"
    )
    with pytest.raises(SafetyError):
        assert_vision_archive_path("/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-10.zip")
    with pytest.raises(SafetyError):
        assert_vision_archive_path("/api/v3/order")
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=b"{}")

    client = FundingRateClient(transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None)
    with pytest.raises(SafetyError):
        client._get_json("/fapi/v1/order", {"symbol": "BTCUSDT"})
    with pytest.raises(SafetyError):
        assert_funding_research_path("/fapi/v1/leverage")
    assert calls["n"] == 0


def test_funding_parser_keeps_empty_mark_price_and_blocks_api_keys():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-mbx-apikey" not in {key.lower() for key in request.headers}
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "BTCUSDT",
                    "fundingTime": 1_762_000_000_001,
                    "fundingRate": "-0.00012359",
                    "markPrice": "",
                    "rateType": "Regular",
                }
            ],
        )

    client = FundingRateClient(transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None)
    events = client.get_funding_rates(
        symbol="BTCUSDT",
        start_time_ms=0,
        end_time_ms=2_000_000_000_000,
        limit=200,
        source="binance_fapi:test",
        ingested_at=START,
    )
    assert events[0].mark_price is None
    assert events[0].funding_rate == Decimal("-0.00012359")
    assert events[0].tradable is False
    assert events[0].research_feature_only is True
    assert events[0].funding_time == datetime.fromtimestamp(1_762_000_000_001 / 1000, tz=timezone.utc)


def test_epoch_and_archive_rows_match_millisecond_rest_fields():
    open_ms = 1_793_491_200_000
    close_ms = open_ms + 3_600_000 - 1
    assert epoch_to_ms(open_ms * 1000) == open_ms
    assert epoch_to_ms(close_ms * 1000 + 999) == close_ms
    line = (
        f"{open_ms * 1000},77582.02000000,77715.50000000,77517.20000000,77690.41000000,"
        f"155.06017000,{close_ms * 1000 + 999},12034253.52625550,37826,93.99385000,7294765.97075200,0\n"
    )
    parsed = parse_spot_kline_zip(
        _zip("BTCUSDT-1h-2026-11-01.csv", line),
        symbol="BTCUSDT",
        interval="1h",
        server_time=datetime(2026, 11, 2, tzinfo=timezone.utc),
        source="binance_vision:test",
        ingested_at=START,
    )
    assert parsed[0].is_closed is True
    assert parsed[0].open_time == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert parsed[0].trade_count == 37826
    assert parsed[0].taker_buy_base_volume == Decimal("93.99385000")
    assert parsed[0].volume == Decimal("155.06017000")
    funding = parse_funding_zip(
        _zip(
            "BTCUSDT-fundingRate-2026-10.csv",
            "calc_time,funding_interval_hours,last_funding_rate\n1790812800001,8,0.00007981\n",
        ),
        symbol="BTCUSDT",
        source="binance_vision:test",
        ingested_at=START,
    )
    assert funding[0].funding_time == datetime(2026, 10, 1, 0, 0, 0, 1000, tzinfo=timezone.utc)
    assert funding[0].funding_interval_hours == Decimal("8")
    assert funding[0].funding_rate == Decimal("0.00007981")
    assert funding[0].mark_price is None


def test_checksum_round_trip_and_mismatch():
    payload = _zip("BTCUSDT-1h-2026-10.csv", "1,2,3\n")
    digest = hashlib.sha256(payload).hexdigest()
    assert parse_checksum(f"{digest}  BTCUSDT-1h-2026-10.zip\n") == digest

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=f"{digest}  BTCUSDT-1h-2026-10.zip\n")
        if request.url.path.endswith("BTCUSDT-1h-2099-01.zip"):
            return httpx.Response(404)
        return httpx.Response(200, content=payload)

    client = VisionArchiveClient(transport=httpx.MockTransport(handler), sleeper=lambda _seconds: None)
    fetched = client.fetch("/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-10.zip")
    assert fetched is not None
    assert fetched.checksum_verified is True
    assert fetched.sha256 == digest
    assert client.fetch("/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2099-01.zip") is None

    def bad(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, text=("0" * 64) + "  BTCUSDT-1h-2026-10.zip\n")
        return httpx.Response(200, content=payload)

    broken = VisionArchiveClient(transport=httpx.MockTransport(bad), sleeper=lambda _seconds: None)
    with pytest.raises(ExchangeError):
        broken.fetch("/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-10.zip")


def test_asof_funding_does_not_see_the_future_and_does_not_forward_fill():
    close_time = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
    published = _event(close_time, "0.0002")
    later = _event(close_time + timedelta(milliseconds=1), "0.9")
    earlier = _event(close_time - timedelta(hours=8), "0.0001")
    assert asof_funding(close_time, [earlier, published, later]).funding_rate == Decimal("0.0002")
    assert asof_funding(close_time - timedelta(milliseconds=1), [earlier, published, later]).funding_rate == Decimal(
        "0.0001"
    )
    assert asof_funding(earlier.funding_time - timedelta(milliseconds=1), [earlier, published]) is None


def test_feature_formulas_windows_and_unclosed_bars_are_excluded():
    settings = load_v2_settings(ROOT)
    windows = {
        "1h": settings.features.windows["1h"].model_copy(
            update={
                "taker_imbalance": 2,
                "volume_baseline": 2,
                "price_volume_corr": 2,
                "relative_strength": 2,
                "realized_vol": 2,
            }
        )
    }
    candles = _custom_candles(
        [
            ("100", "10", "4"),
            ("110", "10", "8"),
            ("121", "5", "1"),
            ("90", "5", "1"),
        ],
        interval="1h",
        symbol="BTCUSDT",
    )
    future = candles[2].close_time + timedelta(milliseconds=1)
    funding = [
        _event(candles[1].close_time, "0.0002"),
        _event(candles[2].close_time, "0.00005"),
        _event(future, "0.9"),
        _event(candles[3].close_time, "0.0003"),
    ]
    unclosed = candles[-1].model_copy(update={"is_closed": False, "close": Decimal("1")})
    built = _build([("BTCUSDT", "1h", candles[:-1] + [unclosed], funding)], windows, settings)
    frame = built.frames[("BTCUSDT", "1h")]
    assert built.dropped_unclosed[("BTCUSDT", "1h")] == 1
    assert len(frame) == 3
    assert list(frame["open_time"]) == [candle.open_time for candle in candles[:3]]
    assert _cell(frame, 0, "taker_imbalance") == pytest.approx((4 - 6) / 10)
    assert _cell(frame, 1, "taker_imbalance_roll") == pytest.approx(0.2)
    assert _cell(frame, 1, "taker_buy_exceeds_sell_roll") == 1.0
    assert _cell(frame, 2, "volume_ratio") == pytest.approx(-0.5)
    assert _cell(frame, 2, "price_volume_divergence") == pytest.approx(math.log(1.1) + 0.5)
    assert _cell(frame, 2, "price_up_volume_not") == 1.0
    assert _cell(frame, 1, "price_up_volume_not") is None
    assert _cell(frame, 2, "funding_rate") == pytest.approx(0.00005)
    assert _cell(frame, 2, "funding_rate") != pytest.approx(0.9)
    assert _cell(frame, 2, "price_down_funding_high") == 0.0
    assert frame["available_time"].tolist() == frame["close_time"].tolist()
    assert "target_weight" not in frame.columns
    assert "order_qty" not in frame.columns


def test_features_do_not_change_when_the_future_is_removed_or_tampered():
    settings = load_v2_settings(ROOT)
    windows = {
        key: value.model_copy(
            update={
                "taker_imbalance": 3,
                "volume_baseline": 3,
                "price_volume_corr": 3,
                "relative_strength": 3,
                "realized_vol": 3,
            }
        )
        for key, value in settings.features.windows.items()
    }
    btc = _custom_candles(
        [(str(100 + index), str(10 + (index % 3)), str(6 if index % 2 == 0 else 3)) for index in range(30)],
        interval="4h",
        symbol="BTCUSDT",
    )
    eth = _custom_candles(
        [(str(50 + (index % 5)), "8", "3") for index in range(30)],
        interval="4h",
        symbol="ETHUSDT",
    )
    funding = [_event(btc[index].close_time, "0.0001" if index % 2 == 0 else "-0.0002") for index in range(0, 30, 2)]
    full = _build(
        [("BTCUSDT", "4h", btc, funding), ("ETHUSDT", "4h", eth, funding)],
        windows,
        settings,
    )
    cut = 16
    truncated_funding = [event for event in funding if event.funding_time <= btc[cut - 1].close_time]
    truncated = _build(
        [
            ("BTCUSDT", "4h", btc[:cut], truncated_funding),
            ("ETHUSDT", "4h", eth[:cut], truncated_funding),
        ],
        windows,
        settings,
    )
    _assert_prefix_equal(full.frames[("BTCUSDT", "4h")], truncated.frames[("BTCUSDT", "4h")])
    tampered = [
        candle.model_copy(update={"close": candle.close * Decimal("3"), "high": candle.high * Decimal("3")})
        if index >= cut
        else candle
        for index, candle in enumerate(btc)
    ]
    future_funding = funding + [_event(btc[-1].close_time + timedelta(hours=8), "0.5")]
    changed = _build(
        [("BTCUSDT", "4h", tampered, future_funding), ("ETHUSDT", "4h", eth, future_funding)],
        windows,
        settings,
    )
    _assert_prefix_equal(full.frames[("BTCUSDT", "4h")], changed.frames[("BTCUSDT", "4h")], rows=cut)


def test_missing_pair_bar_is_not_filled_from_a_neighbour():
    settings = load_v2_settings(ROOT)
    windows = {
        "4h": settings.features.windows["4h"].model_copy(
            update={
                "taker_imbalance": 2,
                "volume_baseline": 2,
                "price_volume_corr": 2,
                "relative_strength": 2,
                "realized_vol": 2,
            }
        )
    }
    btc = _custom_candles([(str(100 + index), "10", "6") for index in range(8)], interval="4h", symbol="BTCUSDT")
    eth = _custom_candles([(str(80 - index), "9", "2") for index in range(8)], interval="4h", symbol="ETHUSDT")
    eth_missing = [candle for index, candle in enumerate(eth) if index != 4]
    built = _build(
        [("BTCUSDT", "4h", btc, []), ("ETHUSDT", "4h", eth_missing, [])],
        windows,
        settings,
    )
    frame = built.frames[("BTCUSDT", "4h")]
    assert _cell(frame, 4, "eth_btc_log_return") is None
    assert _cell(frame, 4, "btc_up_ethbtc_weak") is None
    assert len(built.frames[("ETHUSDT", "4h")]) == 7
    assert _cell(frame, 5, "eth_btc_log_return") is not None


def test_daily_relative_strength_uses_only_complete_closed_4h_days():
    day = _custom_candles([(str(100 + index), "10", "4") for index in range(6)], interval="4h", symbol="BTCUSDT")
    incomplete = day[:5]
    next_day = [
        candle.model_copy(
            update={
                "open_time": candle.open_time + timedelta(days=1),
                "close_time": candle.close_time + timedelta(days=1),
                "close": Decimal("500"),
                "open": Decimal("500"),
                "high": Decimal("500"),
                "low": Decimal("500"),
            }
        )
        for candle in day
    ]
    aggregated = aggregate_closed_4h_to_1d(day + next_day)
    assert len(aggregated) == 2
    assert aggregated[0].interval == "1d"
    assert aggregated[0].close == day[-1].close
    assert aggregated[0].close_time == day[-1].close_time
    assert aggregated[0].source == "v2_aggregate:closed_4h"
    assert aggregate_closed_4h_to_1d(incomplete) == []
    unclosed = day[:-1] + [day[-1].model_copy(update={"is_closed": False})]
    assert aggregate_closed_4h_to_1d(unclosed) == []


def test_pipeline_writes_features_without_touching_the_v1_daily_path(tmp_path):
    settings = load_v2_settings(ROOT)
    storage = settings.storage.model_copy(
        update={
            "parquet_dir": tmp_path / "parquet",
            "raw_dir": tmp_path / "raw",
            "reports_dir": tmp_path / "reports",
            "feature_dir": tmp_path / "features",
        }
    )
    market = settings.market_data.model_copy(update={"rest_crosscheck_max_bars": 1})
    local = settings.model_copy(update={"storage": storage, "market_data": market, "root": tmp_path})
    end = START + timedelta(days=1)
    archive = _archive_for_day(end)
    report = collect_v2(
        local,
        start=START,
        end=end,
        explicit_end=True,
        archive=archive,
        spot_client=_Spot(),
        funding_client=_Funding(),
        sleeper=lambda _seconds: None,
    )
    assert all(item["quality_status"] == "qualified" for item in report["spot"])
    assert all(item["status"] == "qualified" and item["tradable"] is False for item in report["funding"])
    assert all(item["rest_error"] for item in report["funding"])
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for interval in ("1h", "4h"):
            assert parquet_path(storage.parquet_dir, "binance", "spot", symbol, interval).exists()
        assert not parquet_path(storage.parquet_dir, "binance", "spot", symbol, "1d").exists()
        stored = read_funding(storage.parquet_dir / f"binance_um_funding_{symbol}.parquet")
        assert stored
        assert all(event.tradable is False and event.research_feature_only for event in stored)
    features = build_v2_features(local)
    assert features["v1_daily_path_written"] is False
    assert features["funding_tradable"] is False
    btc_4h = read_feature_frame(storage.feature_dir / "BTCUSDT_4h.parquet")
    btc_1h = read_feature_frame(storage.feature_dir / "BTCUSDT_1h.parquet")
    btc_1d = read_feature_frame(storage.feature_dir / "BTCUSDT_1d.parquet")
    assert len(btc_1d) == 1
    assert _cell(btc_4h, 1, "btc_up_ethbtc_weak") == 1.0
    assert all(_cell(btc_1h, index, "eth_btc_log_return") is None for index in range(len(btc_1h)))
    assert "leverage" not in btc_4h.columns
    manifest = (tmp_path / "reports" / "v2" / "p1_manifest.json").read_text(encoding="utf-8")
    assert '"funding_tradable": false' in manifest
    assert "run_backtest" not in manifest


def test_feature_config_hash_changes_when_a_window_changes():
    settings = load_v2_settings(ROOT)
    first = config_fingerprint(
        windows=settings.features.windows,
        funding_high_threshold=settings.features.funding_high_threshold,
        annualize_realized_vol=True,
        relative_strength_intervals=settings.market_data.relative_strength_intervals,
    )
    changed = dict(settings.features.windows)
    changed["1h"] = settings.features.windows["1h"].model_copy(update={"realized_vol": 48})
    second = config_fingerprint(
        windows=changed,
        funding_high_threshold=settings.features.funding_high_threshold,
        annualize_realized_vol=True,
        relative_strength_intervals=settings.market_data.relative_strength_intervals,
    )
    assert first != second


def _build(items, windows, settings):
    fingerprint = config_fingerprint(
        windows=windows,
        funding_high_threshold=settings.features.funding_high_threshold,
        annualize_realized_vol=True,
        relative_strength_intervals=["4h", "1d"],
    )
    inputs = [
        SymbolFeatureInput(
            symbol=symbol,
            interval=interval,
            candles=candles,
            funding=funding,
            spot_dataset_hash="synthetic-test",
            funding_dataset_hash="synthetic-test",
        )
        for symbol, interval, candles, funding in items
    ]
    return build_feature_frames(
        inputs,
        windows=windows,
        funding_high_threshold=settings.features.funding_high_threshold,
        annualize_realized_vol=True,
        relative_strength_intervals={"4h", "1d"},
        config_hash=fingerprint,
    )


def _assert_prefix_equal(left: pd.DataFrame, right: pd.DataFrame, rows: int | None = None) -> None:
    count = len(right) if rows is None else rows
    for index in range(count):
        for column in left.columns:
            assert _cell(left, index, column) == _cell(right, index, column), column


def _cell(frame: pd.DataFrame, index: int, column: str):
    value = frame.iloc[index][column]
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float):
        return float(value)
    return value


def _custom_candles(rows: list[tuple[str, str, str]], *, interval: str, symbol: str) -> list[Candle]:
    base = make_candles(
        [item[0] for item in rows],
        start=START,
        interval=interval,
        symbol=symbol,
        source="binance_vision:test",
    )
    built = []
    for candle, (close_text, volume_text, taker_text) in zip(base, rows):
        close = Decimal(close_text)
        volume = Decimal(volume_text)
        taker = Decimal(taker_text)
        built.append(
            candle.model_copy(
                update={
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": volume,
                    "quote_volume": volume * close,
                    "taker_buy_base_volume": taker,
                    "taker_buy_quote_volume": taker * close,
                }
            )
        )
    return built


def _event(when: datetime, rate: str) -> FundingEvent:
    return FundingEvent(
        symbol="BTCUSDT",
        funding_time=when,
        funding_rate=Decimal(rate),
        funding_interval_hours=Decimal("8"),
        source="binance_vision:test",
        ingested_at=START,
    )


def _zip(name: str, text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def _kline_csv(interval: str, count: int, *, symbol: str) -> str:
    step = interval_ms(interval)
    lines = []
    for index in range(count):
        open_time = START + timedelta(milliseconds=step * index)
        open_ms = int(open_time.timestamp() * 1000)
        close_ms = open_ms + step - 1
        close = 100 + index if symbol == "BTCUSDT" else 100
        lines.append(
            f"{open_ms},{close},{close},{close},{close},10,{close_ms},1000,5,6,600,0"
        )
    return "\n".join(lines) + "\n"


def _funding_csv() -> str:
    lines = ["calc_time,funding_interval_hours,last_funding_rate"]
    for hour in (0, 8, 16):
        moment = START + timedelta(hours=hour, milliseconds=1 if hour == 0 else 0)
        lines.append(f"{int(moment.timestamp() * 1000)},8,0.0001")
    return "\n".join(lines) + "\n"


class _Archive:
    base_url = "https://data.binance.vision"

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def fetch(self, path: str) -> ArchiveFile | None:
        assert_vision_archive_path(path)
        payload = self.files.get(path)
        if payload is None:
            return None
        return ArchiveFile(path, hashlib.sha256(payload).hexdigest(), True, payload)


def _archive_for_day(_end: datetime) -> _Archive:
    files = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        files[spot_kline_archive_path(symbol, "1h", "2026-10", monthly=True)] = _zip(
            f"{symbol}-1h-2026-10.csv",
            _kline_csv("1h", 24, symbol=symbol),
        )
        files[spot_kline_archive_path(symbol, "4h", "2026-10", monthly=True)] = _zip(
            f"{symbol}-4h-2026-10.csv",
            _kline_csv("4h", 6, symbol=symbol),
        )
        files[f"/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-2026-10.zip"] = _zip(
            f"{symbol}-fundingRate-2026-10.csv",
            _funding_csv(),
        )
    return _Archive(files)


class _Spot:
    def get_server_time(self) -> datetime:
        return datetime(2026, 11, 2, tzinfo=timezone.utc)

    def get_klines(self, **_kwargs):
        raise AssertionError("spot REST is not required when the archive covers the window")


class _Funding:
    def get_funding_rates(self, **_kwargs):
        raise ExchangeError(
            "HTTP 451 from fapi.binance.com fundingRate. Public funding REST is unavailable from this location."
        )
