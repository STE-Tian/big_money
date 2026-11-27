"""Create DuckDB and SQLite schemas."""

from __future__ import annotations

from pquant.jobs.common import bootstrap
from pquant.storage.market_store import MarketStore
from pquant.storage.research_store import ResearchStore
from pquant.storage.trading_store import TradingStore


def main(argv: list[str] | None = None) -> int:
    settings, logger = bootstrap(argv, "Initialize local DuckDB and SQLite databases.")
    for directory in (
        settings.storage.duckdb_path.parent,
        settings.storage.sqlite_path.parent,
        settings.storage.parquet_dir,
        settings.storage.raw_dir,
        settings.storage.reports_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    MarketStore(settings.storage.duckdb_path).init_schema()
    ResearchStore(settings.storage.sqlite_path).init_schema()
    TradingStore(settings.storage.sqlite_path).init_schema()
    logger.info("Initialized %s and %s", settings.storage.duckdb_path, settings.storage.sqlite_path)
    print(f"Initialized {settings.storage.duckdb_path}")
    print(f"Initialized {settings.storage.sqlite_path}")
    return 0
