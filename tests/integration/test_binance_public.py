"""Small public-data round trip. Network failures skip instead of failing the unit suite."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from pquant.adapters.exchanges.binance.client import BinanceMarketDataClient
from pquant.domain.errors import BanError, ExchangeError, NetworkError
from pquant.ingestion.collector import HistoryCollector
from pquant.ingestion.quality import closed_only
from pquant.research.backtest.artifacts import write_run_artifacts
from pquant.research.backtest.engine import run_backtest
from pquant.research.backtest.metrics import compute_metrics
from pquant.research.strategies.buy_hold import BuyAndHoldStrategy
from pquant.storage.market_store import MarketStore
from pquant.storage.parquet_store import parquet_path, read_candles


@pytest.mark.integration
def test_public_klines_round_trip_and_backtest(tmp_path, settings):
    client = BinanceMarketDataClient(
        base_url=settings.market_data.base_url,
        fallback_base_url=settings.market_data.fallback_base_url,
        timeout_seconds=20,
        max_retries=3,
    )
    try:
        client.get_server_time()
    except (NetworkError, ExchangeError, BanError, OSError) as exc:
        pytest.skip(f"Binance public API unreachable: {exc}")
    storage = settings.storage.model_copy(
        update={
            "duckdb_path": tmp_path / "market.duckdb",
            "sqlite_path": tmp_path / "research.sqlite",
            "parquet_dir": tmp_path / "parquet",
            "raw_dir": tmp_path / "raw",
            "reports_dir": tmp_path / "reports",
        }
    )
    local = settings.model_copy(update={"storage": storage, "root": tmp_path})
    store = MarketStore(storage.duckdb_path)
    store.init_schema()
    collector = HistoryCollector(local, client, store)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 11, tzinfo=timezone.utc)
    try:
        result = collector.collect_symbol("BTCUSDT", "1d", history_start=start, history_end=end)
    except (NetworkError, ExchangeError, BanError, OSError) as exc:
        pytest.skip(f"Binance public API unreachable: {exc}")
    path = parquet_path(storage.parquet_dir, "binance", "spot", "BTCUSDT", "1d")
    loaded = read_candles(path)
    closed = closed_only(loaded)
    assert loaded
    assert result.rows_stored == len(loaded)
    assert closed[0].open_time >= start
    assert closed[-1].open_time <= end
    assert store.count_candles("BTCUSDT", "1d") == len(loaded)
    output = BuyAndHoldStrategy().run(closed)
    backtest = run_backtest(
        closed,
        output.close_targets,
        opening_target=output.opening_target,
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="buy_and_hold",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
    )
    metrics = compute_metrics(
        backtest,
        start=closed[0].open_time,
        end=closed[-1].close_time,
        risk_free_rate=Decimal("0"),
        interval="1d",
    )
    paths = write_run_artifacts(
        tmp_path / "run",
        backtest,
        metrics,
        {"data_origin": closed[0].source, "synthetic": False},
    )
    assert (tmp_path / "run" / "trades.csv").exists()
    assert (tmp_path / "run" / "metrics.json").exists()
    assert (tmp_path / "run" / "equity.png").exists()
    assert paths["equity_png"].endswith("equity.png")
