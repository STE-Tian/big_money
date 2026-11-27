"""Rolling 90-day test windows. Parameter choice for a window sees only earlier bars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from pquant.domain.models import Candle
from pquant.research.backtest.engine import run_backtest
from pquant.research.backtest.metrics import compute_metrics
from pquant.research.strategies.base import Strategy
from pquant.research.strategies.momentum import MomentumStrategy
from pquant.research.strategies.sma_cross import SmaCrossStrategy
from pquant.research.validation.splits import carried_target


@dataclass(frozen=True)
class WalkWindow:
    train_start: datetime
    test_start: datetime
    test_end: datetime
    test_index_start: int
    test_index_end: int
    train_index_start: int
    train_index_end: int


def select_train_days(span_days: float, minimum_days: int, preferred_days: int, test_days: int) -> int | None:
    if span_days >= preferred_days + test_days:
        return preferred_days
    if span_days >= minimum_days + test_days:
        return minimum_days
    return None


def build_windows(candles: list[Candle], train_days: int, test_days: int, step_days: int) -> list[WalkWindow]:
    if not candles:
        return []
    origin = candles[0].open_time
    last = candles[-1].close_time
    windows: list[WalkWindow] = []
    test_start = origin + timedelta(days=train_days)
    while test_start + timedelta(days=test_days) <= last + timedelta(seconds=1):
        test_end = test_start + timedelta(days=test_days)
        train_start = test_start - timedelta(days=train_days)
        train_index_start = _first_index_at_or_after(candles, train_start)
        train_index_end = _first_index_at_or_after(candles, test_start)
        test_index_start = train_index_end
        test_index_end = _first_index_at_or_after(candles, test_end)
        if test_index_end > test_index_start and train_index_end > train_index_start:
            covered_days = (candles[test_index_end - 1].close_time - candles[test_index_start].open_time).total_seconds() / 86400
            if covered_days >= test_days * 0.8:
                windows.append(
                    WalkWindow(
                        train_start=train_start,
                        test_start=test_start,
                        test_end=test_end,
                        test_index_start=test_index_start,
                        test_index_end=test_index_end,
                        train_index_start=train_index_start,
                        train_index_end=train_index_end,
                    )
                )
        test_start += timedelta(days=step_days)
    return windows


def run_fixed_walk_forward(
    candles: list[Candle],
    strategy: Strategy,
    windows: list[WalkWindow],
    *,
    initial_cash: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    risk_free_rate: Decimal,
) -> list[dict[str, object]]:
    """Signals may be computed on the whole series because the strategy is causal and params are pre-registered.

    Each window's account still resets, and its opening target comes only from earlier signals.
    """
    output = strategy.run(candles)
    rows: list[dict[str, object]] = []
    for window in windows:
        result = _run_slice(
            candles,
            output.close_targets,
            output.opening_target,
            window.test_index_start,
            window.test_index_end,
            initial_cash=initial_cash,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            symbol=candles[0].symbol,
            interval=candles[0].interval,
            segment="walk_forward",
        )
        metrics = compute_metrics(
            result,
            start=candles[window.test_index_start].open_time,
            end=candles[window.test_index_end - 1].close_time,
            risk_free_rate=risk_free_rate,
            interval=candles[0].interval,
        )
        rows.append(
            {
                "strategy_id": strategy.strategy_id,
                "params": _params(strategy),
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "total_return": metrics["total_return"].value,
                "sharpe_ratio": metrics["sharpe_ratio"].value,
                "max_drawdown": metrics["max_drawdown"].value,
                "fill_count": metrics["fill_count"].value,
                "final_equity": metrics["final_equity"].value,
            }
        )
    return rows


def run_selected_walk_forward(
    candles: list[Candle],
    windows: list[WalkWindow],
    *,
    family: str,
    grid: list[tuple[int, int]] | list[int],
    initial_cash: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    risk_free_rate: Decimal,
    default_params: tuple[int, int] | int,
) -> list[dict[str, object]]:
    """Pick parameters on the train window only, then evaluate the following test window once."""
    rows: list[dict[str, object]] = []
    for window in windows:
        prefix = candles[: window.train_index_end]
        chosen, train_sharpe = _select(prefix, window, family, grid, initial_cash, fee_rate, slippage_rate, risk_free_rate, default_params)
        strategy = _make(family, chosen)
        # Indicators inside the test window see that window's past bars and nothing after the window.
        visible = candles[: window.test_index_end]
        output = strategy.run(visible)
        local_start = window.test_index_start
        result = _run_slice(
            visible,
            output.close_targets,
            output.opening_target,
            local_start,
            len(visible),
            initial_cash=initial_cash,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            symbol=candles[0].symbol,
            interval=candles[0].interval,
            segment="walk_forward_selected",
        )
        metrics = compute_metrics(
            result,
            start=visible[local_start].open_time,
            end=visible[-1].close_time,
            risk_free_rate=risk_free_rate,
            interval=candles[0].interval,
        )
        rows.append(
            {
                "strategy_id": strategy.strategy_id,
                "family": family,
                "selected_params": chosen,
                "train_sharpe": train_sharpe,
                "test_start": window.test_start.isoformat(),
                "test_end": window.test_end.isoformat(),
                "total_return": metrics["total_return"].value,
                "sharpe_ratio": metrics["sharpe_ratio"].value,
                "max_drawdown": metrics["max_drawdown"].value,
                "fill_count": metrics["fill_count"].value,
            }
        )
    return rows


def _select(prefix, window, family, grid, initial_cash, fee_rate, slippage_rate, risk_free_rate, default_params):
    best_key = None
    best_params = default_params
    best_sharpe = None
    for params in grid:
        strategy = _make(family, params)
        output = strategy.run(prefix)
        start = window.train_index_start
        end = window.train_index_end
        if end - start < 2:
            continue
        result = _run_slice(
            prefix,
            output.close_targets,
            output.opening_target,
            start,
            end,
            initial_cash=initial_cash,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            symbol=prefix[0].symbol,
            interval=prefix[0].interval,
            segment="walk_forward_train",
        )
        metrics = compute_metrics(
            result,
            start=prefix[start].open_time,
            end=prefix[end - 1].close_time,
            risk_free_rate=risk_free_rate,
            interval=prefix[0].interval,
        )
        sharpe = metrics["sharpe_ratio"].value
        total = metrics["total_return"].value
        drawdown = metrics["max_drawdown"].value
        # Higher Sharpe wins. Undefined Sharpe sorts below any defined value.
        # Tie-break: higher total return, then smaller drawdown magnitude, then the pre-registered default.
        rank = (
            sharpe is not None,
            sharpe if sharpe is not None else float("-inf"),
            total if total is not None else float("-inf"),
            drawdown if drawdown is not None else float("-inf"),
            params == default_params,
        )
        if best_key is None or rank > best_key:
            best_key = rank
            best_params = params
            best_sharpe = sharpe
    return best_params, best_sharpe


def _make(family: str, params) -> Strategy:
    if family == "sma_cross":
        short, long = params
        return SmaCrossStrategy(short_window=int(short), long_window=int(long))
    if family == "momentum":
        return MomentumStrategy(window=int(params))
    raise ValueError(family)


def _params(strategy: Strategy) -> str:
    if isinstance(strategy, SmaCrossStrategy):
        return f"{strategy.short_window}/{strategy.long_window}"
    if isinstance(strategy, MomentumStrategy):
        return str(strategy.window)
    return "fixed"


def _run_slice(candles, close_targets, opening_target, start, end, **kwargs):
    opening = carried_target(close_targets, opening_target, start)
    return run_backtest(
        candles[start:end],
        close_targets[start:end],
        opening_target=opening,
        **kwargs,
    )


def _first_index_at_or_after(candles: list[Candle], when: datetime) -> int:
    for index, candle in enumerate(candles):
        if candle.open_time >= when:
            return index
    return len(candles)
