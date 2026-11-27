"""Score forecasts and run the pre-registered rule through the V1 backtest engine.

The engine still fills a close signal at the next open, with the V1 fee and
slippage formulas. This module does not reimplement fills. Baselines are the
V1 buy-and-hold, SMA, momentum, and cash strategies, with the parameters in
configs/strategies.yaml, evaluated on the same bars and the same costs.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from pquant.config.settings import Settings
from pquant.domain.errors import ConfigError
from pquant.domain.models import Candle
from pquant.research.backtest.engine import run_backtest
from pquant.research.backtest.metrics import compute_metrics
from pquant.research.strategies.registry import strategy_by_id
from pquant.v2.decision import position_from_expected, round_trip_cost
from pquant.v2.labels import DecisionFrame
from pquant.v2.p2_config import P2Settings

STRATEGY_METRICS = (
    "total_return",
    "cagr",
    "sharpe_ratio",
    "max_drawdown",
    "exposure",
    "turnover",
    "final_equity",
    "fee_paid",
    "slippage_cost",
    "fill_count",
    "closed_trade_count",
)


def prediction_metrics(
    predictions: list[dict[str, object]],
    settings: P2Settings,
    round_trip: Decimal,
) -> list[dict[str, object]]:
    if not predictions:
        return []
    frame = pd.DataFrame(predictions)
    frame["open_dt"] = pd.to_datetime(frame["open_time"], utc=True)
    slices = {
        "oos": pd.Series(True, index=frame.index),
        "before_v1_seen": frame["open_dt"] < pd.Timestamp(settings.v1_seen_start),
        "overlaps_v1_seen_not_a_fresh_holdout": frame["open_dt"] >= pd.Timestamp(settings.v1_seen_start),
    }
    rows: list[dict[str, object]] = []
    threshold = float(round_trip)
    grouped = frame.groupby(["symbol", "model", "feature_set", "horizon"], sort=True)
    for (symbol, model, feature_set, horizon), _ in grouped:
        for slice_name, mask in slices.items():
            part = frame[
                (frame["symbol"] == symbol)
                & (frame["model"] == model)
                & (frame["feature_set"] == feature_set)
                & (frame["horizon"] == horizon)
                & mask
            ]
            rows.append(_score_block(part, symbol, model, feature_set, horizon, slice_name, threshold, settings.rule_horizon))
    return rows


def strategy_comparison(
    frames: dict[str, DecisionFrame],
    predictions: list[dict[str, object]],
    folds: list[dict[str, object]],
    settings: P2Settings,
    v1: Settings,
    costs: dict[str, tuple[Decimal, Decimal]],
) -> list[dict[str, object]]:
    _assert_protocol(v1, costs, settings)
    scenarios = ["base", settings.evidence.stress_scenario]
    baselines = strategy_by_id(v1)
    rows: list[dict[str, object]] = []
    for symbol, frame in frames.items():
        start, end = _oos_bounds(folds, symbol)
        cut = _first_index_at_or_after(frame.candles, start, end, settings.v1_seen_start)
        windows = {
            "oos": (start, end),
            "before_v1_seen": (start, cut),
            "overlaps_v1_seen_not_a_fresh_holdout": (cut, end),
        }
        paths = _model_paths(predictions, frame, settings, costs["base"][0], costs["base"][1])
        for slice_name, (left, right) in windows.items():
            if right <= left:
                continue
            candles = frame.candles[left:right]
            for scenario in scenarios:
                fee, slip = costs[scenario]
                for strategy in baselines.values():
                    output = strategy.run(candles)
                    result = run_backtest(
                        candles,
                        output.close_targets,
                        opening_target=output.opening_target,
                        initial_cash=v1.backtest.initial_cash,
                        fee_rate=fee,
                        slippage_rate=slip,
                        strategy_id=strategy.strategy_id,
                        strategy_version=strategy.version,
                        symbol=symbol,
                        interval=frame.interval,
                        segment=slice_name,
                    )
                    rows.append(_pack(result, scenario, slice_name, v1.backtest.risk_free_rate))
                for strategy_id, full_targets in paths.items():
                    targets = full_targets[left:right]
                    if any(item not in {Decimal("0"), Decimal("1")} for item in targets):
                        raise ConfigError("P2 target weights must stay in {0, 1}")
                    result = run_backtest(
                        candles,
                        targets,
                        opening_target=Decimal("0"),
                        initial_cash=v1.backtest.initial_cash,
                        fee_rate=fee,
                        slippage_rate=slip,
                        strategy_id=strategy_id,
                        strategy_version=settings.model_version,
                        symbol=symbol,
                        interval=frame.interval,
                        segment=slice_name,
                    )
                    rows.append(_pack(result, scenario, slice_name, v1.backtest.risk_free_rate))
    return rows


def judge_evidence(comparison: list[dict[str, object]], settings: P2Settings) -> dict[str, object]:
    """Pre-registered bar. Any miss means there is not enough evidence, so stay in cash.

    Required on both BTCUSDT and ETHUSDT, for v2_lightgbm_with_hmm:
    - base-cost out-of-sample total return > 0
    - that return is greater than buy-and-hold on the same slice
    - base-cost Sharpe > 0
    - stress-scenario total return > 0
    - base-cost total return on the pre-2025-01-18 portion of the out-of-sample
      window > 0

    Passing this bar is still not a DSR/PBO result and not a fresh hold-out.
    """
    if settings.evidence.rule != "lightgbm_with_hmm_positive_net_both_symbols":
        raise ConfigError("unknown evidence rule")
    primary = f"v2_{settings.evidence.primary_model}_{settings.evidence.primary_feature_set}"
    reasons: list[str] = []
    for symbol in settings.symbols:
        base = _find(comparison, primary, symbol, "base", "oos")
        hold = _find(comparison, "buy_and_hold", symbol, "base", "oos")
        stress = _find(comparison, primary, symbol, settings.evidence.stress_scenario, "oos")
        early = _find(comparison, primary, symbol, "base", "before_v1_seen")
        base_return = None if base is None else base.get("total_return")
        hold_return = None if hold is None else hold.get("total_return")
        sharpe = None if base is None else base.get("sharpe_ratio")
        stress_return = None if stress is None else stress.get("total_return")
        early_return = None if early is None else early.get("total_return")
        if not isinstance(base_return, float) or base_return <= 0:
            reasons.append(f"{symbol}: base-cost out-of-sample total return is not positive")
        if not isinstance(base_return, float) or not isinstance(hold_return, float) or base_return <= hold_return:
            reasons.append(f"{symbol}: base-cost out-of-sample return does not beat buy-and-hold")
        if not isinstance(sharpe, float) or sharpe <= 0:
            reasons.append(f"{symbol}: base-cost out-of-sample Sharpe is not positive")
        if not isinstance(stress_return, float) or stress_return <= 0:
            reasons.append(f"{symbol}: {settings.evidence.stress_scenario} total return is not positive")
        if not isinstance(early_return, float) or early_return <= 0:
            reasons.append(f"{symbol}: out-of-sample return before 2025-01-18 is not positive")
    enough = not reasons
    if enough:
        conclusion = (
            "预先登记的证据规则在本次滚动样本上被满足。这不是 P4 的 DSR 或 PBO 验收，"
            "也不是实盘许可。2025-01-18 至 2026-11-26 已经在 V1 中被查看，"
            "不能称为 V2 的全新、未触碰测试集。真正的前向样本仍要等后续模拟盘。"
        )
    else:
        conclusion = (
            "当前没有足够证据证明存在可交易的优势，因此保持现金。"
            "预先登记的证据规则没有被满足。"
            "2025-01-18 至 2026-11-26 已经在 V1 中被查看，不能称为 V2 的全新、未触碰测试集。"
            "预测准确率不是目标。真正的前向样本仍要等后续模拟盘。"
        )
    return {
        "rule": settings.evidence.rule,
        "primary_strategy": primary,
        "enough_evidence": enough,
        "stay_in_cash": not enough,
        "reasons": reasons,
        "conclusion": conclusion,
    }


def _score_block(
    part: pd.DataFrame,
    symbol: str,
    model: str,
    feature_set: str,
    horizon: str,
    slice_name: str,
    threshold: float,
    rule_horizon: str,
) -> dict[str, object]:
    realized = pd.to_numeric(part["realized_return"], errors="coerce").to_numpy(dtype=np.float64)
    expected = pd.to_numeric(part["expected_return"], errors="coerce").to_numpy(dtype=np.float64)
    probability = pd.to_numeric(part["positive_probability"], errors="coerce").to_numpy(dtype=np.float64)
    uncertainty = pd.to_numeric(part["uncertainty"], errors="coerce").to_numpy(dtype=np.float64)
    paired = np.isfinite(realized) & np.isfinite(expected)
    row: dict[str, object] = {
        "symbol": symbol,
        "model": model,
        "feature_set": feature_set,
        "horizon": horizon,
        "slice": slice_name,
        "rows": int(len(part)),
        "scored_rows": int(paired.sum()),
    }
    if paired.sum() < 2:
        row.update(
            {
                "mae": None,
                "rmse": None,
                "direction_accuracy": None,
                "classifier_accuracy": None,
                "brier": None,
                "spearman": None,
                "mean_expected_return": None,
                "mean_realized_return": None,
                "mean_uncertainty": None,
                "fraction_above_round_trip": None,
            }
        )
        return row
    error = expected[paired] - realized[paired]
    outcome = realized[paired] > 0
    direction = (expected[paired] > 0) == outcome
    prob_ok = np.isfinite(probability[paired])
    brier = None
    classifier_accuracy = None
    if prob_ok.any():
        prob = np.clip(probability[paired][prob_ok], 0.0, 1.0)
        brier = float(np.mean((prob - outcome[prob_ok].astype(np.float64)) ** 2))
        classifier_accuracy = float(np.mean((prob >= 0.5) == outcome[prob_ok]))
    row.update(
        {
            "mae": float(np.mean(np.abs(error))),
            "rmse": float(np.sqrt(np.mean(error**2))),
            "direction_accuracy": float(np.mean(direction)),
            "classifier_accuracy": classifier_accuracy,
            "brier": brier,
            "spearman": _spearman(expected[paired], realized[paired]),
            "mean_expected_return": float(np.mean(expected[paired])),
            "mean_realized_return": float(np.mean(realized[paired])),
            "mean_uncertainty": float(np.nanmean(uncertainty[paired])),
            "fraction_above_round_trip": float(np.mean(expected[paired] > threshold)) if horizon == rule_horizon else None,
        }
    )
    return row


def _model_paths(
    predictions: list[dict[str, object]],
    frame: DecisionFrame,
    settings: P2Settings,
    fee: Decimal,
    slip: Decimal,
) -> dict[str, list[Decimal]]:
    n = len(frame.candles)
    cost = round_trip_cost(fee, slip)
    paths: dict[str, list[Decimal]] = {}
    for model in ("logistic", "lightgbm"):
        for feature_set in ("no_hmm", "with_hmm"):
            expected = np.full(n, np.nan, dtype=np.float64)
            for row in predictions:
                if (
                    row["symbol"] == frame.symbol
                    and row["model"] == model
                    and row["feature_set"] == feature_set
                    and row["horizon"] == settings.rule_horizon
                ):
                    expected[int(row["decision_index"])] = float(row["expected_return"])
            paths[f"v2_{model}_{feature_set}"] = [position_from_expected(value, cost) for value in expected]
    return paths


def _pack(result, scenario: str, slice_name: str, risk_free: Decimal) -> dict[str, object]:
    start = result.equity_curve[0].time
    end = result.equity_curve[-1].time
    metrics = compute_metrics(result, start=start, end=end, risk_free_rate=risk_free, interval=result.interval)
    packed: dict[str, object] = {
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "symbol": result.symbol,
        "interval": result.interval,
        "scenario": scenario,
        "slice": slice_name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "warning_count": len(result.warnings),
    }
    for name in STRATEGY_METRICS:
        packed[name] = metrics[name].value
    return packed


def _oos_bounds(folds: list[dict[str, object]], symbol: str) -> tuple[int, int]:
    rows = [row for row in folds if row["symbol"] == symbol]
    if not rows:
        raise ConfigError(f"no walk-forward folds for {symbol}")
    start = int(rows[0]["test_start_index"])
    end = int(rows[-1]["test_end_index"])
    if any(int(row["test_start_index"]) < start or int(row["test_end_index"]) > end for row in rows):
        raise ConfigError(f"{symbol} folds are not inside the first-to-last test span")
    return start, end


def _first_index_at_or_after(candles: list[Candle], start: int, end: int, cutoff: datetime) -> int:
    for index in range(start, end):
        if candles[index].open_time >= cutoff:
            return index
    return end


def _find(
    rows: list[dict[str, object]],
    strategy_id: str,
    symbol: str,
    scenario: str,
    slice_name: str,
) -> dict[str, object] | None:
    matches = [
        row
        for row in rows
        if row["strategy_id"] == strategy_id
        and row["symbol"] == symbol
        and row["scenario"] == scenario
        and row["slice"] == slice_name
    ]
    if len(matches) > 1:
        raise ConfigError(f"duplicate comparison row for {strategy_id} {symbol} {scenario} {slice_name}")
    return matches[0] if matches else None


def _assert_protocol(v1: Settings, costs: dict[str, tuple[Decimal, Decimal]], settings: P2Settings) -> None:
    if v1.backtest.initial_cash != Decimal("10000"):
        raise ConfigError("P2 evaluation requires the V1 initial cash of 10000")
    if v1.backtest.signal_execution != "next_bar_open":
        raise ConfigError("P2 evaluation requires the V1 next-bar-open fill")
    base = costs.get("base")
    if base != (Decimal("0.001"), Decimal("0.0005")):
        raise ConfigError("V1 base cost must stay fee 0.001 and slippage 0.0005 per side")
    if settings.evidence.stress_scenario not in costs:
        raise ConfigError(f"missing stress scenario {settings.evidence.stress_scenario}")
    set_ids = set(strategy_by_id(v1))
    if set_ids != {"buy_and_hold", "sma_cross", "momentum", "cash"}:
        raise ConfigError(f"V1 baseline registry changed: {sorted(set_ids)}")


def _spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 3:
        return None
    left_rank = pd.Series(left).rank(method="average").to_numpy(dtype=np.float64)
    right_rank = pd.Series(right).rank(method="average").to_numpy(dtype=np.float64)
    if float(np.std(left_rank)) == 0 or float(np.std(right_rank)) == 0:
        return None
    return float(np.corrcoef(left_rank, right_rank)[0, 1])
