"""Purging and embargo for labels that overlap an evaluation window.

The label of a decision at bar i with horizon H uses the closes of bars
i+1 through i+H. Those bars are after the decision. A training row is removed
when that span intersects the evaluation interval. Rows inside the evaluation
interval are not training rows. Embargo also removes training rows whose
decision index falls in [eval_end, eval_end + embargo_bars), the ban after
the evaluation interval required by the V2 spec.

Walk-forward training uses only decisions before the test start. The embargo
clause still matters when a split has training rows on both sides of the
evaluation interval, which the tests cover and which later folds would hit
if training were allowed to follow a test block.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class IndexSplit:
    test_start: int
    test_end: int


def training_mask(
    decision_index: np.ndarray,
    label_end_index: np.ndarray,
    eval_start: int,
    eval_end: int,
    embargo_bars: int,
) -> np.ndarray:
    """True where a row may be used as a training row for this evaluation window.

    label_end_index is the bar whose close completes the label, or -1 when the
    label is undefined. Undefined labels are not trainable.
    """
    if eval_end <= eval_start:
        raise ValueError("evaluation interval must be non-empty")
    if embargo_bars < 0:
        raise ValueError("embargo_bars must be >= 0")
    decision = np.asarray(decision_index, dtype=np.int64)
    label_end = np.asarray(label_end_index, dtype=np.int64)
    if decision.shape != label_end.shape:
        raise ValueError("decision index and label end index must have the same shape")
    valid = label_end >= 0
    inside = (decision >= eval_start) & (decision < eval_end)
    # Label bars are (decision, label_end]. They overlap [eval_start, eval_end).
    overlaps = valid & (label_end >= eval_start) & ((decision + 1) < eval_end)
    embargo = (decision >= eval_end) & (decision < eval_end + embargo_bars) if embargo_bars else np.zeros(len(decision), dtype=bool)
    return valid & ~inside & ~overlaps & ~embargo


def walk_forward_splits(
    n_bars: int,
    *,
    train_min_bars: int,
    validation_bars: int,
    test_bars: int,
    step_bars: int,
    max_horizon: int,
    embargo_bars: int,
) -> list[IndexSplit]:
    """Expanding-window test blocks that leave room for a purged fit and validation slice.

    The origin is early enough that, for the longest horizon, the fit slice
    still has about train_min_bars decisions after purging against validation,
    and validation labels end before the test starts. Partial trailing blocks
    are not emitted.
    """
    if min(train_min_bars, validation_bars, test_bars, step_bars) < 1:
        raise ValueError("walk-forward lengths must be positive")
    if max_horizon < 1 or embargo_bars < 0:
        raise ValueError("horizon and embargo must be non-negative, and horizon at least 1")
    origin = train_min_bars + validation_bars + 2 * max_horizon + embargo_bars
    splits: list[IndexSplit] = []
    start = origin
    while start + test_bars <= n_bars:
        splits.append(IndexSplit(test_start=start, test_end=start + test_bars))
        start += step_bars
    return splits


def validation_bounds(test_start: int, horizon: int, validation_bars: int) -> tuple[int, int]:
    """Decision-index slice [val_start, val_end) whose labels finish before test_start."""
    if horizon < 1 or validation_bars < 1:
        raise ValueError("horizon and validation_bars must be positive")
    val_end = test_start - horizon
    val_start = val_end - validation_bars
    if val_start < horizon:
        raise ValueError("not enough bars to purge a validation split from the following test")
    return val_start, val_end
