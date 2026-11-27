"""Checksummed downloads from data.binance.vision.

Spot kline zips and USD-M fundingRate zips only. HTTP 404 means the archive
is not published yet. This client does not follow redirects and does not
call an order API.
"""

from __future__ import annotations

import hashlib
import io
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from pquant.adapters.exchanges.binance.client import parse_kline_row
from pquant.domain.errors import ExchangeError, NetworkError
from pquant.domain.models import Candle
from pquant.v2.epochs import epoch_to_ms
from pquant.v2.funding import FundingEvent
from pquant.v2.safety import VISION_HOST, assert_https_host, assert_vision_archive_path

USER_AGENT = "personal-quant-ai/1.0 (research; public-archive-only; no-orders)"


@dataclass(frozen=True)
class ArchiveFile:
    path: str
    sha256: str
    checksum_verified: bool
    payload: bytes


class VisionArchiveClient:
    def __init__(
        self,
        *,
        base_url: str = f"https://{VISION_HOST}",
        timeout_seconds: float = 60,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleeper: Any = time.sleep,
    ) -> None:
        assert_https_host(base_url, VISION_HOST)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._transport = transport
        self._sleeper = sleeper

    def fetch(self, path: str) -> ArchiveFile | None:
        """Return the zip, or None when Binance has not published it (HTTP 404)."""
        assert_vision_archive_path(path)
        payload = self._get_bytes(path)
        if payload is None:
            return None
        checksum_text = self._get_text(path + ".CHECKSUM")
        digest = hashlib.sha256(payload).hexdigest()
        if checksum_text is None:
            raise ExchangeError(f"archive {path} has no published .CHECKSUM file")
        expected = parse_checksum(checksum_text)
        filename = path.rsplit("/", 1)[-1]
        if expected != digest:
            raise ExchangeError(f"checksum mismatch for {filename}")
        if filename not in checksum_text:
            raise ExchangeError(f"checksum file for {path} does not name {filename}")
        return ArchiveFile(path=path, sha256=digest, checksum_verified=True, payload=payload)

    def _get_bytes(self, path: str) -> bytes | None:
        response = self._get(path)
        if response is None:
            return None
        return response.content

    def _get_text(self, path: str) -> str | None:
        response = self._get(path)
        if response is None:
            return None
        return response.text

    def _get(self, path: str) -> httpx.Response | None:
        assert_vision_archive_path(path)
        url = f"{self.base_url}{path}"
        backoff = 1.0
        with httpx.Client(
            transport=self._transport,
            timeout=self.timeout_seconds,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            follow_redirects=False,
        ) as client:
            for attempt in range(self.max_retries):
                try:
                    response = client.get(url)
                except httpx.TransportError as exc:
                    if attempt == self.max_retries - 1:
                        raise NetworkError(f"transport error calling {path}: {exc}") from exc
                    self._sleeper(backoff)
                    backoff *= 2
                    continue
                if response.status_code in {301, 302, 303, 307, 308}:
                    raise ExchangeError(f"refusing redirect from {path}")
                if response.status_code == 404:
                    return None
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt == self.max_retries - 1:
                        raise NetworkError(f"HTTP {response.status_code} from {path} after retries")
                    self._sleeper(backoff)
                    backoff *= 2
                    continue
                if response.status_code >= 400:
                    raise ExchangeError(f"HTTP {response.status_code} from {path}")
                return response
        raise NetworkError(f"exhausted retries for {path}")


def spot_kline_archive_path(symbol: str, interval: str, stamp: str, *, monthly: bool) -> str:
    symbol = symbol.upper()
    kind = "monthly" if monthly else "daily"
    path = f"/data/spot/{kind}/klines/{symbol}/{interval}/{symbol}-{interval}-{stamp}.zip"
    assert_vision_archive_path(path)
    return path


def funding_archive_path(symbol: str, year_month: str) -> str:
    symbol = symbol.upper()
    path = f"/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{year_month}.zip"
    assert_vision_archive_path(path)
    return path


def parse_checksum(text: str) -> str:
    token = text.strip().split()[0] if text.strip() else ""
    if len(token) != 64 or any(ch not in "0123456789abcdef" for ch in token.lower()):
        raise ExchangeError("archive checksum file does not start with a sha256 hex digest")
    return token.lower()


def parse_spot_kline_zip(
    payload: bytes,
    *,
    symbol: str,
    interval: str,
    server_time: datetime,
    source: str,
    ingested_at: datetime,
) -> list[Candle]:
    """Parse a vision kline zip. The ignore column is discarded."""
    candles: list[Candle] = []
    for line in _csv_lines(payload):
        parts = [item.strip() for item in line.split(",")]
        if not parts or parts[0].lower() in {"open_time", "opentime"}:
            continue
        if len(parts) < 11:
            raise ExchangeError(f"spot kline archive row has {len(parts)} fields, expected at least 11")
        row = [
            epoch_to_ms(int(parts[0])),
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
            epoch_to_ms(int(parts[6])),
            parts[7],
            int(Decimal(parts[8])),
            parts[9],
            parts[10],
            "0",
        ]
        candles.append(
            parse_kline_row(
                row,
                symbol=symbol,
                interval=interval,
                server_time=server_time,
                source=source,
                ingested_at=ingested_at,
            )
        )
    return candles


def parse_funding_zip(
    payload: bytes,
    *,
    symbol: str,
    source: str,
    ingested_at: datetime,
) -> list[FundingEvent]:
    """Parse calc_time, funding_interval_hours, last_funding_rate.

    Funding events are research features. They are not orders and not candles.
    """
    events: list[FundingEvent] = []
    for line in _csv_lines(payload):
        parts = [item.strip() for item in line.split(",")]
        if not parts or parts[0].lower() in {"calc_time", "fundingtime", "funding_time"}:
            continue
        if len(parts) < 3:
            raise ExchangeError(f"funding archive row has {len(parts)} fields, expected 3")
        events.append(
            FundingEvent(
                symbol=symbol.upper(),
                funding_time=_ms_to_utc(epoch_to_ms(int(parts[0]))),
                funding_rate=Decimal(parts[2]),
                funding_interval_hours=Decimal(parts[1]),
                mark_price=None,
                rate_type=None,
                source=source,
                ingested_at=ingested_at,
            )
        )
    events.sort(key=lambda item: item.funding_time)
    return events


def _csv_lines(payload: bytes) -> list[str]:
    lines: list[str] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".csv") and ".." not in name]
        if not names:
            raise ExchangeError("archive zip does not contain a csv member")
        for name in names:
            text = archive.read(name).decode("utf-8")
            lines.extend(line for line in text.splitlines() if line.strip())
    return lines


def _ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
