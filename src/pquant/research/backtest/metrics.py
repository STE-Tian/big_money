"""Performance statistics.

Daily bars use sqrt(365). 4h bars use sqrt(365*6). 1h bars use sqrt(365*24).
The risk-free rate is an annual research assumption (default 0) and is converted
to a per-bar rate by dividing by the annualization factor. Sharpe uses the sample
standard deviation (ddof=1). Sortino uses the square root of the mean squared
negative excess return (divide by N). Calmar is undefined when max drawdown is 0.
Win rate uses closed round trips only: a win is a flat-to-flat cash increase.
Profit factor is undefined when there is no losing closed trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from pquant.research.backtest.engine import BacktestResult
from pquant.utils.time import annualization_periods


@dataclass(frozen=True)
class MetricValue:
    name: str
    value: float | None
    unit: str


def compute_metrics(
    result: BacktestResult,
    *,
    start,
    end,
    risk_free_rate: Decimal,
    interval: str,
) -> dict[str, MetricValue]:
    equities = [float(point.equity) for point in result.equity_curve]
    returns = _returns(equities)
    factor = annualization_periods(interval)
    rf_bar = float(risk_free_rate) / factor
    excess = [value - rf_bar for value in returns]
    total_return = (equities[-1] / equities[0] - 1.0) if equities and equities[0] else None
    days = (end - start).total_seconds() / 86400.0
    cagr = _cagr(equities[0], equities[-1], days) if equities else None
    vol = _sample_std(excess) * math.sqrt(factor) if len(excess) >= 2 else None
    sharpe = _sharpe(excess, factor)
    sortino = _sortino(excess, factor)
    drawdowns = [float(point.drawdown) for point in result.equity_curve]
    max_dd = min(drawdowns) if drawdowns else None
    calmar = None
    calmar_unit = "ratio"
    if cagr is not None and max_dd is not None:
        if max_dd == 0:
            calmar = None
            calmar_unit = "undefined_zero_drawdown"
        else:
            calmar = cagr / abs(max_dd)
    closed = result.closed_trades
    wins = sum(1 for trade in closed if trade.won)
    if closed:
        win_rate = wins / len(closed)
        win_unit = "ratio_of_closed_round_trips"
    else:
        win_rate = None
        win_unit = "no_closed_trades"
    gross_profit = sum(float(trade.pnl) for trade in closed if trade.pnl > 0)
    gross_loss = sum(float(-trade.pnl) for trade in closed if trade.pnl < 0)
    if not closed:
        profit_factor = None
        pf_unit = "no_closed_trades"
    elif gross_loss == 0:
        profit_factor = None
        pf_unit = "incomparable_no_losing_trades"
    else:
        profit_factor = gross_profit / gross_loss
        pf_unit = "ratio"
    bar_points = result.equity_curve[1:]
    if bar_points:
        exposure = sum(1 for point in bar_points if point.quantity > 0) / len(bar_points)
    else:
        exposure = None
    notionals = [float(fill.quantity * fill.fill_price) for fill in result.fills]
    mean_equity = float(np.mean(equities)) if equities else 0.0
    if mean_equity > 0:
        turnover = sum(notionals) / mean_equity
    else:
        turnover = None
    metrics = {
        "total_return": MetricValue("total_return", total_return, "ratio"),
        "cagr": MetricValue("cagr", cagr, "ratio_365_day"),
        "annualized_volatility": MetricValue("annualized_volatility", vol, f"stdev_times_sqrt_{factor}"),
        "sharpe_ratio": MetricValue("sharpe_ratio", sharpe, f"mean_excess_over_sample_stdev_sqrt_{factor}_rf_{rf_bar}"),
        "sortino_ratio": MetricValue("sortino_ratio", sortino, f"downside_population_target_rf_per_bar_{rf_bar}"),
        "max_drawdown": MetricValue("max_drawdown", max_dd, "equity_over_running_peak_minus_one"),
        "calmar_ratio": MetricValue("calmar_ratio", calmar, calmar_unit),
        "win_rate": MetricValue("win_rate", win_rate, win_unit),
        "profit_factor": MetricValue("profit_factor", profit_factor, pf_unit),
        "exposure": MetricValue("exposure", exposure, "fraction_of_bars_with_positive_quantity"),
        "turnover": MetricValue("turnover", turnover, "traded_notional_over_mean_equity"),
        "fill_count": MetricValue("fill_count", float(len(result.fills)), "fills"),
        "closed_trade_count": MetricValue("closed_trade_count", float(len(closed)), "flat_to_flat_round_trips"),
        "fee_paid": MetricValue("fee_paid", float(result.fee_paid), "quote_asset"),
        "slippage_cost": MetricValue("slippage_cost", float(result.slippage_cost), "quote_notional_vs_raw_open_embedded_in_fills"),
        "final_equity": MetricValue("final_equity", float(result.final_equity), "quote_asset"),
    }
    return metrics


def max_drawdown(equity: list[float]) -> float:
    if not equity:
        raise ValueError("equity series is empty")
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def _returns(equity: list[float]) -> list[float]:
    out: list[float] = []
    for previous, current in zip(equity, equity[1:]):
        if previous <= 0:
            raise ValueError("non-positive equity in return calculation")
        out.append(current / previous - 1.0)
    return out


def _cagr(start_equity: float, end_equity: float, days: float) -> float | None:
    if days <= 0 or start_equity <= 0 or end_equity <= 0:
        return None
    return (end_equity / start_equity) ** (365.0 / days) - 1.0


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(np.std(np.asarray(values, dtype=float), ddof=1))


def _sharpe(excess: list[float], factor: int) -> float | None:
    if len(excess) < 2:
        return None
    std = _sample_std(excess)
    if std == 0:
        return None
    return float(np.mean(excess) / std * math.sqrt(factor))


def _sortino(excess: list[float], factor: int) -> float | None:
    if not excess:
        return None
    downside = [min(value, 0.0) for value in excess]
    downside_dev = math.sqrt(sum(value * value for value in downside) / len(downside))
    if downside_dev == 0:
        return None
    return float(np.mean(excess) / downside_dev * math.sqrt(factor))
