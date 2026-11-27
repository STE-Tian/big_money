"""Purged walk-forward fits for the logistic baseline and LightGBM.

Hyperparameters are chosen on the first test fold's validation slice and then
frozen. Each later fold refits on its own purged training prefix. HMM state
probabilities are filtered with parameters fit only on that prefix. Test
labels are not passed to any fit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from pquant.domain.errors import DataQualityError
from pquant.v2.hmm import filtered_probabilities
from pquant.v2.labels import DecisionFrame
from pquant.v2.modeling import (
    LGBMChoice,
    LinearChoice,
    fit_predict_lightgbm,
    fit_predict_linear,
    select_lightgbm,
    select_linear,
    shap_crosscheck,
)
from pquant.v2.p2_config import P2Settings
from pquant.v2.purging import training_mask, validation_bounds, walk_forward_splits

logger = logging.getLogger("pquant.v2.walkforward")

FEATURE_SETS = ("no_hmm", "with_hmm")
MODELS = ("logistic", "lightgbm")


@dataclass
class WalkForwardResult:
    predictions: list[dict[str, object]] = field(default_factory=list)
    folds: list[dict[str, object]] = field(default_factory=list)
    hyperparameters: dict[str, dict[str, object]] = field(default_factory=dict)
    attribution: list[dict[str, object]] = field(default_factory=list)
    shap_crosscheck: dict[str, object] | None = None
    splits: list[tuple[int, int]] = field(default_factory=list)


def run_walk_forward(frames: dict[str, DecisionFrame], settings: P2Settings) -> WalkForwardResult:
    result = WalkForwardResult()
    if not frames:
        raise DataQualityError("walk-forward received no decision frames")
    for symbol, frame in frames.items():
        splits = walk_forward_splits(
            len(frame.candles),
            train_min_bars=settings.walk_forward.train_min_bars,
            validation_bars=settings.walk_forward.validation_bars,
            test_bars=settings.walk_forward.test_bars,
            step_bars=settings.walk_forward.step_bars,
            max_horizon=settings.max_horizon_bars(),
            embargo_bars=settings.walk_forward.embargo_bars,
        )
        if len(splits) < 1:
            raise DataQualityError(
                f"{symbol} has {len(frame.candles)} closed {frame.interval} bars, "
                "which is shorter than the pre-registered walk-forward"
            )
        logger.info("%s walk-forward folds=%s first_test=%s", symbol, len(splits), splits[0].test_start)
        chosen = _select_on_first_fold(frame, splits[0].test_start, settings)
        result.hyperparameters[symbol] = {
            f"{model_name}|{feature_set}|{horizon}": _choice_dict(value)
            for (model_name, feature_set, horizon), value in chosen.items()
        }
        abs_sum: dict[str, np.ndarray] = {}
        abs_count: dict[str, int] = {}
        for fold_id, split in enumerate(splits):
            probs, info = _hmm(frame, split.test_start, settings)
            result.folds.append(
                {
                    "symbol": symbol,
                    "fold_id": fold_id,
                    "test_start_index": split.test_start,
                    "test_end_index": split.test_end,
                    "test_start": frame.candles[split.test_start].open_time.isoformat(),
                    "test_end_open": frame.candles[split.test_end - 1].open_time.isoformat(),
                    "hmm_states": info.chosen_states,
                    "hmm_criterion": info.criterion,
                    "hmm_scores": info.scores,
                    "hmm_n_train": info.n_train,
                    "hmm_loglik": info.loglik,
                }
            )
            for feature_set in FEATURE_SETS:
                design = _design(frame, probs, feature_set)
                names = settings.feature_names(feature_set)
                for model_name in MODELS:
                    for horizon, bars in settings.horizons_bars.items():
                        _fit_fold(
                            result,
                            frame=frame,
                            design=design,
                            names=names,
                            split_start=split.test_start,
                            split_end=split.test_end,
                            horizon=horizon,
                            bars=bars,
                            feature_set=feature_set,
                            model_name=model_name,
                            choice=chosen[(model_name, feature_set, horizon)],
                            settings=settings,
                            fold_id=fold_id,
                            abs_sum=abs_sum,
                            abs_count=abs_count,
                            crosscheck_here=(
                                fold_id == 0
                                and model_name == "lightgbm"
                                and feature_set == "with_hmm"
                                and horizon == settings.rule_horizon
                                and result.shap_crosscheck is None
                            ),
                        )
        for key, total in abs_sum.items():
            count = abs_count[key]
            order = np.argsort(-total)
            model_name, feature_set, horizon = key.split("|")
            result.attribution.append(
                {
                    "symbol": symbol,
                    "model": model_name,
                    "feature_set": feature_set,
                    "horizon": horizon,
                    "rows": count,
                    "mean_abs_shap": [
                        {"feature": settings.feature_names(feature_set)[int(index)], "mean_abs": float(total[index] / count)}
                        for index in order
                    ],
                }
            )
    return result


def _select_on_first_fold(frame: DecisionFrame, test_start: int, settings: P2Settings) -> dict[tuple[str, str, str], object]:
    chosen: dict[tuple[str, str, str], object] = {}
    index = np.arange(len(frame.candles), dtype=np.int64)
    for horizon, bars in settings.horizons_bars.items():
        val_start, val_end = validation_bounds(test_start, bars, settings.walk_forward.validation_bars)
        probs, info = _hmm(frame, val_start, settings)
        logger.info(
            "%s select %s hmm_states=%s val=%s:%s",
            frame.symbol,
            horizon,
            info.chosen_states,
            val_start,
            val_end,
        )
        for feature_set in FEATURE_SETS:
            design = _design(frame, probs, feature_set)
            y_reg = frame.labels[horizon]
            finite = np.isfinite(design).all(axis=1) & np.isfinite(y_reg)
            fit_mask = training_mask(
                index,
                frame.label_end_index[horizon],
                val_start,
                val_end,
                settings.walk_forward.embargo_bars,
            )
            fit_mask &= index < val_start
            fit_mask &= finite
            val_mask = (index >= val_start) & (index < val_end) & finite
            x_fit, y_fit, y_cls_fit = _xy(design, y_reg, fit_mask)
            x_val, y_val, y_cls_val = _xy(design, y_reg, val_mask)
            if len(x_fit) < _minimum_fit_rows(settings) or len(x_val) < _minimum_val_rows(settings):
                raise DataQualityError(
                    f"{frame.symbol} {horizon} selection split is too small: fit={len(x_fit)} val={len(x_val)}"
                )
            linear = select_linear(
                x_fit, y_fit, y_cls_fit, x_val, y_val, y_cls_val, settings.linear, settings.seed
            )
            trees = select_lightgbm(
                x_fit, y_fit, y_cls_fit, x_val, y_val, y_cls_val, settings.lightgbm, settings.seed
            )
            chosen[("logistic", feature_set, horizon)] = linear
            chosen[("lightgbm", feature_set, horizon)] = trees
            logger.info(
                "%s frozen %s %s logistic C=%s alpha=%s lightgbm leaves=%s min_child=%s l2=%s",
                frame.symbol,
                feature_set,
                horizon,
                linear.C,
                linear.alpha,
                trees.num_leaves,
                trees.min_child_samples,
                trees.lambda_l2,
            )
    return chosen


def _fit_fold(
    result: WalkForwardResult,
    *,
    frame: DecisionFrame,
    design: np.ndarray,
    names: list[str],
    split_start: int,
    split_end: int,
    horizon: str,
    bars: int,
    feature_set: str,
    model_name: str,
    choice: object,
    settings: P2Settings,
    fold_id: int,
    abs_sum: dict[str, np.ndarray],
    abs_count: dict[str, int],
    crosscheck_here: bool,
) -> None:
    del bars, names
    index = np.arange(len(frame.candles), dtype=np.int64)
    y_reg = frame.labels[horizon]
    finite_x = np.isfinite(design).all(axis=1)
    train_mask = training_mask(
        index,
        frame.label_end_index[horizon],
        split_start,
        split_end,
        settings.walk_forward.embargo_bars,
    )
    train_mask &= index < split_start
    train_mask &= finite_x & np.isfinite(y_reg)
    test_mask = (index >= split_start) & (index < split_end) & finite_x
    x_train, y_train, y_cls = _xy(design, y_reg, train_mask)
    test_index = np.flatnonzero(test_mask)
    x_test = design[test_index]
    if len(x_train) < 30 or len(x_test) == 0:
        raise DataQualityError(
            f"{frame.symbol} fold {fold_id} {model_name} {feature_set} {horizon} "
            f"has fit={len(x_train)} test={len(x_test)}"
        )
    attribution = None
    coefficients = None
    if model_name == "logistic":
        batch = fit_predict_linear(
            x_train,
            y_train,
            y_cls,
            x_test,
            choice,  # type: ignore[arg-type]
            settings.linear,
            settings.seed,
        )
        coefficients = batch.linear_coefficients
    elif model_name == "lightgbm":
        batch, regressor = fit_predict_lightgbm(
            x_train,
            y_train,
            y_cls,
            x_test,
            choice,  # type: ignore[arg-type]
            settings.lightgbm,
            settings.seed,
            with_attribution=horizon == settings.rule_horizon and feature_set == "with_hmm",
        )
        attribution = batch.attribution
        if crosscheck_here and attribution is not None:
            rows = min(settings.shap_crosscheck_rows, len(x_test))
            result.shap_crosscheck = shap_crosscheck(regressor, x_test[:rows], attribution[:rows])
            result.shap_crosscheck["symbol"] = frame.symbol
            result.shap_crosscheck["fold_id"] = fold_id
            result.shap_crosscheck["horizon"] = horizon
    else:
        raise DataQualityError(f"unknown model {model_name}")
    if attribution is not None:
        key = f"{model_name}|{feature_set}|{horizon}"
        block = np.abs(attribution[:, :-1])
        abs_sum[key] = block.sum(axis=0) if key not in abs_sum else abs_sum[key] + block.sum(axis=0)
        abs_count[key] = abs_count.get(key, 0) + int(len(block))
    realized = y_reg[test_index]
    for row_number, decision in enumerate(test_index):
        candle = frame.candles[int(decision)]
        value = realized[row_number]
        result.predictions.append(
            {
                "symbol": frame.symbol,
                "open_time": candle.open_time.isoformat(),
                "close_time": candle.close_time.isoformat(),
                "decision_index": int(decision),
                "fold_id": fold_id,
                "model": model_name,
                "feature_set": feature_set,
                "horizon": horizon,
                "expected_return": float(batch.expected_return[row_number]),
                "positive_probability": float(batch.positive_probability[row_number]),
                "uncertainty": float(batch.uncertainty[row_number]),
                "realized_return": None if not np.isfinite(value) else float(value),
                "linear_coefficient_l2": None
                if coefficients is None
                else float(np.sqrt(np.dot(coefficients, coefficients))),
            }
        )


def _hmm(frame: DecisionFrame, train_end: int, settings: P2Settings):
    observations = np.column_stack(
        [frame.column(f"h4_{name}") for name in settings.hmm.observation_columns]
    )
    return filtered_probabilities(
        observations,
        train_end=train_end,
        state_grid=list(settings.hmm.state_grid),
        criterion=settings.hmm.criterion,
        max_iter=settings.hmm.max_iter,
        tol=settings.hmm.tol,
        n_restarts=settings.hmm.n_restarts,
        seed=settings.seed,
        variance_floor=settings.hmm.variance_floor,
    )


def _design(frame: DecisionFrame, probs: np.ndarray, feature_set: str) -> np.ndarray:
    if feature_set == "no_hmm":
        return frame.features
    if feature_set == "with_hmm":
        return np.hstack([frame.features, probs])
    raise DataQualityError(f"unknown feature set {feature_set}")


def _xy(design: np.ndarray, y_reg: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = np.flatnonzero(mask)
    y = np.asarray(y_reg[rows], dtype=np.float64)
    y_cls = (y > 0).astype(np.int32)
    return design[rows], y, y_cls


def _minimum_fit_rows(settings: P2Settings) -> int:
    """Fit rows after dropping the leading null feature window.

    The pre-registered train_min_bars count includes that warmup. Relative
    strength uses 42 bars and the 1h features use 24, so 120 leading nulls
    are allowed before a split is rejected. This is not a trading threshold.
    """
    return max(30, settings.walk_forward.train_min_bars - 120)


def _minimum_val_rows(settings: P2Settings) -> int:
    return min(30, max(10, settings.walk_forward.validation_bars // 2))


def _choice_dict(choice: object) -> dict[str, object]:
    if isinstance(choice, LinearChoice):
        return {"C": choice.C, "alpha": choice.alpha}
    if isinstance(choice, LGBMChoice):
        return {
            "num_leaves": choice.num_leaves,
            "min_child_samples": choice.min_child_samples,
            "lambda_l2": choice.lambda_l2,
        }
    raise TypeError(f"unknown hyperparameter choice {type(choice).__name__}")
