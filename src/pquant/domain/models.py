"""Domain models. Prices inside candles keep the exchange decimal strings."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


class Candle(BaseModel):
    """One OHLCV bar. Invalid OHLC is allowed here so quality reports can count it."""

    model_config = ConfigDict(frozen=True)

    exchange: str
    market_type: str
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal
    is_closed: bool
    source: str
    ingested_at: datetime

    @field_validator("open_time", "close_time", "ingested_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    def ohlcv_identity(self) -> str:
        parts = [
            format(self.open, "f"),
            format(self.high, "f"),
            format(self.low, "f"),
            format(self.close, "f"),
            format(self.volume, "f"),
            format(self.quote_volume, "f"),
            str(self.trade_count),
            format(self.taker_buy_base_volume, "f"),
            format(self.taker_buy_quote_volume, "f"),
        ]
        return "|".join(parts)


class Signal(BaseModel):
    """Strategy output. A signal is not an order."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    strategy_version: str
    symbol: str
    signal_time: datetime
    target_weight: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    signal_value: Decimal | None = None
    reason: str
    input_data_timestamp: datetime

    @field_validator("signal_time", "input_data_timestamp")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    exchange: str
    market_type: str
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    price_tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    min_notional: Decimal
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class CostAssumption(BaseModel):
    model_config = ConfigDict(frozen=True)

    fee_rate_per_side: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    slippage_rate_per_side: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    name: str


JudgmentLabel = Literal["REJECT", "INCONCLUSIVE", "CANDIDATE_FOR_PAPER", "BENCHMARK_ONLY"]
