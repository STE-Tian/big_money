"""Metric definitions, contiguous splits, and the pre-registered decision rule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pquant.research.backtest.engine import EquityPoint
from pquant.research.backtest.metrics import compute_metrics, max_drawdown
from pquant.research.backtest.report_markdown import render_backtest_report
from pquant.research.validation.judgment import JudgmentInput, judge
from pquant.research.validation.splits import time_split_bounds

from tests.conftest import make_candles


def _point(day: int, equity: str) -> EquityPoint:
    return EquityPoint(
        time=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=day),
        equity=Decimal(equity),
        cash=Decimal(equity),
        quantity=Decimal("0"),
        mark_price=Decimal("1"),
        drawdown=Decimal("0"),
    )


def test_max_drawdown_matches_hand_series():
    assert max_drawdown([100, 120, 90, 90, 150]) == pytest.approx(-0.25)


def test_cagr_doubles_over_365_days():
    from pquant.research.backtest.engine import BacktestResult

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=365)
    points = [
        EquityPoint(start, Decimal("100"), Decimal("100"), Decimal("0"), Decimal("1"), Decimal("0")),
        EquityPoint(end, Decimal("200"), Decimal("200"), Decimal("0"), Decimal("1"), Decimal("0")),
    ]
    result = BacktestResult(
        strategy_id="cash",
        strategy_version="1.0.0",
        symbol="BTCUSDT",
        interval="1d",
        segment="full",
        fills=[],
        closed_trades=[],
        equity_curve=points,
        initial_cash=Decimal("100"),
        fee_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
        fee_paid=Decimal("0"),
        slippage_cost=Decimal("0"),
        final_cash=Decimal("200"),
        final_quantity=Decimal("0"),
        final_equity=Decimal("200"),
    )
    metrics = compute_metrics(result, start=start, end=end, risk_free_rate=Decimal("0"), interval="1d")
    assert metrics["total_return"].value == pytest.approx(1.0)
    assert metrics["cagr"].value == pytest.approx(1.0)
    assert metrics["sharpe_ratio"].value is None
    assert "252" not in metrics["sharpe_ratio"].unit
    assert "365" in metrics["sharpe_ratio"].unit


def test_sortino_and_sharpe_use_the_documented_formula():
    from pquant.research.backtest.metrics import _sharpe, _sortino

    excess = [0.02, -0.01, 0.03, -0.04]
    mean = sum(excess) / 4
    var = sum((value - mean) ** 2 for value in excess) / 3
    std = var ** 0.5
    expected_sharpe = mean / std * (365 ** 0.5)
    assert _sharpe(excess, 365) == pytest.approx(expected_sharpe)
    downside = [min(value, 0.0) for value in excess]
    downside_dev = (sum(value * value for value in downside) / len(downside)) ** 0.5
    expected_sortino = mean / downside_dev * (365 ** 0.5)
    assert _sortino(excess, 365) == pytest.approx(expected_sortino)


def test_splits_are_contiguous_and_cover_the_sample():
    bounds = time_split_bounds(10, Decimal("0.6"), Decimal("0.2"), Decimal("0.2"))
    assert bounds["train"] == (0, 6)
    assert bounds["validation"] == (6, 8)
    assert bounds["test"] == (8, 10)
    with pytest.raises(ValueError):
        time_split_bounds(10, 0.5, 0.5, 0.5)


def test_judgment_rules_are_stable():
    base = dict(
        strategy_id="sma_cross",
        data_quality_status="qualified",
        accounting_ok=True,
        test_span_days=400,
        minimum_test_days=180,
        test_total_return=0.1,
        test_sharpe=0.4,
        test_max_drawdown=-0.2,
        train_total_return=0.2,
        stress_1_test_total_return=0.05,
        stress_2_test_total_return=0.01,
        validation_grid_positive_fraction=0.8,
        walk_forward_nonnegative_fraction=0.7,
        parameterized=True,
    )
    assert judge(JudgmentInput(**base)).label == "CANDIDATE_FOR_PAPER"
    bad_data = dict(base)
    bad_data["data_quality_status"] = "unqualified"
    assert judge(JudgmentInput(**bad_data)).label == "REJECT"
    short = dict(base)
    short["test_span_days"] = 30
    short_judgment = judge(JudgmentInput(**short))
    assert short_judgment.label == "INCONCLUSIVE"
    assert short_judgment.history_status == "INSUFFICIENT_HISTORY"
    ugly = dict(base)
    ugly["test_total_return"] = -0.3
    ugly["test_sharpe"] = -0.2
    assert judge(JudgmentInput(**ugly)).label == "REJECT"
    flip = dict(base)
    flip["stress_1_test_total_return"] = -0.01
    assert judge(JudgmentInput(**flip)).label == "INCONCLUSIVE"
    cash = dict(base)
    cash["strategy_id"] = "cash"
    assert judge(JudgmentInput(**cash)).label == "BENCHMARK_ONLY"
    _ = make_candles


def test_report_states_the_buy_and_hold_comparison_without_reversing_it():
    summary = {
        "generated_at": "2026-11-27T00:00:00+00:00",
        "code_commit": "test",
        "base_url": "https://data-api.binance.vision",
        "assumptions": {},
        "symbols": {
            "BTCUSDT": {
                "interval": "1d",
                "row_count_closed": 10,
                "row_count_including_open": 10,
                "start": "2020-01-01",
                "end": "2020-01-10",
                "dataset_hash": "abc",
                "quality": {"status": "qualified", "duplicate_rows": 0, "out_of_order_rows": 0, "missing_count": 0, "missing_ratio": 0, "ohlc_invalid_count": 0, "negative_volume_count": 0, "future_timestamp_count": 0, "revised_candle_count": 0, "latest_closed_lag_seconds": 1, "reasons": []},
                "robustness": {"sma_cross": [], "momentum": []},
                "walk_forward_train_days": None,
                "walk_forward_window_count": 0,
                "walk_forward_skip_reason": "unit test",
                "strategies": {
                    "buy_and_hold": {"segments": {"test": {"metrics": {"total_return": {"value": -0.4}}}}, "judgment": {"label": "REJECT", "history_status": "ok", "reasons": ["test loss"]}, "costs": {}, "artifact_paths": {}, "drawdown_episode": {}, "negative_years": [], "negative_quarters": [], "regime_mean_bar_return": {}},
                    "sma_cross": {"segments": {"test": {"metrics": {"total_return": {"value": 0.1}}}}, "judgment": {"label": "CANDIDATE_FOR_PAPER", "history_status": "ok", "reasons": ["rule passed"]}, "costs": {}, "artifact_paths": {}, "drawdown_episode": {}, "negative_years": [], "negative_quarters": [], "regime_mean_bar_return": {}},
                },
            }
        },
    }
    text = render_backtest_report(summary)
    assert "测试区间收益高于同一区间的买入持有" in text
    assert "没有改善持有体验" not in text
