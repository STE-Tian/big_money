"""Typed configuration. LIVE and live-order flags are rejected, not stored as enabled."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pquant.domain.enums import TradingMode
from pquant.domain.errors import ConfigError, LiveTradingDisabledError
from pquant.domain.models import ensure_utc
from pquant.utils.time import SUPPORTED_INTERVALS


class AppSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    environment: str
    trading_mode: TradingMode
    live_trading_enabled: bool
    log_level: str = "INFO"


class MarketDataSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange: str
    market_type: str
    base_url: str
    fallback_base_url: str
    symbols: list[str]
    intervals: list[str]
    history_start: datetime | None
    exclude_open_candle: bool = True
    klines_limit: int = Field(default=1000, ge=1, le=1000)
    klines_request_weight: int = Field(default=2, ge=1)
    request_timeout_seconds: float = 30
    max_retries: int = Field(default=5, ge=1, le=10)
    request_pause_seconds: float = 0.25
    request_weight_limit_per_minute: int = 6000

    @field_validator("history_start")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value)

    @field_validator("intervals")
    @classmethod
    def _intervals(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in SUPPORTED_INTERVALS]
        if unknown:
            raise ValueError(f"unsupported intervals {unknown}; allowed {sorted(SUPPORTED_INTERVALS)}")
        return value

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one symbol is required")
        return [item.upper() for item in value]


class BacktestSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_cash: Decimal = Field(gt=Decimal("0"))
    quote_asset: str
    annualization_days: int = 365
    signal_execution: str
    default_fee_rate_per_side: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    default_slippage_rate_per_side: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    risk_free_rate: Decimal = Decimal("0")
    force_close_at_end: bool = False

    @field_validator("signal_execution")
    @classmethod
    def _execution(cls, value: str) -> str:
        if value != "next_bar_open":
            raise ValueError("v1 only allows signal_execution=next_bar_open")
        return value

    @field_validator("annualization_days")
    @classmethod
    def _days(cls, value: int) -> int:
        if value != 365:
            raise ValueError("crypto annualization_days must stay 365 in v1")
        return value


class ValidationSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_ratio: Decimal
    validation_ratio: Decimal
    test_ratio: Decimal
    walk_forward_train_days: int = 365
    walk_forward_preferred_train_days: int = 730
    walk_forward_test_days: int = 90
    walk_forward_step_days: int = 90
    minimum_test_days: int = 180
    max_missing_ratio: Decimal = Decimal("0.01")

    @model_validator(mode="after")
    def _ratios(self) -> ValidationSection:
        total = self.train_ratio + self.validation_ratio + self.test_ratio
        if abs(total - Decimal("1")) > Decimal("0.0000001"):
            raise ValueError("train/validation/test ratios must sum to 1")
        if self.train_ratio <= 0 or self.validation_ratio <= 0 or self.test_ratio <= 0:
            raise ValueError("split ratios must be positive")
        return self


class SafetySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_leverage: bool
    allow_short: bool
    allow_withdrawals: bool
    allow_live_orders: bool
    require_paper_mode_for_orders: bool = True


class StorageSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duckdb_path: Path
    sqlite_path: Path
    parquet_dir: Path
    raw_dir: Path
    reports_dir: Path


class CostScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fee_rate_per_side: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    slippage_rate_per_side: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))


class CostsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios: dict[str, CostScenario]

    @model_validator(mode="after")
    def _required(self) -> CostsFile:
        for name in ("base", "stress_1", "stress_2"):
            if name not in self.scenarios:
                raise ValueError(f"costs.yaml missing scenario {name}")
        return self


class StrategyParam(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str
    description: str = ""


class StrategiesFile(BaseModel):
    model_config = ConfigDict(extra="allow")

    buy_and_hold: StrategyParam
    sma_cross: StrategyParam
    momentum: StrategyParam
    cash: StrategyParam
    robustness: dict[str, list[int]]


class RiskFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_position_weight: Decimal = Decimal("1")
    min_position_weight: Decimal = Decimal("0")
    max_drawdown_event: Decimal = Decimal("0.20")
    allow_leverage: bool = False
    allow_short: bool = False
    allow_withdrawals: bool = False
    allow_live_orders: bool = False
    fallback_qty_step: Decimal = Decimal("0.00000001")
    fallback_min_notional: Decimal = Decimal("5")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: AppSection
    market_data: MarketDataSection
    backtest: BacktestSection
    validation: ValidationSection
    safety: SafetySection
    storage: StorageSection
    costs: CostsFile
    strategies: StrategiesFile
    risk: RiskFile
    universe_symbols: list[str]
    root: Path
    ignored_env_names: list[str] = []

    def scenario(self, name: str) -> CostScenario:
        try:
            return self.costs.scenarios[name]
        except KeyError as exc:
            raise ConfigError(f"unknown cost scenario {name}") from exc


def enforce_safety(settings: Settings) -> None:
    """Refuse LIVE and any flag that would enable real orders, leverage, shorts, or withdrawals."""
    problems: list[str] = []
    if settings.app.trading_mode is TradingMode.LIVE:
        problems.append("trading_mode=live")
    if settings.app.live_trading_enabled:
        problems.append("live_trading_enabled=true")
    for label, section in (
        ("safety", settings.safety),
        ("risk", settings.risk),
    ):
        if section.allow_live_orders:
            problems.append(f"{label}.allow_live_orders=true")
        if section.allow_leverage:
            problems.append(f"{label}.allow_leverage=true")
        if section.allow_short:
            problems.append(f"{label}.allow_short=true")
        if section.allow_withdrawals:
            problems.append(f"{label}.allow_withdrawals=true")
    if settings.backtest.force_close_at_end:
        # Supported as a config field but v1 reports mark-to-market without a same-sample forced
        # close, because there is no bar after the last close to trade on.
        problems.append("force_close_at_end=true is not enabled in v1 (no post-sample bar to fill)")
    if problems:
        raise LiveTradingDisabledError(
            "v1 refuses to start: " + ", ".join(problems) + ". Live trading, leverage, shorting, and withdrawals are disabled in code."
        )
    if settings.market_data.exchange.lower() != "binance" or settings.market_data.market_type.lower() != "spot":
        raise ConfigError("v1 market data is Binance Spot only")
    unknown = [s for s in settings.market_data.symbols if s not in settings.universe_symbols]
    if unknown:
        raise ConfigError(f"symbols outside the configured universe: {unknown}")
