"""Read local market data. Dashboard and agent code use this instead of calling Binance."""

from __future__ import annotations

import json
from pathlib import Path

from pquant.config.settings import Settings
from pquant.domain.models import Candle
from pquant.storage.parquet_store import parquet_path, read_candles


def load_candles(settings: Settings, symbol: str, interval: str) -> list[Candle]:
    path = parquet_path(settings.storage.parquet_dir, "binance", "spot", symbol, interval)
    return read_candles(path)


def load_quality(settings: Settings, symbol: str, interval: str) -> dict | None:
    path = settings.storage.reports_dir / "data_quality" / f"{symbol}_{interval}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def reports_root(settings: Settings) -> Path:
    return settings.storage.reports_dir
