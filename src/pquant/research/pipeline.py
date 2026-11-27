"""Run the v1 research stack on stored candles. This module does not download data and does not place orders."""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pquant.config.settings import Settings
from pquant.domain.errors import DataQualityError, SafetyError
from pquant.domain.models import Candle
from pquant.ingestion.quality import build_quality_report, closed_only
from pquant.research.backtest.artifacts import write_csv, write_overlay_html, write_run_artifacts
from pquant.research.backtest.engine import BacktestResult, run_backtest
from pquant.research.backtest.metrics import MetricValue, compute_metrics
from pquant.research.strategies.base import Strategy, StrategyOutput
from pquant.research.strategies.registry import build_strategies, robustness_grid
from pquant.research.validation.judgment import JudgmentInput, judge
from pquant.research.validation.periods import (
    calendar_returns,
    drawdown_episode,
    negative_buckets,
    regime_labels,
    regime_mean_bar_return,
)
from pquant.research.validation.robustness import momentum_robustness, sma_robustness
from pquant.research.validation.splits import carried_target, time_split_bounds
from pquant.research.validation.walkforward import (
    build_windows,
    run_fixed_walk_forward,
    run_selected_walk_forward,
    select_train_days,
)
from pquant.storage.research_store import ResearchStore
from pquant.utils.hashing import dataset_hash


def analyze_symbol(
    candles: list[Candle],
    settings: Settings,
    *,
    reports_dir: Path,
    generated_at: datetime | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    if not candles:
        raise DataQualityError("no candles to analyze")
    symbol = candles[0].symbol
    interval = candles[0].interval
    if any(c.source.startswith("synthetic") for c in candles):
        raise SafetyError("refusing to treat synthetic candles as a real historical backtest")
    closed = closed_only(candles)
    if len(closed) != len(candles):
        # Caller should already have filtered. Keep the guard explicit.
        pass
    if any(not c.is_closed for c in closed):
        raise DataQualityError("unclosed bar passed the closed filter")
    quality = build_quality_report(
        candles,
        symbol=symbol,
        interval=interval,
        generated_at=generated_at,
        server_time=generated_at,
        max_missing_ratio=float(settings.validation.max_missing_ratio),
        check_freshness=True,
    )
    digest = dataset_hash(closed)
    commit = code_commit(settings.root)
    base = settings.scenario("base")
    strategies = [item for item in build_strategies(settings)]
    bounds = time_split_bounds(
        len(closed),
        settings.validation.train_ratio,
        settings.validation.validation_ratio,
        settings.validation.test_ratio,
    )
    outputs = {strategy.strategy_id: strategy.run(closed) for strategy in strategies}
    hard_fail = quality.status == "empty" or quality.ohlc_invalid_count or quality.negative_volume_count or quality.future_timestamp_count or quality.duplicate_rows
    payload: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "generated_at": generated_at.isoformat(),
        "dataset_hash": digest,
        "code_commit": commit,
        "row_count_closed": len(closed),
        "row_count_including_open": len(candles),
        "start": closed[0].open_time.isoformat() if closed else None,
        "end": closed[-1].close_time.isoformat() if closed else None,
        "quality": quality.to_json_dict(),
        "split_bounds": {key: list(value) for key, value in bounds.items()},
        "hard_fail": bool(hard_fail),
        "strategies": {},
    }
    if hard_fail or len(closed) < 2:
        payload["error"] = "hard data-quality failure or fewer than 2 closed bars; performance numbers were not computed"
        return payload

    sma_pairs, momentum_windows = robustness_grid(settings)
    robustness = {
        "sma_cross": sma_robustness(
            closed,
            sma_pairs,
            bounds,
            initial_cash=settings.backtest.initial_cash,
            fee_rate=base.fee_rate_per_side,
            slippage_rate=base.slippage_rate_per_side,
            risk_free_rate=settings.backtest.risk_free_rate,
        ),
        "momentum": momentum_robustness(
            closed,
            momentum_windows,
            bounds,
            initial_cash=settings.backtest.initial_cash,
            fee_rate=base.fee_rate_per_side,
            slippage_rate=base.slippage_rate_per_side,
            risk_free_rate=settings.backtest.risk_free_rate,
        ),
    }
    payload["robustness"] = robustness
    write_csv(reports_dir / f"{symbol}_{interval}_robustness_sma.csv", robustness["sma_cross"])
    write_csv(reports_dir / f"{symbol}_{interval}_robustness_momentum.csv", robustness["momentum"])

    span_days = (closed[-1].close_time - closed[0].open_time).total_seconds() / 86400.0
    train_days = select_train_days(
        span_days,
        settings.validation.walk_forward_train_days,
        settings.validation.walk_forward_preferred_train_days,
        settings.validation.walk_forward_test_days,
    )
    windows = []
    if train_days is not None:
        windows = build_windows(
            closed,
            train_days,
            settings.validation.walk_forward_test_days,
            settings.validation.walk_forward_step_days,
        )
    payload["walk_forward_train_days"] = train_days
    payload["walk_forward_window_count"] = len(windows)
    payload["walk_forward_skip_reason"] = None if windows else (
        "history shorter than the pre-registered train window plus a 90-day test window"
        if train_days is None
        else "no complete 90-day test window survived the coverage check"
    )

    store = ResearchStore(settings.storage.sqlite_path) if persist else None
    experiment_id = str(uuid.uuid4())
    if store is not None:
        store.init_schema()
        _register_strategies(store, settings, commit, generated_at)
        store.create_experiment(
            experiment_id=experiment_id,
            title=f"{symbol} {interval} v1 baseline",
            hypothesis="Pre-registered benchmarks are evaluated with next-open fills and explicit costs. No parameter is chosen from the test segment.",
            objective="Compare buy-and-hold, SMA 20/50, and 60-day momentum with a cash benchmark.",
            status="completed",
            created_at=generated_at.isoformat(),
        )

    full_results: dict[str, BacktestResult] = {}
    for strategy in strategies:
        output = outputs[strategy.strategy_id]
        strategy_payload = _evaluate_strategy(
            strategy,
            output,
            closed,
            settings,
            bounds,
            windows,
            robustness,
            reports_dir,
            commit,
            experiment_id,
            store,
            generated_at,
            quality.status,
        )
        payload["strategies"][strategy.strategy_id] = strategy_payload
        if "full_result" in strategy_payload:
            full_results[strategy.strategy_id] = strategy_payload.pop("full_result")

    if full_results:
        write_overlay_html(
            reports_dir / f"{symbol}_{interval}_equity_overlay.html",
            full_results,
            f"{symbol} {interval} equity, base costs, UTC",
        )
    return payload


