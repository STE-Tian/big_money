"""Structured experiment input. Extra fields are rejected, including any code payload."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=500)
    symbols: list[str] = Field(min_length=1)
    timeframe: Literal["1d", "4h", "1h"]
    strategy_ids: list[str] = Field(min_length=1)
    parameter_overrides: dict[str, dict[str, int]] = Field(default_factory=dict)
    fee_scenario: Literal["base", "stress_1", "stress_2"] = "base"
    slippage_scenario: Literal["base", "stress_1", "stress_2"] = "base"
    validation_scheme: Literal["full", "time_split"] = "full"
    experiment_budget: int = Field(default=6, ge=1, le=10)
    date_start: str | None = None
    date_end: str | None = None
