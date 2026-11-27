"""Whitelist of research tools. Live orders, secrets, and generated code are not callable."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pquant.agents.schemas import ExperimentSpec
from pquant.config.settings import Settings
from pquant.domain.errors import SafetyError
from pquant.domain.models import Candle
from pquant.research.backtest.engine import run_backtest
from pquant.research.backtest.metrics import compute_metrics
from pquant.research.strategies.momentum import MomentumStrategy
from pquant.research.strategies.registry import MOMENTUM_GRID, SMA_LONG_GRID, SMA_SHORT_GRID, build_strategies
from pquant.research.strategies.sma_cross import SmaCrossStrategy
from pquant.research.validation.splits import carried_target, time_split_bounds
from pquant.utils.hashing import dataset_hash

ALLOWED_TOOLS = (
    "list_available_symbols",
    "get_market_data",
    "get_data_quality_report",
    "list_strategies",
    "create_experiment",
    "run_backtest",
    "compare_backtests",
    "get_risk_metrics",
    "generate_report",
)

FORBIDDEN_TOOLS = (
    "place_live_order",
    "cancel_live_order",
    "withdraw_asset",
    "transfer_asset",
    "modify_api_permissions",
    "read_environment",
    "execute_python",
    "execute_sql",
    "execute_shell",
)


class ResearchToolbox:
    def __init__(
        self,
        settings: Settings,
        candles_by_symbol: dict[str, list[Candle]],
        quality_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings
        self.candles_by_symbol = candles_by_symbol
        self.quality_by_symbol = quality_by_symbol or {}
        self.experiments: list[dict[str, Any]] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.backtests_used = 0

    def invoke(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if name in FORBIDDEN_TOOLS or name not in ALLOWED_TOOLS:
            raise SafetyError(f"tool is not on the research whitelist: {name}")
        method = getattr(self, name)
        return method(payload or {})

    def list_available_symbols(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"symbols": list(self.settings.universe_symbols)}

    def get_market_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol", "")).upper()
        self._require_symbol(symbol)
        candles = self.candles_by_symbol.get(symbol) or []
        if not candles:
            return {"symbol": symbol, "error": "暂无结果"}
        closed = [c for c in candles if c.is_closed]
        last = closed[-1] if closed else None
        return {
            "symbol": symbol,
            "interval": candles[0].interval,
            "rows_closed": len(closed),
            "start": closed[0].open_time.isoformat() if closed else None,
            "end": closed[-1].close_time.isoformat() if closed else None,
            "last_close": format(last.close, "f") if last else None,
            "data_origin": candles[0].source,
            "dataset_hash": dataset_hash(closed),
        }

    def get_data_quality_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol", "")).upper()
        self._require_symbol(symbol)
        report = self.quality_by_symbol.get(symbol)
        if not report:
            return {"symbol": symbol, "error": "暂无结果"}
        return report

    def list_strategies(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "strategies": [
                {"strategy_id": item.strategy_id, "version": item.version}
                for item in build_strategies(self.settings)
            ]
        }

    def create_experiment(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = ExperimentSpec.model_validate(payload)
        errors = self._validate_spec(spec)
        if errors:
            return {"ok": False, "errors": errors}
        experiment_id = f"exp-{len(self.experiments) + 1}"
        record = {"experiment_id": experiment_id, "spec": spec.model_dump()}
        self.experiments.append(record)
        return {"ok": True, "experiment_id": experiment_id, "spec": spec.model_dump()}

    def run_backtest(self, payload: dict[str, Any]) -> dict[str, Any]:
        budget = min(int(payload.get("experiment_budget") or 1), 10)
        if self.backtests_used >= budget:
            raise SafetyError("experiment budget exhausted")
        symbol = str(payload["symbol"]).upper()
        self._require_symbol(symbol)
        strategy_id = str(payload["strategy_id"])
        candles = [c for c in self.candles_by_symbol.get(symbol, []) if c.is_closed]
        if len(candles) < 2:
            return {"error": "暂无结果", "symbol": symbol}
        strategy = self._strategy(strategy_id, payload.get("parameter_overrides") or {})
        scenario_fee = self.settings.scenario(str(payload.get("fee_scenario") or "base"))
        scenario_slip = self.settings.scenario(str(payload.get("slippage_scenario") or "base"))
        output = strategy.run(candles)
        start, end = _date_slice(candles, payload.get("date_start"), payload.get("date_end"))
        opening = output.opening_target if start == 0 else carried_target(output.close_targets, output.opening_target, start)
        result = run_backtest(
            candles[start:end],
            output.close_targets[start:end],
            opening_target=opening,
            initial_cash=self.settings.backtest.initial_cash,
            fee_rate=scenario_fee.fee_rate_per_side,
            slippage_rate=scenario_slip.slippage_rate_per_side,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            symbol=symbol,
            interval=candles[0].interval,
            segment="agent_full",
        )
        metrics = compute_metrics(
            result,
            start=candles[start].open_time,
            end=candles[end - 1].close_time,
            risk_free_rate=self.settings.backtest.risk_free_rate,
            interval=candles[0].interval,
        )
        segments = {}
        if payload.get("validation_scheme") == "time_split" and end - start >= 5:
            bounds = time_split_bounds(
                end - start,
                self.settings.validation.train_ratio,
                self.settings.validation.validation_ratio,
                self.settings.validation.test_ratio,
            )
            local = candles[start:end]
            local_targets = output.close_targets[start:end]
            for name, (seg_start, seg_end) in bounds.items():
                if seg_end - seg_start < 2:
                    continue
                opening_seg = output.opening_target if start + seg_start == 0 else carried_target(
                    output.close_targets, output.opening_target, start + seg_start
                )
                seg = run_backtest(
                    local[seg_start:seg_end],
                    local_targets[seg_start:seg_end],
                    opening_target=opening_seg,
                    initial_cash=self.settings.backtest.initial_cash,
                    fee_rate=scenario_fee.fee_rate_per_side,
                    slippage_rate=scenario_slip.slippage_rate_per_side,
                    strategy_id=strategy.strategy_id,
                    strategy_version=strategy.version,
                    symbol=symbol,
                    interval=candles[0].interval,
                    segment=name,
                )
                seg_metrics = compute_metrics(
                    seg,
                    start=local[seg_start].open_time,
                    end=local[seg_end - 1].close_time,
                    risk_free_rate=self.settings.backtest.risk_free_rate,
                    interval=candles[0].interval,
                )
                segments[name] = {key: metric.value for key, metric in seg_metrics.items()}
        self.backtests_used += 1
        run_id = f"run-{self.backtests_used}"
        origin = candles[0].source
        record = {
            "run_id": run_id,
            "symbol": symbol,
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.version,
            "data_origin": origin,
            "synthetic": origin.startswith("synthetic"),
            "dataset_hash": dataset_hash(candles[start:end]),
            "metrics": {key: metric.value for key, metric in metrics.items()},
            "segments": segments,
            "fee_rate_per_side": format(scenario_fee.fee_rate_per_side, "f"),
            "slippage_rate_per_side": format(scenario_slip.slippage_rate_per_side, "f"),
        }
        self.runs[run_id] = record
        return record

    def compare_backtests(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = []
        for run_id in payload.get("run_ids") or []:
            run = self.runs.get(run_id)
            if run is None:
                rows.append({"run_id": run_id, "error": "暂无结果"})
                continue
            rows.append(
                {
                    "run_id": run_id,
                    "symbol": run["symbol"],
                    "strategy_id": run["strategy_id"],
                    "total_return": run["metrics"].get("total_return"),
                    "sharpe_ratio": run["metrics"].get("sharpe_ratio"),
                    "max_drawdown": run["metrics"].get("max_drawdown"),
                    "fee_paid": run["metrics"].get("fee_paid"),
                }
            )
        return {"runs": rows}

    def get_risk_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.runs.get(str(payload.get("run_id")))
        if run is None:
            return {"error": "暂无结果"}
        metrics = run["metrics"]
        return {
            "run_id": run["run_id"],
            "max_drawdown": metrics.get("max_drawdown"),
            "annualized_volatility": metrics.get("annualized_volatility"),
            "fee_paid": metrics.get("fee_paid"),
            "slippage_cost": metrics.get("slippage_cost"),
            "exposure": metrics.get("exposure"),
        }

    def generate_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        lines = [
            "Research report generated from tool results only.",
            "Historical numbers are not a forecast and are not permission to trade real money.",
        ]
        for run_id in payload.get("run_ids") or []:
            run = self.runs.get(run_id)
            if run is None:
                lines.append(f"{run_id}: 暂无结果")
                continue
            origin = "SYNTHETIC TEST" if run["synthetic"] else "binance public market data"
            lines.append(
                f"run_id={run['run_id']} origin={origin} symbol={run['symbol']} strategy={run['strategy_id']} "
                f"total_return={run['metrics'].get('total_return')} sharpe={run['metrics'].get('sharpe_ratio')} "
                f"max_drawdown={run['metrics'].get('max_drawdown')} fee_paid={run['metrics'].get('fee_paid')}"
            )
        text = "\n".join(lines)
        return {"report": text, "run_ids": list(payload.get("run_ids") or [])}

    def _require_symbol(self, symbol: str) -> None:
        if symbol not in self.settings.universe_symbols:
            raise SafetyError(f"symbol {symbol} is outside the configured universe")

    def _validate_spec(self, spec: ExperimentSpec) -> list[str]:
        errors: list[str] = []
        known = {item.strategy_id for item in build_strategies(self.settings)}
        for symbol in spec.symbols:
            if symbol.upper() not in self.settings.universe_symbols:
                errors.append(f"symbol {symbol} is outside the universe")
        for strategy_id in spec.strategy_ids:
            if strategy_id not in known:
                errors.append(f"strategy {strategy_id} is not registered")
        for strategy_id, params in spec.parameter_overrides.items():
            if strategy_id == "sma_cross":
                short = params.get("short_window")
                long = params.get("long_window")
                if short not in SMA_SHORT_GRID or long not in SMA_LONG_GRID or short >= long:
                    errors.append("sma override is outside the pre-registered grid")
            elif strategy_id == "momentum":
                if params.get("window") not in MOMENTUM_GRID:
                    errors.append("momentum override is outside the pre-registered grid")
            else:
                errors.append(f"{strategy_id} does not accept parameter overrides")
        product = len(spec.symbols) * len(spec.strategy_ids)
        if product > spec.experiment_budget:
            errors.append("requested runs exceed experiment_budget")
        return errors

    def _strategy(self, strategy_id: str, overrides: dict[str, Any]):
        params = overrides.get(strategy_id) or {}
        if strategy_id == "sma_cross" and params:
            short = int(params["short_window"])
            long = int(params["long_window"])
            if short not in SMA_SHORT_GRID or long not in SMA_LONG_GRID or short >= long:
                raise SafetyError("sma override is outside the pre-registered grid")
            return SmaCrossStrategy(short_window=short, long_window=long)
        if strategy_id == "momentum" and params:
            window = int(params["window"])
            if window not in MOMENTUM_GRID:
                raise SafetyError("momentum override is outside the pre-registered grid")
            return MomentumStrategy(window=window)
        for strategy in build_strategies(self.settings):
            if strategy.strategy_id == strategy_id:
                return strategy
        raise SafetyError(f"strategy {strategy_id} is not registered")


def _date_slice(candles: list[Candle], start: str | None, end: str | None) -> tuple[int, int]:
    start_dt = _aware(start) if start else None
    end_dt = _aware(end) if end else None
    indexes = range(len(candles))
    first = 0
    last = len(candles)
    for index in indexes:
        if start_dt is not None and candles[index].open_time < start_dt:
            first = index + 1
        if end_dt is not None and candles[index].open_time >= end_dt and last == len(candles):
            last = index
    if last - first < 2:
        return 0, len(candles)
    return first, last


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