def _evaluate_strategy(
    strategy: Strategy,
    output: StrategyOutput,
    candles: list[Candle],
    settings: Settings,
    bounds: dict[str, tuple[int, int]],
    windows,
    robustness: dict[str, list[dict[str, Any]]],
    reports_dir: Path,
    commit: str,
    experiment_id: str,
    store: ResearchStore | None,
    generated_at: datetime,
    quality_status: str,
) -> dict[str, Any]:
    base = settings.scenario("base")
    segments: dict[str, Any] = {}
    full = _execute(
        candles,
        output,
        0,
        len(candles),
        fee_rate=base.fee_rate_per_side,
        slippage_rate=base.slippage_rate_per_side,
        settings=settings,
        strategy=strategy,
        segment="full",
    )
    segments["full"] = _pack(full)
    for name, (start, end) in bounds.items():
        if end - start < 2:
            segments[name] = {"metrics": {}, "skipped": True}
            continue
        result = _execute(
            candles,
            output,
            start,
            end,
            fee_rate=base.fee_rate_per_side,
            slippage_rate=base.slippage_rate_per_side,
            settings=settings,
            strategy=strategy,
            segment=name,
        )
        segments[name] = _pack(result)

    costs: dict[str, Any] = {}
    cost_rows: list[dict[str, Any]] = []
    for scenario_name in ("base", "stress_1", "stress_2"):
        scenario = settings.scenario(scenario_name)
        full_cost = _execute(
            candles,
            output,
            0,
            len(candles),
            fee_rate=scenario.fee_rate_per_side,
            slippage_rate=scenario.slippage_rate_per_side,
            settings=settings,
            strategy=strategy,
            segment=f"full_{scenario_name}",
        )
        test_start, test_end = bounds["test"]
        test_cost = None
        if test_end - test_start >= 2:
            test_cost = _execute(
                candles,
                output,
                test_start,
                test_end,
                fee_rate=scenario.fee_rate_per_side,
                slippage_rate=scenario.slippage_rate_per_side,
                settings=settings,
                strategy=strategy,
                segment=f"test_{scenario_name}",
            )
        costs[scenario_name] = {
            "fee_rate_per_side": format(scenario.fee_rate_per_side, "f"),
            "slippage_rate_per_side": format(scenario.slippage_rate_per_side, "f"),
            "full": _metrics_public(full_cost[1]),
            "test": _metrics_public(test_cost[1]) if test_cost else {},
        }
        cost_rows.append(
            {
                "strategy_id": strategy.strategy_id,
                "scenario": scenario_name,
                "fee_rate_per_side": format(scenario.fee_rate_per_side, "f"),
                "slippage_rate_per_side": format(scenario.slippage_rate_per_side, "f"),
                "full_total_return": full_cost[1]["total_return"].value,
                "full_cagr": full_cost[1]["cagr"].value,
                "full_sharpe": full_cost[1]["sharpe_ratio"].value,
                "full_max_drawdown": full_cost[1]["max_drawdown"].value,
                "full_fill_count": full_cost[1]["fill_count"].value,
                "test_total_return": None if test_cost is None else test_cost[1]["total_return"].value,
                "test_sharpe": None if test_cost is None else test_cost[1]["sharpe_ratio"].value,
                "test_max_drawdown": None if test_cost is None else test_cost[1]["max_drawdown"].value,
                "test_fill_count": None if test_cost is None else test_cost[1]["fill_count"].value,
            }
        )

    fixed_wf: list[dict[str, Any]] = []
    if windows:
        fixed_wf = run_fixed_walk_forward(
            candles,
            strategy,
            windows,
            initial_cash=settings.backtest.initial_cash,
            fee_rate=base.fee_rate_per_side,
            slippage_rate=base.slippage_rate_per_side,
            risk_free_rate=settings.backtest.risk_free_rate,
        )
        write_csv(reports_dir / f"{candles[0].symbol}_{candles[0].interval}_walk_forward_{strategy.strategy_id}.csv", fixed_wf)

    selected_wf: list[dict[str, Any]] = []
    if windows and strategy.strategy_id in {"sma_cross", "momentum"}:
        if strategy.strategy_id == "sma_cross":
            selected_wf = run_selected_walk_forward(
                candles,
                windows,
                family="sma_cross",
                grid=robustness_grid(settings)[0],
                initial_cash=settings.backtest.initial_cash,
                fee_rate=base.fee_rate_per_side,
                slippage_rate=base.slippage_rate_per_side,
                risk_free_rate=settings.backtest.risk_free_rate,
                default_params=_default_sma(settings),
            )
        else:
            selected_wf = run_selected_walk_forward(
                candles,
                windows,
                family="momentum",
                grid=robustness_grid(settings)[1],
                initial_cash=settings.backtest.initial_cash,
                fee_rate=base.fee_rate_per_side,
                slippage_rate=base.slippage_rate_per_side,
                risk_free_rate=settings.backtest.risk_free_rate,
                default_params=_default_momentum(settings),
            )
        write_csv(
            reports_dir / f"{candles[0].symbol}_{candles[0].interval}_walk_forward_selected_{strategy.strategy_id}.csv",
            selected_wf,
        )

    periods = calendar_returns(full[0].equity_curve, candles)
    episode = drawdown_episode(full[0].equity_curve)
    labels = regime_labels(candles)
    regimes = regime_mean_bar_return(full[0].equity_curve, labels)
    write_csv(reports_dir / f"{candles[0].symbol}_{candles[0].interval}_{strategy.strategy_id}_yearly.csv", periods["yearly"])
    write_csv(reports_dir / f"{candles[0].symbol}_{candles[0].interval}_{strategy.strategy_id}_quarterly.csv", periods["quarterly"])
    write_csv(reports_dir / f"{candles[0].symbol}_{candles[0].interval}_cost_sensitivity_{strategy.strategy_id}.csv", cost_rows)

    run_id = str(uuid.uuid4())
    snapshot = _snapshot(settings, candles, strategy, output, full[0], run_id, commit)
    artifact_dir = reports_dir / "runs" / run_id
    paths = write_run_artifacts(artifact_dir, full[0], full[1], snapshot)
    test_pack = segments.get("test", {})
    if test_pack and not test_pack.get("skipped"):
        test_result = _execute(
            candles,
            output,
            bounds["test"][0],
            bounds["test"][1],
            fee_rate=base.fee_rate_per_side,
            slippage_rate=base.slippage_rate_per_side,
            settings=settings,
            strategy=strategy,
            segment="test",
        )
        test_dir = reports_dir / "runs" / f"{run_id}_test"
        write_run_artifacts(test_dir, test_result[0], test_result[1], {**snapshot, "segment": "test"})

    judgment = _judgment(
        strategy_id=strategy.strategy_id,
        quality_status=quality_status,
        settings=settings,
        segments=segments,
        costs=costs,
        robustness=robustness,
        fixed_wf=fixed_wf,
        candles=candles,
        bounds=bounds,
    )
    if store is not None:
        _persist_run(store, run_id, experiment_id, strategy, candles, snapshot, full[1], commit, generated_at, "full")

    test_start, test_end = bounds["test"]
    test_span = None
    if test_end > test_start:
        test_span = (candles[test_end - 1].close_time - candles[test_start].open_time).total_seconds() / 86400.0
    return {
        "run_id": run_id,
        "params": _public_params(strategy),
        "segments": {key: _public_segment(value) for key, value in segments.items()},
        "costs": costs,
        "walk_forward": fixed_wf,
        "walk_forward_selected": selected_wf,
        "periods": periods,
        "negative_years": negative_buckets(periods["yearly"]),
        "negative_quarters": negative_buckets(periods["quarterly"]),
        "drawdown_episode": episode,
        "regime_mean_bar_return": regimes,
        "judgment": {"label": judgment.label, "history_status": judgment.history_status, "reasons": judgment.reasons},
        "artifact_paths": paths,
        "warnings": full[0].warnings,
        "test_span_days": test_span,
        "fee_paid_full": format(full[0].fee_paid, "f"),
        "slippage_cost_full": format(full[0].slippage_cost, "f"),
        "fill_count_full": len(full[0].fills),
        "closed_trade_count_full": len(full[0].closed_trades),
        "full_result": full[0],
    }


