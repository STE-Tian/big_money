"""P2 configuration. The economic rule and the grids are pre-registered.

Loading this file refuses LIVE, leverage, shorting, and perpetual trading.
It does not read API secrets.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pquant.config.loader import apply_env_allowlist, project_root
from pquant.domain.enums import TradingMode
from pquant.domain.errors import ConfigError, LiveTradingDisabledError
from pquant.domain.models import ensure_utc
from pquant.v2 import MODEL_VERSION
from pquant.v2.config import V2SafetySection

HORIZON_BARS_4H = {"4h": 1, "12h": 3, "24h": 6}
EVIDENCE_RULE = "lightgbm_with_hmm_positive_net_both_symbols"


class P2AppSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trading_mode: TradingMode
    live_trading_enabled: bool


class WalkForwardSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_min_bars: int = Field(ge=10)
    validation_bars: int = Field(ge=5)
    test_bars: int = Field(ge=5)
    step_bars: int = Field(ge=1)
    embargo_bars: int = Field(ge=0)

    @model_validator(mode="after")
    def _contiguous(self) -> WalkForwardSection:
        if self.step_bars != self.test_bars:
            raise ValueError("step_bars must equal test_bars so out-of-sample folds have no gap and no overlap")
        return self


class HMMSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: Literal["bic", "aic"]
    state_grid: list[int]
    max_iter: int = Field(ge=5, le=500)
    tol: float = Field(gt=0, lt=1)
    n_restarts: int = Field(ge=1, le=8)
    observation_columns: list[str]
    variance_floor: float = Field(gt=0, lt=1)

    @field_validator("state_grid")
    @classmethod
    def _states(cls, value: list[int]) -> list[int]:
        if len(value) < 2:
            raise ValueError("HMM state_grid must contain at least two candidates so the count is chosen, not fixed")
        if any(item < 2 or item > 6 for item in value):
            raise ValueError("HMM state counts must lie in 2..6")
        if len(set(value)) != len(value):
            raise ValueError("HMM state_grid contains duplicates")
        return value

    @field_validator("observation_columns")
    @classmethod
    def _obs(cls, value: list[str]) -> list[str]:
        if value != ["log_return", "realized_vol"]:
            raise ValueError("HMM observations are log_return and realized_vol, in that order")
        return value


class LinearSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    C: list[float]
    alpha: list[float]
    max_iter: int = Field(ge=50, le=5000)

    @field_validator("C", "alpha")
    @classmethod
    def _positive(cls, value: list[float]) -> list[float]:
        if not value or any(item <= 0 for item in value):
            raise ValueError("regularization grids must be non-empty and positive")
        return value


class LightGBMSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learning_rate: float = Field(gt=0, lt=1)
    n_estimators: int = Field(ge=10, le=2000)
    num_leaves: list[int]
    min_child_samples: list[int]
    feature_fraction: float = Field(gt=0, le=1)
    lambda_l2: list[float]
    quantile_low: float = Field(gt=0, lt=0.5)
    quantile_high: float = Field(gt=0.5, lt=1)
    num_threads: int = Field(ge=1, le=32)

    @field_validator("num_leaves", "min_child_samples")
    @classmethod
    def _positive_int(cls, value: list[int]) -> list[int]:
        if not value or any(item < 2 for item in value):
            raise ValueError("LightGBM integer grids must be non-empty and >= 2")
        return value

    @field_validator("lambda_l2")
    @classmethod
    def _l2(cls, value: list[float]) -> list[float]:
        if not value or any(item < 0 for item in value):
            raise ValueError("lambda_l2 grid must be non-empty and non-negative")
        return value


class EvidenceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: Literal["lightgbm_with_hmm_positive_net_both_symbols"]
    primary_model: Literal["lightgbm"]
    primary_feature_set: Literal["with_hmm"]
    stress_scenario: str


class P2Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: P2AppSection
    model_version: Literal["v2.p2"]
    seed: int
    decision_interval: Literal["4h"]
    rule_horizon: str
    horizons_bars: dict[str, int]
    feature_columns: list[str]
    hourly_asof_columns: list[str]
    walk_forward: WalkForwardSection
    hmm: HMMSection
    linear: LinearSection
    lightgbm: LightGBMSection
    evidence: EvidenceSection
    v1_seen_start: datetime
    v1_seen_end: datetime
    symbols: list[str]
    shap_crosscheck_rows: int = Field(ge=8, le=2000)
    safety: V2SafetySection
    root: Path
    config_path: Path
    config_sha256: str

    @field_validator("v1_seen_start", "v1_seen_end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, value: list[str]) -> list[str]:
        upper = [item.upper() for item in value]
        if upper != ["BTCUSDT", "ETHUSDT"]:
            raise ValueError("P2 research symbols are BTCUSDT and ETHUSDT, in that order")
        return upper

    @model_validator(mode="after")
    def _horizons(self) -> P2Settings:
        if dict(self.horizons_bars) != HORIZON_BARS_4H:
            raise ValueError(f"4h decision clock requires horizons {HORIZON_BARS_4H}")
        if self.rule_horizon not in self.horizons_bars:
            raise ValueError("rule_horizon is not one of the predicted horizons")
        if self.v1_seen_end <= self.v1_seen_start:
            raise ValueError("v1_seen_end must be later than v1_seen_start")
        if len(set(self.feature_columns)) != len(self.feature_columns):
            raise ValueError("duplicate feature column")
        if len(set(self.hourly_asof_columns)) != len(self.hourly_asof_columns):
            raise ValueError("duplicate hourly column")
        return self

    def max_horizon_bars(self) -> int:
        return max(self.horizons_bars.values())

    def feature_names(self, feature_set: str) -> list[str]:
        names = [f"h4_{column}" for column in self.feature_columns]
        names.extend(f"h1_{column}" for column in self.hourly_asof_columns)
        if feature_set == "with_hmm":
            names.extend(hmm_feature_names(max(self.hmm.state_grid)))
        elif feature_set != "no_hmm":
            raise ConfigError(f"unknown feature set {feature_set}")
        return names


def hmm_feature_names(max_states: int) -> list[str]:
    """Unlabeled state probabilities. Names are indices, not market regimes."""
    return [f"hmm_state_prob_{index}" for index in range(max_states)]


def load_p2_settings(root: Path | None = None, config_path: Path | None = None) -> P2Settings:
    root = project_root() if root is None else Path(root).resolve()
    apply_env_allowlist(root / ".env")
    path = config_path or (root / "configs" / "v2_p2.yaml")
    raw_bytes = path.read_bytes()
    raw = _read_yaml(path)
    app = dict(raw["app"])
    app["trading_mode"] = os.environ.get("TRADING_MODE", app["trading_mode"])
    settings = P2Settings(
        app=P2AppSection.model_validate(app),
        model_version=raw["model_version"],
        seed=int(raw["seed"]),
        decision_interval=raw["decision_interval"],
        rule_horizon=raw["rule_horizon"],
        horizons_bars={str(key): int(value) for key, value in raw["horizons_bars"].items()},
        feature_columns=list(raw["feature_columns"]),
        hourly_asof_columns=list(raw["hourly_asof_columns"]),
        walk_forward=WalkForwardSection.model_validate(raw["walk_forward"]),
        hmm=HMMSection.model_validate(raw["hmm"]),
        linear=LinearSection.model_validate(raw["linear"]),
        lightgbm=LightGBMSection.model_validate(raw["lightgbm"]),
        evidence=EvidenceSection.model_validate(raw["evidence"]),
        v1_seen_start=raw["v1_seen_start"],
        v1_seen_end=raw["v1_seen_end"],
        symbols=list(raw["symbols"]),
        shap_crosscheck_rows=int(raw["shap_crosscheck_rows"]),
        safety=V2SafetySection.model_validate(raw["safety"]),
        root=root,
        config_path=path,
        config_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
    if settings.model_version != MODEL_VERSION:
        raise ConfigError(f"config model_version {settings.model_version} does not match code {MODEL_VERSION}")
    enforce_p2_safety(settings)
    return settings


def enforce_p2_safety(settings: P2Settings) -> None:
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
    if settings.evidence.rule != EVIDENCE_RULE:
        problems.append("evidence rule was replaced")
    if problems:
        raise LiveTradingDisabledError(
            "V2 P2 refuses to start: "
            + ", ".join(problems)
            + ". Live trading, leverage, shorting, withdrawals, and perpetual trading are disabled."
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must be a mapping")
    return data
