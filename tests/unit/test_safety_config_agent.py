"""Safety switches, the Binance client guard, and the agent whitelist."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from pquant.adapters.exchanges.binance.client import BinanceMarketDataClient, parse_kline_row
from pquant.agents.schemas import ExperimentSpec
from pquant.agents.tools import FORBIDDEN_TOOLS, ResearchToolbox
from pquant.agents.workflow import run_research
from pquant.config.loader import apply_env_allowlist
from pquant.config.settings import enforce_safety
from pquant.domain.enums import TradingMode
from pquant.domain.errors import BanError, LiveTradingDisabledError, SafetyError
from pquant.domain.models import Candle
from pquant.ingestion.collector import HistoryCollector
from pquant.storage.market_store import MarketStore
from pquant.utils.time import ms_to_utc

from tests.conftest import ROOT, make_candles


def test_live_mode_is_rejected(settings):
    broken = settings.model_copy(update={"app": settings.app.model_copy(update={"trading_mode": TradingMode.LIVE})})
    with pytest.raises(LiveTradingDisabledError):
        enforce_safety(broken)
    flagged = settings.model_copy(update={"safety": settings.safety.model_copy(update={"allow_live_orders": True})})
    with pytest.raises(LiveTradingDisabledError):
        enforce_safety(flagged)


def test_env_secret_is_not_loaded(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TRADING_MODE=paper\nBINANCE_API_SECRET=supersecretvalue123\n", encoding="utf-8")
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    monkeypatch.delenv("TRADING_MODE", raising=False)
    ignored = apply_env_allowlist(env_file)
    assert os.environ["TRADING_MODE"] == "paper"
    assert "BINANCE_API_SECRET" not in os.environ
    assert "BINANCE_API_SECRET" in ignored
    assert "supersecretvalue123" not in " ".join(os.environ.values())


def test_client_refuses_order_paths_and_stops_on_418():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(418, headers={"Retry-After": "120"}, json={"msg": "banned"})

    client = BinanceMarketDataClient(
        base_url="https://data-api.binance.vision",
        fallback_base_url="https://api.binance.com",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(SafetyError):
        client._get_json("/api/v3/order", {"symbol": "BTCUSDT"})
    assert calls["n"] == 0
    with pytest.raises(BanError):
        client.get_server_time()
    assert calls["n"] == 1


def test_429_then_success_uses_retry_after():
    calls = {"n": 0}
    delays = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"serverTime": 1_702_000_000_000})

    client = BinanceMarketDataClient(
        base_url="https://data-api.binance.vision",
        fallback_base_url=None,
        transport=httpx.MockTransport(handler),
        sleeper=delays.append,
    )
    assert client.get_server_time() == ms_to_utc(1_702_000_000_000)
    assert delays == [0.01]


def test_kline_close_flag_uses_server_time():
    row = [1_702_000_000_000, "1", "2", "1", "1.5", "10", 1_702_086_399_999, "15", 3, "4", "6", "0"]
    closed = parse_kline_row(
        row,
        symbol="BTCUSDT",
        interval="1d",
        server_time=ms_to_utc(1_702_086_400_000),
        source="binance_rest:test",
        ingested_at=ms_to_utc(1_702_086_400_000),
    )
    open_bar = parse_kline_row(
        row,
        symbol="BTCUSDT",
        interval="1d",
        server_time=ms_to_utc(1_702_000_100_000),
        source="binance_rest:test",
        ingested_at=ms_to_utc(1_702_000_100_000),
    )
    assert closed.is_closed is True
    assert open_bar.is_closed is False
    assert closed.open == Decimal("1")
    _ = Candle


def test_pagination_is_complete_and_idempotent(tmp_path, settings):
    candles = make_candles([str(100 + index) for index in range(7)], source="binance_rest:test")

    class FakeClient:
        def __init__(self):
            self.limit_seen = []
            self.last_used_weight_1m = "2"
            self.last_request_record = {}
            self._sleeper = lambda _seconds: None

        def get_server_time(self):
            return candles[-1].close_time

        def get_klines(self, **kwargs):
            start = kwargs["start_time_ms"]
            end = kwargs["end_time_ms"]
            limit = kwargs["limit"]
            self.limit_seen.append(limit)
            rows = [c for c in candles if int(c.open_time.timestamp() * 1000) >= start]
            if end is not None:
                rows = [c for c in rows if int(c.open_time.timestamp() * 1000) <= end]
            batch = rows[:limit]
            self.last_request_record = {
                "base_url": "https://data-api.binance.vision",
                "start_time_ms": start,
                "end_time_ms": end,
                "http_status": 200,
                "used_weight_1m": "2",
                "elapsed_ms": 1,
            }
            return batch

    storage = settings.storage.model_copy(
        update={
            "duckdb_path": tmp_path / "market.duckdb",
            "sqlite_path": tmp_path / "research.sqlite",
            "parquet_dir": tmp_path / "parquet",
            "raw_dir": tmp_path / "raw",
            "reports_dir": tmp_path / "reports",
        }
    )
    market = settings.market_data.model_copy(update={"klines_limit": 3})
    local = settings.model_copy(update={"storage": storage, "root": tmp_path, "market_data": market})
    store = MarketStore(storage.duckdb_path)
    store.init_schema()
    client = FakeClient()
    collector = HistoryCollector(local, client, store)
    first = collector.collect_symbol("BTCUSDT", "1d", history_start=candles[0].open_time, history_end=candles[-1].close_time)
    assert first.rows_stored == 7
    assert len(client.limit_seen) >= 3
    assert store.count_candles("BTCUSDT", "1d") == 7
    second = collector.collect_symbol("BTCUSDT", "1d", history_start=candles[0].open_time, history_end=candles[-1].close_time)
    assert second.rows_stored == 7
    assert store.count_candles("BTCUSDT", "1d") == 7


def test_agent_whitelist_and_synthetic_report_label(settings):
    candles = make_candles([str(100 + index) for index in range(40)], source="synthetic")
    tools = ResearchToolbox(settings, {"BTCUSDT": candles})
    with pytest.raises(SafetyError):
        tools.invoke("place_live_order", {})
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(
            {
                "objective": "x",
                "symbols": ["BTCUSDT"],
                "timeframe": "1d",
                "strategy_ids": ["buy_and_hold"],
                "python": "print(1)",
            }
        )
    result = run_research(
        tools,
        {
            "objective": "synthetic unit test",
            "symbols": ["BTCUSDT"],
            "timeframe": "1d",
            "strategy_ids": ["buy_and_hold"],
            "fee_scenario": "base",
            "slippage_scenario": "base",
            "validation_scheme": "full",
            "experiment_budget": 2,
        },
    )
    assert result["run_ids"]
    assert "SYNTHETIC TEST" in result["report"]
    assert "run-1" in result["report"]
    os.environ["BINANCE_API_SECRET"] = "supersecretvalue123"
    try:
        assert "supersecretvalue123" not in result["report"]
    finally:
        del os.environ["BINANCE_API_SECRET"]
    for name in FORBIDDEN_TOOLS:
        with pytest.raises(SafetyError):
            tools.invoke(name, {})


def test_dashboard_and_agents_do_not_reference_order_apis():
    dashboard = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "pquant" / "dashboard").rglob("*.py"))
    agents = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "pquant" / "agents").rglob("*.py"))
    assert "data-api.binance" not in dashboard
    assert "httpx" not in dashboard
    assert "place_live_order(" not in agents
    assert "exec(" not in agents
    assert "subprocess" not in agents
    _ = Path
