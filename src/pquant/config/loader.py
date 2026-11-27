"""Load YAML config and a strict .env allowlist. Secret keys in .env are not applied."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from pquant.config.settings import (
    AppSection,
    BacktestSection,
    CostsFile,
    MarketDataSection,
    RiskFile,
    SafetySection,
    Settings,
    StorageSection,
    StrategiesFile,
    ValidationSection,
    enforce_safety,
)
from pquant.domain.enums import TradingMode
from pquant.domain.errors import ConfigError

ENV_ALLOWLIST = frozenset({"TRADING_MODE", "LOG_LEVEL", "PQUANT_CONFIG_DIR"})
SECRET_ENV_NAMES = frozenset(
    {
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_SECRET",
        "API_KEY",
        "API_SECRET",
        "SECRET_KEY",
    }
)


def project_root(start: Path | None = None) -> Path:
    env = os.environ.get("PQUANT_ROOT")
    if env:
        return Path(env).resolve()
    cwd = (start or Path.cwd()).resolve()
    if (cwd / "configs" / "app.yaml").exists():
        return cwd
    for parent in Path(__file__).resolve().parents:
        if (parent / "configs" / "app.yaml").exists():
            return parent
    return cwd


def load_settings(root: Path | None = None) -> Settings:
    root = project_root() if root is None else Path(root).resolve()
    env_ignored = apply_env_allowlist(root / ".env")
    config_dir = Path(os.environ.get("PQUANT_CONFIG_DIR", root / "configs"))
    if not config_dir.is_absolute():
        config_dir = root / config_dir
    app_raw = _read_yaml(config_dir / "app.yaml")
    costs_raw = _read_yaml(config_dir / "costs.yaml")
    strategies_raw = _read_yaml(config_dir / "strategies.yaml")
    risk_raw = _read_yaml(config_dir / "risk.yaml")
    universe_raw = _read_yaml(config_dir / "universe.yaml")
    mode = os.environ.get("TRADING_MODE", app_raw["app"]["trading_mode"])
    log_level = os.environ.get("LOG_LEVEL", app_raw["app"].get("log_level", "INFO"))
    app_section = dict(app_raw["app"])
    app_section["trading_mode"] = mode
    app_section["log_level"] = log_level
    market = dict(app_raw["market_data"])
    storage = {key: root / value for key, value in app_raw["storage"].items()}
    symbols = [item["symbol"].upper() for item in universe_raw["symbols"]]
    settings = Settings(
        app=AppSection.model_validate(app_section),
        market_data=MarketDataSection.model_validate(market),
        backtest=BacktestSection.model_validate(app_raw["backtest"]),
        validation=ValidationSection.model_validate(app_raw["validation"]),
        safety=SafetySection.model_validate(app_raw["safety"]),
        storage=StorageSection.model_validate(storage),
        costs=CostsFile.model_validate(costs_raw),
        strategies=StrategiesFile.model_validate(strategies_raw),
        risk=RiskFile.model_validate(risk_raw),
        universe_symbols=symbols,
        root=root,
        ignored_env_names=sorted(set(env_ignored)),
    )
    enforce_safety(settings)
    return settings


def apply_env_allowlist(env_file: Path) -> list[str]:
    """Apply only allowlisted keys from .env. Returns names of ignored secret-like keys."""
    ignored: list[str] = []
    if env_file.exists():
        for key, value in _parse_env_file(env_file):
            if key in ENV_ALLOWLIST:
                os.environ[key] = value
            elif key in SECRET_ENV_NAMES or "SECRET" in key.upper() or key.upper().endswith("_KEY"):
                ignored.append(key)
            # Other unknown keys are ignored and not exported.
    # A secret that is already in the process environment is not copied into settings.
    for key in list(os.environ):
        if key in SECRET_ENV_NAMES and key not in ignored:
            ignored.append(key)
    return ignored


def _parse_env_file(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        rows.append((key.strip(), value.strip().strip('"').strip("'")))
    return rows


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"missing config file {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must be a mapping")
    return data


def mode_label(mode: TradingMode) -> str:
    return mode.value.upper()
