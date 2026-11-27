"""Load the P1 store, fit P2, and build the report payload.

Raw parquet stays on disk and is not part of the report body. The payload
records hashes, windows, and the pre-registered comparison.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from pquant.config.loader import load_settings
from pquant.domain.errors import ConfigError, DataQualityError
from pquant.storage.parquet_store import parquet_path, read_candles
from pquant.utils.hashing import dataset_hash
from pquant.v2.config import load_v2_settings
from pquant.v2.features import feature_frame_hash
from pquant.v2.labels import DecisionFrame, assemble_decision_frame
from pquant.v2.p2_config import P2Settings, load_p2_settings
from pquant.v2.p2_eval import judge_evidence, prediction_metrics, strategy_comparison
from pquant.v2.p2_report import render_report
from pquant.v2.quality import funding_dataset_hash, funding_quality, spot_quality_report
from pquant.v2.store import feature_parquet_path, funding_parquet_path, read_feature_frame, read_funding
from pquant.v2.walkforward import run_walk_forward

NOT_IMPLEMENTED = [
    "P3 mean-variance sizing, volatility targeting, drawdown state machine, CVaR, and circuit breakers",
    "P4 PBO, DSR, nested walk-forward acceptance gates, and bootstrap",
    "P5 agent extensions",
    "P6 paper trading and live trading",
    "LSTM and Transformer models",
    "EWMA/GARCH as a volatility model",
    "shorting, leverage, withdrawals, and perpetual orders",
]


def run_study(root: Path | None = None) -> dict[str, Any]:
    p2 = load_p2_settings(root)
    v2 = load_v2_settings(p2.root)
    v1 = load_settings(p2.root)
    costs = load_cost_scenarios(p2.root)
    frames, data_meta = load_decision_frames(v2, p2)
    trained = run_walk_forward(frames, p2)
    round_trip = (costs["base"][0] + costs["base"][1]) * 2
    metrics = prediction_metrics(trained.predictions, p2, round_trip)
    comparison = strategy_comparison(frames, trained.predictions, trained.folds, p2, v1, costs)
    judgment = judge_evidence(comparison, p2)
    summary: dict[str, Any] = {
        "stage": "p2",
        "model_version": p2.model_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": p2.seed,
        "config_sha256": p2.config_sha256,
        "decision_interval": p2.decision_interval,
        "rule_horizon": p2.rule_horizon,
        "horizons_bars": dict(p2.horizons_bars),
        "round_trip_base_cost": format(round_trip, "f"),
        "fee_rate_per_side": format(costs["base"][0], "f"),
        "slippage_rate_per_side": format(costs["base"][1], "f"),
        "stress_scenario": p2.evidence.stress_scenario,
        "stress_fee_rate_per_side": format(costs[p2.evidence.stress_scenario][0], "f"),
        "stress_slippage_rate_per_side": format(costs[p2.evidence.stress_scenario][1], "f"),
        "initial_cash": format(v1.backtest.initial_cash, "f"),
        "v1_seen_window": {
            "start": p2.v1_seen_start.isoformat(),
            "end": p2.v1_seen_end.isoformat(),
            "note": (
                "2025-01-18 through 2026-11-26 was already inspected in the V1 backtest. "
                "It is not a fresh untouched V2 test set. True forward evidence still requires later paper trading."
            ),
        },
        "data": data_meta,
        "folds": trained.folds,
        "hyperparameters": trained.hyperparameters,
        "prediction_metrics": metrics,
        "strategy_comparison": comparison,
        "attribution": trained.attribution,
        "shap_crosscheck": trained.shap_crosscheck,
        "prediction_rows": len(trained.predictions),
        "prediction_sha256": _prediction_hash(trained.predictions),
        "judgment": judgment,
        "not_implemented": NOT_IMPLEMENTED,
        "unused_tail": _unused_tail(frames, trained.folds),
    }
    write_outputs(p2.root, summary, trained.predictions)
    return summary


def load_decision_frames(v2_settings, p2: P2Settings) -> tuple[dict[str, DecisionFrame], dict[str, Any]]:
    generated_at = datetime.now(timezone.utc)
    spot: dict[str, Any] = {}
    frames: dict[str, DecisionFrame] = {}
    feature_frames: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in p2.symbols:
        candles_by_interval = {}
        for interval in ("1h", "4h"):
            path = parquet_path(v2_settings.storage.parquet_dir, "binance", "spot", symbol, interval)
            candles = read_candles(path)
            if not candles:
                raise DataQualityError(f"missing spot parquet for {symbol} {interval}")
            report = spot_quality_report(
                candles,
                symbol=symbol,
                interval=interval,
                generated_at=generated_at,
                server_time=None,
                max_missing_ratio=float(v2_settings.validation.max_missing_ratio),
                check_freshness=False,
            )
            if report.status != "qualified":
                raise DataQualityError(f"{symbol} {interval} is {report.status}: {report.reasons}")
            spot[f"{symbol}_{interval}"] = {
                "rows": report.total_rows,
                "closed_rows": report.closed_rows,
                "open_rows": report.open_rows,
                "missing_count": report.missing_count,
                "missing_ratio": report.missing_ratio,
                "status": report.status,
                "dataset_hash": report.dataset_hash,
                "start": report.start.isoformat() if report.start else None,
                "end": report.end.isoformat() if report.end else None,
                "reasons": report.reasons,
            }
            candles_by_interval[interval] = candles
            feature_path = feature_parquet_path(v2_settings.storage.feature_dir, symbol, interval)
            feature_frames[(symbol, interval)] = read_feature_frame(feature_path)
        daily = read_feature_frame(feature_parquet_path(v2_settings.storage.feature_dir, symbol, "1d"))
        if not daily.empty:
            feature_frames[(symbol, "1d")] = daily
        frames[symbol] = assemble_decision_frame(
            candles_by_interval["4h"],
            feature_frames[(symbol, "4h")],
            feature_frames[(symbol, "1h")],
            p2,
            symbol=symbol,
        )
    funding: dict[str, Any] = {}
    for symbol in p2.symbols:
        events = read_funding(funding_parquet_path(v2_settings.storage.parquet_dir, symbol))
        summary = funding_quality(events, symbol=symbol, window_start=None, window_end=None)
        summary["dataset_hash"] = funding_dataset_hash(events)
        funding[symbol] = {
            "rows": summary["rows"],
            "status": summary["status"],
            "gap_count": summary["gap_count"],
            "dataset_hash": summary["dataset_hash"],
            "first_funding_time": summary["first_funding_time"],
            "last_funding_time": summary["last_funding_time"],
            "reasons": summary["reasons"],
            "tradable": False,
        }
        if summary["rows"] == 0:
            raise DataQualityError(f"{symbol} funding history is empty")
    manifest_path = v2_settings.storage.reports_dir / "v2" / "p1_manifest.json"
    rest_errors = _funding_rest_errors(manifest_path)
    config_hashes = sorted(
        {
            str(frame["config_hash"].iloc[0])
            for frame in feature_frames.values()
            if not frame.empty and "config_hash" in frame.columns
        }
    )
    meta = {
        "spot": spot,
        "funding": funding,
        "funding_rest_errors": rest_errors,
        "feature_hash": feature_frame_hash(feature_frames),
        "feature_config_hashes": config_hashes,
        "closed_decision_rows": {symbol: len(frame.candles) for symbol, frame in frames.items()},
        "dropped_noncanonical_4h": {symbol: frame.dropped_noncanonical for symbol, frame in frames.items()},
        "dropped_noncanonical_1h_features": {
            symbol: frame.dropped_hourly_noncanonical for symbol, frame in frames.items()
        },
        "decision_start": {symbol: frame.candles[0].open_time.isoformat() for symbol, frame in frames.items()},
        "decision_end_close": {symbol: frame.candles[-1].close_time.isoformat() for symbol, frame in frames.items()},
        "spot_dataset_hashes_check": {
            symbol: dataset_hash(frames[symbol].candles) for symbol in frames
        },
    }
    return frames, meta


def load_cost_scenarios(root: Path) -> dict[str, tuple[Decimal, Decimal]]:
    path = root / "configs" / "costs.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "scenarios" not in raw:
        raise ConfigError("configs/costs.yaml has no scenarios")
    costs: dict[str, tuple[Decimal, Decimal]] = {}
    for name, item in raw["scenarios"].items():
        costs[str(name)] = (Decimal(item["fee_rate_per_side"]), Decimal(item["slippage_rate_per_side"]))
    return costs


def write_outputs(root: Path, summary: dict[str, Any], predictions: list[dict[str, object]]) -> None:
    directory = root / "data" / "reports" / "v2"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "p2_summary.json").write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    pd.DataFrame(predictions).to_parquet(directory / "p2_predictions.parquet", index=False)
    (root / "docs" / "V2_P2_REPORT.md").write_text(render_report(summary), encoding="utf-8")


def _unused_tail(frames: dict[str, DecisionFrame], folds: list[dict[str, object]]) -> dict[str, Any]:
    tails = {}
    for symbol, frame in frames.items():
        rows = [row for row in folds if row["symbol"] == symbol]
        end = int(rows[-1]["test_end_index"]) if rows else 0
        tails[symbol] = {
            "bars_after_last_full_fold": len(frame.candles) - end,
            "first_unused_open": frame.candles[end].open_time.isoformat() if end < len(frame.candles) else None,
        }
    return tails


def _funding_rest_errors(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    errors = []
    for item in payload.get("collect", {}).get("funding", []):
        if item.get("rest_error"):
            errors.append({"symbol": item.get("symbol"), "rest_error": item.get("rest_error")})
    return errors


def _prediction_hash(predictions: list[dict[str, object]]) -> str:
    import hashlib

    digest = hashlib.sha256()
    for row in sorted(
        predictions,
        key=lambda item: (
            str(item["symbol"]),
            str(item["open_time"]),
            str(item["model"]),
            str(item["feature_set"]),
            str(item["horizon"]),
        ),
    ):
        line = "|".join(
            [
                str(row["symbol"]),
                str(row["open_time"]),
                str(row["model"]),
                str(row["feature_set"]),
                str(row["horizon"]),
                f"{float(row['expected_return']):.10g}",
                f"{float(row['positive_probability']):.10g}",
                f"{float(row['uncertainty']):.10g}",
                "" if row["realized_return"] is None else f"{float(row['realized_return']):.10g}",
            ]
        )
        digest.update((line + "\n").encode("utf-8"))
    return digest.hexdigest()


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    return str(value)
