"""Logistic / Ridge baseline and LightGBM, fit only on the rows passed in.

Expected return, the probability that the return is positive, and a
prediction uncertainty are produced for every horizon by the caller. Logistic
regression supplies the probability. Ridge supplies the linear expected
return, because a classifier does not emit E[r]. LightGBM supplies both, and
its uncertainty is half the gap between the 0.1 and 0.9 quantile models.
Ridge uncertainty is the in-sample residual standard deviation of that fold.
Logistic uncertainty is the Bernoulli variance p(1-p).

Hyperparameters are chosen by validation log loss (classifiers) or validation
MAE (regressors). The test rows are not an argument to selection.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import log_loss, mean_absolute_error
from sklearn.preprocessing import StandardScaler

from pquant.v2.p2_config import LightGBMSection, LinearSection


@dataclass(frozen=True)
class LinearChoice:
    C: float
    alpha: float


@dataclass(frozen=True)
class LGBMChoice:
    num_leaves: int
    min_child_samples: int
    lambda_l2: float


@dataclass(frozen=True)
class PredictionBatch:
    expected_return: np.ndarray
    positive_probability: np.ndarray
    uncertainty: np.ndarray
    attribution: np.ndarray | None
    linear_coefficients: np.ndarray | None


def select_linear(
    x_fit: np.ndarray,
    y_reg_fit: np.ndarray,
    y_cls_fit: np.ndarray,
    x_val: np.ndarray,
    y_reg_val: np.ndarray,
    y_cls_val: np.ndarray,
    grid: LinearSection,
    seed: int,
) -> LinearChoice:
    scaler = _scaler(x_fit)
    fit_x = scaler.transform(x_fit)
    val_x = scaler.transform(x_val)
    best_c = grid.C[0]
    best_loss = np.inf
    for C in grid.C:
        proba = _logistic_proba(fit_x, y_cls_fit, val_x, C, grid.max_iter, seed)
        loss = float(log_loss(y_cls_val, np.column_stack([1.0 - proba, proba]), labels=[0, 1]))
        if loss < best_loss:
            best_loss = loss
            best_c = float(C)
    best_alpha = grid.alpha[0]
    best_mae = np.inf
    for alpha in grid.alpha:
        pred = _ridge_predict(fit_x, y_reg_fit, val_x, float(alpha))
        mae = float(mean_absolute_error(y_reg_val, pred))
        if mae < best_mae:
            best_mae = mae
            best_alpha = float(alpha)
    return LinearChoice(C=best_c, alpha=best_alpha)


def select_lightgbm(
    x_fit: np.ndarray,
    y_reg_fit: np.ndarray,
    y_cls_fit: np.ndarray,
    x_val: np.ndarray,
    y_reg_val: np.ndarray,
    y_cls_val: np.ndarray,
    grid: LightGBMSection,
    seed: int,
) -> LGBMChoice:
    best: LGBMChoice | None = None
    best_key = (np.inf, np.inf)
    for num_leaves in grid.num_leaves:
        for min_child in grid.min_child_samples:
            for lambda_l2 in grid.lambda_l2:
                choice = LGBMChoice(int(num_leaves), int(min_child), float(lambda_l2))
                proba = _lgbm_proba(x_fit, y_cls_fit, x_val, grid, choice, seed)
                loss = float(log_loss(y_cls_val, np.column_stack([1.0 - proba, proba]), labels=[0, 1]))
                reg = _lgbm_regressor(grid, choice, seed, objective="regression")
                reg.fit(x_fit, y_reg_fit)
                mae = float(mean_absolute_error(y_reg_val, reg.predict(x_val)))
                key = (loss, mae)
                if key < best_key:
                    best_key = key
                    best = choice
    if best is None:
        raise ValueError("LightGBM grid was empty")
    return best


def fit_predict_linear(
    x_train: np.ndarray,
    y_reg_train: np.ndarray,
    y_cls_train: np.ndarray,
    x_test: np.ndarray,
    choice: LinearChoice,
    grid: LinearSection,
    seed: int,
) -> PredictionBatch:
    scaler = _scaler(x_train)
    train_x = scaler.transform(x_train)
    test_x = scaler.transform(x_test) if len(x_test) else np.empty((0, x_train.shape[1]))
    proba = _logistic_proba(train_x, y_cls_train, test_x, choice.C, grid.max_iter, seed)
    expected, residual_std, coefficients = _ridge_train_predict(train_x, y_reg_train, test_x, choice.alpha)
    # Uncertainty here is the in-sample residual scale of the Ridge fit.
    # It is one number per fold. Bernoulli variance p(1-p) is a function of
    # positive_probability and is not a second stored series.
    return PredictionBatch(
        expected_return=expected,
        positive_probability=proba,
        uncertainty=np.full(len(x_test), residual_std, dtype=np.float64),
        attribution=None,
        linear_coefficients=coefficients,
    )


def fit_predict_lightgbm(
    x_train: np.ndarray,
    y_reg_train: np.ndarray,
    y_cls_train: np.ndarray,
    x_test: np.ndarray,
    choice: LGBMChoice,
    grid: LightGBMSection,
    seed: int,
    *,
    with_attribution: bool,
) -> tuple[PredictionBatch, LGBMRegressor]:
    classifier = _lgbm_classifier(grid, choice, seed)
    classifier.fit(x_train, y_cls_train)
    proba = _positive_from_classifier(classifier, x_test)
    regressor = _lgbm_regressor(grid, choice, seed, objective="regression")
    regressor.fit(x_train, y_reg_train)
    expected = np.asarray(regressor.predict(x_test), dtype=np.float64) if len(x_test) else np.empty(0)
    low = _lgbm_regressor(grid, choice, seed, objective="quantile", alpha=grid.quantile_low)
    high = _lgbm_regressor(grid, choice, seed, objective="quantile", alpha=grid.quantile_high)
    low.fit(x_train, y_reg_train)
    high.fit(x_train, y_reg_train)
    if len(x_test):
        width = 0.5 * np.abs(np.asarray(high.predict(x_test), dtype=np.float64) - np.asarray(low.predict(x_test), dtype=np.float64))
    else:
        width = np.empty(0)
    attribution = None
    if with_attribution and len(x_test):
        attribution = np.asarray(regressor.booster_.predict(x_test, pred_contrib=True), dtype=np.float64)
    return (
        PredictionBatch(
            expected_return=expected,
            positive_probability=proba,
            uncertainty=width,
            attribution=attribution,
            linear_coefficients=None,
        ),
        regressor,
    )


def shap_crosscheck(model: LGBMRegressor, x_sample: np.ndarray, native_contrib: np.ndarray) -> dict[str, object]:
    """Compare LightGBM pred_contrib with shap.TreeExplainer on one sample.

    pred_contrib is the tree SHAP value used in the report. The cross-check
    records the max absolute difference and does not choose a model.
    """
    import shap

    if len(x_sample) == 0:
        return {"rows": 0, "max_abs_diff": None, "note": "no rows"}
    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(x_sample)
    if isinstance(values, list):
        values = values[-1]
    values = np.asarray(values, dtype=np.float64)
    native = native_contrib[:, :-1] if native_contrib.shape[1] == values.shape[1] + 1 else native_contrib
    if values.shape != native.shape:
        return {
            "rows": int(len(x_sample)),
            "max_abs_diff": None,
            "note": f"shape mismatch shap {values.shape} contrib {native.shape}",
        }
    return {
        "rows": int(len(x_sample)),
        "max_abs_diff": float(np.max(np.abs(values - native))),
        "note": "shap.TreeExplainer versus LightGBM pred_contrib on the mean regressor",
    }


def _scaler(x_train: np.ndarray) -> StandardScaler:
    scaler = StandardScaler()
    scaler.fit(x_train)
    return scaler


def _logistic_proba(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    C: float,
    max_iter: int,
    seed: int,
) -> np.ndarray:
    if len(x_test) == 0:
        return np.empty(0, dtype=np.float64)
    classes = np.unique(y_train)
    if len(classes) < 2:
        return np.full(len(x_test), float(classes[0] == 1), dtype=np.float64)
    model = LogisticRegression(C=C, solver="lbfgs", max_iter=max_iter, random_state=seed)
    model.fit(x_train, y_train)
    return _positive_from_classifier(model, x_test)


def _positive_from_classifier(model: LogisticRegression | LGBMClassifier, x_test: np.ndarray) -> np.ndarray:
    if len(x_test) == 0:
        return np.empty(0, dtype=np.float64)
    proba = np.asarray(model.predict_proba(x_test), dtype=np.float64)
    classes = [int(item) for item in model.classes_]
    if 1 not in classes:
        return np.zeros(len(x_test), dtype=np.float64)
    return proba[:, classes.index(1)]


def _ridge_predict(x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, alpha: float) -> np.ndarray:
    model = Ridge(alpha=alpha)
    model.fit(x_train, y_train)
    return np.asarray(model.predict(x_test), dtype=np.float64)


def _ridge_train_predict(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, float, np.ndarray]:
    model = Ridge(alpha=alpha)
    model.fit(x_train, y_train)
    fitted = np.asarray(model.predict(x_train), dtype=np.float64)
    residual = y_train - fitted
    scale = float(np.std(residual, ddof=1)) if len(residual) > 1 else 0.0
    predicted = np.asarray(model.predict(x_test), dtype=np.float64) if len(x_test) else np.empty(0)
    return predicted, scale, np.asarray(model.coef_, dtype=np.float64)


def _lgbm_common(grid: LightGBMSection, choice: LGBMChoice, seed: int) -> dict[str, object]:
    return {
        "learning_rate": grid.learning_rate,
        "n_estimators": grid.n_estimators,
        "num_leaves": choice.num_leaves,
        "min_child_samples": choice.min_child_samples,
        "feature_fraction": grid.feature_fraction,
        "lambda_l2": choice.lambda_l2,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": grid.num_threads,
        "seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
    }


def _lgbm_classifier(grid: LightGBMSection, choice: LGBMChoice, seed: int) -> LGBMClassifier:
    return LGBMClassifier(objective="binary", **_lgbm_common(grid, choice, seed))


def _lgbm_regressor(
    grid: LightGBMSection,
    choice: LGBMChoice,
    seed: int,
    *,
    objective: str,
    alpha: float | None = None,
) -> LGBMRegressor:
    params = _lgbm_common(grid, choice, seed)
    if objective == "quantile":
        params["objective"] = "quantile"
        params["alpha"] = alpha
    else:
        params["objective"] = "regression"
    return LGBMRegressor(**params)


def _lgbm_proba(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    grid: LightGBMSection,
    choice: LGBMChoice,
    seed: int,
) -> np.ndarray:
    if len(np.unique(y_train)) < 2:
        return np.full(len(x_test), float(np.mean(y_train)), dtype=np.float64)
    model = _lgbm_classifier(grid, choice, seed)
    model.fit(x_train, y_train)
    return _positive_from_classifier(model, x_test)