def _execute(candles, output: StrategyOutput, start: int, end: int, *, fee_rate, slippage_rate, settings: Settings, strategy: Strategy, segment: str):
    opening = output.opening_target if start == 0 else carried_target(output.close_targets, output.opening_target, start)
    result = run_backtest(
        candles[start:end],
        output.close_targets[start:end],
        opening_target=opening,
        initial_cash=settings.backtest.initial_cash,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        symbol=candles[0].symbol,
        interval=candles[0].interval,
        segment=segment,
    )
    metrics = compute_metrics(
        result,
        start=candles[start].open_time,
        end=candles[end - 1].close_time,
        risk_free_rate=settings.backtest.risk_free_rate,
        interval=candles[0].interval,
    )
    return result, metrics


def _pack(pair) -> dict[str, Any]:
    result, metrics = pair
    return {"metrics": _metrics_public(metrics), "warnings": result.warnings, "skipped": False}


def _public_segment(value: dict[str, Any]) -> dict[str, Any]:
    return value


def _metrics_public(metrics: dict[str, MetricValue]) -> dict[str, Any]:
    return {key: {"value": metric.value, "unit": metric.unit} for key, metric in metrics.items()}


def _judgment(strategy_id, quality_status, settings: Settings, segments, costs, robustness, fixed_wf, candles, bounds):
    test_metrics = segments.get("test", {}).get("metrics", {})
    train_metrics = segments.get("train", {}).get("metrics", {})
    test_start, test_end = bounds["test"]
    span = None
    if test_end > test_start:
        span = (candles[test_end - 1].close_time - candles[test_start].open_time).total_seconds() / 86400.0
    parameterized = strategy_id in {"sma_cross", "momentum"}
    fraction = None
    if parameterized:
        rows = robustness[strategy_id]
        if rows:
            positives = sum(1 for row in rows if isinstance(row.get("validation_total_return"), float) and row["validation_total_return"] > 0)
            fraction = positives / len(rows)
    wf_fraction = None
    if fixed_wf:
        nonnegative = sum(1 for row in fixed_wf if isinstance(row.get("total_return"), float) and row["total_return"] >= 0)
        wf_fraction = nonnegative / len(fixed_wf)
    return judge(
        JudgmentInput(
            strategy_id=strategy_id,
            data_quality_status=quality_status,
            accounting_ok=True,
            test_span_days=span,
            minimum_test_days=settings.validation.minimum_test_days,
            test_total_return=_metric(test_metrics, "total_return"),
            test_sharpe=_metric(test_metrics, "sharpe_ratio"),
            test_max_drawdown=_metric(test_metrics, "max_drawdown"),
            train_total_return=_metric(train_metrics, "total_return"),
            stress_1_test_total_return=_metric(costs["stress_1"]["test"], "total_return"),
            stress_2_test_total_return=_metric(costs["stress_2"]["test"], "total_return"),
            validation_grid_positive_fraction=fraction,
            walk_forward_nonnegative_fraction=wf_fraction,
            parameterized=parameterized,
        )
    )


