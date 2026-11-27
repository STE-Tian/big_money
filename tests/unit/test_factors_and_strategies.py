"""Factor and strategy causality, including warm-up."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from pquant.research.factors.momentum import momentum
from pquant.research.factors.moving_average import simple_moving_average
from pquant.research.strategies.momentum import MomentumStrategy
from pquant.research.strategies.sma_cross import SmaCrossStrategy

from tests.conftest import make_candles


def test_sma_matches_hand_calculation():
    close = pd.Series([1, 2, 3, 4, 5], dtype="float64")
    result = simple_moving_average(close, 3)
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[1])
    assert result.iloc[2] == 2
    assert result.iloc[4] == 4


def test_momentum_matches_hand_calculation():
    close = pd.Series([10, 10, 11, 12], dtype="float64")
    result = momentum(close, 2)
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx(0.1)
    assert result.iloc[3] == pytest.approx(0.2)


def test_warmup_does_not_emit_a_target():
    candles = make_candles([str(100 + index) for index in range(30)])
    output = SmaCrossStrategy(short_window=5, long_window=10).run(candles)
    assert all(value is None for value in output.close_targets[:9])
    assert output.close_targets[9] in {Decimal("0"), Decimal("1")}
    momentum_output = MomentumStrategy(window=20).run(candles)
    assert all(value is None for value in momentum_output.close_targets[:20])
    assert momentum_output.opening_target is None


def test_equal_sma_and_non_positive_momentum_are_cash():
    flat = make_candles(["10"] * 12)
    sma = SmaCrossStrategy(3, 5).run(flat)
    assert sma.close_targets[-1] == Decimal("0")
    mom = MomentumStrategy(window=4).run(flat)
    assert mom.close_targets[-1] == Decimal("0")
