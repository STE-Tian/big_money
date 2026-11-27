"""V2 P2: purge, embargo, unlabeled HMM, and the pre-registered long-or-cash rule.

Synthetic series in this file are not BTC or ETH history.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from pquant.config.loader import load_settings
from pquant.domain.enums import TradingMode
from pquant.domain.errors import LiveTradingDisabledError
from pquant.research.backtest.engine import run_backtest
from pquant.research.strategies.registry import strategy_by_id
from pquant.v2.config import FeatureWindows
from pquant.v2.decision import position_from_expected, round_trip_cost
from pquant.v2.features import SymbolFeatureInput, build_feature_frames
from pquant.v2.hmm import filtered_probabilities, probability_column_names
from pquant.v2.labels import DecisionFrame, _align_hourly, assemble_decision_frame, forward_simple_returns
from pquant.v2.p2_config import enforce_p2_safety, load_p2_settings
from pquant.v2.p2_eval import _model_paths, judge_evidence, strategy_comparison
from pquant.v2.purging import training_mask, validation_bounds, walk_forward_splits
from pquant.v2.study import NOT_IMPLEMENTED, load_cost_scenarios
from pquant.v2.walkforward import run_walk_forward

from tests.conftest import ROOT, make_candles


def test_p2_config_locks_the_preregistered_protocol_and_refuses_live(monkeypatch):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    settings = load_p2_settings(ROOT)
    assert settings.app.trading_mode.value == "research"
    assert settings.app.live_trading_enabled is False
    assert settings.safety.trade_perpetual is False
    assert settings.safety.allow_short is False
    assert settings.seed == 157
    assert settings.decision_interval == "4h"
    assert settings.rule_horizon == "24h"
    assert settings.horizons_bars == {"4h": 1, "12h": 3, "24h": 6}
    assert settings.hmm.criterion == "bic"
    assert settings.hmm.state_grid == [2, 3, 4]
    assert settings.walk_forward.embargo_bars == 6
    assert settings.walk_forward.train_min_bars == 2190
    assert settings.walk_forward.test_bars == settings.walk_forward.step_bars == 540
    assert settings.evidence.rule == "lightgbm_with_hmm_positive_net_both_symbols"
    assert settings.evidence.stress_scenario == "stress_2"
    assert "bull" not in settings.model_dump_json()
    costs = load_cost_scenarios(ROOT)
    assert round_trip_cost(*costs["base"]) == Decimal("0.003")
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(LiveTradingDisabledError):
        load_p2_settings(ROOT)
    monkeypatch.delenv("TRADING_MODE", raising=False)
    loaded = load_p2_settings(ROOT)
    perpetual = loaded.model_copy(update={"safety": loaded.safety.model_copy(update={"trade_perpetual": True})})
    with pytest.raises(LiveTradingDisabledError):
        enforce_p2_safety(perpetual)


def test_v1_strategy_registry_is_unchanged_and_p3_is_not_implemented():
    names = set(strategy_by_id(load_settings(ROOT)))
    assert names == {"buy_and_hold", "sma_cross", "momentum", "cash"}
    joined = " ".join(NOT_IMPLEMENTED)
    assert "P3" in joined
    assert "LSTM" in joined
    assert "PBO" in joined


def test_purge_drops_overlapping_labels_and_embargo_drops_the_following_rows():
    decision = np.arange(20, dtype=np.int64)
    label_end = decision + 3
    mask = training_mask(decision, label_end, 10, 15, 2)
    assert mask[6]
    assert not mask[7]
    assert not mask[9]
    assert not mask[10]
    assert not mask[14]
    assert not mask[15]
    assert not mask[16]
    assert mask[17]
    label_end = label_end.copy()
    label_end[4] = -1
    assert not training_mask(decision, label_end, 10, 15, 2)[4]


def test_walk_forward_origin_leaves_room_to_purge_validation():
    splits = walk_forward_splits(
        200,
        train_min_bars=40,
        validation_bars=20,
        test_bars=20,
        step_bars=20,
        max_horizon=6,
        embargo_bars=6,
    )
    assert splits[0].test_start == 40 + 20 + 2 * 6 + 6
    assert splits[1].test_start == splits[0].test_end
    val_start, val_end = validation_bounds(splits[0].test_start, 6, 20)
    assert val_end == splits[0].test_start - 6
    assert val_end - val_start == 20
    decision = np.arange(splits[0].test_start, dtype=np.int64)
    label_end = decision + 6
    mask = training_mask(decision, label_end, val_start, val_end, 6)
    assert mask.any()
    assert not np.any(mask & (label_end >= val_start))


def test_future_close_does_not_change_past_features_or_visible_train_labels():
    closes = [f"{100 + i * 0.2:.4f}" for i in range(40)]
    candles = make_candles(closes, interval="4h", symbol="BTCUSDT", source="synthetic")
    windows = {
        "4h": FeatureWindows(
            taker_imbalance=3,
            volume_baseline=3,
            price_volume_corr=3,
            relative_strength=3,
            realized_vol=3,
        )
    }
    built = _features(candles, windows)
    before = built.loc[built["open_time"] == candles[10].open_time].iloc[0]
    returns, ends = forward_simple_returns(candles, bars=3, interval="4h")
    decision = np.arange(len(candles), dtype=np.int64)
    mask = training_mask(decision, ends, 20, 30, 3)
    train_labels = returns[mask & (decision < 20)].copy()
    changed = list(candles)
    changed[20] = candles[20].model_copy(update={"close": candles[20].close + Decimal("50")})
    rebuilt = _features(changed, windows)
    after = rebuilt.loc[rebuilt["open_time"] == candles[10].open_time].iloc[0]
    assert after["log_return"] == before["log_return"]
    assert after["taker_imbalance"] == before["taker_imbalance"]
    returns_after, ends_after = forward_simple_returns(changed, bars=3, interval="4h")
    mask_after = training_mask(decision, ends_after, 20, 30, 3)
    assert np.array_equal(mask, mask_after)
    assert np.allclose(returns_after[mask & (decision < 20)], train_labels, equal_nan=True)
    # Decision 17's 3-bar label ends on the first test bar, so it is purged.
    # Changing that close must move the purged label and must not be required
    # to keep it inside the training set.
    assert ends[17] == 20
    assert not mask[17]
    assert returns_after[17] != returns[17]


def test_hourly_asof_ignores_a_bar_that_closes_later():
    candles = make_candles(["100", "101", "102", "103", "104", "105"], interval="4h", symbol="BTCUSDT")
    close_time = candles[5].close_time
    matching_open = close_time - timedelta(hours=1) + timedelta(milliseconds=1)
    older_open = matching_open - timedelta(hours=2)
    future_open = close_time + timedelta(milliseconds=1)
    hourly = pd.DataFrame(
        {
            "open_time": [older_open, matching_open, future_open],
            "available_time": [
                older_open + timedelta(hours=1) - timedelta(milliseconds=1),
                close_time,
                future_open + timedelta(hours=1) - timedelta(milliseconds=1),
            ],
            "log_return": [1.0, 2.0, 9.0],
        }
    )
    block, names, dropped = _align_hourly(candles, hourly, ["log_return"], "BTCUSDT")
    assert names == ["h1_log_return"]
    assert dropped == 0
    assert block[5, 0] == 2.0
    future_only = hourly.iloc[[2]].reset_index(drop=True)
    blocked, _, _ = _align_hourly(candles, future_only, ["log_return"], "BTCUSDT")
    assert np.isnan(blocked[5, 0])
    early = hourly.iloc[[1]].copy()
    early["available_time"] = early["open_time"]
    excluded, _, dropped_early = _align_hourly(candles, early, ["log_return"], "BTCUSDT")
    assert dropped_early == 1
    assert np.isnan(excluded[5, 0])


def test_hmm_filter_ignores_the_future_and_does_not_name_regimes():
    names = probability_column_names(4)
    assert names == ["hmm_state_prob_0", "hmm_state_prob_1", "hmm_state_prob_2", "hmm_state_prob_3"]
    banned = ("bull", "bear", "chop", "high_vol", "high-vol")
    assert all(token not in name for name in names for token in banned)
    rng = np.random.default_rng(157)
    observations = rng.normal(size=(90, 2))
    observations[:30, 0] -= 2.0
    observations[30:60, 0] += 2.0
    kwargs = dict(
        train_end=60,
        state_grid=[2, 3],
        criterion="bic",
        max_iter=15,
        tol=1e-3,
        n_restarts=1,
        seed=157,
        variance_floor=1e-4,
    )
    probs, info = filtered_probabilities(observations, **kwargs)
    changed = observations.copy()
    changed[80, 0] += 6.0
    probs_future, info_future = filtered_probabilities(changed, **kwargs)
    assert info.chosen_states == info_future.chosen_states
    assert info.chosen_states in (2, 3)
    assert info.n_train == info_future.n_train
    assert info.scores.keys() == {2, 3}
    np.testing.assert_allclose(probs[:75], probs_future[:75], rtol=1e-8, atol=1e-8)
    head = probs[:60]
    finite = np.isfinite(head).all(axis=1)
    assert finite.any()
    np.testing.assert_allclose(head[finite, : info.chosen_states].sum(axis=1), 1.0, atol=1e-6)


def test_cost_rule_is_long_or_cash_and_does_not_read_a_label():
    cost = Decimal("0.003")
    assert position_from_expected(0.003, cost) == Decimal("0")
    assert position_from_expected(0.01, cost) == Decimal("1")
    assert position_from_expected(-0.2, cost) == Decimal("0")
    assert position_from_expected(None, cost) == Decimal("0")
    assert position_from_expected(float("nan"), cost) == Decimal("0")
    assert position_from_expected(float("inf"), cost) == Decimal("0")


def test_strategy_weights_use_the_v1_engine_fill_price():
    settings = load_p2_settings(ROOT)
    v1 = load_settings(ROOT)
    candles = make_candles(
        ["100", "101", "102", "103", "104", "105", "106", "107"],
        opens=["100", "110", "120", "130", "140", "150", "160", "170"],
        interval="4h",
        symbol="BTCUSDT",
        start=__import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc),
    )
    frame = DecisionFrame(
        symbol="BTCUSDT",
        interval="4h",
        candles=candles,
        feature_names=["h4_log_return"],
        features=np.ones((len(candles), 1)),
        labels={"24h": np.zeros(len(candles))},
        label_end_index={"24h": np.arange(len(candles))},
    )
    predictions = [
        {
            "symbol": "BTCUSDT",
            "decision_index": index,
            "model": "lightgbm",
            "feature_set": "with_hmm",
            "horizon": "24h",
            "expected_return": 0.01,
        }
        for index in range(len(candles))
    ]
    paths = _model_paths(predictions, frame, settings, Decimal("0.001"), Decimal("0.0005"))
    assert set(paths["v2_lightgbm_with_hmm"]) == {Decimal("1")}
    direct = run_backtest(
        candles,
        paths["v2_lightgbm_with_hmm"],
        opening_target=Decimal("0"),
        initial_cash=Decimal("10000"),
        fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
        strategy_id="v2_lightgbm_with_hmm",
        strategy_version="v2.p2",
        symbol="BTCUSDT",
        interval="4h",
    )
    assert direct.fills[0].fill_price == candles[1].open * (Decimal("1") + Decimal("0.0005"))
    assert direct.fills[0].fill_time == candles[1].open_time
    comparison = strategy_comparison(
        {"BTCUSDT": frame},
        predictions,
        [{"symbol": "BTCUSDT", "test_start_index": 0, "test_end_index": len(candles)}],
        settings,
        v1,
        load_cost_scenarios(ROOT),
    )
    matched = [
        row
        for row in comparison
        if row["strategy_id"] == "v2_lightgbm_with_hmm" and row["scenario"] == "base" and row["slice"] == "oos"
    ]
    assert len(matched) == 1
    assert matched[0]["final_equity"] == pytest.approx(float(direct.final_equity))
    assert matched[0]["fill_count"] == float(len(direct.fills))


def test_evidence_rule_fails_closed_and_does_not_treat_the_v1_window_as_enough():
    settings = load_p2_settings(ROOT)

    def row(strategy, symbol, scenario, slice_name, total, sharpe):
        return {
            "strategy_id": strategy,
            "symbol": symbol,
            "scenario": scenario,
            "slice": slice_name,
            "total_return": total,
            "sharpe_ratio": sharpe,
        }

    good = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        good.append(row("v2_lightgbm_with_hmm", symbol, "base", "oos", 0.2, 1.0))
        good.append(row("buy_and_hold", symbol, "base", "oos", 0.1, 0.4))
        good.append(row("v2_lightgbm_with_hmm", symbol, "stress_2", "oos", 0.05, 0.2))
        good.append(row("v2_lightgbm_with_hmm", symbol, "base", "before_v1_seen", 0.04, 0.2))
        good.append(row("v2_lightgbm_with_hmm", symbol, "base", "overlaps_v1_seen_not_a_fresh_holdout", 0.5, 2.0))
    assert judge_evidence(good, settings)["enough_evidence"] is True
    # A strong result only inside the already-seen V1 window is not enough.
    contaminated = [item for item in good if item["slice"] != "before_v1_seen"]
    contaminated.append(row("v2_lightgbm_with_hmm", "BTCUSDT", "base", "before_v1_seen", -0.01, -0.2))
    contaminated.append(row("v2_lightgbm_with_hmm", "ETHUSDT", "base", "before_v1_seen", 0.04, 0.2))
    failed = judge_evidence(contaminated, settings)
    assert failed["enough_evidence"] is False
    assert failed["stay_in_cash"] is True
    assert "2025-01-18" in failed["conclusion"]


def test_training_does_not_use_test_or_purged_labels():
    settings = _tiny_settings()
    frame = _decision_frame(settings, n=130)
    first = run_walk_forward({"BTCUSDT": frame}, settings)
    splits = walk_forward_splits(
        len(frame.candles),
        train_min_bars=settings.walk_forward.train_min_bars,
        validation_bars=settings.walk_forward.validation_bars,
        test_bars=settings.walk_forward.test_bars,
        step_bars=settings.walk_forward.step_bars,
        max_horizon=settings.max_horizon_bars(),
        embargo_bars=settings.walk_forward.embargo_bars,
    )
    mutated_labels = {name: values.copy() for name, values in frame.labels.items()}
    for horizon, bars in settings.horizons_bars.items():
        mutated_labels[horizon][splits[0].test_start - bars :] += 0.75
    mutated = DecisionFrame(
        symbol=frame.symbol,
        interval=frame.interval,
        candles=frame.candles,
        feature_names=list(frame.feature_names),
        features=frame.features.copy(),
        labels=mutated_labels,
        label_end_index={name: values.copy() for name, values in frame.label_end_index.items()},
    )
    second = run_walk_forward({"BTCUSDT": mutated}, settings)
    left = pd.DataFrame(first.predictions)
    right = pd.DataFrame(second.predictions)
    left = left.loc[left["fold_id"] == 0].sort_values(["open_time", "model", "feature_set", "horizon"])
    right = right.loc[right["fold_id"] == 0].sort_values(["open_time", "model", "feature_set", "horizon"])
    assert len(left) == len(right) > 0
    assert np.allclose(left["expected_return"], right["expected_return"], rtol=1e-8, atol=1e-8)
    assert np.allclose(left["positive_probability"], right["positive_probability"], rtol=1e-8, atol=1e-8)
    assert first.hyperparameters == second.hyperparameters
    assert first.shap_crosscheck is not None
    assert first.shap_crosscheck["max_abs_diff"] is not None
    assert first.shap_crosscheck["max_abs_diff"] < 1e-5


def _features(candles, windows):
    built = build_feature_frames(
        [
            SymbolFeatureInput(
                symbol="BTCUSDT",
                interval="4h",
                candles=candles,
                funding=[],
                spot_dataset_hash="synthetic-spot",
                funding_dataset_hash="synthetic-funding",
            )
        ],
        windows=windows,
        funding_high_threshold=Decimal("0.0001"),
        annualize_realized_vol=True,
        relative_strength_intervals=set(),
        config_hash="synthetic",
    )
    return built.frames[("BTCUSDT", "4h")]


def _decision_frame(settings, n: int) -> DecisionFrame:
    closes = [f"{100 + (i % 5) * 0.4 + i * 0.03:.4f}" for i in range(n)]
    candles = make_candles(closes, interval="4h", symbol="BTCUSDT", source="synthetic")
    windows = {
        "4h": FeatureWindows(
            taker_imbalance=3,
            volume_baseline=3,
            price_volume_corr=3,
            relative_strength=3,
            realized_vol=3,
        )
    }
    features = _features(candles, windows)
    return assemble_decision_frame(candles, features, None, settings, symbol="BTCUSDT")


def _tiny_settings():
    settings = load_p2_settings(ROOT)
    return settings.model_copy(
        update={
            "feature_columns": ["log_return", "taker_imbalance", "realized_vol", "volume_ratio"],
            "hourly_asof_columns": [],
            "shap_crosscheck_rows": 16,
            "walk_forward": settings.walk_forward.model_copy(
                update={
                    "train_min_bars": 40,
                    "validation_bars": 20,
                    "test_bars": 20,
                    "step_bars": 20,
                    "embargo_bars": 6,
                }
            ),
            "linear": settings.linear.model_copy(update={"C": [1.0], "alpha": [1.0]}),
            "lightgbm": settings.lightgbm.model_copy(
                update={
                    "n_estimators": 15,
                    "num_leaves": [7],
                    "min_child_samples": [5],
                    "lambda_l2": [1.0],
                    "num_threads": 1,
                }
            ),
            "hmm": settings.hmm.model_copy(update={"state_grid": [2, 3], "max_iter": 12, "n_restarts": 1}),
        }
    )
