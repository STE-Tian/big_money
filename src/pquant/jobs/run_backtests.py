"""Run the v1 baseline backtests on local Parquet data and write the report."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pquant.domain.errors import DataQualityError, SafetyError
from pquant.ingestion.quality import closed_only
from pquant.jobs.common import bootstrap
from pquant.research.backtest.report_markdown import render_backtest_report
from pquant.research.pipeline import analyze_symbol
from pquant.services.market import load_candles
from pquant.storage.research_store import ResearchStore


def main(argv: list[str] | None = None) -> int:
    settings, logger = bootstrap(argv, "Run baseline backtests on stored candles.")
    ResearchStore(settings.storage.sqlite_path).init_schema()
    generated_at = datetime.now(timezone.utc)
    summary = {
        "generated_at": generated_at.isoformat(),
        "code_commit": None,
        "base_url": settings.market_data.base_url,
        "assumptions": _assumptions(settings),
        "symbols": {},
        "blockers": _blockers(settings),
    }
    any_success = False
    for symbol in settings.market_data.symbols:
        for interval in settings.market_data.intervals:
            candles = load_candles(settings, symbol, interval)
            if not candles:
                message = f"No local candles for {symbol} {interval}."
                logger.error(message)
                summary["blockers"].append(message)
                continue
            closed = closed_only(candles)
            logger.info("Backtesting %s %s closed_rows=%s", symbol, interval, len(closed))
            try:
                payload = analyze_symbol(
                    candles,
                    settings,
                    reports_dir=settings.storage.reports_dir,
                    generated_at=generated_at,
                    persist=True,
                )
            except (DataQualityError, SafetyError) as exc:
                logger.error("Backtest refused for %s %s: %s", symbol, interval, exc)
                summary["blockers"].append(f"{symbol} {interval}: {exc}")
                continue
            summary["symbols"][symbol] = payload
            summary["code_commit"] = payload.get("code_commit")
            if payload.get("strategies") and not payload.get("error"):
                any_success = True
            for strategy_id, item in (payload.get("strategies") or {}).items():
                judgment = (item.get("judgment") or {}).get("label")
                full = ((item.get("segments") or {}).get("full") or {}).get("metrics") or {}
                total = (full.get("total_return") or {}).get("value")
                logger.info("%s %s full_return=%s judgment=%s run=%s", symbol, strategy_id, total, judgment, item.get("run_id"))
    text = render_backtest_report(summary)
    report_path = settings.root / "BACKTEST_REPORT.md"
    report_path.write_text(text, encoding="utf-8")
    summary_path = settings.storage.reports_dir / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s and %s", report_path, summary_path)
    print(f"Wrote {report_path}")
    return 0 if any_success else 1


def _assumptions(settings) -> dict[str, str]:
    base = settings.scenario("base")
    stress_1 = settings.scenario("stress_1")
    stress_2 = settings.scenario("stress_2")
    return {
        "initial_cash": format(settings.backtest.initial_cash, "f"),
        "quote_asset": settings.backtest.quote_asset,
        "base_fee": format(base.fee_rate_per_side, "f"),
        "base_slippage": format(base.slippage_rate_per_side, "f"),
        "stress_1_fee": format(stress_1.fee_rate_per_side, "f"),
        "stress_1_slippage": format(stress_1.slippage_rate_per_side, "f"),
        "stress_2_fee": format(stress_2.fee_rate_per_side, "f"),
        "stress_2_slippage": format(stress_2.slippage_rate_per_side, "f"),
        "risk_free_rate": format(settings.backtest.risk_free_rate, "f"),
    }


def _blockers(settings) -> list[str]:
    path = settings.storage.reports_dir / "ingest_error.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [path.read_text(encoding="utf-8")]
    if isinstance(payload, list):
        return [str(item.get("error") if isinstance(item, dict) else item) for item in payload]
    return [str(payload)]
