"""Parameter grid on train and validation only. The test slice is not passed in."""

from __future__ import annotations

from decimal import Decimal

from pquant.domain.models import Candle
from pquant.research.backtest.engine import run_backtest
from pquant.research.backtest.metrics import compute_metrics
from pquant.research.strategies.momentum import MomentumStrategy
from pquant.research.strategies.sma_cross import SmaCrossStrategy
from pquant.research.validation.splits import carried_target


def sma_robustness(
    candles: list[Candle],
    pairs: list[tuple[int, int]],
    bounds: dict[str, tuple[int, int]],
    *,
    initial_cash: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    risk_free_rate: Decimal,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for short, long in pairs:
        if short >= long:
            continue
        strategy = SmaCrossStrategy(short_window=short, long_window=long)
        output = strategy.run(candles)
        row: dict[str, object] = {"family": "sma_cross", "short_window": short, "long_window": long}
        for segment in ("train", "validation"):
            start, end = bounds[segment]
            metrics = _segment_metrics(
                candles,
                output.close_targets,
                output.opening_target,
                start,
                end,
                strategy_id=strategy.strategy_id,
                strategy_version=strategy.version,
                initial_cash=initial_cash,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                risk_free_rate=risk_free_rate,
                segment=segment,
            )
            row[f"{segment}_total_return"] = metrics["total_return"].value
            row[f"{segment}_sharpe"] = metrics["sharpe_ratio"].value
            row[f"{segment}_max_drawdown"] = metrics["max_drawdown"].value
            row[f"{segment}_fill_count"] = metrics["fill_count"].value
        rows.append(row)
    return rows


def momentum_robustness(
    candles: list[Candle],
    windows: list[int],
    bounds: dict[str, tuple[int, int]],
    *,
    initial_cash: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    risk_free_rate: Decimal,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for window in windows:
        strategy = MomentumStrategy(window=window)
        output = strategy.run(candles)
        row: dict[str, object] = {"family": "momentum", "window": window}
        for segment in ("train", "validation"):
            start, end = bounds[segment]
            metrics = _segment_metrics(
                candles,
                output.close_targets,
                output.opening_target,
                start,
                end,
                strategy_id=strategy.strategy_id,
                strategy_version=strategy.version,
                initial_cash=initial_cash,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
                risk_free_rate=risk_free_rate,
                segment=segment,
            )
            row[f"{segment}_total_return"] = metrics["total_return"].value
            row[f"{segment}_sharpe"] = metrics["sharpe_ratio"].value
            row[f"{segment}_max_drawdown"] = metrics["max_drawdown"].value
            row[f"{segment}_fill_count"] = metrics["fill_count"].value
        rows.append(row)
    return rows


def _segment_metrics(
    candles,
    close_targets,
    opening_target,
    start,
    end,
    *,
    strategy_id,
    strategy_version,
    initial_cash,
    fee_rate,
    slippage_rate,
    risk_free_rate,
    segment,
):
    if end - start < 2:
        from pquant.research.backtest.metrics import MetricValue

        empty = {
            "total_return": MetricValue("total_return", None, "insufficient_segment"),
            "sharpe_ratio": MetricValue("sharpe_ratio", None, "insufficient_segment"),
            "max_drawdown": MetricValue("max_drawdown", None, "insufficient_segment"),
            "fill_count": MetricValue("fill_count", None, "insufficient_segment"),
        }
        return empty
    opening = carried_target(close_targets, opening_target, start)
    result = run_backtest(
        candles[start:end],
        close_targets[start:end],
        opening_target=opening,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        symbol=candles[0].symbol,
        interval=candles[0].interval,
        segment=segment,
    )
    return compute_metrics(
        result,
        start=candles[start].open_time,
        end=candles[end - 1].close_time,
        risk_free_rate=risk_free_rate,
        interval=candles[0].interval,
    )
