"""Diagonal Gaussian HMM with forward-filtered state probabilities.

Parameters are fit by EM on observations strictly before train_end. State
count is chosen there by BIC or AIC. Filtered probabilities at time t use
only observations at or before t and the frozen training parameters. This is
the forward filter, not the forward-backward smoother: a later observation
does not revise an earlier probability.

States stay unlabeled. After fitting, they are sorted by the training
emission mean of the first observation column so that index 0 is the lower
mean. That sort is an alignment device. It is not a bull, bear, chop, or
high-volatility name, and those names are not assigned.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pquant.v2.p2_config import hmm_feature_names


@dataclass(frozen=True)
class HMMFitInfo:
    chosen_states: int
    criterion: str
    scores: dict[int, float]
    n_train: int
    loglik: float


def filtered_probabilities(
    observations: np.ndarray,
    *,
    train_end: int,
    state_grid: list[int],
    criterion: str,
    max_iter: int,
    tol: float,
    n_restarts: int,
    seed: int,
    variance_floor: float,
) -> tuple[np.ndarray, HMMFitInfo]:
    """Return (n, max_states) filtered probabilities and the train-only selection."""
    obs = np.asarray(observations, dtype=np.float64)
    if obs.ndim != 2 or obs.shape[1] < 1:
        raise ValueError("HMM observations must be a 2-d array")
    if not 0 < train_end <= len(obs):
        raise ValueError("train_end must index a non-empty training prefix")
    max_states = max(state_grid)
    train = obs[:train_end]
    finite_train = np.isfinite(train).all(axis=1)
    if int(finite_train.sum()) < max(20, max_states * 5):
        raise ValueError("not enough finite training observations to fit an HMM")
    mean = train[finite_train].mean(axis=0)
    std = train[finite_train].std(axis=0)
    std = np.where(std < 1e-8, 1.0, std)
    scaled = (obs - mean) / std
    scaled_train = scaled[:train_end]
    scores: dict[int, float] = {}
    fits: dict[int, _GaussianHMM] = {}
    for n_states in state_grid:
        model, loglik = _fit_best(
            scaled_train,
            n_states=n_states,
            max_iter=max_iter,
            tol=tol,
            n_restarts=n_restarts,
            seed=seed,
            variance_floor=variance_floor,
        )
        n_obs = int(np.isfinite(scaled_train).all(axis=1).sum())
        penalty = _information_criterion(loglik, _n_params(n_states, obs.shape[1]), n_obs, criterion)
        scores[n_states] = penalty
        fits[n_states] = model
    chosen = min(scores, key=lambda count: (scores[count], count))
    model = fits[chosen]
    order = np.argsort(model.means[:, 0], kind="mergesort")
    model = model.permute(order)
    filtered = _forward_filter(scaled, model)
    padded = np.full((len(obs), max_states), np.nan, dtype=np.float64)
    finite_row = np.isfinite(filtered).all(axis=1)
    padded[finite_row, :chosen] = filtered[finite_row]
    padded[finite_row, chosen:] = 0.0
    info = HMMFitInfo(
        chosen_states=int(chosen),
        criterion=criterion,
        scores={int(key): float(value) for key, value in scores.items()},
        n_train=int(finite_train.sum()),
        loglik=float(_sequence_loglik(scaled_train, model)),
    )
    return padded, info


def probability_column_names(max_states: int) -> list[str]:
    return hmm_feature_names(max_states)


@dataclass
class _GaussianHMM:
    start: np.ndarray
    transition: np.ndarray
    means: np.ndarray
    variances: np.ndarray

    def permute(self, order: np.ndarray) -> _GaussianHMM:
        return _GaussianHMM(
            start=self.start[order],
            transition=self.transition[np.ix_(order, order)],
            means=self.means[order],
            variances=self.variances[order],
        )


def _fit_best(
    scaled: np.ndarray,
    *,
    n_states: int,
    max_iter: int,
    tol: float,
    n_restarts: int,
    seed: int,
    variance_floor: float,
) -> tuple[_GaussianHMM, float]:
    finite = scaled[np.isfinite(scaled).all(axis=1)]
    rng = np.random.default_rng(seed + n_states * 17)
    best_model: _GaussianHMM | None = None
    best_ll = -np.inf
    for restart in range(n_restarts):
        model = _initialize(finite, n_states, rng, restart, variance_floor)
        model, loglik = _em(scaled, model, max_iter=max_iter, tol=tol, variance_floor=variance_floor)
        if loglik > best_ll:
            best_model = model
            best_ll = loglik
    if best_model is None or not np.isfinite(best_ll):
        raise ValueError(f"HMM with {n_states} states did not produce a finite likelihood")
    return best_model, float(best_ll)


def _initialize(
    finite: np.ndarray,
    n_states: int,
    rng: np.random.Generator,
    restart: int,
    variance_floor: float,
) -> _GaussianHMM:
    order = np.argsort(finite[:, 0], kind="mergesort")
    ordered = finite[order]
    bins = np.array_split(ordered, n_states)
    means = np.vstack([chunk.mean(axis=0) if len(chunk) else finite.mean(axis=0) for chunk in bins])
    variances = np.vstack(
        [
            chunk.var(axis=0) if len(chunk) > 1 else np.ones(finite.shape[1])
            for chunk in bins
        ]
    )
    if restart == 1:
        means = means + rng.normal(0.0, 0.25, size=means.shape)
    elif restart > 1:
        means = means[::-1] + rng.normal(0.0, 0.1, size=means.shape)
    variances = np.maximum(variances, variance_floor)
    start = np.full(n_states, 1.0 / n_states)
    transition = np.full((n_states, n_states), 0.15 / max(n_states - 1, 1))
    np.fill_diagonal(transition, 0.85)
    transition = transition / transition.sum(axis=1, keepdims=True)
    return _GaussianHMM(start=start, transition=transition, means=means, variances=variances)


def _em(
    scaled: np.ndarray,
    model: _GaussianHMM,
    *,
    max_iter: int,
    tol: float,
    variance_floor: float,
) -> tuple[_GaussianHMM, float]:
    finite_index = np.flatnonzero(np.isfinite(scaled).all(axis=1))
    series = scaled[finite_index]
    previous = -np.inf
    current = model
    loglik = -np.inf
    for _ in range(max_iter):
        log_emis = _log_emission(series, current)
        log_alpha, log_beta, loglik = _forward_backward(log_emis, current)
        if not np.isfinite(loglik):
            break
        current = _m_step(series, log_emis, log_alpha, log_beta, current, variance_floor)
        if loglik - previous < tol and previous > -np.inf:
            break
        previous = loglik
    full_ll = _sequence_loglik(scaled, current)
    return current, full_ll


def _m_step(
    series: np.ndarray,
    log_emis: np.ndarray,
    log_alpha: np.ndarray,
    log_beta: np.ndarray,
    model: _GaussianHMM,
    variance_floor: float,
) -> _GaussianHMM:
    log_gamma = log_alpha + log_beta
    log_gamma = log_gamma - _logsumexp(log_gamma, axis=1)[:, None]
    gamma = np.exp(log_gamma)
    n_states = model.start.shape[0]
    log_trans = np.log(np.maximum(model.transition, 1e-12))
    if len(series) < 2:
        xi_sum = np.zeros((n_states, n_states))
    else:
        log_xi = (
            log_alpha[:-1, :, None]
            + log_trans[None, :, :]
            + log_emis[1:, None, :]
            + log_beta[1:, None, :]
        )
        flat = log_xi.reshape(len(series) - 1, -1)
        log_xi = log_xi - _logsumexp(flat, axis=1)[:, None, None]
        xi_sum = np.exp(_logsumexp(log_xi, axis=0))
    transition = xi_sum / np.maximum(xi_sum.sum(axis=1, keepdims=True), 1e-12)
    start = gamma[0] / max(gamma[0].sum(), 1e-12)
    weight = gamma.sum(axis=0)
    means = (gamma.T @ series) / np.maximum(weight[:, None], 1e-12)
    variances = np.zeros_like(means)
    for state in range(n_states):
        delta = series - means[state]
        variances[state] = (gamma[:, state][:, None] * delta * delta).sum(axis=0) / max(weight[state], 1e-12)
    variances = np.maximum(variances, variance_floor)
    return _GaussianHMM(start=start, transition=transition, means=means, variances=variances)


def _forward_backward(
    log_emis: np.ndarray,
    model: _GaussianHMM,
) -> tuple[np.ndarray, np.ndarray, float]:
    n_times, n_states = log_emis.shape
    log_start = np.log(np.maximum(model.start, 1e-12))
    log_trans = np.log(np.maximum(model.transition, 1e-12))
    log_alpha = np.empty_like(log_emis)
    log_alpha[0] = log_start + log_emis[0]
    for time in range(1, n_times):
        log_alpha[time] = log_emis[time] + _logsumexp(log_alpha[time - 1][:, None] + log_trans, axis=0)
    log_beta = np.zeros_like(log_emis)
    for time in range(n_times - 2, -1, -1):
        log_beta[time] = _logsumexp(log_trans + log_emis[time + 1][None, :] + log_beta[time + 1][None, :], axis=1)
    loglik = float(_logsumexp(log_alpha[-1], axis=0))
    return log_alpha, log_beta, loglik


def _forward_filter(scaled: np.ndarray, model: _GaussianHMM) -> np.ndarray:
    """Causal filter. A non-finite row resets the state and stays NaN."""
    n_times = len(scaled)
    n_states = model.start.shape[0]
    probs = np.full((n_times, n_states), np.nan, dtype=np.float64)
    log_start = np.log(np.maximum(model.start, 1e-12))
    log_trans = np.log(np.maximum(model.transition, 1e-12))
    log_alpha: np.ndarray | None = None
    for time in range(n_times):
        row = scaled[time]
        if not np.isfinite(row).all():
            log_alpha = None
            continue
        log_emis = _log_emission(row[None, :], model)[0]
        if log_alpha is None:
            log_alpha = log_start + log_emis
        else:
            log_alpha = log_emis + _logsumexp(log_alpha[:, None] + log_trans, axis=0)
        probs[time] = np.exp(log_alpha - _logsumexp(log_alpha, axis=0))
    return probs


def _sequence_loglik(scaled: np.ndarray, model: _GaussianHMM) -> float:
    finite = scaled[np.isfinite(scaled).all(axis=1)]
    if len(finite) == 0:
        return -np.inf
    log_emis = _log_emission(finite, model)
    _, _, loglik = _forward_backward(log_emis, model)
    return loglik


def _log_emission(series: np.ndarray, model: _GaussianHMM) -> np.ndarray:
    # series (T, D), means/variances (K, D)
    delta = series[:, None, :] - model.means[None, :, :]
    var = np.maximum(model.variances, 1e-12)
    quad = (delta * delta / var[None, :, :]).sum(axis=2)
    log_det = np.log(var).sum(axis=1)
    return -0.5 * (series.shape[1] * np.log(2.0 * np.pi) + log_det[None, :] + quad)


def _n_params(n_states: int, n_dim: int) -> int:
    start = n_states - 1
    transition = n_states * (n_states - 1)
    mean = n_states * n_dim
    variance = n_states * n_dim
    return start + transition + mean + variance


def _information_criterion(loglik: float, n_params: int, n_obs: int, criterion: str) -> float:
    if criterion == "bic":
        return -2.0 * loglik + n_params * np.log(n_obs)
    if criterion == "aic":
        return -2.0 * loglik + 2.0 * n_params
    raise ValueError(f"unsupported criterion {criterion}")


def _logsumexp(values: np.ndarray, axis: int) -> np.ndarray:
    maximum = np.max(values, axis=axis, keepdims=True)
    stable = np.exp(values - maximum)
    total = np.sum(stable, axis=axis)
    out = np.squeeze(maximum, axis=axis) + np.log(np.maximum(total, 1e-300))
    return out
