"""Pre-registered decision rule.

The labels are research triage labels. CANDIDATE_FOR_PAPER is not permission to trade
real money and is not a claim of future profit.

Evaluation order:
1. Unqualified data or an accounting failure -> REJECT.
2. Test span under the configured minimum -> INCONCLUSIVE, history_status INSUFFICIENT_HISTORY.
3. Test total return <= -20% and test Sharpe < 0 -> REJECT.
4. Every CANDIDATE condition below -> CANDIDATE_FOR_PAPER.
5. Otherwise INCONCLUSIVE.

CANDIDATE_FOR_PAPER requires all of:
- qualified data
- test span >= minimum_test_days (default 180)
- test total return > 0
- test Sharpe defined and > 0
- test max drawdown > -0.60
- stress-1 test total return > 0
- stress-2 test total return > -0.10
- train total return > 0 and test total return > 0
- for parameterized strategies, at least half of the validation grid has validation total return > 0
- if fixed-parameter walk-forward ran, at least half of its windows have total return >= 0

Cash is BENCHMARK_ONLY and is not judged with this rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class JudgmentInput:
    strategy_id: str
    data_quality_status: str
    accounting_ok: bool
    test_span_days: float | None
    minimum_test_days: int
    test_total_return: float | None
    test_sharpe: float | None
    test_max_drawdown: float | None
    train_total_return: float | None
    stress_1_test_total_return: float | None
    stress_2_test_total_return: float | None
    validation_grid_positive_fraction: float | None
    walk_forward_nonnegative_fraction: float | None
    parameterized: bool


@dataclass
class Judgment:
    label: str
    history_status: str
    reasons: list[str] = field(default_factory=list)


def judge(item: JudgmentInput) -> Judgment:
    if item.strategy_id == "cash":
        return Judgment("BENCHMARK_ONLY", "not_applicable", ["cash is the zero-return benchmark"])
    if item.data_quality_status != "qualified" or not item.accounting_ok:
        return Judgment(
            "REJECT",
            "data_or_accounting_failed",
            ["data quality is not qualified or an accounting check failed"],
        )
    span = item.test_span_days
    if span is None or span < item.minimum_test_days:
        return Judgment(
            "INCONCLUSIVE",
            "INSUFFICIENT_HISTORY",
            ["test span is below the pre-registered minimum, so the out-of-sample result is not treated as validated"],
        )
    reasons: list[str] = []
    if (
        item.test_total_return is not None
        and item.test_sharpe is not None
        and item.test_total_return <= -0.20
        and item.test_sharpe < 0
    ):
        return Judgment(
            "REJECT",
            "ok",
            ["test total return is <= -20% and test Sharpe is negative"],
        )
    checks = [
        (item.test_total_return is not None and item.test_total_return > 0, "test total return is not positive"),
        (item.test_sharpe is not None and item.test_sharpe > 0, "test Sharpe is not defined and positive"),
        (item.test_max_drawdown is not None and item.test_max_drawdown > -0.60, "test max drawdown is not above -60%"),
        (
            item.stress_1_test_total_return is not None and item.stress_1_test_total_return > 0,
            "stress-1 test total return is not positive",
        ),
        (
            item.stress_2_test_total_return is not None and item.stress_2_test_total_return > -0.10,
            "stress-2 test total return is not above -10%",
        ),
        (
            item.train_total_return is not None and item.train_total_return > 0 and item.test_total_return is not None and item.test_total_return > 0,
            "train and test total returns are not both positive",
        ),
    ]
    if item.parameterized:
        checks.append(
            (
                item.validation_grid_positive_fraction is not None and item.validation_grid_positive_fraction >= 0.5,
                "fewer than half of the validation parameter rows have positive validation return",
            )
        )
    if item.walk_forward_nonnegative_fraction is not None:
        checks.append(
            (
                item.walk_forward_nonnegative_fraction >= 0.5,
                "fewer than half of the fixed-parameter walk-forward windows have non-negative return",
            )
        )
    failed = [reason for ok, reason in checks if not ok]
    if not failed:
        return Judgment(
            "CANDIDATE_FOR_PAPER",
            "ok",
            ["pre-registered paper-observation rule passed; this is not permission to trade real money"],
        )
    reasons.extend(failed)
    return Judgment("INCONCLUSIVE", "ok", reasons)
