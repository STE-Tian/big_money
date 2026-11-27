"""Collect, validate, and build the V2 point-in-time feature store.

Spot 1h/4h bars are the market data. USD-M funding events are joined as of each
bar close and never become a traded instrument. Derived 1d bars used for
relative strength are kept in memory and in the feature files. They are not
written to the V1 daily candle path, and this module does not call the
backtest engine.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pquant.adapters.exchanges.binance.client import BinanceMarketDataClient
from pquant.domain.errors import BanError, ConfigError, DataQualityError, ExchangeError, NetworkError
from pquant.domain.models import Candle
from pquant.ingestion.quality import closed_only
from pquant.storage.parquet_store import merge_candles, parquet_path, read_candles, write_candles
from pquant.utils.hashing import content_hash, dataset_hash
from pquant.utils.time import interval_ms, ms_to_utc, utc_to_ms
from pquant.v2.archive import (
    VisionArchiveClient,
    funding_archive_path,
    parse_funding_zip,
    parse_spot_kline_zip,
    spot_kline_archive_path,
)
from pquant.v2.config import V2Settings
from pquant.v2.features import (
    FeatureBuild,
    SymbolFeatureInput,
    aggregate_closed_4h_to_1d,
    build_feature_frames,
    config_fingerprint,
    feature_frame_hash,
)
from pquant.v2.funding import FundingEvent, FundingRateClient, merge_funding
from pquant.v2.quality import funding_dataset_hash, funding_quality, spot_quality_report
from pquant.v2.store import (
    feature_parquet_path,
    funding_parquet_path,
    read_funding,
    write_feature_frame,
    write_funding,
)

MANIFEST_NAME = "p1_manifest.json"
logger = logging.getLogger("pquant.v2.pipeline")


def collect_v2(
    settings: V2Settings,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    symbols: list[str] | None = None,
    intervals: list[str] | None = None,
    archive: VisionArchiveClient | None = None,
    spot_client: BinanceMarketDataClient | None = None,
    funding_client: FundingRateClient | None = None,
    sleeper: Any = time.sleep,
    explicit_end: bool = False,
) -> dict[str, Any]:
    symbols = [item.upper() for item in (symbols or settings.market_data.symbols)]
    intervals = list(intervals or settings.market_data.intervals)
    _reject_unknown(settings, symbols, intervals)
    archive = archive or VisionArchiveClient(base_url=settings.market_data.archive_base_url)
    spot_client = spot_client or BinanceMarketDataClient(
        base_url=settings.market_data.base_url,
        fallback_base_url=settings.market_data.fallback_base_url,
        timeout_seconds=settings.market_data.request_timeout_seconds,
        max_retries=settings.market_data.max_retries,
        sleeper=sleeper,
    )
    funding_client = funding_client or FundingRateClient(
        base_url=settings.funding.base_url,
        timeout_seconds=settings.funding.request_timeout_seconds,
        max_retries=settings.funding.max_retries,
        sleeper=sleeper,
    )
    server_time = spot_client.get_server_time()
    window_start = start or settings.market_data.history_start
    window_end = end or settings.market_data.history_end
    if window_end is None:
        window_end = _floor_time(server_time, min(interval_ms(item) for item in intervals))
    if window_end <= window_start:
        raise ConfigError("V2 collection window is empty")
    _assert_window(window_start, window_end if explicit_end or settings.market_data.history_end else None, intervals)
    ingested_at = datetime.now(timezone.utc)
    spot_results = []
    for symbol in symbols:
        for interval in intervals:
            spot_results.append(
                _collect_spot(
                    settings,
                    archive=archive,
                    spot_client=spot_client,
                    symbol=symbol,
                    interval=interval,
                    start=window_start,
                    end=window_end,
                    server_time=server_time,
                    ingested_at=ingested_at,
                    sleeper=sleeper,
                    check_freshness=not explicit_end and settings.market_data.history_end is None,
                )
            )
    funding_results = []
    for symbol in symbols:
        funding_results.append(
            _collect_funding(
                settings,
                archive=archive,
                funding_client=funding_client,
                symbol=symbol,
                start=window_start,
                end=window_end,
                ingested_at=ingested_at,
                sleeper=sleeper,
            )
        )
    report = {
        "stage": "collect",
        "generated_at": ingested_at.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "server_time": server_time.isoformat(),
        "trading_mode": settings.app.trading_mode.value,
        "live_trading_enabled": False,
        "funding_tradable": False,
        "spot": spot_results,
        "funding": funding_results,
    }
    _write_manifest(settings, report)
    return report


def validate_v2(settings: V2Settings) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    spot_reports = []
    failed = False
    for symbol in settings.market_data.symbols:
        for interval in settings.market_data.intervals:
            path = parquet_path(settings.storage.parquet_dir, "binance", "spot", symbol, interval)
            candles = read_candles(path)
            if not candles:
                failed = True
                spot_reports.append(
                    {"symbol": symbol, "interval": interval, "status": "empty", "dataset_hash": None, "closed_rows": 0}
                )
                continue
            report = spot_quality_report(
                candles,
                symbol=symbol,
                interval=interval,
                generated_at=generated_at,
                server_time=None,
                max_missing_ratio=float(settings.validation.max_missing_ratio),
                check_freshness=False,
            )
            _write_quality(settings, f"{symbol}_{interval}.json", report.to_json_dict())
            spot_reports.append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "status": report.status,
                    "dataset_hash": report.dataset_hash,
                    "closed_rows": report.closed_rows,
                    "open_rows": report.open_rows,
                    "missing_count": report.missing_count,
                    "reasons": report.reasons,
                }
            )
            if report.status != "qualified":
                failed = True
    funding_reports = []
    for symbol in settings.funding.symbols:
        events = read_funding(funding_parquet_path(settings.storage.parquet_dir, symbol))
        summary = funding_quality(events, symbol=symbol, window_start=None, window_end=None)
        _write_quality(settings, f"funding_{symbol}.json", summary)
        funding_reports.append(summary)
        if summary["status"] != "qualified":
            failed = True
    payload = {
        "stage": "validate",
        "generated_at": generated_at.isoformat(),
        "ok": not failed,
        "spot": spot_reports,
        "funding": funding_reports,
    }
    _write_manifest(settings, payload)
    return payload


def build_v2_features(settings: V2Settings) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    fingerprint = config_fingerprint(
        windows=settings.features.windows,
        funding_high_threshold=settings.features.funding_high_threshold,
        annualize_realized_vol=settings.features.annualize_realized_vol,
        relative_strength_intervals=settings.market_data.relative_strength_intervals,
    )
    inputs: list[SymbolFeatureInput] = []
    derived_counts: dict[str, int] = {}
    for interval in settings.market_data.intervals:
        for symbol in settings.market_data.symbols:
            candles = read_candles(parquet_path(settings.storage.parquet_dir, "binance", "spot", symbol, interval))
            if not candles:
                raise DataQualityError(f"no spot candles stored for {symbol} {interval}")
            _reject_synthetic(candles)
            events = read_funding(funding_parquet_path(settings.storage.parquet_dir, symbol))
            _reject_tradable_funding(events)
            inputs.append(
                SymbolFeatureInput(
                    symbol=symbol,
                    interval=interval,
                    candles=candles,
                    funding=events,
                    spot_dataset_hash=dataset_hash(candles),
                    funding_dataset_hash=funding_dataset_hash(events),
                )
            )
    if "1d" in settings.market_data.relative_strength_intervals and "4h" in settings.market_data.intervals:
        for symbol in settings.market_data.symbols:
            source = read_candles(parquet_path(settings.storage.parquet_dir, "binance", "spot", symbol, "4h"))
            _reject_synthetic(source)
            derived = aggregate_closed_4h_to_1d(source)
            derived_counts[symbol] = len(derived)
            events = read_funding(funding_parquet_path(settings.storage.parquet_dir, symbol))
            inputs.append(
                SymbolFeatureInput(
                    symbol=symbol,
                    interval="1d",
                    candles=derived,
                    funding=events,
                    spot_dataset_hash=dataset_hash(derived),
                    funding_dataset_hash=funding_dataset_hash(events),
                )
            )
    built = build_feature_frames(
        inputs,
        windows=settings.features.windows,
        funding_high_threshold=settings.features.funding_high_threshold,
        annualize_realized_vol=settings.features.annualize_realized_vol,
        relative_strength_intervals=set(settings.market_data.relative_strength_intervals),
        config_hash=fingerprint,
    )
    _assert_no_v1_daily_write(settings)
    feature_rows = _write_frames(settings, built)
    payload = {
        "stage": "features",
        "generated_at": generated_at.isoformat(),
        "feature_set_version": "v2.p1",
        "config_hash": fingerprint,
        "feature_hash": feature_frame_hash(built.frames),
        "dropped_unclosed": {f"{symbol}_{interval}": count for (symbol, interval), count in built.dropped_unclosed.items()},
        "derived_1d_rows": derived_counts,
        "features": feature_rows,
        "funding_tradable": False,
        "v1_daily_path_written": False,
    }
    _write_manifest(settings, payload)
    return payload


def _collect_spot(
    settings: V2Settings,
    *,
    archive: VisionArchiveClient,
    spot_client: BinanceMarketDataClient,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    server_time: datetime,
    ingested_at: datetime,
    sleeper: Any,
    check_freshness: bool,
) -> dict[str, Any]:
    archived, files = _load_spot_archives(
        archive,
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        server_time=server_time,
        ingested_at=ingested_at,
    )
    windowed = [candle for candle in archived if start <= candle.open_time < end]
    missing = _missing_opens(windowed, start, end, interval)
    rest_error = None
    rest: list[Candle] = []
    crosscheck = not missing and len(closed_only(windowed)) <= settings.market_data.rest_crosscheck_max_bars
    if missing or crosscheck:
        rest_start = missing[0] if missing else start
        try:
            rest = _fetch_rest_klines(
                spot_client,
                settings,
                symbol=symbol,
                interval=interval,
                start=rest_start,
                end=end,
                server_time=server_time,
                sleeper=sleeper,
            )
        except BanError:
            raise
        except (NetworkError, ExchangeError) as exc:
            rest_error = str(exc)
            if missing:
                raise ExchangeError(
                    f"spot archives for {symbol} {interval} do not cover the window and REST failed: {exc}"
                ) from exc
    revised = _revision_count(windowed, rest)
    merged_window = merge_candles(windowed, rest)
    existing = read_candles(parquet_path(settings.storage.parquet_dir, "binance", "spot", symbol, interval))
    merged = merge_candles(existing, merged_window)
    path = parquet_path(settings.storage.parquet_dir, "binance", "spot", symbol, interval)
    write_candles(path, merged)
    report = spot_quality_report(
        merged,
        symbol=symbol,
        interval=interval,
        generated_at=ingested_at,
        server_time=server_time,
        max_missing_ratio=float(settings.validation.max_missing_ratio),
        check_freshness=check_freshness,
        revised_candle_count=revised,
    )
    _write_quality(settings, f"{symbol}_{interval}.json", report.to_json_dict())
    return {
        "symbol": symbol,
        "interval": interval,
        "rows_stored": len(merged),
        "closed_rows": report.closed_rows,
        "open_rows": report.open_rows,
        "quality_status": report.status,
        "dataset_hash": report.dataset_hash,
        "missing_count": report.missing_count,
        "revised_against_archive": revised,
        "archive_files": files,
        "rest_error": rest_error,
        "reasons": report.reasons,
        "market_type": "spot",
    }


def _collect_funding(
    settings: V2Settings,
    *,
    archive: VisionArchiveClient,
    funding_client: FundingRateClient,
    symbol: str,
    start: datetime,
    end: datetime,
    ingested_at: datetime,
    sleeper: Any,
) -> dict[str, Any]:
    events: list[FundingEvent] = []
    files: list[dict[str, object]] = []
    for month in _iter_months(start, end):
        stamp = month.strftime("%Y-%m")
        path = funding_archive_path(symbol, stamp)
        logger.info("funding archive %s", path)
        fetched = archive.fetch(path)
        if fetched is None:
            continue
        parsed = parse_funding_zip(
            fetched.payload,
            symbol=symbol,
            source=f"binance_vision:{settings.market_data.archive_base_url}{path}",
            ingested_at=ingested_at,
        )
        events.extend(event for event in parsed if start <= event.funding_time < end)
        files.append({"path": path, "sha256": fetched.sha256, "checksum_verified": fetched.checksum_verified})
    rest_error = None
    rest: list[FundingEvent] = []
    try:
        rest = _fetch_rest_funding(
            funding_client,
            settings,
            symbol=symbol,
            start=start,
            end=end,
            ingested_at=ingested_at,
            sleeper=sleeper,
        )
    except BanError:
        raise
    except (NetworkError, ExchangeError) as exc:
        rest_error = str(exc)
    merged_window, revised = merge_funding(events, [event for event in rest if start <= event.funding_time < end])
    existing = read_funding(funding_parquet_path(settings.storage.parquet_dir, symbol))
    merged, _ = merge_funding(existing, merged_window)
    _reject_tradable_funding(merged)
    write_funding(funding_parquet_path(settings.storage.parquet_dir, symbol), merged)
    summary = funding_quality(merged, symbol=symbol, window_start=start, window_end=end)
    summary["revised_against_archive"] = revised
    summary["archive_files"] = files
    summary["rest_error"] = rest_error
    summary["market_type"] = "usd_m_perpetual"
    summary["note"] = (
        "Funding-rate history is a research feature only. "
        "It is not a traded symbol, and this pipeline does not place perpetual orders."
    )
    _write_quality(settings, f"funding_{symbol}.json", summary)
    return summary


def _load_spot_archives(
    archive: VisionArchiveClient,
    *,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    server_time: datetime,
    ingested_at: datetime,
) -> tuple[list[Candle], list[dict[str, object]]]:
    candles: list[Candle] = []
    files: list[dict[str, object]] = []
    covered: set[tuple[int, int]] = set()
    for month in _iter_months(start, end):
        stamp = month.strftime("%Y-%m")
        path = spot_kline_archive_path(symbol, interval, stamp, monthly=True)
        logger.info("spot archive %s", path)
        fetched = archive.fetch(path)
        if fetched is None:
            continue
        covered.add((month.year, month.month))
        candles.extend(
            _parse_spot_file(fetched.payload, fetched.path, symbol, interval, server_time, ingested_at, archive)
        )
        files.append({"path": path, "sha256": fetched.sha256, "checksum_verified": True, "kind": "monthly"})
    day = start.date()
    last = (end - timedelta(microseconds=1)).date()
    while day <= last:
        if (day.year, day.month) not in covered:
            stamp = day.strftime("%Y-%m-%d")
            path = spot_kline_archive_path(symbol, interval, stamp, monthly=False)
            fetched = archive.fetch(path)
            if fetched is not None:
                candles.extend(
                    _parse_spot_file(fetched.payload, fetched.path, symbol, interval, server_time, ingested_at, archive)
                )
                files.append({"path": path, "sha256": fetched.sha256, "checksum_verified": True, "kind": "daily"})
        day += timedelta(days=1)
    return candles, files


def _parse_spot_file(payload, path, symbol, interval, server_time, ingested_at, archive) -> list[Candle]:
    source = f"binance_vision:{archive.base_url}{path}"
    return parse_spot_kline_zip(
        payload,
        symbol=symbol,
        interval=interval,
        server_time=server_time,
        source=source,
        ingested_at=ingested_at,
    )


def _fetch_rest_klines(
    client: BinanceMarketDataClient,
    settings: V2Settings,
    *,
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    server_time: datetime,
    sleeper: Any,
) -> list[Candle]:
    step = timedelta(milliseconds=interval_ms(interval))
    limit = settings.market_data.klines_limit
    cursor = start
    end_ms = utc_to_ms(end) - 1
    pages: list[Candle] = []
    seen: datetime | None = None
    source = f"binance_rest:{settings.market_data.base_url}"
    while cursor < end:
        _pause_for_weight(client, sleeper)
        batch = client.get_klines(
            symbol=symbol,
            interval=interval,
            start_time_ms=utc_to_ms(cursor),
            end_time_ms=end_ms,
            limit=limit,
            server_time=server_time,
            source=source,
            ingested_at=datetime.now(timezone.utc),
        )
        if not batch:
            break
        fresh = [candle for candle in batch if seen is None or candle.open_time > seen]
        pages.extend(batch)
        if not fresh:
            break
        seen = max(candle.open_time for candle in batch)
        if len(batch) < limit:
            break
        cursor = seen + step
        sleeper(settings.market_data.request_pause_seconds)
    return [candle for candle in pages if start <= candle.open_time < end]


def _fetch_rest_funding(
    client: FundingRateClient,
    settings: V2Settings,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    ingested_at: datetime,
    sleeper: Any,
) -> list[FundingEvent]:
    cursor = utc_to_ms(start)
    end_ms = utc_to_ms(end) - 1
    source = f"binance_fapi:{settings.funding.base_url}/fapi/v1/fundingRate"
    events: list[FundingEvent] = []
    seen: datetime | None = None
    while True:
        batch = client.get_funding_rates(
            symbol=symbol,
            start_time_ms=cursor,
            end_time_ms=end_ms,
            limit=settings.funding.limit,
            source=source,
            ingested_at=ingested_at,
        )
        if not batch:
            break
        fresh = [event for event in batch if seen is None or event.funding_time > seen]
        events.extend(batch)
        if not fresh:
            break
        seen = batch[-1].funding_time
        if len(batch) < settings.funding.limit:
            break
        next_cursor = utc_to_ms(seen) + 1
        if next_cursor <= cursor or next_cursor > end_ms:
            break
        cursor = next_cursor
        sleeper(settings.funding.request_pause_seconds)
    return events


def _missing_opens(candles: list[Candle], start: datetime, end: datetime, interval: str) -> list[datetime]:
    step = timedelta(milliseconds=interval_ms(interval))
    present = {candle.open_time for candle in closed_only(candles) if start <= candle.open_time < end}
    missing: list[datetime] = []
    cursor = start
    while cursor < end:
        if cursor not in present:
            missing.append(cursor)
        cursor += step
    return missing


def _revision_count(archived: list[Candle], rest: list[Candle]) -> int:
    by_open = {candle.open_time: candle for candle in archived}
    revised = 0
    for candle in rest:
        previous = by_open.get(candle.open_time)
        if previous is not None and content_hash(previous) != content_hash(candle):
            revised += 1
    return revised


def _write_frames(settings: V2Settings, built: FeatureBuild) -> list[dict[str, object]]:
    rows = []
    for (symbol, interval), frame in sorted(built.frames.items()):
        path = feature_parquet_path(settings.storage.feature_dir, symbol, interval)
        write_feature_frame(path, frame)
        rows.append({"symbol": symbol, "interval": interval, "rows": int(len(frame)), "path": str(path.name)})
    return rows


def _assert_no_v1_daily_write(settings: V2Settings) -> None:
    """The feature build must not be pointed at the V1 daily candle file."""
    for symbol in settings.market_data.symbols:
        v1_daily = parquet_path(settings.storage.parquet_dir, "binance", "spot", symbol, "1d")
        feature_daily = feature_parquet_path(settings.storage.feature_dir, symbol, "1d")
        if v1_daily.resolve() == feature_daily.resolve():
            raise DataQualityError("refusing to write derived 1d features onto the V1 daily candle path")


def _reject_synthetic(candles: list[Candle]) -> None:
    for candle in candles:
        if "synthetic" in candle.source.lower():
            raise DataQualityError("refusing to build stored V2 research features from synthetic candles")


def _reject_tradable_funding(events: list[FundingEvent]) -> None:
    for event in events:
        if event.tradable or not event.research_feature_only:
            raise DataQualityError("funding event was marked tradable; V2 cannot store it")


def _reject_unknown(settings: V2Settings, symbols: list[str], intervals: list[str]) -> None:
    unknown_symbols = [item for item in symbols if item not in settings.market_data.symbols]
    unknown_intervals = [item for item in intervals if item not in settings.market_data.intervals]
    if unknown_symbols or unknown_intervals:
        raise ConfigError(f"collection request is outside V2 config: {unknown_symbols} {unknown_intervals}")


def _assert_window(start: datetime, end: datetime | None, intervals: list[str]) -> None:
    from pquant.v2.config import _assert_aligned

    for interval in intervals:
        _assert_aligned(start, interval)
        if end is not None:
            _assert_aligned(end, interval)


def _floor_time(value: datetime, step_ms: int) -> datetime:
    ms = utc_to_ms(value)
    return ms_to_utc(ms - (ms % step_ms))


def _iter_months(start: datetime, end: datetime) -> list[datetime]:
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    months: list[datetime] = []
    while cursor < end:
        months.append(cursor)
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1, tzinfo=timezone.utc)
    return months


def _pause_for_weight(client: BinanceMarketDataClient, sleeper: Any) -> None:
    raw = getattr(client, "last_used_weight_1m", None)
    if not raw:
        return
    try:
        used = int(raw)
    except (TypeError, ValueError):
        return
    if used > 4800:
        sleeper(5)


def _write_quality(settings: V2Settings, name: str, payload: dict[str, Any]) -> None:
    directory = settings.storage.reports_dir / "v2" / "quality"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _write_manifest(settings: V2Settings, section: dict[str, Any]) -> None:
    directory = settings.storage.reports_dir / "v2"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MANIFEST_NAME
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            existing = loaded
    stage = str(section.get("stage", "unknown"))
    existing[stage] = section
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing["live_trading_enabled"] = False
    existing["funding_tradable"] = False
    existing["note"] = (
        "V2 P1 research evidence. Parquet under data/parquet is gitignored. "
        "A bounded --start/--end window is an acceptance sample, not the full history. "
        "HTTP 451 from fapi.binance.com is recorded and not bypassed; "
        "published data.binance.vision fundingRate archives are used when they cover the window."
    )
    path.write_text(json.dumps(existing, indent=2, default=_json_default), encoding="utf-8")


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)
