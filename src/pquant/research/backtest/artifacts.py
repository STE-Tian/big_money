"""Write CSV, JSON, PNG, and HTML artifacts for one backtest."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pquant.research.backtest.engine import BacktestResult
from pquant.research.backtest.metrics import MetricValue


def write_run_artifacts(
    directory: Path,
    result: BacktestResult,
    metrics: dict[str, MetricValue],
    snapshot: dict[str, Any],
) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    metrics_csv = directory / "metrics.csv"
    metrics_json = directory / "metrics.json"
    trades_csv = directory / "trades.csv"
    equity_csv = directory / "equity.csv"
    snapshot_path = directory / "config_snapshot.json"
    equity_png = directory / "equity.png"
    drawdown_png = directory / "drawdown.png"
    yearly_png = directory / "yearly_returns.png"
    equity_html = directory / "equity.html"
    drawdown_html = directory / "drawdown.html"

    _write_metrics_csv(metrics_csv, metrics, result.segment)
    metrics_json.write_text(json.dumps({key: _metric_dict(value) for key, value in metrics.items()}, indent=2), encoding="utf-8")
    _write_trades(trades_csv, result)
    _write_equity(equity_csv, result)
    snapshot_path.write_text(json.dumps(snapshot, indent=2, default=_json_default), encoding="utf-8")
    _write_pngs(result, equity_png, drawdown_png, yearly_png)
    _write_html(result, equity_html, drawdown_html)
    return {
        "metrics_csv": str(metrics_csv),
        "metrics_json": str(metrics_json),
        "trades_csv": str(trades_csv),
        "equity_csv": str(equity_csv),
        "config_snapshot": str(snapshot_path),
        "equity_png": str(equity_png),
        "drawdown_png": str(drawdown_png),
        "yearly_png": str(yearly_png),
        "equity_html": str(equity_html),
        "drawdown_html": str(drawdown_html),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _json_default(row.get(key)) for key in fieldnames})


def write_overlay_html(path: Path, curves: dict[str, BacktestResult], title: str) -> None:
    import plotly.graph_objects as go

    figure = go.Figure()
    for name, result in curves.items():
        figure.add_trace(
            go.Scatter(
                x=[point.time.isoformat() for point in result.equity_curve],
                y=[float(point.equity) for point in result.equity_curve],
                mode="lines",
                name=name,
            )
        )
    figure.update_layout(title=title, xaxis_title="UTC", yaxis_title="Equity (quote)")
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(path, include_plotlyjs="cdn")


def _write_metrics_csv(path: Path, metrics: dict[str, MetricValue], segment: str) -> None:
    rows = [
        {
            "metric_name": metric.name,
            "metric_value": "" if metric.value is None else repr(metric.value),
            "metric_unit": metric.unit,
            "segment": segment,
        }
        for metric in metrics.values()
    ]
    write_csv(path, rows)


def _write_trades(path: Path, result: BacktestResult) -> None:
    rows = [
        {
            "symbol": fill.symbol,
            "strategy_id": fill.strategy_id,
            "side": fill.side,
            "signal_time": fill.signal_time.isoformat() if fill.signal_time else "",
            "fill_time": fill.fill_time.isoformat(),
            "raw_open": format(fill.raw_open, "f"),
            "fill_price": format(fill.fill_price, "f"),
            "quantity": format(fill.quantity, "f"),
            "fee": format(fill.fee, "f"),
            "slippage_cost": format(fill.slippage_cost, "f"),
            "target_weight": format(fill.target_weight, "f"),
            "cash_after": format(fill.cash_after, "f"),
            "quantity_after": format(fill.quantity_after, "f"),
        }
        for fill in result.fills
    ]
    write_csv(path, rows)


def _write_equity(path: Path, result: BacktestResult) -> None:
    rows = [
        {
            "time": point.time.isoformat(),
            "equity": format(point.equity, "f"),
            "cash": format(point.cash, "f"),
            "quantity": format(point.quantity, "f"),
            "mark_price": format(point.mark_price, "f"),
            "drawdown": format(point.drawdown, "f"),
        }
        for point in result.equity_curve
    ]
    write_csv(path, rows)


def _write_pngs(result: BacktestResult, equity_path: Path, drawdown_path: Path, yearly_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    times = [point.time for point in result.equity_curve]
    equity = [float(point.equity) for point in result.equity_curve]
    drawdown = [float(point.drawdown) for point in result.equity_curve]
    figure, axis = plt.subplots(figsize=(10, 4))
    axis.plot(times, equity)
    axis.set_title(f"{result.symbol} {result.strategy_id} equity ({result.segment})")
    axis.set_ylabel("Equity")
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(equity_path, dpi=120)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(10, 4))
    axis.fill_between(times, drawdown, 0)
    axis.set_title(f"{result.symbol} {result.strategy_id} drawdown ({result.segment})")
    axis.set_ylabel("Drawdown")
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(drawdown_path, dpi=120)
    plt.close(figure)

    from pquant.research.validation.periods import calendar_returns

    # Yearly chart needs candles only for the length check. Build a stub alignment via close points.
    # The helper expects candles of equal length, so plot directly from equity timestamps instead.
    buckets: dict[str, float] = {}
    previous = result.equity_curve[0].equity
    last_by_year: dict[str, Decimal] = {}
    for point in result.equity_curve[1:]:
        last_by_year[str(point.time.year)] = point.equity
    ordered_years = sorted(last_by_year)
    values = []
    for year in ordered_years:
        end_equity = last_by_year[year]
        values.append(float(end_equity / previous - 1) if previous > 0 else 0.0)
        previous = end_equity
        buckets[year] = values[-1]
    figure, axis = plt.subplots(figsize=(10, 4))
    colors = ["#2ca02c" if value >= 0 else "#d62728" for value in values]
    axis.bar(ordered_years, values, color=colors)
    axis.set_title(f"{result.symbol} {result.strategy_id} calendar-year equity change")
    axis.set_ylabel("Return")
    figure.tight_layout()
    figure.savefig(yearly_path, dpi=120)
    plt.close(figure)
    _ = buckets


def _write_html(result: BacktestResult, equity_path: Path, drawdown_path: Path) -> None:
    import plotly.graph_objects as go

    times = [point.time.isoformat() for point in result.equity_curve]
    equity = go.Figure(go.Scatter(x=times, y=[float(point.equity) for point in result.equity_curve], mode="lines"))
    equity.update_layout(title=f"{result.symbol} {result.strategy_id} equity", xaxis_title="UTC", yaxis_title="Equity")
    equity.write_html(equity_path, include_plotlyjs="cdn")
    drawdown = go.Figure(
        go.Scatter(x=times, y=[float(point.drawdown) for point in result.equity_curve], mode="lines", fill="tozeroy")
    )
    drawdown.update_layout(title=f"{result.symbol} {result.strategy_id} drawdown", xaxis_title="UTC", yaxis_title="Drawdown")
    drawdown.write_html(drawdown_path, include_plotlyjs="cdn")


def _metric_dict(metric: MetricValue) -> dict[str, Any]:
    return {"name": metric.name, "value": metric.value, "unit": metric.unit}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value
