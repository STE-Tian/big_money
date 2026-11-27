"""V2 data-layer configuration. LIVE stays off. Funding is not tradable."""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pquant.config.loader import apply_env_allowlist, project_root
from pquant.domain.enums import TradingMode
from pquant.domain.errors import ConfigError, LiveTradingDisabledError
from pquant.domain.models import ensure_utc
from pquant.utils.time import SUPPORTED_INTERVALS
from pquant.v2.safety import FUNDING_HOST, VISION_HOST, assert_https_host

RESEARCH_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT"})
COLLECT_INTERVALS = frozenset({"1h", "4h"})
RELATIVE_STRENGTH_INTERVALS = frozenset({"4h", "1d"})


class V2AppSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trading_mode: TradingMode
    live_trading_enabled: bool


class FeatureWindows(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taker_imbalance: int = Field(ge=2, le=10000)
    volume_baseline: int = Field(ge=2, le=10000)
    price_volume_corr: int = Field(ge=2, le=10000)
    relative_strength: int = Field(ge=2, le=10000)
    realized_vol: int = Field(ge=2, le=10000)


class V2MarketDataSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: str
    market_type: str
    base_url: str
    fallback_base_url: str
    archive_base_url: str
    symbols: list[str]
    intervals: list[str]
    relative_strength_intervals: list[str]
    history_start: datetime
    history_end: datetime | None = None
    exclude_open_candle: bool
    klines_limit: int = Field(default=1000, ge=1, le=1000)
    request_timeout_seconds: float = 30
    max_retries: int = Field(default=5, ge=1, le=10)
    request_pause_seconds: float = 0.25
    rest_crosscheck_max_bars: int = Field(default=2000, ge=1)

    @field_validator("history_start", "history_end")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        upper = [item.upper() for item in value]
        unknown = [item for item in upper if item not in RESEARCH_SYMBOLS]
        if unknown or not upper:
            raise ValueError(f"V2 spot symbols must be a non-empty subset of {sorted(RESEARCH_SYMBOLS)}")
        return upper

    @field_validator("intervals")
    @classmethod
    def _intervals(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in COLLECT_INTERVALS]
        if unknown or not value:
            raise ValueError(f"V2 collection intervals must be a non-empty subset of {sorted(COLLECT_INTERVALS)}")
        return value

    @field_validator("relative_strength_intervals")
    @classmethod
    def _rs(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in RELATIVE_STRENGTH_INTERVALS]
        if unknown or not value:
            raise ValueError(
                f"relative strength intervals must be a non-empty subset of {sorted(RELATIVE_STRENGTH_INTERVALS)}"
            )
        return value


class V2FundingSection(BaseModel):
    """USD-M funding history is a feature. tradable must stay false."""

    model_config = ConfigDict(extra="forbid")

    tradable: bool
    market_type: str
    base_url: str
    path: str
    symbols: list[str]
    limit: int = Field(default=200, ge=1, le=1000)
    request_pause_seconds: float = 0.35
    request_timeout_seconds: float = 30
    max_retries: int = Field(default=4, ge=1, le=8)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        upper = [item.upper() for item in value]
        unknown = [item for item in upper if item not in RESEARCH_SYMBOLS]
        if unknown or not upper:
            raise ValueError(f"funding symbols must be a non-empty subset of {sorted(RESEARCH_SYMBOLS)}")
        return upper


class V2FeatureSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    funding_high_threshold: Decimal
    annualize_realized_vol: bool = True
    windows: dict[str, FeatureWindows]

    @field_validator("windows")
    @classmethod
    def _windows(cls, value: dict[str, FeatureWindows]) -> dict[str, FeatureWindows]:
        missing = [item for item in ("1h", "4h", "1d") if item not in value]
        if missing:
            raise ValueError(f"feature windows missing intervals {missing}")
        unknown = [item for item in value if item not in SUPPORTED_INTERVALS]
        if unknown:
            raise ValueError(f"unsupported feature windows {unknown}")
        return value


class V2SafetySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_leverage: bool
    allow_short: bool
    allow_withdrawals: bool
    allow_live_orders: bool
    trade_perpetual: bool


class V2StorageSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parquet_dir: Path
    raw_dir: Path
    reports_dir: Path
    feature_dir: Path


class V2ValidationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_missing_ratio: Decimal = Decimal("0.01")


class V2Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: V2AppSection
    market_data: V2MarketDataSection
    funding: V2FundingSection
    features: V2FeatureSection
    safety: V2SafetySection
    validation: V2ValidationSection
    storage: V2StorageSection
    root: Path

    def windows_for(self, interval: str) -> FeatureWindows:
        try:
            return self.features.windows[interval]
        except KeyError as exc:
            raise ConfigError(f"no feature windows configured for {interval}") from exc


def load_v2_settings(root: Path | None = None, config_path: Path | None = None) -> V2Settings:
    root = project_root() if root is None else Path(root).resolve()
    apply_env_allowlist(root / ".env")
    path = config_path or (root / "configs" / "v2_data.yaml")
    raw = _read_yaml(path)
    storage = {key: _resolve(root, value) for key, value in raw["storage"].items()}
    mode = os.environ.get("TRADING_MODE", raw["app"]["trading_mode"])
    app = dict(raw["app"])
    app["trading_mode"] = mode
    settings = V2Settings(
        app=V2AppSection.model_validate(app),
        market_data=V2MarketDataSection.model_validate(raw["market_data"]),
        funding=V2FundingSection.model_validate(raw["funding"]),
        features=V2FeatureSection.model_validate(raw["features"]),
        safety=V2SafetySection.model_validate(raw["safety"]),
        validation=V2ValidationSection.model_validate(raw["validation"]),
        storage=V2StorageSection.model_validate(storage),
        root=root,
    )
    enforce_v2_safety(settings)
    return settings


def enforce_v2_safety(settings: V2Settings) -> None:
    problems: list[str] = []
    if settings.app.trading_mode is TradingMode.LIVE:
        problems.append("trading_mode=live")
    if settings.app.live_trading_enabled:
        problems.append("live_trading_enabled=true")
    if settings.safety.allow_live_orders:
        problems.append("safety.allow_live_orders=true")
    if settings.safety.allow_leverage:
        problems.append("safety.allow_leverage=true")
    if settings.safety.allow_short:
        problems.append("safety.allow_short=true")
    if settings.safety.allow_withdrawals:
        problems.append("safety.allow_withdrawals=true")
    if settings.safety.trade_perpetual:
        problems.append("safety.trade_perpetual=true")
    if settings.funding.tradable:
        problems.append("funding.tradable=true")
    if problems:
        raise LiveTradingDisabledError(
            "V2 refuses to start: "
            + ", ".join(problems)
            + ". Live trading, leverage, shorting, withdrawals, and perpetual trading are disabled."
        )
    if settings.market_data.market_type.lower() != "spot":
        raise ConfigError("V2 traded market data is Binance spot. Funding is stored separately and is not tradable.")
    if settings.market_data.exchange.lower() != "binance":
        raise ConfigError("V2 market data is Binance only")
    if not settings.market_data.exclude_open_candle:
        raise ConfigError("exclude_open_candle must stay true. Unclosed bars cannot enter research samples.")
    if settings.funding.market_type != "usd_m_perpetual":
        raise ConfigError("funding.market_type must be usd_m_perpetual and is a feature label, not an order route")
    if settings.funding.path != "/fapi/v1/fundingRate":
        raise ConfigError("funding.path must stay /fapi/v1/fundingRate")
    if set(settings.funding.symbols) != set(settings.market_data.symbols):
        raise ConfigError("funding symbols must match the spot research symbols")
    assert_https_host(settings.market_data.archive_base_url, VISION_HOST)
    assert_https_host(settings.funding.base_url, FUNDING_HOST)
    if settings.market_data.history_end is not None and settings.market_data.history_end <= settings.market_data.history_start:
        raise ConfigError("history_end must be later than history_start")
    for interval in settings.market_data.intervals:
        _assert_aligned(settings.market_data.history_start, interval)
        if settings.market_data.history_end is not None:
            _assert_aligned(settings.market_data.history_end, interval)


def _assert_aligned(value: datetime, interval: str) -> None:
    from pquant.utils.time import interval_ms, utc_to_ms

    step = interval_ms(interval)
    if utc_to_ms(value) % step != 0:
        raise ConfigError(f"{value.isoformat()} is not aligned to interval {interval}")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must be a mapping")
    return data
