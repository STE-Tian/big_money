"""Binance Spot public market-data client.

Only the allowlisted read endpoints are reachable. Order, account, withdraw, and
margin paths raise SafetyError before a socket is used.

Endpoint facts checked against binance-spot-api-docs rest-api.md on 2026-11-27:
GET /api/v3/klines weight 2, limit maximum 1000.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from pquant.domain.errors import BanError, ExchangeError, NetworkError, RateLimitError
from pquant.domain.models import Candle, Instrument
from pquant.safety import assert_public_path
from pquant.utils.time import ms_to_utc

USER_AGENT = "personal-quant-ai/1.0 (research; public-market-data-only)"


class BinanceMarketDataClient:
    def __init__(
        self,
        *,
        base_url: str,
        fallback_base_url: str | None,
        timeout_seconds: float = 30,
        max_retries: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleeper: Any = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback_base_url = fallback_base_url.rstrip("/") if fallback_base_url else None
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._transport = transport
        self._sleeper = sleeper
        self.last_used_weight_1m: str | None = None
        self.last_request_record: dict[str, Any] | None = None

    def get_server_time(self) -> datetime:
        payload = self._get_json("/api/v3/time", {})
        return ms_to_utc(int(payload["serverTime"]))

    def ping(self) -> None:
        self._get_json("/api/v3/ping", {})

    def get_exchange_info(self, symbols: list[str]) -> list[Instrument]:
        # One symbol per call keeps the response small and matches the public endpoint.
        instruments: list[Instrument] = []
        captured = datetime.now(timezone.utc)
        for symbol in symbols:
            payload = self._get_json("/api/v3/exchangeInfo", {"symbol": symbol})
            rows = payload.get("symbols") or []
            if not rows:
                raise ExchangeError(f"exchangeInfo returned no symbol payload for {symbol}")
            instruments.append(_instrument_from_exchange_info(rows[0], captured))
        return instruments

    def get_ticker_price(self, symbol: str) -> Decimal:
        payload = self._get_json("/api/v3/ticker/price", {"symbol": symbol})
        return Decimal(str(payload["price"]))

    def get_ticker_24hr(self, symbol: str) -> dict[str, Any]:
        payload = self._get_json("/api/v3/ticker/24hr", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise ExchangeError("ticker/24hr returned an unexpected payload")
        return payload

    def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
        server_time: datetime,
        source: str,
        ingested_at: datetime,
    ) -> list[Candle]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms
        payload = self._get_json("/api/v3/klines", params)
        if not isinstance(payload, list):
            raise ExchangeError(f"klines payload is not a list: {type(payload).__name__}")
        return [
            parse_kline_row(
                row,
                symbol=symbol,
                interval=interval,
                server_time=server_time,
                source=source,
                ingested_at=ingested_at,
            )
            for row in payload
        ]

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        assert_public_path(path)
        bases = [self.base_url]
        if self.fallback_base_url and self.fallback_base_url != self.base_url:
            bases.append(self.fallback_base_url)
        last_error: Exception | None = None
        for index, base in enumerate(bases):
            try:
                return self._get_json_with_retries(base, path, params)
            except BanError:
                raise
            except (NetworkError, RateLimitError, ExchangeError) as exc:
                last_error = exc
                # 429 already slept inside the host loop. A ban never falls through.
                # Fallback only helps when the primary host itself failed.
                if index == len(bases) - 1:
                    raise
                if isinstance(exc, RateLimitError):
                    raise
        if last_error:
            raise last_error
        raise NetworkError(f"no response for {path}")

    def _get_json_with_retries(self, base: str, path: str, params: dict[str, Any]) -> Any:
        backoff = 1.0
        url = f"{base}{path}"
        last_status: int | None = None
        with httpx.Client(transport=self._transport, timeout=self.timeout_seconds, headers=_headers()) as client:
            for attempt in range(self.max_retries):
                started = time.perf_counter()
                try:
                    response = client.get(url, params=params)
                except httpx.TransportError as exc:
                    elapsed = int((time.perf_counter() - started) * 1000)
                    self._remember(base, path, params, None, 0, elapsed, str(exc))
                    if attempt == self.max_retries - 1:
                        raise NetworkError(f"transport error calling {path} on {base}: {exc}") from exc
                    self._sleeper(backoff)
                    backoff *= 2
                    continue
                elapsed = int((time.perf_counter() - started) * 1000)
                weight = response.headers.get("x-mbx-used-weight-1m") or response.headers.get("X-MBX-USED-WEIGHT-1M")
                self.last_used_weight_1m = weight
                last_status = response.status_code
                self._remember(base, path, params, response.status_code, _row_count(response), elapsed, None, weight)
                if response.status_code == 418:
                    retry_after = response.headers.get("Retry-After")
                    raise BanError(
                        f"HTTP 418 from {base}{path}. Stopping requests. Retry-After={retry_after or 'unspecified'}."
                    )
                if response.status_code == 429:
                    retry_after_raw = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after_raw) if retry_after_raw is not None else backoff
                    except ValueError:
                        delay = backoff
                    if attempt == self.max_retries - 1:
                        raise RateLimitError(f"HTTP 429 from {base}{path} after retries. Retry-After={retry_after_raw}.")
                    self._sleeper(delay)
                    backoff *= 2
                    continue
                if 500 <= response.status_code < 600:
                    if attempt == self.max_retries - 1:
                        raise NetworkError(
                            f"HTTP {response.status_code} from {base}{path} after {self.max_retries} attempts. Body: {_safe_body(response)}"
                        )
                    self._sleeper(backoff)
                    backoff *= 2
                    continue
                if response.status_code >= 400:
                    raise ExchangeError(
                        f"HTTP {response.status_code} from {base}{path}. Body: {_safe_body(response)}"
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise ExchangeError(f"non-JSON response from {base}{path}") from exc
        raise NetworkError(f"exhausted retries for {url}, last status {last_status}")

    def _remember(
        self,
        base: str,
        path: str,
        params: dict[str, Any],
        status: int | None,
        rows: int,
        elapsed_ms: int,
        error: str | None,
        weight: str | None = None,
    ) -> None:
        self.last_request_record = {
            "base_url": base,
            "path": path,
            "symbol": params.get("symbol"),
            "interval": params.get("interval"),
            "start_time_ms": params.get("startTime"),
            "end_time_ms": params.get("endTime"),
            "http_status": status,
            "rows_returned": rows,
            "used_weight_1m": weight if weight is not None else self.last_used_weight_1m,
            "elapsed_ms": elapsed_ms,
            "error_message": error,
        }


def parse_kline_row(
    row: list[Any],
    *,
    symbol: str,
    interval: str,
    server_time: datetime,
    source: str,
    ingested_at: datetime,
) -> Candle:
    if len(row) < 11:
        raise ExchangeError(f"kline row has {len(row)} fields, expected at least 11")
    open_time = ms_to_utc(int(row[0]))
    close_time = ms_to_utc(int(row[6]))
    # The forming candle's close_time is still ahead of exchange time.
    is_closed = server_time > close_time
    return Candle(
        exchange="binance",
        market_type="spot",
        symbol=symbol.upper(),
        interval=interval,
        open_time=open_time,
        close_time=close_time,
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        quote_volume=Decimal(str(row[7])),
        trade_count=int(row[8]),
        taker_buy_base_volume=Decimal(str(row[9])),
        taker_buy_quote_volume=Decimal(str(row[10])),
        is_closed=is_closed,
        source=source,
        ingested_at=ingested_at,
    )


def _instrument_from_exchange_info(payload: dict[str, Any], captured_at: datetime) -> Instrument:
    filters = {item["filterType"]: item for item in payload.get("filters", [])}
    price = filters.get("PRICE_FILTER", {})
    lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
    min_notional = notional.get("minNotional", "0")
    return Instrument(
        exchange="binance",
        market_type="spot",
        symbol=str(payload["symbol"]),
        status=str(payload.get("status", "UNKNOWN")),
        base_asset=str(payload.get("baseAsset", "")),
        quote_asset=str(payload.get("quoteAsset", "")),
        price_tick=Decimal(str(price.get("tickSize", "0.00000001"))),
        qty_step=Decimal(str(lot.get("stepSize", "0.00000001"))),
        min_qty=Decimal(str(lot.get("minQty", "0"))),
        min_notional=Decimal(str(min_notional)),
        captured_at=captured_at,
    )


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _safe_body(response: httpx.Response) -> str:
    text = response.text[:300].replace("\n", " ")
    return text


def _row_count(response: httpx.Response) -> int:
    try:
        payload = response.json()
    except Exception:
        return 0
    if isinstance(payload, list):
        return len(payload)
    return 0
