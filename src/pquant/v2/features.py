"""Point-in-time V2 features.

Every value on bar t uses only closed bars at or before t, and only funding
events with funding_time <= close_time. Missing bars are not invented. Rolling
windows are trailing and become null until they are full, including when a
null sits inside the window. Unclosed bars are dropped before any calculation.

Funding columns describe a research feature. They are not target weights,
orders, or perpetual positions.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pandas as pd

from pquant.domain.errors import DataQualityError
from pquant.domain.models import Candle
from pquant.ingestion.quality import closed_only
from pquant.utils.time import annualization_periods
from pquant.v2 import FEATURE_SET_VERSION
from pquant.v2.config import FeatureWindows
from pquant.v2.funding import FundingEvent, asof_funding

FEATURE_COLUMNS = [
    "exchange",
    "market_type",
    "symbol",
    "interval",
    "open_time",
    "close_time",
    "available_time",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "taker_sell_base_volume",
    "taker_sell_quote_volume",
    "taker_imbalance",
    "taker_imbalance_roll",
    "taker_buy_exceeds_sell_roll",
    "taker_imbalance_quote",
    "taker_imbalance_quote_roll",
    "log_return",
    "volume_ratio",
    "price_volume_divergence",
    "price_up_volume_not",
    "price_volume_corr",
    "funding_rate",
    "funding_time",
    "funding_interval_hours",
    "funding_rate_times_return",
    "price_down_funding_high",
    "eth_btc_log_return",
    "eth_btc_relative_strength",
    "btc_up_ethbtc_weak",
    "btc_up_ethbtc_weak_roll",
    "realized_vol",
    "realized_vol_annualized",
    "feature_set_version",
    "config_hash",
    "spot_dataset_hash",
    "funding_dataset_hash",
]

@dataclass(frozen=True)
class SymbolFeatureInput:
    symbol: str
    interval: str
    candles: list[Candle]
    funding: list[FundingEvent]
    spot_dataset_hash: str
    funding_dataset_hash: str


@dataclass(frozen=True)
class FeatureBuild:
    frames: dict[tuple[str, str], pd.DataFrame]
    dropped_unclosed: dict[tuple[str, str], int]


def build_feature_frames(
    inputs: list[SymbolFeatureInput],
    *,
    windows: dict[str, FeatureWindows],
    funding_high_threshold: Decimal,
    annualize_realized_vol: bool,
    relative_strength_intervals: set[str],
    config_hash: str,
) -> FeatureBuild:
    dropped: dict[tuple[str, str], int] = {}
    prepared: dict[tuple[str, str], list[dict[str, object]]] = {}
    for item in inputs:
        key = (item.symbol, item.interval)
        closed, dropped_count = _closed_or_reject(item.candles, item.symbol, item.interval)
        dropped[key] = dropped_count
        prepared[key] = _single_symbol_rows(
            closed,
            funding=sorted(item.funding, key=lambda event: event.funding_time),
            windows=windows[item.interval],
            funding_high_threshold=funding_high_threshold,
            annualize=annualize_realized_vol,
            config_hash=config_hash,
            spot_dataset_hash=item.spot_dataset_hash,
            funding_dataset_hash=item.funding_dataset_hash,
        )
    _attach_relative_strength(
        prepared,
        windows=windows,
        relative_strength_intervals=relative_strength_intervals,
    )
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for key, rows in prepared.items():
        frame = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
        frames[key] = frame
    return FeatureBuild(frames=frames, dropped_unclosed=dropped)


def aggregate_closed_4h_to_1d(candles: list[Candle]) -> list[Candle]:
    """Build a UTC daily bar only when all six closed 4h bars of that day exist.

    The daily bar is a feature input. It is not written to the V1 daily parquet.
    """
    groups: dict[datetime, list[Candle]] = {}
    for candle in closed_only(candles):
        if candle.interval != "4h":
            continue
        if not _is_utc_4h_open(candle.open_time):
            continue
        day_key = candle.open_time.replace(hour=0, minute=0, second=0, microsecond=0)
        groups.setdefault(day_key, []).append(candle)
    days: list[Candle] = []
    for day_key in sorted(groups):
        bars = sorted(groups[day_key], key=lambda item: item.open_time)
        hours = [bar.open_time.hour for bar in bars]
        if hours != [0, 4, 8, 12, 16, 20] or len(bars) != 6:
            continue
        if any(bar.symbol != bars[0].symbol for bar in bars):
            continue
        days.append(_daily_from_4h(bars))
    return days


def feature_frame_hash(frames: dict[tuple[str, str], pd.DataFrame]) -> str:
    digest = hashlib.sha256()
    for key in sorted(frames):
        frame = frames[key]
        digest.update(f"{key[0]}|{key[1]}|{len(frame)}\n".encode("utf-8"))
        for record in frame.to_dict(orient="records"):
            parts = [key[0], key[1]]
            for column in FEATURE_COLUMNS:
                parts.append(_canon(record.get(column)))
            digest.update(("|".join(parts) + "\n").encode("utf-8"))
    return digest.hexdigest()


def config_fingerprint(
    *,
    windows: dict[str, FeatureWindows],
    funding_high_threshold: Decimal,
    annualize_realized_vol: bool,
    relative_strength_intervals: list[str],
) -> str:
    lines = [
        FEATURE_SET_VERSION,
        format(funding_high_threshold, "f"),
        str(annualize_realized_vol),
        ",".join(sorted(relative_strength_intervals)),
    ]
    for interval in sorted(windows):
        window = windows[interval]
        lines.append(
            "|".join(
                [
                    interval,
                    str(window.taker_imbalance),
                    str(window.volume_baseline),
                    str(window.price_volume_corr),
                    str(window.relative_strength),
                    str(window.realized_vol),
                ]
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _single_symbol_rows(
    candles: list[Candle],
    *,
    funding: list[FundingEvent],
    windows: FeatureWindows,
    funding_high_threshold: Decimal,
    annualize: bool,
    config_hash: str,
    spot_dataset_hash: str,
    funding_dataset_hash: str,
) -> list[dict[str, object]]:
    closes = [float(candle.close) for candle in candles]
    volumes = [float(candle.volume) for candle in candles]
    quote_volumes = [float(candle.quote_volume) for candle in candles]
    taker_base = [float(candle.taker_buy_base_volume) for candle in candles]
    taker_quote = [float(candle.taker_buy_quote_volume) for candle in candles]
    log_returns = _log_returns(closes)
    imbalances = [_imbalance(buy, volume) for buy, volume in zip(taker_base, volumes)]
    quote_imbalances = [_imbalance(buy, volume) for buy, volume in zip(taker_quote, quote_volumes)]
    imbalance_roll = _rolling_mean(imbalances, windows.taker_imbalance)
    quote_roll = _rolling_mean(quote_imbalances, windows.taker_imbalance)
    baselines = _prior_mean(volumes, windows.volume_baseline)
    volume_ratios = _volume_ratios(volumes, baselines)
    correlations = _rolling_corr(log_returns, volume_ratios, windows.price_volume_corr)
    realized = _rolling_std(log_returns, windows.realized_vol)
    scale = math.sqrt(annualization_periods(candles[0].interval)) if candles and annualize else None
    threshold = float(funding_high_threshold)
    rows: list[dict[str, object]] = []
    for index, candle in enumerate(candles):
        event = asof_funding(candle.close_time, funding)
        log_return = log_returns[index]
        volume_ratio = volume_ratios[index]
        funding_rate = float(event.funding_rate) if event is not None else None
        rows.append(
            {
                "exchange": candle.exchange,
                "market_type": "spot",
                "symbol": candle.symbol,
                "interval": candle.interval,
                "open_time": candle.open_time,
                "close_time": candle.close_time,
                "available_time": candle.close_time,
                "close": closes[index],
                "volume": volumes[index],
                "quote_volume": quote_volumes[index],
                "trade_count": candle.trade_count,
                "taker_buy_base_volume": taker_base[index],
                "taker_buy_quote_volume": taker_quote[index],
                "taker_sell_base_volume": _sell(volumes[index], taker_base[index]),
                "taker_sell_quote_volume": _sell(quote_volumes[index], taker_quote[index]),
                "taker_imbalance": imbalances[index],
                "taker_imbalance_roll": imbalance_roll[index],
                "taker_buy_exceeds_sell_roll": _flag_positive(imbalance_roll[index]),
                "taker_imbalance_quote": quote_imbalances[index],
                "taker_imbalance_quote_roll": quote_roll[index],
                "log_return": log_return,
                "volume_ratio": volume_ratio,
                "price_volume_divergence": _sub(log_return, volume_ratio),
                "price_up_volume_not": _flag_price_up_volume_not(log_return, volume_ratio),
                "price_volume_corr": correlations[index],
                "funding_rate": funding_rate,
                "funding_time": event.funding_time if event is not None else None,
                "funding_interval_hours": float(event.funding_interval_hours)
                if event is not None and event.funding_interval_hours is not None
                else None,
                "funding_rate_times_return": _mul(funding_rate, log_return),
                "price_down_funding_high": _flag_price_down_funding_high(log_return, funding_rate, threshold),
                "eth_btc_log_return": None,
                "eth_btc_relative_strength": None,
                "btc_up_ethbtc_weak": None,
                "btc_up_ethbtc_weak_roll": None,
                "realized_vol": realized[index],
                "realized_vol_annualized": None
                if realized[index] is None or scale is None
                else realized[index] * scale,
                "feature_set_version": FEATURE_SET_VERSION,
                "config_hash": config_hash,
                "spot_dataset_hash": spot_dataset_hash,
                "funding_dataset_hash": funding_dataset_hash,
            }
        )
    return rows


def _attach_relative_strength(
    prepared: dict[tuple[str, str], list[dict[str, object]]],
    *,
    windows: dict[str, FeatureWindows],
    relative_strength_intervals: set[str],
) -> None:
    intervals = {interval for _, interval in prepared if interval in relative_strength_intervals}
    for interval in intervals:
        btc_rows = prepared.get(("BTCUSDT", interval))
        eth_rows = prepared.get(("ETHUSDT", interval))
        if not btc_rows or not eth_rows:
            continue
        window = windows[interval].relative_strength
        btc_by_open = {row["open_time"]: row for row in btc_rows}
        eth_by_open = {row["open_time"]: row for row in eth_rows}
        shared = sorted(set(btc_by_open) & set(eth_by_open))
        eth_btc: list[float | None] = []
        btc_returns: list[float | None] = []
        for open_time in shared:
            btc_return = btc_by_open[open_time]["log_return"]
            eth_return = eth_by_open[open_time]["log_return"]
            btc_returns.append(btc_return if isinstance(btc_return, float) else None)
            if isinstance(btc_return, float) and isinstance(eth_return, float):
                eth_btc.append(eth_return - btc_return)
            else:
                eth_btc.append(None)
        strength = _rolling_sum(eth_btc, window)
        btc_sum = _rolling_sum(btc_returns, window)
        for index, open_time in enumerate(shared):
            bar_spread = eth_btc[index]
            btc_return = btc_returns[index]
            values = {
                "eth_btc_log_return": bar_spread,
                "eth_btc_relative_strength": strength[index],
                "btc_up_ethbtc_weak": _flag_btc_up_eth_weak(btc_return, bar_spread),
                "btc_up_ethbtc_weak_roll": _flag_btc_up_eth_weak(btc_sum[index], strength[index]),
            }
            btc_by_open[open_time].update(values)
            eth_by_open[open_time].update(values)
        # Opens present on only one symbol stay null. The other symbol's
        # future or neighbouring close is not copied in.


def _closed_or_reject(candles: list[Candle], symbol: str, interval: str) -> tuple[list[Candle], int]:
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    for candle in ordered:
        if candle.symbol != symbol or candle.interval != interval:
            raise DataQualityError(
                f"feature input {candle.symbol} {candle.interval} does not match {symbol} {interval}"
            )
    seen: set[datetime] = set()
    for candle in ordered:
        if candle.open_time in seen:
            raise DataQualityError(f"duplicate open_time in {symbol} {interval} feature input")
        seen.add(candle.open_time)
    closed = closed_only(ordered)
    return closed, len(ordered) - len(closed)


def _log_returns(closes: list[float]) -> list[float | None]:
    out: list[float | None] = [None]
    for index in range(1, len(closes)):
        previous = closes[index - 1]
        current = closes[index]
        if previous <= 0 or current <= 0:
            out.append(None)
        else:
            out.append(math.log(current / previous))
    return out


def _imbalance(taker_buy: float, volume: float) -> float | None:
    if volume <= 0 or taker_buy < 0 or taker_buy > volume:
        return None
    taker_sell = volume - taker_buy
    return (taker_buy - taker_sell) / volume


def _sell(volume: float, taker_buy: float) -> float | None:
    if volume < 0 or taker_buy < 0 or taker_buy > volume:
        return None
    return volume - taker_buy


def _volume_ratios(volumes: list[float], baselines: list[float | None]) -> list[float | None]:
    out: list[float | None] = []
    for volume, baseline in zip(volumes, baselines):
        if baseline is None or baseline <= 0 or volume < 0:
            out.append(None)
        else:
            out.append(volume / baseline - 1.0)
    return out


def _rolling_mean(values: list[float | None], window: int) -> list[float | None]:
    return [_reduce(values, index, window, _mean) for index in range(len(values))]


def _prior_mean(values: list[float], window: int) -> list[float | None]:
    """Mean of the previous `window` observations, excluding the current bar."""
    out: list[float | None] = []
    for index in range(len(values)):
        start = index - window
        if start < 0:
            out.append(None)
            continue
        chunk = values[start:index]
        if len(chunk) != window or any(item <= 0 for item in chunk):
            out.append(None)
        else:
            out.append(sum(chunk) / window)
    return out


def _rolling_sum(values: list[float | None], window: int) -> list[float | None]:
    return [_reduce(values, index, window, sum) for index in range(len(values))]


def _rolling_std(values: list[float | None], window: int) -> list[float | None]:
    return [_reduce(values, index, window, _sample_std) for index in range(len(values))]


def _rolling_corr(xs: list[float | None], ys: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    for index in range(len(xs)):
        start = index - window + 1
        if start < 0:
            out.append(None)
            continue
        left = xs[start : index + 1]
        right = ys[start : index + 1]
        if len(left) != window or any(item is None for item in left) or any(item is None for item in right):
            out.append(None)
            continue
        out.append(_pearson([float(item) for item in left], [float(item) for item in right]))
    return out


def _reduce(values: list[float | None], index: int, window: int, fn) -> float | None:
    start = index - window + 1
    if start < 0:
        return None
    chunk = values[start : index + 1]
    if len(chunk) != window or any(item is None for item in chunk):
        return None
    return fn([float(item) for item in chunk])


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sample_std(values: list[float]) -> float | None:
    count = len(values)
    if count < 2:
        return None
    mean = sum(values) / count
    variance = sum((item - mean) ** 2 for item in values) / (count - 1)
    return math.sqrt(variance)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    count = len(xs)
    if count < 2 or count != len(ys):
        return None
    mean_x = sum(xs) / count
    mean_y = sum(ys) / count
    dx = [item - mean_x for item in xs]
    dy = [item - mean_y for item in ys]
    var_x = sum(item * item for item in dx)
    var_y = sum(item * item for item in dy)
    if var_x == 0 or var_y == 0:
        return None
    cov = sum(left * right for left, right in zip(dx, dy))
    return cov / math.sqrt(var_x * var_y)


def _sub(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _mul(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left * right


def _flag_positive(value: float | None) -> float | None:
    if value is None:
        return None
    return 1.0 if value > 0 else 0.0


def _flag_price_up_volume_not(log_return: float | None, volume_ratio: float | None) -> float | None:
    if log_return is None or volume_ratio is None:
        return None
    return 1.0 if log_return > 0 and volume_ratio <= 0 else 0.0


def _flag_price_down_funding_high(
    log_return: float | None,
    funding_rate: float | None,
    threshold: float,
) -> float | None:
    if log_return is None or funding_rate is None:
        return None
    return 1.0 if log_return < 0 and funding_rate > threshold else 0.0


def _flag_btc_up_eth_weak(btc_return: float | None, eth_btc_return: float | None) -> float | None:
    if btc_return is None or eth_btc_return is None:
        return None
    return 1.0 if btc_return > 0 and eth_btc_return < 0 else 0.0


def _is_utc_4h_open(value: datetime) -> bool:
    return value.minute == 0 and value.second == 0 and value.microsecond == 0 and value.hour in {0, 4, 8, 12, 16, 20}


def _daily_from_4h(bars: list[Candle]) -> Candle:
    first = bars[0]
    last = bars[-1]
    return Candle(
        exchange=first.exchange,
        market_type="spot",
        symbol=first.symbol,
        interval="1d",
        open_time=first.open_time,
        close_time=last.close_time,
        open=first.open,
        high=max(bar.high for bar in bars),
        low=min(bar.low for bar in bars),
        close=last.close,
        volume=sum((bar.volume for bar in bars), Decimal("0")),
        quote_volume=sum((bar.quote_volume for bar in bars), Decimal("0")),
        trade_count=sum(bar.trade_count for bar in bars),
        taker_buy_base_volume=sum((bar.taker_buy_base_volume for bar in bars), Decimal("0")),
        taker_buy_quote_volume=sum((bar.taker_buy_quote_volume for bar in bars), Decimal("0")),
        is_closed=True,
        source="v2_aggregate:closed_4h",
        ingested_at=last.ingested_at,
    )


def _canon(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return format(value, ".12g")
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value)
