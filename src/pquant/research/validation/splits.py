"""Contiguous time splits. Shuffling a series into train and test is not implemented."""

from __future__ import annotations

from decimal import Decimal


def time_split_bounds(
    n: int,
    train_ratio: Decimal | float,
    validation_ratio: Decimal | float,
    test_ratio: Decimal | float,
) -> dict[str, tuple[int, int]]:
    total = float(train_ratio) + float(validation_ratio) + float(test_ratio)
    if abs(total - 1.0) > 1e-9:
        raise ValueError("split ratios must sum to 1")
    if n < 0:
        raise ValueError("n must be non-negative")
    train_end = int(n * float(train_ratio))
    val_end = int(n * (float(train_ratio) + float(validation_ratio)))
    train_end = min(max(train_end, 0), n)
    val_end = min(max(val_end, train_end), n)
    bounds = {
        "train": (0, train_end),
        "validation": (train_end, val_end),
        "test": (val_end, n),
    }
    cursor = 0
    for name in ("train", "validation", "test"):
        start, end = bounds[name]
        if start != cursor or end < start or end > n:
            raise ValueError(f"split {name} is not a contiguous forward slice")
        cursor = end
    if cursor != n:
        raise ValueError("splits do not cover the sample")
    return bounds


def carried_target(close_targets: list, opening_target, start: int):
    """Target known at the open of `start`, using only decisions at indexes < start."""
    if start <= 0:
        return opening_target
    last = None
    found = False
    for index in range(start):
        value = close_targets[index]
        if value is not None:
            last = value
            found = True
    if found:
        return last
    return opening_target
