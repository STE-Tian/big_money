"""Parquet storage for funding events and V2 feature frames.

Spot klines reuse the V1 candle parquet writer. Funding and features live in
separate files. Derived 1d feature inputs are not written onto the V1 1d path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from pquant.v2.features import FEATURE_COLUMNS
from pquant.v2.funding import FundingEvent


def funding_parquet_path(directory: Path, symbol: str) -> Path:
    return directory / f"binance_um_funding_{symbol.upper()}.parquet"


def feature_parquet_path(directory: Path, symbol: str, interval: str) -> Path:
    return directory / f"{symbol.upper()}_{interval}.parquet"


def write_funding(path: Path, events: list[FundingEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(events, key=lambda item: item.funding_time)
    frame = pd.DataFrame([_funding_row(event) for event in ordered])
    if frame.empty:
        frame = pd.DataFrame(columns=list(_funding_row(_empty_funding()).keys()))
    for column in ("funding_rate", "funding_interval_hours", "mark_price"):
        frame[column] = frame[column].astype("string")
    frame.to_parquet(path, index=False)


def read_funding(path: Path) -> list[FundingEvent]:
    if not path.exists():
        return []
    frame = pd.read_parquet(path)
    events: list[FundingEvent] = []
    for record in frame.to_dict(orient="records"):
        events.append(
            FundingEvent(
                exchange=record["exchange"],
                market_type=record["market_type"],
                symbol=record["symbol"],
                funding_time=_time(record["funding_time"]),
                funding_rate=Decimal(str(record["funding_rate"])),
                funding_interval_hours=_optional_decimal(record["funding_interval_hours"]),
                mark_price=_optional_decimal(record["mark_price"]),
                rate_type=_optional_text(record["rate_type"]),
                source=record["source"],
                ingested_at=_time(record["ingested_at"]),
            )
        )
    events.sort(key=lambda item: item.funding_time)
    return events


def write_feature_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = frame.loc[:, FEATURE_COLUMNS].copy()
    ordered.to_parquet(path, index=False)


def read_feature_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=FEATURE_COLUMNS)
    frame = pd.read_parquet(path)
    return frame.loc[:, FEATURE_COLUMNS]


def _funding_row(event: FundingEvent) -> dict[str, object]:
    return {
        "exchange": event.exchange,
        "market_type": event.market_type,
        "symbol": event.symbol,
        "funding_time": event.funding_time,
        "funding_rate": format(event.funding_rate, "f"),
        "funding_interval_hours": None
        if event.funding_interval_hours is None
        else format(event.funding_interval_hours, "f"),
        "mark_price": None if event.mark_price is None else format(event.mark_price, "f"),
        "rate_type": event.rate_type,
        "source": event.source,
        "ingested_at": event.ingested_at,
        "research_feature_only": True,
        "tradable": False,
    }


def _empty_funding() -> FundingEvent:
    now = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return FundingEvent(
        symbol="BTCUSDT",
        funding_time=now,
        funding_rate=Decimal("0"),
        source="",
        ingested_at=now,
    )


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() == "none" or text == "<NA>":
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return Decimal(text)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    text = str(value).strip()
    return text or None


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
