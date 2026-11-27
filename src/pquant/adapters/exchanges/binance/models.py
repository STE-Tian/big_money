"""Parsed public Binance payloads. Order payloads are intentionally absent."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from pquant.domain.models import ensure_utc


class ServerTime(BaseModel):
    model_config = ConfigDict(frozen=True)

    server_time: datetime

    def _check(self) -> None:
        ensure_utc(self.server_time)


class KlineRequestRecord(BaseModel):
    """Metadata saved for every kline page."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    symbol: str
    interval: str
    start_time_ms: int | None
    end_time_ms: int | None
    http_status: int
    rows_returned: int
    data_open_first: datetime | None
    data_open_last: datetime | None
    used_weight_1m: str | None
    elapsed_ms: int
    error_message: str | None = None
