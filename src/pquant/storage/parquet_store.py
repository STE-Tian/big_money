"""Parquet snapshots keep exchange prices as decimal strings. This is the backtest source."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from pquant.domain.models import Candle


def parquet_path(directory: Path, exchange: str, market_type: str, symbol: str, interval: str) -> Path:
    return directory / f"{exchange}_{market_type}_{symbol}_{interval}.parquet"


def write_candles(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([_row(candle) for candle in sorted(candles, key=lambda c: c.open_time)])
    if frame.empty:
        frame = pd.DataFrame(columns=list(_row(_empty_template()).keys()))
    # Keep exchange prices as strings. Pandas would otherwise infer float64.
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ):
        frame[column] = frame[column].astype("string")
    frame.to_parquet(path, index=False)


def read_candles(path: Path) -> list[Candle]:
    if not path.exists():
        return []
    frame = pd.read_parquet(path)
    candles: list[Candle] = []
    for record in frame.to_dict(orient="records"):
        candles.append(
            Candle(
                exchange=record["exchange"],
                market_type=record["market_type"],
                symbol=record["symbol"],
                interval=record["interval"],
                open_time=_time(record["open_time"]),
                close_time=_time(record["close_time"]),
                open=Decimal(str(record["open"])),
                high=Decimal(str(record["high"])),
                low=Decimal(str(record["low"])),
                close=Decimal(str(record["close"])),
                volume=Decimal(str(record["volume"])),
                quote_volume=Decimal(str(record["quote_volume"])),
                trade_count=int(record["trade_count"]),
                taker_buy_base_volume=Decimal(record["taker_buy_base_volume"]),
                taker_buy_quote_volume=Decimal(record["taker_buy_quote_volume"]),
                is_closed=bool(record["is_closed"]),
                source=record["source"],
                ingested_at=_time(record["ingested_at"]),
            )
        )
    candles.sort(key=lambda c: c.open_time)
    return candles


def merge_candles(existing: list[Candle], incoming: list[Candle]) -> list[Candle]:
    """Upsert by open_time. Incoming replaces an existing bar. No forward fill."""
    by_open = {candle.open_time: candle for candle in existing}
    for candle in incoming:
        by_open[candle.open_time] = candle
    return [by_open[key] for key in sorted(by_open)]


def _row(candle: Candle) -> dict[str, object]:
    return {
        "exchange": candle.exchange,
        "market_type": candle.market_type,
        "symbol": candle.symbol,
        "interval": candle.interval,
        "open_time": candle.open_time,
        "close_time": candle.close_time,
        "open": format(candle.open, "f"),
        "high": format(candle.high, "f"),
        "low": format(candle.low, "f"),
        "close": format(candle.close, "f"),
        "volume": format(candle.volume, "f"),
        "quote_volume": format(candle.quote_volume, "f"),
        "trade_count": candle.trade_count,
        "taker_buy_base_volume": format(candle.taker_buy_base_volume, "f"),
        "taker_buy_quote_volume": format(candle.taker_buy_quote_volume, "f"),
        "is_closed": candle.is_closed,
        "source": candle.source,
        "ingested_at": candle.ingested_at,
    }


def _empty_template() -> Candle:
    now = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return Candle(
        exchange="",
        market_type="",
        symbol="",
        interval="1d",
        open_time=now,
        close_time=now,
        open=Decimal("0"),
        high=Decimal("0"),
        low=Decimal("0"),
        close=Decimal("0"),
        volume=Decimal("0"),
        quote_volume=Decimal("0"),
        trade_count=0,
        taker_buy_base_volume=Decimal("0"),
        taker_buy_quote_volume=Decimal("0"),
        is_closed=False,
        source="",
        ingested_at=now,
    )


def _time(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.to_pydatetime()