def _metric(metrics: dict[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    if isinstance(value, dict):
        raw = value.get("value")
        return None if raw is None else float(raw)
    return None


def _snapshot(settings, candles, strategy, output, result: BacktestResult, run_id: str, commit: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.version,
        "params": _public_params(strategy),
        "symbol": candles[0].symbol,
        "interval": candles[0].interval,
        "start": candles[0].open_time.isoformat(),
        "end": candles[-1].close_time.isoformat(),
        "dataset_hash": dataset_hash(candles),
        "code_commit": commit,
        "initial_cash": format(settings.backtest.initial_cash, "f"),
        "fee_rate_per_side": format(result.fee_rate, "f"),
        "slippage_rate_per_side": format(result.slippage_rate, "f"),
        "risk_free_rate": format(settings.backtest.risk_free_rate, "f"),
        "signal_execution": "next_bar_open",
        "force_close_at_end": False,
        "opening_target": None if output.opening_target is None else format(output.opening_target, "f"),
        "annualization": "sqrt(365) for 1d, sqrt(365*6) for 4h, sqrt(365*24) for 1h",
        "data_origin": candles[0].source,
    }


def _public_params(strategy: Strategy) -> dict[str, Any]:
    from pquant.research.strategies.momentum import MomentumStrategy
    from pquant.research.strategies.sma_cross import SmaCrossStrategy

    if isinstance(strategy, SmaCrossStrategy):
        return {"short_window": strategy.short_window, "long_window": strategy.long_window}
    if isinstance(strategy, MomentumStrategy):
        return {"window": strategy.window}
    return {}


def _default_sma(settings: Settings) -> tuple[int, int]:
    from pquant.research.strategies.registry import build_strategies

    for strategy in build_strategies(settings):
        params = _public_params(strategy)
        if strategy.strategy_id == "sma_cross":
            return (int(params["short_window"]), int(params["long_window"]))
    return (20, 50)


def _default_momentum(settings: Settings) -> int:
    for strategy in build_strategies(settings):
        if strategy.strategy_id == "momentum":
            return int(_public_params(strategy)["window"])
    return 60


def _persist_run(store, run_id, experiment_id, strategy, candles, snapshot, metrics, commit, generated_at, segment) -> None:
    store.insert_run(
        {
            "run_id": run_id,
            "experiment_id": experiment_id,
            "strategy_version_id": f"{strategy.strategy_id}:{strategy.version}",
            "strategy_id": strategy.strategy_id,
            "symbol": candles[0].symbol,
            "interval": candles[0].interval,
            "start_time": candles[0].open_time.isoformat(),
            "end_time": candles[-1].close_time.isoformat(),
            "dataset_hash": snapshot["dataset_hash"],
            "config_json": json.dumps(snapshot),
            "code_commit": commit,
            "started_at": generated_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "error_message": None,
            "segment": segment,
        }
    )
    rows = [
        (run_id, metric.name, None if metric.value is None else repr(metric.value), metric.unit, segment)
        for metric in metrics.values()
    ]
    store.insert_metrics(rows)


def _register_strategies(store: ResearchStore, settings: Settings, commit: str, generated_at: datetime) -> None:
    descriptions = {
        "buy_and_hold": settings.strategies.buy_and_hold.description,
        "sma_cross": settings.strategies.sma_cross.description,
        "momentum": settings.strategies.momentum.description,
        "cash": settings.strategies.cash.description,
    }
    for strategy in build_strategies(settings):
        store.upsert_strategy(
            strategy_id=strategy.strategy_id,
            name=strategy.strategy_id,
            description=descriptions.get(strategy.strategy_id, ""),
            asset_scope="binance_spot_long_or_cash",
            timeframe="1d",
            created_at=generated_at.isoformat(),
            status="active",
            version=strategy.version,
            parameters_schema_json=json.dumps(_public_params(strategy)),
            code_commit=commit,
            version_id=f"{strategy.strategy_id}:{strategy.version}",
        )


def code_commit(root: Path) -> str:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            text=True,
        ).strip()
        return commit + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"
