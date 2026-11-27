"""Perpetual funding-rate history as a research feature.

These rows describe USD-M funding events. They are not tradable instruments.
The code does not open, hedge, or short a perpetual, and it does not apply
leverage. Empty historical markPrice values stay missing; they are not zero.
"""

from __future__ import annotations

import bisect
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from pquant.domain.errors import BanError, ExchangeError, NetworkError, RateLimitError
from pquant.domain.models import ensure_utc
from pquant.v2.safety import FUNDING_HOST, assert_funding_research_path, assert_https_host

USER_AGENT = "personal-quant-ai/1.0 (research; funding-rate-feature-only; no-orders)"


class FundingEvent(BaseModel):
    """One published funding event. research_feature_only is always true."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exchange: str = "binance"
    market_type: str = "usd_m_perpetual"
    symbol: str
    funding_time: datetime
    funding_rate: Decimal
    funding_interval_hours: Decimal | None = None
    mark_price: Decimal | None = None
    rate_type: str | None = None
    source: str
    ingested_at: datetime
    research_feature_only: Literal[True] = True
    tradable: Literal[False] = False

    @field_validator("funding_time", "ingested_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return value.upper()


class FundingRateClient:
    """GET /fapi/v1/fundingRate and nothing else.

    Checked against binance-connector-python's USD-M OpenAPI notes on 2026-11-27:
    startTime and endTime are inclusive; results are ascending; a range longer
    than limit returns the first limit rows from startTime. Weight is shared
    500/5min/IP with fundingInfo when limit <= 200, and 100/5min/IP above that.
    No API key is sent.
    """

    def __init__(
        self,
        *,
        base_url: str = f"https://{FUNDING_HOST}",
        timeout_seconds: float = 30,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleeper: Any = time.sleep,
    ) -> None:
        assert_https_host(base_url, FUNDING_HOST)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._transport = transport
        self._sleeper = sleeper

    def get_funding_rates(
        self,
        *,
        symbol: str,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
        source: str,
        ingested_at: datetime,
    ) -> list[FundingEvent]:
        if limit < 1 or limit > 1000:
            raise ExchangeError("fundingRate limit must be inside 1..1000")
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": limit}
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        payload = self._get_json("/fapi/v1/fundingRate", params)
        if not isinstance(payload, list):
            raise ExchangeError(f"fundingRate payload is not a list: {type(payload).__name__}")
        events = [
            parse_funding_payload(item, symbol=symbol, source=source, ingested_at=ingested_at)
            for item in payload
        ]
        events.sort(key=lambda item: item.funding_time)
        return events

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        assert_funding_research_path(path)
        url = f"{self.base_url}{path}"
        backoff = 1.0
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        with httpx.Client(
            transport=self._transport,
            timeout=self.timeout_seconds,
            headers=headers,
            follow_redirects=False,
        ) as client:
            for attempt in range(self.max_retries):
                try:
                    response = client.get(url, params=params)
                except httpx.TransportError as exc:
                    if attempt == self.max_retries - 1:
                        raise NetworkError(f"transport error calling {path}: {exc}") from exc
                    self._sleeper(backoff)
                    backoff *= 2
                    continue
                if response.status_code in {301, 302, 303, 307, 308}:
                    raise ExchangeError(f"refusing redirect from {path}")
                if response.status_code == 418:
                    raise BanError(f"HTTP 418 from {path}. Stopping funding requests.")
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after is not None else backoff
                    except ValueError:
                        delay = backoff
                    if attempt == self.max_retries - 1:
                        raise RateLimitError(f"HTTP 429 from {path} after retries")
                    self._sleeper(delay)
                    backoff *= 2
                    continue
                if response.status_code == 451:
                    raise ExchangeError(
                        "HTTP 451 from fapi.binance.com fundingRate. Public funding REST is "
                        "unavailable from this location. The collector does not bypass that "
                        "restriction. Published data.binance.vision fundingRate archives are "
                        "used when they exist."
                    )
                if 500 <= response.status_code < 600:
                    if attempt == self.max_retries - 1:
                        raise NetworkError(f"HTTP {response.status_code} from {path} after retries")
                    self._sleeper(backoff)
                    backoff *= 2
                    continue
                if response.status_code >= 400:
                    raise ExchangeError(f"HTTP {response.status_code} from {path}")
                if any(key.lower() == "x-mbx-apikey" for key in response.request.headers):
                    raise ExchangeError("funding research client sent an API key header")
                try:
                    return response.json()
                except ValueError as exc:
                    raise ExchangeError(f"non-JSON response from {path}") from exc
        raise NetworkError(f"exhausted retries for {path}")


def parse_funding_payload(
    payload: dict[str, Any],
    *,
    symbol: str,
    source: str,
    ingested_at: datetime,
) -> FundingEvent:
    if not isinstance(payload, dict):
        raise ExchangeError("fundingRate row is not an object")
    for key in ("symbol", "fundingTime", "fundingRate"):
        if key not in payload:
            raise ExchangeError(f"fundingRate row missing {key}")
    return FundingEvent(
        symbol=str(payload["symbol"] or symbol),
        funding_time=datetime.fromtimestamp(int(payload["fundingTime"]) / 1000, tz=timezone.utc),
        funding_rate=Decimal(str(payload["fundingRate"])),
        funding_interval_hours=None,
        mark_price=_optional_decimal(payload.get("markPrice")),
        rate_type=_optional_text(payload.get("rateType")),
        source=source,
        ingested_at=ingested_at,
    )


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return Decimal(text)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def asof_funding(close_time: datetime, events: list[FundingEvent]) -> FundingEvent | None:
    """Latest funding event with funding_time <= close_time.

    An event after the bar close is invisible. A missing history stays None.
    This is not a forward fill: the value was already published at funding_time.
    """
    if not events:
        return None
    ordered = sorted(events, key=lambda item: item.funding_time)
    times = [event.funding_time for event in ordered]
    index = bisect.bisect_right(times, close_time) - 1
    if index < 0:
        return None
    chosen = ordered[index]
    if chosen.funding_time > close_time:
        return None
    return chosen


def merge_funding(existing: list[FundingEvent], incoming: list[FundingEvent]) -> tuple[list[FundingEvent], int]:
    """Upsert by funding_time. Incoming replaces an existing event. No interpolation."""
    by_time = {event.funding_time: event for event in existing}
    revised = 0
    for event in incoming:
        previous = by_time.get(event.funding_time)
        if previous is not None and previous.funding_rate != event.funding_rate:
            revised += 1
        by_time[event.funding_time] = event
    ordered = [by_time[key] for key in sorted(by_time)]
    return ordered, revised
